from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..extraction.normalize import parse_datetime
from ..extraction.ocr import ocr_available
from ..instruments import INSTRUMENTS
from ..llm import ai_configured
from ..services import account, analytics, assistant, drive
from ..services.worker import queue_size

router = APIRouter(prefix="/api")


# ---------- analytics ----------

@router.get("/analytics")
def get_analytics(
    db: Session = Depends(get_db),
    date_from: str | None = None,
    date_to: str | None = None,
    asset: str | None = None,
    strategy: str | None = None,
    direction: str | None = None,
    entry_timeframe: str | None = None,
    include_demo: bool = True,
):
    f = analytics.Filters(parse_datetime(date_from), parse_datetime(date_to), asset or None, strategy or None,
                          direction or None, entry_timeframe or None, include_demo)
    return analytics.full_report(db, f)


# ---------- account ----------

class AccountBody(BaseModel):
    starting_balance: float | None = Field(None, gt=0)
    risk_per_trade: float | None = Field(None, gt=0)
    apply_to_existing: bool = False


@router.get("/account")
def get_account(db: Session = Depends(get_db)):
    acc = account.get_account(db)
    db.commit()
    return account.account_dict(acc)


@router.put("/account")
def put_account(body: AccountBody, db: Session = Depends(get_db)):
    changed = account.update_account(db, body.starting_balance, body.risk_per_trade, body.apply_to_existing)
    db.commit()
    return account.account_dict(account.get_account(db)) | {"trades_updated": changed}


# ---------- system ----------

@router.get("/system/status")
def system_status(db: Session = Depends(get_db)):
    s = get_settings()
    ok, err = ocr_available()
    return {
        "ai_configured": ai_configured(),
        "vision_models": s.vision_model_list,
        "assistant_models": s.assistant_model_list,
        "ocr_available": ok,
        "ocr_error": err,
        "database": s.database_url.split(":", 1)[0],
        "queue": queue_size(),
        "drive": drive.status(db),
        "instruments": [{"asset": i.asset, "name": i.name, "point_value": i.point_value} for i in INSTRUMENTS],
        "thresholds": {"high": s.high_confidence, "medium": s.medium_confidence},
    }


# ---------- Google Drive ----------

class FolderBody(BaseModel):
    id: str
    name: str


@router.get("/drive/status")
def drive_status(db: Session = Depends(get_db)):
    return drive.status(db)


@router.get("/drive/auth/start")
def drive_auth_start(db: Session = Depends(get_db)):
    try:
        return {"url": drive.auth_url(db)}
    except drive.DriveNotConnected as e:
        raise HTTPException(400, str(e))


@router.get("/drive/auth/callback")
def drive_auth_callback(state: str = "", code: str = "", error: str = "", db: Session = Depends(get_db)):
    front = get_settings().frontend_url
    if error or not code:
        return RedirectResponse(f"{front}/settings?drive=error&message={error or 'no code'}")
    try:
        drive.complete_auth(db, state, code)
    except Exception as e:
        return RedirectResponse(f"{front}/settings?drive=error&message={str(e)[:200]}")
    return RedirectResponse(f"{front}/settings?drive=connected")


@router.post("/drive/disconnect")
def drive_disconnect(db: Session = Depends(get_db)):
    drive.disconnect(db)
    return drive.status(db)


@router.get("/drive/folders")
def drive_folders(parent: str = "root", db: Session = Depends(get_db)):
    try:
        return drive.list_folders(db, parent)
    except drive.DriveNotConnected as e:
        raise HTTPException(400, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.post("/drive/folder")
def drive_set_folder(body: FolderBody, db: Session = Depends(get_db)):
    drive.set_folder(db, body.id, body.name)
    return drive.status(db)


@router.post("/drive/sync")
def drive_sync():
    return drive.sync_drive()


# ---------- assistant ----------

class ChatBody(BaseModel):
    messages: list[dict]
    trade_id: int | None = None
    period: str | None = None


@router.post("/assistant/chat")
def assistant_chat(body: ChatBody, db: Session = Depends(get_db)):
    return StreamingResponse(assistant.chat(db, body.messages, body.trade_id, body.period),
                             media_type="text/plain; charset=utf-8")
