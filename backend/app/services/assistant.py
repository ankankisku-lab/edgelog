"""AI trading assistant: answers questions from journal records, reviews trades, writes summaries."""

import csv
import io
import json
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from ..config import get_settings
from ..fields import FIELDS
from ..llm import AIUnavailable, ai_configured, stream_chat
from ..models import Trade
from . import analytics
from .images import load_image

MAX_TRADES_IN_CONTEXT = 1500
CSV_COLUMNS = ["id", "trade_date", "asset", "direction", "entry_timeframe", "setup_timeframe", "strategy_name",
               "setup_type", "market_condition", "htf_bias", "htf_zone", "ema_alignment", "impulse_move",
               "fib_retracement", "ob_fvg_sr_reaction", "entry_price", "exit_price", "stop_loss", "take_profit",
               "risk_reward", "tp_r", "tp_reason", "r_multiple", "pnl", "outcome", "emotion", "discipline",
               "followed_plan", "mistakes", "remarks"]

SYSTEM = """You are a trading performance analyst reviewing the user's own trading journal.

Rules:
- Base every statement on the journal records and statistics provided. Cite trade IDs like #12.
- Never invent trades, prices, outcomes or statistics. If the data needed to answer is missing (e.g. no exit
  prices, no strategy recorded), say exactly which fields to fill in.
- When a group has fewer than 10 closed trades, say the sample is small and the result may be noise.
- Keep two kinds of findings apart, under these headings when both appear:
  **From your journal** - statistics and facts computed from recorded fields.
  **Chart interpretation** - your subjective reading of a screenshot; flag it as an opinion.
- Win rate excludes breakeven trades. R-multiple = realised move ÷ initial risk.
- Be concise and practical: lead with the answer, then the evidence, then 1-3 concrete actions."""


def _fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M") if (v.hour, v.minute) != (0, 0) else v.strftime("%Y-%m-%d")
    if isinstance(v, list):
        return "; ".join(map(str, v))
    if isinstance(v, float):
        return f"{v:g}"
    return str(v).replace("\n", " ")[:200]


def journal_csv(trades: list[Trade]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    for t in trades[-MAX_TRADES_IN_CONTEXT:]:
        w.writerow([_fmt(getattr(t, c)) for c in CSV_COLUMNS])
    return buf.getvalue()


def _summary(report: dict) -> dict:
    keep_dims = ("asset", "strategy_name", "setup_type", "entry_timeframe", "setup_timeframe", "market_condition",
                 "direction", "htf_bias", "ema_alignment", "ob_fvg_sr_reaction", "emotion", "followed_plan")
    slim = lambda rows: [{k: r[k] for k in ("key", "closed", "win_rate", "avg_r", "total_r", "net_pnl")} for r in rows]  # noqa: E731
    beh = report["behavior"]
    return {
        "overview": report["overview"],
        "breakdowns": {d: slim(report["breakdowns"][d]) for d in keep_dims},
        "setups": report["setups"],
        "behavior": [{k: v for k, v in i.items() if k not in ("trade_ids",)} | {"trade_ids": i["trade_ids"][:20]}
                     for i in beh["items"]],
        "mistake_tags": beh["mistake_tags"],
        "monthly": report["periods"]["month"][-12:],
        "data_quality": report["data_quality"],
    }


def _period_bounds(period: str) -> tuple[datetime, datetime, datetime, datetime, str]:
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "week":
        start = today - timedelta(days=today.weekday())
        prev_start = start - timedelta(days=7)
        label = f"week of {start:%d %b %Y}"
    else:
        start = today.replace(day=1)
        prev_start = (start - timedelta(days=1)).replace(day=1)
        label = f"{start:%B %Y}"
    return start, today, prev_start, start - timedelta(days=1), label


def _trade_record(t: Trade) -> str:
    lines = [f"Trade #{t.id}"]
    for f in FIELDS:
        v = getattr(t, f.key)
        if v not in (None, []):
            st = ((t.field_meta or {}).get(f.key) or {}).get("status", "")
            lines.append(f"- {f.label}: {_fmt(v)}" + (" (unverified AI reading)" if st == "needs_review" else ""))
    for img in t.images:
        lines.append(f"- Screenshot: {img.file_name} — {img.role or 'unknown role'}, {img.timeframe or 'timeframe ?'}")
    ann = (t.extraction or {}).get("annotations") or []
    if ann:
        lines.append("- Chart annotations detected: " + "; ".join(f"{a.get('type')} {a.get('label', '')}".strip() for a in ann[:20]))
    return "\n".join(lines)


def chat(db: Session, messages: list[dict], trade_id: int | None = None, period: str | None = None) -> Iterator[str]:
    if not ai_configured():
        yield "The assistant needs an OpenRouter API key. Set OPENROUTER_API_KEY in backend/.env and restart the backend."
        return
    s = get_settings()
    all_trades = analytics.load_trades(db, analytics.Filters())
    report = analytics.full_report(db, analytics.Filters())

    context = [
        f"Today is {datetime.now(timezone.utc):%Y-%m-%d}. The journal has {len(all_trades)} trades"
        + (f" (only the latest {MAX_TRADES_IN_CONTEXT} are listed)" if len(all_trades) > MAX_TRADES_IN_CONTEXT else "")
        + ".",
        "Journal statistics (JSON):\n" + json.dumps(_summary(report), default=str),
        "Journal records (CSV):\n" + journal_csv(all_trades),
    ]
    if period in ("week", "month"):
        start, end, pstart, pend, label = _period_bounds(period)
        cur = analytics.full_report(db, analytics.Filters(date_from=start, date_to=end))
        prev = analytics.full_report(db, analytics.Filters(date_from=pstart, date_to=pend))
        context.append(f"This {period} ({label}) statistics:\n" + json.dumps(_summary(cur), default=str))
        context.append(f"Previous {period} statistics:\n" + json.dumps(_summary(prev), default=str))

    user_parts: list[dict] = [{"type": "text", "text": "\n\n".join(context)}]
    if trade_id is not None:
        t = db.get(Trade, trade_id)
        if t is not None:
            same = [x for x in all_trades if x.strategy_name and x.strategy_name == t.strategy_name and x.id != t.id]
            user_parts.append({"type": "text", "text": "Trade under review:\n" + _trade_record(t)
                               + (f"\nSame-strategy stats: {json.dumps(analytics.group_stats(same))}" if same else "")})
            from ..extraction.vision import _encode

            for img in t.images[:4]:
                try:
                    user_parts.append({"type": "text", "text": f"Screenshot {img.file_name} ({img.role or '?'}, {img.timeframe or '?'})"})
                    user_parts.append({"type": "image_url", "image_url": {"url": _encode(load_image(img))}})
                except Exception:
                    user_parts.append({"type": "text", "text": f"(screenshot {img.file_name} could not be loaded)"})

    history = [m for m in messages if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)][-20:]
    if not history or history[-1]["role"] != "user":
        yield "Ask a question to get started."
        return
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": user_parts},
            {"role": "assistant", "content": "I have your journal data. What would you like to know?"},
            *history]
    try:
        yield from stream_chat(msgs, s.assistant_model_list, max_tokens=3000)
    except AIUnavailable as e:
        yield f"[AI unavailable: {e}]"
