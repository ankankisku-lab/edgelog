import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..extraction.normalize import ValidationError, normalize_timeframe, parse_datetime
from ..fields import MISTAKE_TAGS, TIMEFRAMES, registry_payload
from ..models import AuditLog, Suggestion, Trade, TradeImage
from ..services import journal
from ..services.images import IMAGE_EXTS, MAX_UPLOAD_BYTES, decode, image_bytes, save_upload
from ..services.serialize import suggestion_dict, trade_dict
from ..services.worker import queue_trade

router = APIRouter(prefix="/api")


class ValuesBody(BaseModel):
    values: dict[str, Any]


class VerifyBody(BaseModel):
    fields: list[str] | None = None


class ImagePatch(BaseModel):
    role: str | None = None
    timeframe: str | None = None


def _trade(db: Session, trade_id: int) -> Trade:
    t = db.get(Trade, trade_id)
    if t is None:
        raise HTTPException(404, "Trade not found")
    return t


@router.get("/fields")
def fields():
    return registry_payload()


@router.get("/trades")
def list_trades(
    db: Session = Depends(get_db),
    q: str | None = None,
    asset: str | None = None,
    strategy: str | None = None,
    outcome: str | None = None,
    status: str | None = Query(None, description="review | missing | suggestions | processing | error"),
    date_from: str | None = None,
    date_to: str | None = None,
    sort: str = "-trade_date",
    limit: int = Query(500, le=5000),
    offset: int = 0,
):
    stmt = select(Trade)
    if asset:
        stmt = stmt.where(Trade.asset == asset)
    if strategy:
        stmt = stmt.where(Trade.strategy_name == strategy)
    if outcome:
        stmt = stmt.where(Trade.outcome == outcome)
    if df := parse_datetime(date_from):
        stmt = stmt.where(Trade.trade_date >= df)
    if dt := parse_datetime(date_to):
        stmt = stmt.where(Trade.trade_date <= dt.replace(hour=23, minute=59))
    trades = list(db.scalars(stmt).all())
    if q:
        ql = q.lower()
        trades = [t for t in trades if ql in " ".join(str(x or "") for x in (
            t.id, t.asset, t.trading_pair, t.strategy_name, t.setup_type, t.remarks, t.folder_path, t.folder_name, t.tp_reason,
            *(i.file_name for i in t.images))).lower()]
    if status == "review":
        trades = [t for t in trades if journal.review_needed(t)]
    elif status == "missing":
        trades = [t for t in trades if journal.missing_required(t)]
    elif status == "suggestions":
        trades = [t for t in trades if any(s.status == "pending" for s in t.suggestions)]
    elif status == "processing":
        trades = [t for t in trades if t.processing_status in ("queued", "processing", "rate_limited")]
    elif status == "error":
        trades = [t for t in trades if t.processing_status in ("error", "partial")]

    key, desc = sort.lstrip("-"), sort.startswith("-")
    if key not in Trade.__table__.columns:
        raise HTTPException(400, f"cannot sort by {key}")
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)

    def sort_key(t: Trade):
        v = getattr(t, key)
        if key == "trade_date":
            v = v or t.created_at
            v = v if v.tzinfo else v.replace(tzinfo=timezone.utc)
        return (v is not None, v if v is not None else epoch, t.id)

    try:
        trades.sort(key=sort_key, reverse=desc)
    except TypeError:
        trades.sort(key=lambda t: (getattr(t, key) is not None, str(getattr(t, key)), t.id), reverse=desc)
    return {"total": len(trades), "items": [trade_dict(t) for t in trades[offset:offset + limit]]}


@router.get("/trades/options")
def options(db: Session = Depends(get_db)):
    def distinct(col):
        return sorted({v for v in db.scalars(select(col).where(col.is_not(None))).all() if v})

    mistakes = sorted({m for ms in db.scalars(select(Trade.mistakes)).all() if ms for m in ms} | set(MISTAKE_TAGS))
    return {"assets": distinct(Trade.asset), "strategies": distinct(Trade.strategy_name),
            "setups": distinct(Trade.setup_type), "timeframes": TIMEFRAMES, "mistakes": mistakes}


@router.post("/trades")
def create_trade(body: ValuesBody, db: Session = Depends(get_db)):
    t = Trade(source="manual", field_meta={})
    db.add(t)
    db.flush()
    journal.audit(db, t.id, "create", "manual")
    try:
        journal.manual_update(db, t, body.values)
    except ValidationError as e:
        db.rollback()
        raise HTTPException(422, str(e))
    db.commit()
    return trade_dict(t, full=True)


async def _read_images(files: list[UploadFile]) -> list[tuple[str, str, bytes]]:
    out = []
    for f in files:
        name = f.filename or "image.png"
        if Path(name).suffix.lower() not in IMAGE_EXTS:
            raise HTTPException(415, f"{name}: only image files are supported ({', '.join(sorted(IMAGE_EXTS))})")
        data = await f.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"{name} is larger than {MAX_UPLOAD_BYTES // 1024 // 1024} MB")
        if decode(data) is None:
            raise HTTPException(415, f"{name} is not a readable image")
        out.append((name, f.content_type or "image/png", data))
    return out


@router.post("/trades/upload")
async def upload(
    files: list[UploadFile] = File(...),
    mode: str = Form("single", description="single: all files are one trade; separate: one trade per file"),
    trade_id: int | None = Form(None, description="attach the files to this existing trade"),
    folder_name: str | None = Form(None, description="optional trade name, parsed like a Drive folder name"),
    db: Session = Depends(get_db),
):
    images = await _read_images(files)
    if not images:
        raise HTTPException(400, "no files")
    if trade_id is not None:
        groups = [(_trade(db, trade_id), images)]
    elif mode == "separate":
        groups = [(None, [img]) for img in images]
    else:
        groups = [(None, images)]

    touched = []
    for trade, imgs in groups:
        if trade is None:
            trade = Trade(source="upload", field_meta={}, folder_name=(folder_name or None) if mode != "separate" else None)
            db.add(trade)
            db.flush()
            journal.audit(db, trade.id, "create", "manual", None, None, {"upload": [n for n, _, _ in imgs]})
        start = len(trade.images)
        for i, (name, mime, data) in enumerate(imgs):
            path = save_upload(trade.id, name, data)
            db.add(TradeImage(trade_id=trade.id, file_name=name, mime_type=mime, local_path=str(path),
                              file_created_at=datetime.now(timezone.utc), sort_order=start + i))
        touched.append(trade.id)
    db.commit()
    for tid in touched:
        queue_trade(tid)
    return {"trade_ids": touched}


@router.get("/trades/{trade_id}")
def get_trade(trade_id: int, db: Session = Depends(get_db)):
    return trade_dict(_trade(db, trade_id), full=True)


@router.patch("/trades/{trade_id}")
def update_trade(trade_id: int, body: ValuesBody, db: Session = Depends(get_db)):
    t = _trade(db, trade_id)
    try:
        journal.manual_update(db, t, body.values)
    except ValidationError as e:
        db.rollback()
        raise HTTPException(422, str(e))
    db.commit()
    return trade_dict(t, full=True)


@router.post("/trades/{trade_id}/verify")
def verify(trade_id: int, body: VerifyBody, db: Session = Depends(get_db)):
    t = _trade(db, trade_id)
    n = journal.verify_fields(db, t, body.fields)
    db.commit()
    return {"verified": n, "trade": trade_dict(t, full=True)}


@router.post("/trades/{trade_id}/reprocess")
def reprocess(trade_id: int, db: Session = Depends(get_db)):
    t = _trade(db, trade_id)
    if not t.images:
        raise HTTPException(400, "This trade has no screenshots to process")
    journal.audit(db, t.id, "reprocess", "manual")
    db.commit()
    queue_trade(t.id)
    return {"queued": True}


@router.delete("/trades/{trade_id}")
def delete_trade(trade_id: int, db: Session = Depends(get_db)):
    t = _trade(db, trade_id)
    journal.audit(db, t.id, "delete", "manual", None, trade_dict(t), None)
    db.delete(t)
    db.commit()
    shutil.rmtree(get_settings().uploads_dir / str(trade_id), ignore_errors=True)
    return {"deleted": trade_id}


@router.get("/trades/{trade_id}/history")
def history(trade_id: int, db: Session = Depends(get_db)):
    rows = db.scalars(select(AuditLog).where(AuditLog.trade_id == trade_id).order_by(AuditLog.id.desc()).limit(300)).all()
    return [{"id": r.id, "action": r.action, "field": r.field, "old": r.old_value, "new": r.new_value,
             "source": r.source, "at": r.created_at.isoformat()} for r in rows]


@router.get("/images/{image_id}")
def get_image(image_id: int, db: Session = Depends(get_db)):
    img = db.get(TradeImage, image_id)
    if img is None:
        raise HTTPException(404, "Image not found")
    try:
        data = image_bytes(img)
    except Exception as e:
        raise HTTPException(502, f"could not load image: {e}")
    db.commit()  # persists the Drive cache path on first download
    return Response(data, media_type=img.mime_type or "image/png", headers={"Cache-Control": "private, max-age=86400"})


@router.patch("/images/{image_id}")
def patch_image(image_id: int, body: ImagePatch, db: Session = Depends(get_db)):
    img = db.get(TradeImage, image_id)
    if img is None:
        raise HTTPException(404, "Image not found")
    if body.role is not None:
        if body.role not in ("entry", "setup", "other"):
            raise HTTPException(422, "role must be entry, setup or other")
        img.role = body.role
    if body.timeframe is not None:
        tf = normalize_timeframe(body.timeframe)
        if tf is None:
            raise HTTPException(422, f"unknown timeframe {body.timeframe}")
        img.timeframe = tf
    img.role_source = "manual"
    journal.audit(db, img.trade_id, "edit", "manual", f"image:{img.file_name}", None,
                  {"role": img.role, "timeframe": img.timeframe})
    db.commit()
    return trade_dict(img.trade, full=True)


@router.delete("/images/{image_id}")
def delete_image(image_id: int, db: Session = Depends(get_db)):
    img = db.get(TradeImage, image_id)
    if img is None:
        raise HTTPException(404, "Image not found")
    trade = img.trade
    if img.local_path and not img.drive_file_id:
        Path(img.local_path).unlink(missing_ok=True)
    journal.audit(db, trade.id, "edit", "manual", f"image:{img.file_name}", img.file_name, None)
    db.delete(img)
    db.commit()
    db.refresh(trade)
    return trade_dict(trade, full=True)


def _suggestion(db: Session, sid: int) -> Suggestion:
    s = db.get(Suggestion, sid)
    if s is None or s.status != "pending":
        raise HTTPException(404, "Suggestion not found or already resolved")
    return s


@router.post("/suggestions/{sid}/approve")
def approve(sid: int, db: Session = Depends(get_db)):
    s = _suggestion(db, sid)
    try:
        journal.approve_suggestion(db, s)
    except ValidationError as e:
        raise HTTPException(422, str(e))
    db.commit()
    return trade_dict(s.trade, full=True)


@router.post("/suggestions/{sid}/reject")
def reject(sid: int, db: Session = Depends(get_db)):
    s = _suggestion(db, sid)
    journal.reject_suggestion(db, s)
    db.commit()
    return trade_dict(s.trade, full=True)


@router.get("/suggestions")
def list_suggestions(db: Session = Depends(get_db)):
    rows = db.scalars(select(Suggestion).where(Suggestion.status == "pending").order_by(Suggestion.id.desc())).all()
    return [suggestion_dict(s) | {"trade_id": s.trade_id} for s in rows]
