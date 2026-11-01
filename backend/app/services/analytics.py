"""Performance, strategy and behavioural analytics computed from journal records only."""

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..fields import MISTAKE_TAGS
from ..models import Suggestion, Trade

CLOSED = {"Win", "Loss", "Breakeven"}
DIMENSIONS = {
    "asset": "Asset", "session": "Session", "strategy_name": "Strategy", "setup_type": "Setup", "entry_timeframe": "Entry timeframe",
    "setup_timeframe": "Setup timeframe", "market_condition": "Market condition", "direction": "Direction",
    "htf_bias": "HTF bias", "ema_alignment": "EMA alignment", "market": "Market", "emotion": "Emotion",
    "followed_plan": "Followed plan", "discipline": "Discipline", "ob_fvg_sr_reaction": "OB / FVG / SR reaction",
    "fib_retracement": "Fib retracement", "weekday": "Weekday",
}


@dataclass
class Filters:
    date_from: datetime | None = None
    date_to: datetime | None = None
    asset: str | None = None
    strategy: str | None = None
    direction: str | None = None
    entry_timeframe: str | None = None
    include_demo: bool = True


def load_trades(db: Session, f: Filters) -> list[Trade]:
    q = select(Trade)
    if f.date_from:
        q = q.where(Trade.trade_date >= f.date_from)
    if f.date_to:
        q = q.where(Trade.trade_date < f.date_to + timedelta(days=1))
    if f.asset:
        q = q.where(Trade.asset == f.asset)
    if f.strategy:
        q = q.where(Trade.strategy_name == f.strategy)
    if f.direction:
        q = q.where(Trade.direction == f.direction)
    if f.entry_timeframe:
        q = q.where(Trade.entry_timeframe == f.entry_timeframe)
    if not f.include_demo:
        q = q.where(Trade.is_demo.is_(False))
    trades = list(db.scalars(q).all())
    trades.sort(key=lambda t: (_when(t), t.id))
    return trades


def _when(t: Trade) -> datetime:
    d = t.trade_date or t.created_at
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def is_closed(t: Trade) -> bool:
    if t.outcome == "Open":
        return False
    return t.outcome in CLOSED or t.r_multiple is not None or t.pnl is not None


def result(t: Trade) -> str | None:
    if t.outcome in CLOSED:
        return t.outcome
    v = t.r_multiple if t.r_multiple is not None else t.pnl
    if v is None:
        return None
    return "Win" if v > 0 else "Loss" if v < 0 else "Breakeven"


def _r(x: float | None, n: int = 2) -> float | None:
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(x, n)


def group_stats(trades: list[Trade]) -> dict:
    closed = [t for t in trades if is_closed(t)]
    res = [result(t) for t in closed]
    wins, losses, be = res.count("Win"), res.count("Loss"), res.count("Breakeven")
    pnls = [t.pnl for t in closed if t.pnl is not None]
    rs = [t.r_multiple for t in closed if t.r_multiple is not None]
    gp, gl = sum(p for p in pnls if p > 0), -sum(p for p in pnls if p < 0)
    gpr, glr = sum(r for r in rs if r > 0), -sum(r for r in rs if r < 0)
    pf = gp / gl if gl > 0 else None
    pf_r = gpr / glr if glr > 0 else None
    return {
        "trades": len(trades), "closed": len(closed), "wins": wins, "losses": losses, "breakeven": be,
        "win_rate": _r(wins / (wins + losses) * 100, 1) if wins + losses else None,
        "net_pnl": _r(sum(pnls)) if pnls else None,
        "total_r": _r(sum(rs)) if rs else None,
        "avg_r": _r(statistics.mean(rs)) if rs else None,
        "profit_factor": _r(pf if pnls else pf_r),
        "profit_factor_basis": "pnl" if pnls else "r",
        "avg_win": _r(statistics.mean([p for p in pnls if p > 0])) if gp else None,
        "avg_loss": _r(-statistics.mean([-p for p in pnls if p < 0])) if gl else None,
    }


def _equity(closed: list[Trade]) -> tuple[list[dict], dict]:
    eq = eq_r = peak = peak_r = 0.0
    mdd = mdd_r = 0.0
    points = []
    for t in closed:
        eq += t.pnl or 0
        eq_r += t.r_multiple or 0
        peak, peak_r = max(peak, eq), max(peak_r, eq_r)
        dd, dd_r = eq - peak, eq_r - peak_r
        mdd, mdd_r = min(mdd, dd), min(mdd_r, dd_r)
        points.append({"trade_id": t.id, "date": _when(t).isoformat(), "asset": t.asset, "pnl": t.pnl,
                       "r": t.r_multiple, "equity": round(eq, 2), "equity_r": round(eq_r, 2),
                       "drawdown": round(dd, 2), "drawdown_r": round(dd_r, 2)})
    return points, {"max_drawdown": round(mdd, 2), "max_drawdown_r": round(mdd_r, 2)}


def _period_key(d: datetime, g: str) -> str:
    if g == "day":
        return d.strftime("%Y-%m-%d")
    if g == "week":
        y, w, _ = d.isocalendar()
        return f"{y}-W{w:02d}"
    return d.strftime("%Y-%m")


def _periods(closed: list[Trade], g: str) -> list[dict]:
    buckets: dict[str, list[Trade]] = defaultdict(list)
    for t in closed:
        buckets[_period_key(_when(t), g)].append(t)
    out = []
    for k in sorted(buckets):
        st = group_stats(buckets[k])
        out.append({"period": k, **{x: st[x] for x in ("closed", "wins", "losses", "win_rate", "net_pnl", "total_r")}})
    return out


def _streaks(closed: list[Trade]) -> tuple[int, int]:
    best = worst = cur_w = cur_l = 0
    for t in closed:
        r = result(t)
        cur_w, cur_l = (cur_w + 1, 0) if r == "Win" else (0, cur_l + 1) if r == "Loss" else (cur_w, cur_l)
        best, worst = max(best, cur_w), max(worst, cur_l)
    return best, worst


R_BINS = [(-math.inf, -2), (-2, -1.5), (-1.5, -1), (-1, -0.5), (-0.5, 0), (0, 0.5), (0.5, 1), (1, 1.5),
          (1.5, 2), (2, 3), (3, 4), (4, math.inf)]


def _bin_label(lo, hi) -> str:
    if lo == -math.inf:
        return f"< {hi}R"
    if hi == math.inf:
        return f"≥ {lo}R"
    return f"{lo} to {hi}R"


def _distribution(closed: list[Trade]) -> list[dict]:
    rs = [t.r_multiple for t in closed if t.r_multiple is not None]
    return [{"bin": _bin_label(lo, hi), "lo": None if lo == -math.inf else lo, "hi": None if hi == math.inf else hi,
             "count": sum(1 for r in rs if lo <= r < hi)} for lo, hi in R_BINS]


def _dim_values(t: Trade, dim: str) -> list[str]:
    if dim == "weekday":
        return [_when(t).strftime("%A")]
    v = getattr(t, dim)
    if v is None or v == []:
        return ["Unspecified"]
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, float):
        return [f"{v:g}"]
    return [str(v)]


def breakdown(trades: list[Trade], dim: str) -> list[dict]:
    groups: dict[str, list[Trade]] = defaultdict(list)
    for t in trades:
        for v in _dim_values(t, dim):
            groups[v].append(t)
    rows = [{"key": k, **group_stats(v)} for k, v in groups.items()]
    rows.sort(key=lambda r: (r["key"] == "Unspecified", -(r["total_r"] or 0), -(r["net_pnl"] or 0)))
    return rows


def _setups(trades: list[Trade]) -> dict:
    groups: dict[str, list[Trade]] = defaultdict(list)
    for t in trades:
        if t.strategy_name or t.setup_type:
            groups[" · ".join(x for x in (t.strategy_name, t.setup_type) if x)].append(t)
    rows = [{"key": k, **group_stats(v)} for k, v in groups.items()]
    rows = [r for r in rows if r["closed"] >= 3]  # fewer than 3 closed trades says nothing
    metric = "net_pnl" if any(r["net_pnl"] is not None for r in rows) else "total_r"
    rows.sort(key=lambda r: r[metric] or 0, reverse=True)
    return {"metric": metric, "min_trades": 3, "most_profitable": rows[0] if rows else None,
            "least_profitable": rows[-1] if len(rows) > 1 else None}


def _has_time(t: Trade) -> bool:
    d = t.trade_date
    return d is not None and (d.hour, d.minute) != (0, 0)


def _compare(subset: list[Trade], rest: list[Trade]) -> dict:
    a, b = group_stats(subset), group_stats(rest)
    keys = ("closed", "win_rate", "avg_r", "net_pnl", "total_r")
    return {"with": {k: a[k] for k in keys}, "without": {k: b[k] for k in keys}}


def _union(detected: list[Trade], tagged: list[Trade]) -> list[Trade]:
    """A pattern counts whether the rule detected it or the user tagged it."""
    return sorted(set(detected + tagged), key=lambda t: t.id)


def behavior(trades: list[Trade]) -> dict:
    closed = [t for t in trades if is_closed(t)]
    ids = lambda ts: [t.id for t in ts][:100]  # noqa: E731
    items = []

    def tagged(tag: str) -> list[Trade]:
        return [t for t in trades if t.mistakes and tag in t.mistakes]

    # overtrading: days with far more trades than usual
    per_day: dict[str, list[Trade]] = defaultdict(list)
    for t in trades:
        if t.trade_date:
            per_day[_when(t).strftime("%Y-%m-%d")].append(t)
    counts = [len(v) for v in per_day.values()]
    threshold = max(4, math.ceil(statistics.median(counts) * 2)) if counts else 4
    heavy = [t for d, v in per_day.items() if len(v) >= threshold for t in v]
    over_tag = tagged("Overtrading")
    items.append({
        "key": "overtrading", "label": "Overtrading", "tagged": len(over_tag),
        "detected": len(heavy), "detected_rule": f"days with ≥ {threshold} trades (≥ 2× your median day)",
        "trade_ids": ids(flag := _union(heavy, over_tag)),
        "days_flagged": sorted({d for d, v in per_day.items() if len(v) >= threshold}),
        **_compare(flag, [t for t in closed if t not in flag]),
    })

    # revenge trading: a new trade within 60 minutes of a loss (needs time-of-day on both)
    timed = [t for t in closed if _has_time(t)]
    revenge = [b for a, b in zip(timed, timed[1:])
               if result(a) == "Loss" and timedelta(0) <= _when(b) - _when(a) <= timedelta(minutes=60)]
    rev_tag = tagged("Revenge trade")
    items.append({
        "key": "revenge", "label": "Revenge trading", "tagged": len(rev_tag), "detected": len(revenge),
        "detected_rule": "entered within 60 min after a loss" + ("" if timed else " (no trade times recorded)"),
        "trade_ids": ids(flag := _union(revenge, rev_tag)),
        **_compare(flag, [t for t in closed if t not in flag]),
    })

    # early exits: winners closed at under half the planned target
    early = [t for t in closed if result(t) == "Win" and t.r_multiple is not None
             and (planned := t.tp_r or t.risk_reward) and t.r_multiple < planned * 0.5]
    early_tag = tagged("Early exit")
    items.append({
        "key": "early_exit", "label": "Early exits", "tagged": len(early_tag), "detected": len(early),
        "detected_rule": "winner closed below 50% of the planned R target",
        "trade_ids": ids(flag := _union(early, early_tag)),
        "r_left_on_table": _r(sum((t.tp_r or t.risk_reward) - t.r_multiple for t in early)) if early else None,
        **_compare(flag, [t for t in closed if t not in flag and result(t) == "Win"]),
    })

    # late entries can't be measured from prices alone; self-tagged only
    late_tag = tagged("Late entry")
    items.append({"key": "late_entry", "label": "Late entries", "tagged": len(late_tag), "detected": None,
                  "detected_rule": "not detectable from recorded fields; tag trades with “Late entry”",
                  "trade_ids": ids(late_tag), **_compare(late_tag, [t for t in closed if t not in late_tag])})

    # stop-loss violations: losses bigger than the planned 1R
    sl_viol = [t for t in closed if t.r_multiple is not None and t.r_multiple < -1.1]
    sl_tag = tagged("Stop-loss violation") + tagged("Moved stop loss")
    items.append({
        "key": "sl_violation", "label": "Stop-loss violations", "tagged": len(sl_tag), "detected": len(sl_viol),
        "detected_rule": "loss larger than 1.1R (exit beyond the planned stop)",
        "trade_ids": ids(flag := _union(sl_viol, sl_tag)),
        "excess_r": _r(sum(-1 - t.r_multiple for t in sl_viol)) if sl_viol else None,
        **_compare(flag, [t for t in closed if t not in flag]),
    })

    outside = [t for t in trades if t.followed_plan == "No"] + tagged("Outside plan")
    outside = sorted(set(outside), key=lambda t: t.id)
    items.append({"key": "outside_plan", "label": "Trades outside the plan", "tagged": len(outside),
                  "detected": None, "detected_rule": "from “Followed Plan = No” or the “Outside plan” tag",
                  "trade_ids": ids(outside), **_compare(outside, [t for t in closed if t not in outside])})

    calm = {"Calm", "Confident"}
    emotional = sorted(set([t for t in trades if t.emotion and t.emotion not in calm] + tagged("Emotional decision")),
                       key=lambda t: t.id)
    items.append({"key": "emotional", "label": "Emotional decision-making", "tagged": len(emotional),
                  "detected": None, "detected_rule": "emotion recorded as anything other than Calm/Confident, or tagged",
                  "trade_ids": ids(emotional), **_compare(emotional, [t for t in closed if t not in emotional])})

    clean = [t for t in closed if not t.mistakes]
    tags = []
    for tag in sorted({m for t in trades for m in (t.mistakes or [])}, key=lambda m: (m not in MISTAKE_TAGS, m)):
        ts = tagged(tag)
        st = group_stats(ts)
        tags.append({"tag": tag, "count": len(ts), "win_rate": st["win_rate"], "avg_r": st["avg_r"],
                     "net_pnl": st["net_pnl"], "total_r": st["total_r"]})
    tags.sort(key=lambda r: -r["count"])
    return {"items": items, "mistake_tags": tags, "clean_trades": group_stats(clean),
            "by_emotion": breakdown(trades, "emotion"), "by_discipline": breakdown(trades, "discipline")}


def data_quality(db: Session, trades: list[Trade]) -> dict:
    from .journal import missing_required, review_needed

    ids = [t.id for t in trades]
    pending = db.scalars(select(Suggestion.trade_id).where(Suggestion.status == "pending",
                                                           Suggestion.trade_id.in_(ids))).all() if ids else []
    return {
        "missing_required": sum(1 for t in trades if missing_required(t)),
        "needs_review": sum(1 for t in trades if review_needed(t)),
        "pending_suggestions": len(pending),
        "without_r": sum(1 for t in trades if is_closed(t) and t.r_multiple is None),
        "without_pnl": sum(1 for t in trades if is_closed(t) and t.pnl is None),
        "open": sum(1 for t in trades if not is_closed(t)),
        "processing": sum(1 for t in trades if t.processing_status in ("queued", "processing")),
    }


def full_report(db: Session, f: Filters) -> dict:
    trades = load_trades(db, f)
    closed = [t for t in trades if is_closed(t)]
    overview = group_stats(trades)
    curve, dd = _equity(closed)
    best, worst = _streaks(closed)
    planned = [t.tp_r or t.risk_reward for t in trades if (t.tp_r or t.risk_reward)]
    overview.update(dd)
    overview.update({
        "open": len(trades) - len(closed),
        "avg_rr": _r(statistics.mean(planned)) if planned else None,
        "best_win_streak": best, "worst_loss_streak": worst,
        "largest_win": _r(max((t.pnl for t in closed if t.pnl is not None), default=None)),
        "largest_loss": _r(min((t.pnl for t in closed if t.pnl is not None), default=None)),
        "best_r": _r(max((t.r_multiple for t in closed if t.r_multiple is not None), default=None)),
        "worst_r": _r(min((t.r_multiple for t in closed if t.r_multiple is not None), default=None)),
    })
    # the account: balance = starting balance + P&L so far; drawdown % is measured from the peak balance
    from .account import account_dict, get_account

    acc = account_dict(get_account(db))
    start = acc["starting_balance"]
    if start:
        peak = start
        dd_pct = 0.0
        for p in curve:
            p["balance"] = round(start + p["equity"], 2)
            peak = max(peak, p["balance"])
            dd_pct = min(dd_pct, (p["balance"] - peak) / peak * 100 if peak > 0 else 0.0)
        net = overview.get("net_pnl") or 0.0
        overview.update({"starting_balance": start, "balance": round(start + net, 2),
                         "return_pct": round(net / start * 100, 2), "max_drawdown_pct": round(dd_pct, 2)})
    overview["risk_per_trade"] = acc["risk_per_trade"]

    rolling = []
    for i in range(len(closed)):
        window = [result(t) for t in closed[max(0, i - 19): i + 1]]
        decided = [r for r in window if r in ("Win", "Loss")]
        if len(decided) >= 5:
            rolling.append({"trade_id": closed[i].id, "date": _when(closed[i]).isoformat(),
                            "win_rate": round(decided.count("Win") / len(decided) * 100, 1), "window": len(decided)})
    return {
        "overview": overview,
        "equity": curve,
        "periods": {g: _periods(closed, g) for g in ("day", "week", "month")},
        "rolling_win_rate": rolling,
        "distribution": _distribution(closed),
        "breakdowns": {d: breakdown(trades, d) for d in DIMENSIONS},
        "dimension_labels": DIMENSIONS,
        "setups": _setups(trades),
        "behavior": behavior(trades),
        "data_quality": data_quality(db, trades),
    }
