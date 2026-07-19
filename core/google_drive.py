import io
import json
import re
from pathlib import Path

from django.conf import settings

DRIVE_FILE_ID_RE = re.compile(r"/file/d/([A-Za-z0-9_-]+)")


def _load_service_account_info():
    raw = getattr(settings, "GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        return None
    if raw.startswith("{"):
        return json.loads(raw)
    path = Path(raw)
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    raise ValueError("GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON must contain JSON or a readable file path")


def _credentials():
    info = _load_service_account_info()
    if info:
        from google.oauth2 import service_account
        return service_account.Credentials.from_service_account_info(
            info,
            scopes=["https://www.googleapis.com/auth/drive.file"],
        )

    client_id = getattr(settings, "GOOGLE_DRIVE_OAUTH_CLIENT_ID", "").strip()
    client_secret = getattr(settings, "GOOGLE_DRIVE_OAUTH_CLIENT_SECRET", "").strip()
    refresh_token = getattr(settings, "GOOGLE_DRIVE_OAUTH_REFRESH_TOKEN", "").strip()
    if client_id and client_secret and refresh_token:
        from google.oauth2.credentials import Credentials
        return Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=["https://www.googleapis.com/auth/drive.file"],
        )
    return None


def upload_exam_diagram(xml_content: str, filename: str, existing_url: str = "") -> str:
    credentials = _credentials()
    folder_id = getattr(settings, "GOOGLE_DRIVE_EXAM_FOLDER_ID", "").strip()
    if not credentials or not folder_id:
        return ""

    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseUpload

    service = build("drive", "v3", credentials=credentials, cache_discovery=False)
    media = MediaIoBaseUpload(
        io.BytesIO(xml_content.encode("utf-8")),
        mimetype="application/vnd.jgraph.mxfile",
        resumable=False,
    )
    match = DRIVE_FILE_ID_RE.search(existing_url or "")
    if match:
        created = service.files().update(
            fileId=match.group(1),
            body={"name": filename},
            media_body=media,
            fields="id,webViewLink",
            supportsAllDrives=True,
        ).execute()
    else:
        created = service.files().create(
            body={
                "name": filename,
                "mimeType": "application/vnd.jgraph.mxfile",
                "parents": [folder_id],
            },
            media_body=media,
            fields="id,webViewLink",
            supportsAllDrives=True,
        ).execute()
        if getattr(settings, "GOOGLE_DRIVE_EXAM_MAKE_PUBLIC", False):
            service.permissions().create(
                fileId=created["id"],
                body={"type": "anyone", "role": "reader"},
                supportsAllDrives=True,
            ).execute()

    return created.get("webViewLink") or f"https://drive.google.com/file/d/{created['id']}/view"
