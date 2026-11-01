"""Google Drive: OAuth, folder selection, and recursive sync of trade screenshots.

Folder convention:  <root>/2026/January/<trade folder>/{m1 entry.png, m5 setup.png, ...}
Each folder that directly contains images is one trade, except year/month folders (and the root),
whose loose images each become their own trade.
"""

import io
import json
import logging
import os
import re
import secrets
import threading
from datetime import datetime, timezone

from cryptography.fernet import Fernet
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import SessionLocal
from ..models import DriveConnection, SeenDriveFile, Trade, TradeImage
from ..extraction.filename_parser import MONTHS
from .journal import audit

log = logging.getLogger(__name__)

# drive.readonly is the narrowest scope that can see files the user uploads through Drive itself
# (drive.file only covers files this app created).
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
FOLDER_MIME = "application/vnd.google-apps.folder"
_sync_lock = threading.Lock()

if get_settings().backend_url.startswith(("http://localhost", "http://127.0.0.1")):
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")  # local dev callback over http
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")


class DriveNotConnected(RuntimeError):
    pass


def _fernet() -> Fernet:
    settings = get_settings()
    key = settings.app_secret_key
    if not key:
        key_file = settings.data_dir / "secret.key"
        if not key_file.exists():
            key_file.write_text(Fernet.generate_key().decode())
        key = key_file.read_text().strip()
    return Fernet(key.encode())


def _conn(db: Session) -> DriveConnection:
    c = db.get(DriveConnection, 1)
    if c is None:
        c = DriveConnection(id=1)
        db.add(c)
        db.flush()
    return c


def oauth_configured() -> bool:
    st = get_settings()
    return bool(st.google_client_id and st.google_client_secret)


def _flow(state: str | None = None, code_verifier: str | None = None) -> Flow:
    st = get_settings()
    config = {"web": {
        "client_id": st.google_client_id,
        "client_secret": st.google_client_secret,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": [st.google_redirect_uri],
    }}
    flow = Flow.from_client_config(config, scopes=SCOPES, state=state, code_verifier=code_verifier,
                                   autogenerate_code_verifier=code_verifier is None)
    flow.redirect_uri = st.google_redirect_uri
    return flow


def auth_url(db: Session) -> str:
    if not oauth_configured():
        raise DriveNotConnected("GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not set")
    state = secrets.token_urlsafe(24)
    flow = _flow(state=state)
    url, _ = flow.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="true")
    c = _conn(db)
    c.oauth_state, c.oauth_code_verifier = state, flow.code_verifier
    db.commit()
    return url


def complete_auth(db: Session, state: str, code: str) -> None:
    c = _conn(db)
    if not c.oauth_state or not secrets.compare_digest(state, c.oauth_state):
        raise DriveNotConnected("OAuth state mismatch; start the connection again")
    flow = _flow(state=state, code_verifier=c.oauth_code_verifier)
    flow.fetch_token(code=code)
    _save_creds(c, flow.credentials)
    c.oauth_state = c.oauth_code_verifier = None
    try:
        about = build("drive", "v3", credentials=flow.credentials, cache_discovery=False).about().get(fields="user").execute()
        c.account_email = about.get("user", {}).get("emailAddress")
    except Exception:
        log.warning("could not read Drive account email", exc_info=True)
    db.commit()


def _save_creds(c: DriveConnection, creds: Credentials) -> None:
    c.encrypted_credentials = _fernet().encrypt(creds.to_json().encode()).decode()


def _service(db: Session):
    c = _conn(db)
    if not c.encrypted_credentials:
        raise DriveNotConnected("Google Drive is not connected")
    info = json.loads(_fernet().decrypt(c.encrypted_credentials.encode()))
    creds = Credentials.from_authorized_user_info(info, SCOPES)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        _save_creds(c, creds)
        db.commit()
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def disconnect(db: Session) -> None:
    c = _conn(db)
    c.encrypted_credentials = c.account_email = c.folder_id = c.folder_name = None
    db.commit()


def drive_ready(db: Session) -> bool:
    c = db.get(DriveConnection, 1)
    return bool(c and c.encrypted_credentials and c.folder_id)


def status(db: Session) -> dict:
    c = _conn(db)
    db.commit()
    return {
        "oauth_configured": oauth_configured(),
        "connected": bool(c.encrypted_credentials),
        "account_email": c.account_email,
        "folder_id": c.folder_id,
        "folder_name": c.folder_name,
        "last_sync_at": c.last_sync_at.isoformat() if c.last_sync_at else None,
        "last_sync_status": c.last_sync_status,
        "last_sync_message": c.last_sync_message,
        "files_seen": c.files_seen,
        "sync_interval_minutes": get_settings().sync_interval_minutes,
        "syncing": _sync_lock.locked(),
    }


def _list(svc, q: str, fields: str) -> list[dict]:
    out, token = [], None
    while True:
        resp = svc.files().list(q=q, fields=f"nextPageToken, files({fields})", pageSize=1000, pageToken=token,
                                supportsAllDrives=True, includeItemsFromAllDrives=True, orderBy="name").execute()
        out += resp.get("files", [])
        token = resp.get("nextPageToken")
        if not token:
            return out


def list_folders(db: Session, parent: str = "root") -> list[dict]:
    svc = _service(db)
    if not re.fullmatch(r"[\w-]+", parent):
        raise ValueError("invalid folder id")
    return _list(svc, f"'{parent}' in parents and mimeType='{FOLDER_MIME}' and trashed=false", "id, name")


def set_folder(db: Session, folder_id: str, folder_name: str) -> None:
    c = _conn(db)
    c.folder_id, c.folder_name = folder_id, folder_name
    db.commit()


def download_file(file_id: str) -> bytes:
    with SessionLocal() as db:
        svc = _service(db)
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, svc.files().get_media(fileId=file_id, supportsAllDrives=True))
        done = False
        while not done:
            _, done = dl.next_chunk()
        return buf.getvalue()


def _is_calendar_folder(name: str) -> bool:
    """'2026', 'January', '01 January', '2026-03' are organisational folders, not trades."""
    tokens = re.sub(r"[\s._-]+", " ", name.strip().lower()).split()
    return bool(tokens) and all(t in MONTHS or re.fullmatch(r"20\d{2}|\d{1,2}", t) for t in tokens)


def _parse_time(v: str | None) -> datetime | None:
    return datetime.fromisoformat(v.replace("Z", "+00:00")) if v else None


def sync_drive() -> dict:
    """Walk the chosen folder tree and import new screenshots. Safe to call concurrently (serialised)."""
    from .worker import queue_trade

    if not _sync_lock.acquire(blocking=False):
        return {"status": "already_running"}
    db = SessionLocal()
    try:
        c = _conn(db)
        if not c.folder_id:
            raise DriveNotConnected("Choose a Drive folder first")
        svc = _service(db)
        c.last_sync_status, c.last_sync_message = "running", None
        db.commit()

        # breadth-first walk: (folder_id, folder_name, relative path)
        pending = [(c.folder_id, c.folder_name or "", "")]
        groups: list[tuple[str, str, str, list[dict]]] = []
        while pending:
            fid, fname, path = pending.pop(0)
            children = _list(svc, f"'{fid}' in parents and trashed=false",
                             "id, name, mimeType, createdTime, modifiedTime, webViewLink, md5Checksum")
            imgs = [f for f in children if f["mimeType"].startswith("image/")]
            for f in children:
                if f["mimeType"] == FOLDER_MIME:
                    pending.append((f["id"], f["name"], f"{path}/{f['name']}".strip("/")))
            if imgs:
                groups.append((fid, fname, path, imgs))

        seen = set(db.scalars(select(SeenDriveFile.file_id)).all())
        new_trades = new_images = 0
        touched: set[int] = set()
        for fid, fname, path, imgs in groups:
            fresh = [f for f in imgs if f["id"] not in seen]
            if not fresh:
                continue
            per_image = fid == c.folder_id or _is_calendar_folder(fname)
            if per_image:
                batches = [(None, f["name"], [f]) for f in fresh]
            else:
                batches = [(fid, fname, fresh)]
            for folder_id, trade_name, files in batches:
                trade = db.scalar(select(Trade).where(Trade.drive_folder_id == folder_id)) if folder_id else None
                if trade is None:
                    trade = Trade(source="drive", drive_folder_id=folder_id,
                                  folder_name=trade_name if folder_id else None, folder_path=path, field_meta={})
                    db.add(trade)
                    db.flush()
                    audit(db, trade.id, "create", "system", None, None, {"drive_folder": path or fname})
                    new_trades += 1
                start = len(trade.images)
                for i, f in enumerate(sorted(files, key=lambda x: x["name"])):
                    db.add(TradeImage(trade_id=trade.id, file_name=f["name"], mime_type=f["mimeType"],
                                      drive_file_id=f["id"], web_link=f.get("webViewLink"),
                                      file_created_at=_parse_time(f.get("createdTime")), sort_order=start + i))
                    db.add(SeenDriveFile(file_id=f["id"], name=f["name"], trade_id=trade.id))
                    seen.add(f["id"])
                    new_images += 1
                if not trade.screenshot_link:
                    trade.screenshot_link = (f"https://drive.google.com/drive/folders/{folder_id}" if folder_id
                                             else files[0].get("webViewLink"))
                touched.add(trade.id)
            db.commit()

        c = _conn(db)
        c.last_sync_at = datetime.now(timezone.utc)
        c.last_sync_status = "ok"
        c.last_sync_message = f"{new_trades} new trade(s), {new_images} new image(s)"
        c.files_seen = len(seen)
        db.commit()
        for tid in touched:
            queue_trade(tid)
        return {"status": "ok", "new_trades": new_trades, "new_images": new_images, "queued": len(touched)}
    except Exception as e:
        db.rollback()
        c = _conn(db)
        c.last_sync_at = datetime.now(timezone.utc)
        c.last_sync_status, c.last_sync_message = "error", str(e)[:500]
        db.commit()
        log.exception("drive sync failed")
        return {"status": "error", "message": str(e)}
    finally:
        db.close()
        _sync_lock.release()
