"""Runs every extraction stage over all of a trade's screenshots and applies the result."""

import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from ..config import get_settings
from ..db import SessionLocal
from ..fields import EXTRACTABLE_KEYS
from ..instruments import family_from_price
from ..llm import AIRateLimited, AIUnavailable, ai_configured
from ..models import Trade, TradeImage
from ..services import journal
from ..services.images import load_image
from .cv_chart import analyze_chart
from .filename_parser import folder_date_fallback, folder_day_date, folder_session, parse_filename
from .merge import merge_candidates, validate_levels
from .normalize import normalize_timeframe, timeframe_minutes
from .ocr import extract_from_ocr, run_ocr
from .types import Candidate

log = logging.getLogger(__name__)

_ENTRY_WORDS = re.compile(r"(?i)(?:^|[\W_])(entry|exec|execution|ltf)(?:[\W_]|$)")
_SETUP_WORDS = re.compile(r"(?i)(?:^|[\W_])(setup|htf|context|formation|bias)(?:[\W_]|$)")


def _assign_roles(images: list[TradeImage]) -> None:
    """Lowest timeframe = entry chart; higher timeframes = setup charts. Filename words win."""
    auto = [i for i in images if i.role_source != "manual"]
    for img in auto:
        if _ENTRY_WORDS.search(img.file_name):
            img.role, img.role_source = "entry", "filename"
        elif _SETUP_WORDS.search(img.file_name):
            img.role, img.role_source = "setup", "filename"
    timed = [i for i in images if i.timeframe]
    if timed:
        lowest = min(timeframe_minutes(i.timeframe) for i in timed)
        for img in auto:
            if img.role_source == "filename":
                continue
            if img.timeframe:
                img.role = "entry" if timeframe_minutes(img.timeframe) == lowest else "setup"
                img.role_source = img.role_source if img.role_source == "vision" else "timeframe"
    elif len(images) == 1 and images[0].role_source != "manual":
        images[0].role = images[0].role or "entry"


def _timeframe_candidates(images: list[TradeImage], tf_conf: dict[int, float]) -> list[Candidate]:
    out: list[Candidate] = []
    entry = [i for i in images if i.role == "entry" and i.timeframe]
    setup = [i for i in images if i.role == "setup" and i.timeframe]
    if entry:
        e = min(entry, key=lambda i: timeframe_minutes(i.timeframe))
        out.append(Candidate("entry_timeframe", e.timeframe, tf_conf.get(e.id, 0.8), "image", f"{e.file_name} ({e.role_source})"))
    if setup:
        # the setup chart is the higher context; several M5 shots still give one setup timeframe
        s = max(setup, key=lambda i: timeframe_minutes(i.timeframe))
        out.append(Candidate("setup_timeframe", s.timeframe, tf_conf.get(s.id, 0.8), "image", f"{s.file_name} ({s.role_source})"))
    return out


def _asset_from_price(db, merged: dict, loaded: list) -> Candidate | None:
    """No symbol visible anywhere: the price level can still say 'this is gold, not MNQ'.

    Confidence stays at 0.5, so it is only ever offered as a suggestion for the user to approve.
    """
    prices = [merged[k].value for k in ("entry_price", "stop_loss", "take_profit", "exit_price") if k in merged]
    if not prices:
        for img, _ in loaded:
            axis = ((img.analysis or {}).get("cv") or {}).get("price_axis")
            if axis:
                prices = [axis["b"]]  # price at the top edge of the chart
                break
    family = family_from_price(prices)
    if not family:
        return None
    assets = [i.asset for i in family]
    # pick the member of that family the user actually trades most
    counts = db.execute(select(Trade.asset, func.count()).where(Trade.asset.in_(assets)).group_by(Trade.asset)).all()
    asset = max(counts, key=lambda r: r[1])[0] if counts else assets[0]
    return Candidate("asset", asset, 0.5, "price",
                     f"no symbol visible; price ≈ {prices[0]:,.2f} fits the {family[0].family} range")


def process_trade(trade_id: int) -> None:
    s = get_settings()
    db = SessionLocal()
    try:
        trade = db.get(Trade, trade_id)
        if trade is None:
            return
        trade.processing_status, trade.processing_error = "processing", None
        db.commit()

        images = list(trade.images)
        cands: list[Candidate] = []
        errors: list[str] = []
        tf_conf: dict[int, float] = {}
        loaded: list[tuple[TradeImage, object]] = []

        # Stage 1: folder tree (".../OCT/19th | Mon/..."), trade folder name, every image filename
        captured = min((i.file_created_at for i in images if i.file_created_at), default=None)
        if fd := folder_day_date(trade.folder_path, captured):
            cands.append(fd)
        if fs := folder_session(trade.folder_path):
            cands.append(fs)
        if trade.folder_name:
            for c in parse_filename(trade.folder_name, trade.folder_path):
                c.source, c.evidence = "folder", f"folder “{trade.folder_name}”: {c.evidence}"
                cands.append(c)
        for img in images:
            for c in parse_filename(img.file_name, trade.folder_path):
                if c.field == "timeframe":  # a timeframe in a filename describes that image's chart
                    if img.role_source != "manual":
                        img.timeframe, tf_conf[img.id] = c.value, c.confidence
                    continue
                c.evidence = f"{img.file_name}: {c.evidence}"
                cands.append(c)

        # Stage 2: OCR + computer vision, per image
        for img in images:
            try:
                arr = load_image(img)
            except Exception as e:
                errors.append(f"{img.file_name}: {e}")
                continue
            loaded.append((img, arr))
            items = run_ocr(arr)
            ocr_c, ocr_facts = extract_from_ocr(items, img.file_name, arr.shape[1])
            cv_c, cv_facts = analyze_chart(arr, items, img.file_name)
            cands += ocr_c + cv_c
            if ocr_facts.get("timeframe") and img.id not in tf_conf and img.role_source != "manual":
                img.timeframe, tf_conf[img.id] = ocr_facts["timeframe"], 0.85
            img.analysis = {"ocr": ocr_facts, "cv": cv_facts}

        # Stage 3: vision model, only when something extractable is still unresolved
        vision_raw: dict | None = None
        status = "done"
        merged = merge_candidates(cands)
        unresolved = [k for k in EXTRACTABLE_KEYS
                      if not journal.is_locked(trade, k) and (k not in merged or merged[k].confidence < s.high_confidence)]
        if unresolved and loaded and ai_configured():
            from .vision import interpret

            try:
                vc, vision_raw = interpret([(i.file_name, a) for i, a in loaded], trade.folder_path or trade.folder_name)
                cands += vc
                for info in vision_raw.get("images") or []:
                    idx = info.get("image")
                    if not isinstance(idx, int) or not 0 < idx <= len(loaded):
                        continue
                    img = loaded[idx - 1][0]
                    tf = normalize_timeframe(str(info.get("timeframe") or ""))
                    if img.role_source == "manual":
                        continue
                    if tf and img.id not in tf_conf:
                        img.timeframe, tf_conf[img.id] = tf, 0.75
                    if info.get("role") in ("entry", "setup") and img.role_source not in ("filename",):
                        img.role, img.role_source = info["role"], "vision"
            except AIRateLimited as e:
                status = "rate_limited"
                trade.retry_after = e.retry_at or datetime.now(timezone.utc) + timedelta(minutes=s.ai_retry_minutes)
                errors.append(f"AI stage postponed: {e}")
            except AIUnavailable as e:
                status = "partial"
                errors.append(f"AI stage failed: {e}")
        elif unresolved and not ai_configured():
            status = "partial"
            errors.append("AI stage skipped: OPENROUTER_API_KEY not set")

        # Stage 4: merge, validate, apply
        _assign_roles(images)
        cands += _timeframe_candidates(images, tf_conf)
        if not any(c.field == "trade_date" for c in cands):
            created = min((i.file_created_at for i in images if i.file_created_at), default=None)
            if fb := folder_date_fallback(created, trade.folder_path):
                cands.append(fb)
        merged = merge_candidates(cands)
        if "asset" not in merged or merged["asset"].confidence < s.medium_confidence:
            if hint := _asset_from_price(db, merged, loaded):
                cands.append(hint)
                merged = merge_candidates(cands)
        warnings = validate_levels(merged)
        stats = journal.apply_resolved(db, trade, merged)

        trade.extraction = {
            "ran_at": datetime.now(timezone.utc).isoformat(),
            "stats": stats,
            "warnings": warnings,
            "candidates": len(cands),
            "by_source": {src: sum(1 for c in cands if c.source == src) for src in sorted({c.source for c in cands})},
            "vision_model": (vision_raw or {}).get("_model"),
            "annotations": (vision_raw or {}).get("annotations") or [],
            "notes": (vision_raw or {}).get("notes"),
            "is_trading_chart": (vision_raw or {}).get("is_trading_chart"),
        }
        trade.processing_status = status if status != "done" or not errors else "partial"
        trade.processing_error = "\n".join(errors) or None
        trade.processed_at = datetime.now(timezone.utc)
        if status != "rate_limited":
            trade.retry_after = None
        journal.audit(db, trade.id, "extract", "system", None, None, stats)
        db.commit()
    except Exception as e:  # never leave a trade stuck in "processing"
        log.exception("extraction failed for trade %s", trade_id)
        db.rollback()
        trade = db.get(Trade, trade_id)
        if trade:
            trade.processing_status, trade.processing_error = "error", f"{type(e).__name__}: {e}"
            db.commit()
    finally:
        db.close()
