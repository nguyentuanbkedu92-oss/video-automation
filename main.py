import os
import asyncio
import subprocess
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import edge_tts

# ==== CẤU HÌNH ====
SHEET_ID = "1DSK50AbuwB4-VxkrrGaCkYbp-2TtZALrPi5-8oLLxPY"
DRIVE_FOLDER_ID = "19KH22GTYcCa-I9hC_Ive25FSz9gJeEUt"
VOICE = "vi-VN-NamMinhNeural"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

creds = Credentials.from_service_account_file("service_account.json", scopes=SCOPES)
gc = gspread.authorize(creds)
drive_service = build("drive", "v3", credentials=creds)

sheet = gc.open_by_key(SHEET_ID).sheet1

async def text_to_speech(text, out_path):
    communicate = edge_tts.Communicate(text, VOICE)
    await communicate.save(out_path)

def ghep_video(audio_path, background_video, out_path):
    cmd = [
        "ffmpeg", "-y",
        "-i", background_video,
        "-i", audio_path,
        "-map", "0:v", "-map", "1:a",
        "-c:v", "copy", "-c:a", "aac",
        "-shortest",
        out_path,
    ]
    subprocess.run(cmd, check=True)

def upload_to_drive(file_path, file_name):
    file_metadata = {"name": file_name, "parents": [DRIVE_FOLDER_ID]}
    media = MediaFileUpload(file_path, resumable=True)
    file = drive_service.files().create(
        body=file_metadata, media_body=media, fields="id"
    ).execute()
    file_id = file.get("id")

    drive_service.permissions().create(
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
        bg_video = row.get("VideoNen", "")

        if not text or not bg_video:
            continue  # bỏ qua dòng chưa đủ dữ liệu

        ten_file = f"video_{i}.mp4"
        audio_path = f"audio_{i}.mp3"
        asyncio.run(text_to_speech(text, audio_path))

        out_path = f"output_{i}.mp4"
        ghep_video(audio_path, bg_video, out_path)

        link = upload_to_drive(out_path, ten_file)

        sheet.update_cell(i, sheet.find("LinkDrive").col, link)
        sheet.update_cell(i, sheet.find("Status").col, "Done")

        os.remove(audio_path)
        os.remove(out_path)

if __name__ == "__main__":
    main()
