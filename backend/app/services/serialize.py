from ..extraction.normalize import to_json
from ..fields import FIELDS
from ..models import Suggestion, Trade, TradeImage
from .journal import missing_required, review_needed


def image_dict(img: TradeImage) -> dict:
    return {
        "id": img.id, "file_name": img.file_name, "role": img.role, "timeframe": img.timeframe,
        "role_source": img.role_source, "web_link": img.web_link, "url": f"/api/images/{img.id}",
        "drive": bool(img.drive_file_id), "analysis": img.analysis,
    }


def suggestion_dict(s: Suggestion) -> dict:
    return {"id": s.id, "field": s.field, "value": s.value, "source": s.source, "confidence": s.confidence,
            "evidence": s.evidence, "reason": s.reason, "status": s.status, "created_at": to_json(s.created_at)}


def trade_dict(t: Trade, *, full: bool = False) -> dict:
    d = {f.key: to_json(getattr(t, f.key)) for f in FIELDS}
    d.update({
        "id": t.id,
        "field_meta": t.field_meta or {},
        "source": t.source,
        "folder_name": t.folder_name,
        "folder_path": t.folder_path,
        "processing_status": t.processing_status,
        "processing_error": t.processing_error,
        "processed_at": to_json(t.processed_at),
        "retry_after": to_json(t.retry_after),
        "is_demo": t.is_demo,
        "created_at": to_json(t.created_at),
        "updated_at": to_json(t.updated_at),
        "images": [image_dict(i) for i in t.images],
        "missing_required": missing_required(t),
        "needs_review": review_needed(t),
        "pending_suggestions": sum(1 for s in t.suggestions if s.status == "pending"),
    })
    if full:
        d["suggestions"] = [suggestion_dict(s) for s in t.suggestions if s.status == "pending"]
        d["extraction"] = t.extraction
    return d
