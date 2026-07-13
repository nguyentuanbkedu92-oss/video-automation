import os
import random
import asyncio
import subprocess
import gspread
from google.oauth2.service_account import Credentials as SACredentials
from google.oauth2.credentials import Credentials as OAuthCredentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
import edge_tts

# ==== CẤU HÌNH ====
SHEET_ID = "1DSK50AbuwB4-VxkrrGaCkYbp-2TtZALrPi5-8oLLxPY"
DRIVE_FOLDER_ID = "19KH22GTYcCa-I9hC_Ive25FSz9gJeEUt"  # thư mục Video Output (thuộc thanhdatledtdl@gmail.com)
VOICE = "vi-VN-NamMinhNeural"
LOGO_PATH = "logo.png"

FOLDER_VIDEO_NEN = {
    "DenDuong": "1jxGQJEfGRRh8PklSo0wD56QNDccBymKj",
    "DenPha": "11T-n6iQwLhj2iC5rUjnt1dYbbsnj8bwC",
    "DenSanTennis": "12QufTG5Jn1_KAuUTVqDcv0e0dDcQPSzF",
}

TEXT_LIEN_HE = "THANH DAT LED - 0986474671 - 0924734666"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# --- Xác thực bằng Service Account: dùng để đọc Sheet + tải video nền ---
sa_creds = SACredentials.from_service_account_file("service_account.json", scopes=SCOPES)
gc = gspread.authorize(sa_creds)
drive_service_read = build("drive", "v3", credentials=sa_creds)

# --- Xác thực bằng OAuth (tài khoản thanhdatledtdl@gmail.com): dùng để upload video ---
oauth_creds = OAuthCredentials(
    token=None,
    refresh_token=os.environ["OAUTH_REFRESH_TOKEN"],
    client_id=os.environ["OAUTH_CLIENT_ID"],
    client_secret=os.environ["OAUTH_CLIENT_SECRET"],
    token_uri="https://oauth2.googleapis.com/token",
    scopes=["https://www.googleapis.com/auth/drive"],
)
drive_service_upload = build("drive", "v3", credentials=oauth_creds)

sheet = gc.open_by_key(SHEET_ID).sheet1


def lay_danh_sach_video(folder_id):
    results = drive_service_read.files().list(
        q=f"'{folder_id}' in parents and mimeType contains 'video/' and trashed = false",
        fields="files(id, name)"
    ).execute()
    return results.get("files", [])


def tai_video_ve(file_id, out_path):
    request = drive_service_read.files().get_media(fileId=file_id)
    with open(out_path, "wb") as f:
        downloader = MediaIoBaseDownload(f, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()


async def text_to_speech(text, out_path):
    communicate = edge_tts.Communicate(text, VOICE)
    await communicate.save(out_path)


def ghep_video(audio_path, background_video, out_path):
    filter_complex = (
        f"[0:v]scale=1280:720[bg];"
        f"[bg][1:v]overlay=W-w-20:20[withlogo];"
        f"[withlogo]drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
        f"text='{TEXT_LIEN_HE}':fontsize=32:fontcolor=white:"
        f"borderw=2:bordercolor=black@0.7:"
        f"x=w-mod(t*120\\,w+text_w):y=h-60[outv]"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", background_video,
        "-i", LOGO_PATH,
        "-i", audio_path,
        "-filter_complex", filter_complex,
        "-map", "[outv]", "-map", "2:a",
        "-c:v", "libx264", "-c:a", "aac",
        "-shortest",
        out_path,
    ]
    subprocess.run(cmd, check=True)


def upload_to_drive(file_path, file_name):
    file_metadata = {"name": file_name, "parents": [DRIVE_FOLDER_ID]}
    media = MediaFileUpload(file_path, resumable=True)
    file = drive_service_upload.files().create(
        body=file_metadata, media_body=media, fields="id"
    ).execute()
    file_id = file.get("id")

    drive_service_upload.permissions().create(
        fileId=file_id,
        body={"type": "anyone", "role": "reader"},
    ).execute()

    return f"https://drive.google.com/file/d/{file_id}/view"


def main():
    rows = sheet.get_all_records()
    for i, row in enumerate(rows, start=2):
        status = row.get("Status", "")
        if status == "Done":
            continue

        text = row.get("NoiDung", "")
        loai_den = row.get("LoaiDen", "").strip()

        if not text or not loai_den:
            continue

        folder_id = FOLDER_VIDEO_NEN.get(loai_den)
        if not folder_id:
            print(f"Dòng {i}: LoaiDen '{loai_den}' không hợp lệ, bỏ qua.")
            continue

        danh_sach = lay_danh_sach_video(folder_id)
        if not danh_sach:
            print(f"Dòng {i}: thư mục '{loai_den}' không có video, bỏ qua.")
            continue

        video_chon = random.choice(danh_sach)
        bg_local_path = f"bg_{i}.mp4"
        tai_video_ve(video_chon["id"], bg_local_path)

        ten_file = f"video_{i}.mp4"
        audio_path = f"audio_{i}.mp3"
        asyncio.run(text_to_speech(text, audio_path))

        out_path = f"output_{i}.mp4"
        ghep_video(audio_path, bg_local_path, out_path)

        link = upload_to_drive(out_path, ten_file)

        sheet.update_cell(i, sheet.find("LinkDrive").col, link)
        sheet.update_cell(i, sheet.find("Status").col, "Done")

        os.remove(audio_path)
        os.remove(out_path)
        os.remove(bg_local_path)


if __name__ == "__main__":
    main()
