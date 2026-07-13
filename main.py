import os
import re
import json
import random
import unicodedata
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
DRIVE_OUTPUT_FOLDER_ID = "1V27dj-ws6K3xQEtim-P_PhEfhsXgkJ3I"       # thư mục "Video Output"
NGUON_VIDEO_NEN_ROOT_ID = "1q8dWz0BvylzeN8hD5AyeX0_2Rs-Zmfrm"       # thư mục gốc "Nguon Video Nen"

VOICE = "vi-VN-NamMinhNeural"
LOGO_PATH = "logo.png"

TEXT_LIEN_HE = "Thanh Dat Led - 0986474671 - 0924734666"

SO_VIDEO_NEN_MOI_LAN = (2, 3)  # số video nền lấy ngẫu nhiên mỗi lần ghép (min, max)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

sa_creds = SACredentials.from_service_account_file("service_account.json", scopes=SCOPES)
gc = gspread.authorize(sa_creds)
drive_read = build("drive", "v3", credentials=sa_creds)

oauth_creds = OAuthCredentials(
    token=None,
    refresh_token=os.environ["OAUTH_REFRESH_TOKEN"],
    client_id=os.environ["OAUTH_CLIENT_ID"],
    client_secret=os.environ["OAUTH_CLIENT_SECRET"],
    token_uri="https://oauth2.googleapis.com/token",
    scopes=["https://www.googleapis.com/auth/drive"],
)
drive_upload = build("drive", "v3", credentials=oauth_creds)

sheet = gc.open_by_key(SHEET_ID).sheet1


# ================== TIỆN ÍCH ==================

def slugify_vi(text):
    text = text.lower().replace("đ", "d")
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "video"


def wrap_text(text, width=45):
    import textwrap
    return textwrap.fill(text, width=width)


def get_duration(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", path],
        capture_output=True, text=True
    )
    info = json.loads(result.stdout)
    return float(info["format"]["duration"])


def tim_folder_theo_ten_chinh_xac(ten, parent_id, service):
    """Tìm thư mục con có tên khớp CHÍNH XÁC (kể cả dấu, hoa/thường) trong parent_id"""
    ten_escaped = ten.replace("'", "\\'")
    query = (
        f"name = '{ten_escaped}' and mimeType = 'application/vnd.google-apps.folder' "
        f"and '{parent_id}' in parents and trashed = false"
    )
    results = service.files().list(q=query, fields="files(id, name)").execute()
    files = results.get("files", [])
    return files[0]["id"] if files else None


def tim_hoac_tao_thu_muc(ten, parent_id, service):
    existing = tim_folder_theo_ten_chinh_xac(ten, parent_id, service)
    if existing:
        return existing
    metadata = {
        "name": ten,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    folder = service.files().create(body=metadata, fields="id").execute()
    return folder.get("id")


def lay_danh_sach_video(folder_id):
    results = drive_read.files().list(
        q=f"'{folder_id}' in parents and mimeType contains 'video/' and trashed = false",
        fields="files(id, name)"
    ).execute()
    return results.get("files", [])


def tai_video_ve(file_id, out_path):
    request = drive_read.files().get_media(fileId=file_id)
    with open(out_path, "wb") as f:
        downloader = MediaIoBaseDownload(f, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()


async def text_to_speech(text, out_path):
    communicate = edge_tts.Communicate(text, VOICE)
    await communicate.save(out_path)


# ================== GHÉP NHIỀU VIDEO NỀN, XÁO TRỘN ==================

def chuan_bi_video_nen(folder_id, audio_duration, i):
    danh_sach_goc = lay_danh_sach_video(folder_id)
    if not danh_sach_goc:
        return None

    so_luong_muon_lay = random.randint(*SO_VIDEO_NEN_MOI_LAN)
    so_luong_muon_lay = min(so_luong_muon_lay, len(danh_sach_goc))

    # Bước 1: chọn ngẫu nhiên 2-3 video khác nhau, xáo trộn thứ tự
    playlist_video_info = random.sample(danh_sach_goc, so_luong_muon_lay)
    random.shuffle(playlist_video_info)

    playlist_paths = []
    total = 0.0
    count = 0

    for video in playlist_video_info:
        local_path = f"bgsrc_{i}_{count}.mp4"
        tai_video_ve(video["id"], local_path)
        dur = get_duration(local_path)
        playlist_paths.append(local_path)
        total += dur
        count += 1

    # Bước 2: nếu vẫn ngắn hơn audio, lấy thêm ngẫu nhiên (được lặp lại) cho đủ
    while total < audio_duration + 2:
        video = random.choice(danh_sach_goc)
        local_path = f"bgsrc_{i}_{count}.mp4"
        tai_video_ve(video["id"], local_path)
        dur = get_duration(local_path)
        playlist_paths.append(local_path)
        total += dur
        count += 1
        if count > 40:
            break

    # Nối các video lại thành 1 video nền dài đủ dùng
    concat_path = f"bgconcat_{i}.mp4"
    filter_parts = []
    for j in range(len(playlist_paths)):
        filter_parts.append(f"[{j}:v]scale=1280:720,setsar=1[v{j}]")
    concat_inputs = "".join(f"[v{j}]" for j in range(len(playlist_paths)))
    filter_parts.append(f"{concat_inputs}concat=n={len(playlist_paths)}:v=1:a=0[bgv]")
    filter_complex = ";".join(filter_parts)

    cmd = ["ffmpeg", "-y"]
    for p in playlist_paths:
        cmd += ["-i", p]
    cmd += ["-filter_complex", filter_complex, "-map", "[bgv]", "-an", concat_path]
    subprocess.run(cmd, check=True)

    for p in playlist_paths:
        os.remove(p)

    return concat_path


# ================== GHÉP CUỐI: LOGO + CHỮ + AUDIO ==================

def ghep_video(audio_path, background_video, caption_path, out_path):
    filter_complex = (
        f"[0:v]scale=1280:720[bg];"
        f"[1:v]scale=100:-1[logo];"
        f"[bg][logo]overlay=W-w-20:20[bg2];"
        f"[bg2]drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
        f"text='{TEXT_LIEN_HE}':fontsize=30:fontcolor=white:"
        f"borderw=2:bordercolor=black@0.7:x=(w-text_w)/2:y=20[bg3];"
        f"[bg3]drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:"
        f"textfile={caption_path}:fontsize=26:fontcolor=black:"
        f"box=1:boxcolor=yellow@0.85:boxborderw=15:"
        f"x=(w-text_w)/2:y=h-220:line_spacing=6[outv]"
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


def upload_to_drive(file_path, file_name, parent_folder_id):
    file_metadata = {"name": file_name, "parents": [parent_folder_id]}
    media = MediaFileUpload(file_path, resumable=True)
    file = drive_upload.files().create(
        body=file_metadata, media_body=media, fields="id"
    ).execute()
    file_id = file.get("id")

    drive_upload.permissions().create(
        fileId=file_id,
        body={"type": "anyone", "role": "reader"},
    ).execute()

    return f"https://drive.google.com/file/d/{file_id}/view"


# ================== MAIN ==================

def main():
    rows = sheet.get_all_records()
    for i, row in enumerate(rows, start=2):
        status = row.get("Status", "")
        if status == "Done":
            continue

        tieu_de = row.get("TieuDe", "").strip()
        text = row.get("NoiDung", "")
        loai_den = row.get("LoaiDen", "").strip()

        if not text or not loai_den or not tieu_de:
            continue

        # Tìm thư mục nguồn video nền trùng khớp chính xác tên "LoaiDen"
        folder_id = tim_folder_theo_ten_chinh_xac(loai_den, NGUON_VIDEO_NEN_ROOT_ID, drive_read)
        if not folder_id:
            print(f"Dòng {i}: không tìm thấy thư mục '{loai_den}' trong Nguon Video Nen, bỏ qua.")
            continue

        # 1. Tạo audio TTS
        audio_path = f"audio_{i}.mp3"
        asyncio.run(text_to_speech(text, audio_path))
        audio_duration = get_duration(audio_path)

        # 2. Ghép nhiều video nền, xáo trộn, đủ độ dài
        bg_concat_path = chuan_bi_video_nen(folder_id, audio_duration, i)
        if not bg_concat_path:
            print(f"Dòng {i}: thư mục '{loai_den}' không có video, bỏ qua.")
            os.remove(audio_path)
            continue

        # 3. Chuẩn bị file caption
        caption_path = f"caption_{i}.txt"
        with open(caption_path, "w", encoding="utf-8") as f:
            f.write(wrap_text(text, width=45))

        # 4. Ghép video cuối
        out_path = f"output_{i}.mp4"
        ghep_video(audio_path, bg_concat_path, caption_path, out_path)

        # 5. Tìm/tạo thư mục con theo danh mục trong Video Output
        sub_folder_id = tim_hoac_tao_thu_muc(loai_den, DRIVE_OUTPUT_FOLDER_ID, drive_upload)

        # 6. Đặt tên file theo TieuDe
        ten_file = slugify_vi(tieu_de) + ".mp4"

        # 7. Upload
        link = upload_to_drive(out_path, ten_file, sub_folder_id)

        sheet.update_cell(i, sheet.find("LinkDrive").col, link)
        sheet.update_cell(i, sheet.find("Status").col, "Done")

        for p in [audio_path, bg_concat_path, caption_path, out_path]:
            if os.path.exists(p):
                os.remove(p)


if __name__ == "__main__":
    main()
