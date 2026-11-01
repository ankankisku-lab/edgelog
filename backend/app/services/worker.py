"""Background processing: one extraction worker plus the periodic Drive sync loop."""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from sqlalchemy import or_, select

from ..config import get_settings
from ..db import SessionLocal
from ..models import Trade

log = logging.getLogger(__name__)

# a single worker keeps API usage (and free-tier rate limits) predictable
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="extract")
_inflight: set[int] = set()
_lock = threading.Lock()
_stop = threading.Event()


def queue_trade(trade_id: int) -> bool:
    with _lock:
        if trade_id in _inflight:
            return False
        _inflight.add(trade_id)
    with SessionLocal() as db:
        t = db.get(Trade, trade_id)
        if t:
            t.processing_status = "queued"
            db.commit()
    _executor.submit(_run, trade_id)
    return True


def _run(trade_id: int) -> None:
    from ..extraction.pipeline import process_trade

    try:
        process_trade(trade_id)
    finally:
        with _lock:
            _inflight.discard(trade_id)


def queue_size() -> int:
    with _lock:
        return len(_inflight)


def requeue_pending() -> int:
    """Re-queue trades interrupted by a restart, and rate-limited ones whose wait is over."""
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        ids = db.scalars(select(Trade.id).where(or_(
            Trade.processing_status.in_(("queued", "processing")),
            (Trade.processing_status == "rate_limited") & (Trade.retry_after <= now),
        ))).all()
    for i in ids:
        queue_trade(i)
    return len(ids)


def _loop() -> None:
    from .drive import drive_ready, sync_drive

    while not _stop.wait(60):
        try:
            requeue_pending()
            with SessionLocal() as db:
                ready = drive_ready(db)
            if ready and _due():
                sync_drive()
        except Exception:
            log.exception("background loop error")


_last_sync: list[datetime] = []


def _due() -> bool:
    s = get_settings()
    now = datetime.now(timezone.utc)
    if _last_sync and (now - _last_sync[0]).total_seconds() < s.sync_interval_minutes * 60:
        return False
    _last_sync[:] = [now]
    return True


def start_background() -> None:
    requeue_pending()
    threading.Thread(target=_loop, name="journal-bg", daemon=True).start()


def stop_background() -> None:
    _stop.set()
