"""Journal write rules: AI never overwrites user-verified data, and every change is audited."""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..extraction.normalize import normalize_value, to_json, values_equal
from ..extraction.types import Resolved
from ..fields import COMPUTED_KEYS, FIELD_MAP, FIELDS
from ..instruments import BY_ASSET
from ..models import AuditLog, Suggestion, Trade

REQUIRED_KEYS = [f.key for f in FIELDS if f.required]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_meta(trade: Trade, key: str) -> dict:
    return (trade.field_meta or {}).get(key) or {}


def is_locked(trade: Trade, key: str) -> bool:
    """A field is locked once the user has typed it or confirmed it."""
    m = get_meta(trade, key)
    return m.get("status") == "verified" or m.get("source") == "manual"


def audit(db: Session, trade_id: int | None, action: str, source: str, field: str | None = None,
          old: Any = None, new: Any = None) -> None:
    db.add(AuditLog(trade_id=trade_id, action=action, field=field, old_value=to_json(old),
                    new_value=to_json(new), source=source))


def set_field(db: Session, trade: Trade, key: str, value: Any, *, source: str, confidence: float,
              status: str, evidence: str = "", action: str = "extract") -> None:
    old = getattr(trade, key)
    setattr(trade, key, value)
    meta = dict(trade.field_meta or {})
    meta[key] = {"source": source, "confidence": round(confidence, 3), "status": status,
                 "evidence": evidence[:900], "updated_at": _now()}
    trade.field_meta = meta  # reassign so SQLAlchemy sees the JSON change
    if not values_equal(old, value) or action in ("verify", "approve"):
        audit(db, trade.id, action, source, key, old, value)


def add_suggestion(db: Session, trade: Trade, key: str, value: Any, source: str, confidence: float,
                   evidence: str, reason: str) -> bool:
    jv = to_json(value)
    pending = db.scalars(select(Suggestion).where(
        Suggestion.trade_id == trade.id, Suggestion.field == key, Suggestion.status.in_(("pending", "rejected"))
    )).all()
    for p in pending:
        if p.value == jv:
            return False  # already suggested, or the user already rejected this exact value
    db.add(Suggestion(trade_id=trade.id, field=key, value=jv, source=source, confidence=confidence,
                      evidence=evidence[:900], reason=reason))
    return True


def apply_resolved(db: Session, trade: Trade, resolved: dict[str, Resolved]) -> dict:
    s = get_settings()
    stats = {"filled": 0, "needs_review": 0, "suggested": 0, "kept_verified": 0}
    run_sources = {src for r in resolved.values() for c in [r, *r.alternatives] for src in c.source.split("+")}
    for key, r in resolved.items():
        if key not in FIELD_MAP:
            continue
        current = getattr(trade, key)
        if is_locked(trade, key):
            if current is None or not values_equal(current, r.value):
                if add_suggestion(db, trade, key, r.value, r.source, r.confidence, r.evidence, "conflicts_verified"):
                    stats["suggested"] += 1
            stats["kept_verified"] += 1
            continue
        if r.confidence < s.medium_confidence:
            if add_suggestion(db, trade, key, r.value, r.source, r.confidence, r.evidence, "low_confidence"):
                stats["suggested"] += 1
            continue
        prev = get_meta(trade, key)
        # on re-processing, a weaker reading doesn't replace a stronger earlier one that came from a stage
        # which didn't run this time (e.g. AI vision was rate-limited); stages that did run re-read everything
        prev_sources = set(prev.get("source", "").split("+")) - {"", "computed"}
        missing_stage = bool(prev_sources - run_sources)
        if (current is not None and missing_stage and prev.get("confidence", 0) > r.confidence
                and not values_equal(current, r.value)):
            add_suggestion(db, trade, key, r.value, r.source, r.confidence, r.evidence, "conflict")
            stats["suggested"] += 1
            continue
        status = "auto" if r.confidence >= s.high_confidence and not r.conflict else "needs_review"
        evidence = r.evidence
        if r.conflict:
            alts = ", ".join(f"{to_json(c.value)} ({c.source} {c.confidence:.2f})" for c in r.alternatives[:3])
            evidence = f"{evidence} | conflicting readings: {alts}"
        set_field(db, trade, key, r.value, source=r.source, confidence=r.confidence, status=status, evidence=evidence)
        stats["filled"] += 1
        if status == "needs_review":
            stats["needs_review"] += 1
    recompute_derived(db, trade)
    return stats


def _derive(trade: Trade) -> dict[str, tuple[Any, str]]:
    e, sl, tp, x = trade.entry_price, trade.stop_loss, trade.take_profit, trade.exit_price
    sign = {"Long": 1, "Short": -1}.get(trade.direction or "")
    out: dict[str, tuple[Any, str]] = {}
    risk = abs(e - sl) if e and sl else None
    if risk and tp:
        out["risk_reward"] = (round(abs(tp - e) / risk, 2), "|TP − entry| ÷ |entry − SL|")
    if risk and x and sign:
        out["r_multiple"] = (round((x - e) * sign / risk, 2), "(exit − entry) × direction ÷ risk")
    if e and x and sign:
        lev = trade.leverage or 1
        out["pnl_percent"] = (round((x - e) / e * sign * 100 * lev, 2), f"price move × {lev:g}x leverage")
    return out


def _risk_for(db: Session, trade: Trade) -> float | None:
    """The trade's own risk, or the account's risk per trade, stamped on the trade so it stays put."""
    if trade.risk_amount:
        return trade.risk_amount
    from .account import get_account

    default = get_account(db).risk_per_trade
    if default:
        set_field(db, trade, "risk_amount", default, source="computed", confidence=1.0, status="auto",
                  evidence="account risk per trade", action="compute")
    return default


def recompute_derived(db: Session, trade: Trade) -> None:
    derived = _derive(trade)
    inputs_review = any(get_meta(trade, k).get("status") == "needs_review"
                        for k in ("entry_price", "stop_loss", "take_profit", "exit_price", "direction", "leverage"))
    for key in COMPUTED_KEYS:
        if is_locked(trade, key):
            continue
        meta = get_meta(trade, key)
        if key in derived:
            value, how = derived[key]
            # exact prices beat a read-off value (e.g. CV's pixel-height R:R), unless the user locked it
            if meta.get("source") != "computed" and getattr(trade, key) is not None:
                how += f" (replaces {meta.get('source')} reading {getattr(trade, key)})"
            if meta.get("source") != "computed" or not values_equal(getattr(trade, key), value):
                set_field(db, trade, key, value, source="computed", confidence=1.0,
                          status="needs_review" if inputs_review else "auto", evidence=how, action="compute")
        elif meta.get("source") == "computed" and getattr(trade, key) is not None:
            set_field(db, trade, key, None, source="computed", confidence=1.0, status="auto",
                      evidence="inputs removed", action="compute")

    # P&L in currency: only derived when the user hasn't recorded it (it excludes fees/funding)
    sign = {"Long": 1, "Short": -1}.get(trade.direction or "")
    pm = get_meta(trade, "pnl")
    if not is_locked(trade, "pnl") and pm.get("source") in (None, "computed"):
        if trade.entry_price and trade.exit_price and trade.position_size and sign:
            ins = BY_ASSET.get(trade.asset or "")
            pv = ins.point_value if ins and ins.point_value else 1.0
            pnl = round((trade.exit_price - trade.entry_price) * trade.position_size * sign * pv, 2)
            how = f"(exit − entry) × size × direction{f' × {pv:g} per point ({ins.asset})' if ins else ''}; excludes fees"
            set_field(db, trade, "pnl", pnl, source="computed", confidence=1.0, status="needs_review",
                      evidence=how, action="compute")
        elif trade.r_multiple is not None and (risk := _risk_for(db, trade)):
            # no prices: the R the trade made × the money risked on it
            pnl = round(trade.r_multiple * risk, 2)
            rm_review = get_meta(trade, "r_multiple").get("status") == "needs_review"
            set_field(db, trade, "pnl", pnl, source="computed", confidence=1.0,
                      status="needs_review" if rm_review else "auto",
                      evidence=f"{trade.r_multiple:g}R × ${risk:g} risk", action="compute")
        elif pm.get("source") == "computed" and trade.pnl is not None:
            set_field(db, trade, "pnl", None, source="computed", confidence=1.0, status="auto",
                      evidence="inputs removed", action="compute")

    om = get_meta(trade, "outcome")
    if not is_locked(trade, "outcome") and om.get("source") in (None, "computed"):
        outcome = None
        if trade.r_multiple is not None:
            outcome = "Win" if trade.r_multiple > 0.1 else "Loss" if trade.r_multiple < -0.1 else "Breakeven"
        elif trade.pnl is not None:
            outcome = "Win" if trade.pnl > 0 else "Loss" if trade.pnl < 0 else "Breakeven"
        if outcome and outcome != trade.outcome:
            set_field(db, trade, "outcome", outcome, source="computed", confidence=1.0, status="auto",
                      evidence="from R-multiple / P&L sign", action="compute")


def manual_update(db: Session, trade: Trade, updates: dict[str, Any]) -> None:
    """User edits. Normalises + validates every value first, so a bad field rejects the whole save."""
    clean = {k: normalize_value(k, v) for k, v in updates.items()}
    for key, value in clean.items():
        set_field(db, trade, key, value, source="manual", confidence=1.0, status="verified",
                  evidence="entered by user", action="edit")
        # a manual value supersedes any AI suggestion still waiting for this field
        for sug in db.scalars(select(Suggestion).where(Suggestion.trade_id == trade.id, Suggestion.field == key,
                                                       Suggestion.status == "pending")):
            sug.status, sug.resolved_at = "superseded", datetime.now(timezone.utc)
    recompute_derived(db, trade)


def verify_fields(db: Session, trade: Trade, keys: list[str] | None = None) -> int:
    """Confirm AI-filled values as correct. With no keys, confirms every value awaiting review."""
    n = 0
    for key, meta in list((trade.field_meta or {}).items()):
        if keys is not None and key not in keys:
            continue
        if keys is None and meta.get("status") != "needs_review":
            continue
        if meta.get("status") == "verified" or getattr(trade, key, None) is None:
            continue
        set_field(db, trade, key, getattr(trade, key), source=meta.get("source", "manual"),
                  confidence=meta.get("confidence", 1.0), status="verified",
                  evidence=meta.get("evidence", ""), action="verify")
        n += 1
    return n


def approve_suggestion(db: Session, sug: Suggestion) -> None:
    trade = sug.trade
    value = normalize_value(sug.field, sug.value)
    set_field(db, trade, sug.field, value, source=sug.source, confidence=sug.confidence, status="verified",
              evidence=f"approved suggestion: {sug.evidence or ''}", action="approve")
    sug.status, sug.resolved_at = "approved", datetime.now(timezone.utc)
    for other in trade.suggestions:
        if other.id != sug.id and other.field == sug.field and other.status == "pending":
            other.status, other.resolved_at = "superseded", datetime.now(timezone.utc)
    recompute_derived(db, trade)


def reject_suggestion(db: Session, sug: Suggestion) -> None:
    sug.status, sug.resolved_at = "rejected", datetime.now(timezone.utc)
    audit(db, sug.trade_id, "reject", sug.source, sug.field, None, sug.value)


def missing_required(trade: Trade) -> list[str]:
    return [k for k in REQUIRED_KEYS if getattr(trade, k) is None]


def review_needed(trade: Trade) -> list[str]:
    return [k for k, m in (trade.field_meta or {}).items()
            if m.get("status") == "needs_review" and getattr(trade, k, None) is not None]
