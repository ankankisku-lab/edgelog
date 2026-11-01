"""Stage 4: merge candidates from every stage into one value + confidence per field."""

from collections import defaultdict

from ..config import get_settings
from .normalize import values_equal
from .types import Candidate, Resolved


def merge_candidates(cands: list[Candidate]) -> dict[str, Resolved]:
    s = get_settings()
    by_field: dict[str, list[Candidate]] = defaultdict(list)
    for c in cands:
        by_field[c.field].append(c)

    out: dict[str, Resolved] = {}
    for field, group in by_field.items():
        group = sorted(group, key=lambda c: c.confidence, reverse=True)
        best = group[0]
        agreeing = [c for c in group if values_equal(c.value, best.value)]
        dissent = [c for c in group if not values_equal(c.value, best.value)]

        # independent sources agreeing compound: 1 - Π(1 - c), one vote per source
        per_source: dict[str, float] = {}
        for c in agreeing:
            per_source[c.source] = max(per_source.get(c.source, 0), c.confidence)
        miss = 1.0
        for conf in per_source.values():
            miss *= 1 - conf
        conf = min(0.99, 1 - miss) if len(per_source) > 1 else best.confidence

        conflict = any(c.confidence >= s.medium_confidence and c.source != best.source for c in dissent)
        if conflict:
            conf = min(conf, s.high_confidence - 0.01)  # a credible disagreement always gets a human look

        sources = "+".join(sorted(per_source))
        evidence = " | ".join(dict.fromkeys(c.evidence for c in agreeing if c.evidence))
        out[field] = Resolved(field, best.value, round(conf, 3), sources, evidence[:900], dissent, conflict)
    return out


def validate_levels(res: dict[str, Resolved]) -> list[str]:
    """Cross-field sanity checks. Downgrades confidence (never deletes values) and returns warnings."""
    s = get_settings()
    warnings: list[str] = []

    def val(k):
        return res[k].value if k in res else None

    def downgrade(k, why):
        if k in res:
            res[k].confidence = min(res[k].confidence, s.medium_confidence - 0.05)
            res[k].evidence = (res[k].evidence + f" | ⚠ {why}").strip(" |")

    def plausible(k, v):
        d, e = val("direction"), val("entry_price")
        if not e or not isinstance(v, (int, float)):
            return True
        if not (e / 20 < v < e * 20):
            return False
        if d and k == "stop_loss":
            return (v < e) if d == "Long" else (v > e)
        if d and k == "take_profit":
            return (v > e) if d == "Long" else (v < e)
        return True

    # a misread label ("TP 2R" read as TP = 2) can outrank a sound reading from another stage:
    # if the winner is impossible and a dissenting candidate is not, the dissenter takes its place
    for k in ("stop_loss", "take_profit", "exit_price"):
        r = res.get(k)
        if r and not plausible(k, r.value):
            alt = next((c for c in sorted(r.alternatives, key=lambda c: -c.confidence) if plausible(k, c.value)), None)
            if alt:
                rest = [c for c in r.alternatives if c is not alt]
                rest.append(Candidate(k, r.value, r.confidence, r.source, r.evidence))
                conf = min(alt.confidence, s.high_confidence - 0.01)  # still a conflict: a human should look
                res[k] = Resolved(k, alt.value, round(conf, 3), alt.source, alt.evidence, rest, True)
                warnings.append(f"{k}: chose {alt.value} ({alt.source}) over implausible {r.value} ({r.source})")

    d, e, sl, tp = val("direction"), val("entry_price"), val("stop_loss"), val("take_profit")
    if d and e and sl:
        if (d == "Long" and sl >= e) or (d == "Short" and sl <= e):
            msg = f"stop loss {sl} is on the wrong side of entry {e} for a {d}"
            warnings.append(msg)
            downgrade("stop_loss", msg)
    if d and e and tp:
        if (d == "Long" and tp <= e) or (d == "Short" and tp >= e):
            msg = f"take profit {tp} is on the wrong side of entry {e} for a {d}"
            warnings.append(msg)
            downgrade("take_profit", msg)
    for k in ("stop_loss", "take_profit", "exit_price"):
        v = val(k)
        if e and v and not (e / 20 < v < e * 20):
            msg = f"{k} {v} is implausibly far from entry {e}"
            warnings.append(msg)
            downgrade(k, msg)
    rr = val("risk_reward")
    if e and sl and tp and abs(e - sl) > 0:
        implied = abs(tp - e) / abs(e - sl)
        if rr and abs(rr - implied) > max(0.25, implied * 0.15):
            msg = f"risk_reward {rr} disagrees with prices (implied {implied:.2f}R)"
            warnings.append(msg)
            downgrade("risk_reward", msg)
    # the R read off the chart must agree with the result the folder records ("1 | W", "2 | BE", "3 | L")
    o, rm = val("outcome"), val("r_multiple")
    if o and isinstance(rm, (int, float)) and (
            (o == "Win" and rm <= 0) or (o == "Loss" and rm > 0) or (o == "Breakeven" and abs(rm) > 0.25)):
        msg = f"R {rm} doesn't fit a {o}"
        warnings.append(msg)
        downgrade("r_multiple", msg)
    return warnings
