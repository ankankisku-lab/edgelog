"""Value normalisation and validation shared by every extraction stage and by manual edits."""

import re
from datetime import date, datetime, timezone
from typing import Any

from ..fields import FIELD_MAP, TIMEFRAMES

_NUM_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?|[-+]?\.\d+")

_SYNONYMS: dict[str, dict[str, str]] = {
    "direction": {"buy": "Long", "bull": "Long", "bullish": "Long", "long": "Long",
                  "sell": "Short", "bear": "Short", "bearish": "Short", "short": "Short"},
    "position_type": {"perp": "Perpetual", "perps": "Perpetual", "perpetual": "Perpetual", "swap": "Perpetual",
                      "future": "Futures", "futures": "Futures", "spot": "Spot", "margin": "Margin",
                      "option": "Options", "options": "Options", "cfd": "CFD"},
    "outcome": {"win": "Win", "won": "Win", "tp": "Win", "tp hit": "Win", "profit": "Win",
                "loss": "Loss", "lost": "Loss", "sl": "Loss", "sl hit": "Loss", "stopped": "Loss",
                "be": "Breakeven", "breakeven": "Breakeven", "break even": "Breakeven",
                "open": "Open", "running": "Open"},
    "impulse_move": {"yes": "Yes", "true": "Yes", "y": "Yes", "no": "No", "false": "No", "n": "No"},
    "followed_plan": {"yes": "Yes", "y": "Yes", "partial": "Partially", "partially": "Partially", "no": "No", "n": "No"},
    "ob_fvg_sr_reaction": {"ob": "Order Block", "order block": "Order Block", "orderblock": "Order Block",
                           "fvg": "Fair Value Gap", "fair value gap": "Fair Value Gap", "imbalance": "Fair Value Gap",
                           "support": "Support", "demand": "Support", "resistance": "Resistance", "supply": "Resistance",
                           "sr": "Support", "none": "None"},
}

_TF_RE = re.compile(r"^(?:(m|h|d|w)(\d{1,2})|(\d{1,2})\s*(m|min|mins|h|hr|d|w))$", re.I)


def parse_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace("−", "-")  # unicode minus
    s = re.sub(r"[$€£₹¥%xX\s]", "", s) if not re.search(r"\d[xX]\d", s) else s
    m = _NUM_RE.search(s)
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def normalize_timeframe(value: str) -> str | None:
    s = value.strip().lower().replace(" ", "")
    if s in ("mn", "1mn", "monthly", "1month"):
        return "MN"
    m = _TF_RE.match(s)
    if not m:
        return None
    if m.group(1):
        unit, n = m.group(1), m.group(2)
    else:
        n, unit = m.group(3), m.group(4)[0]
    tf = f"{unit.upper()}{int(n)}"
    if unit == "d":
        tf = "D1"
    if unit == "w":
        tf = "W1"
    return tf if tf in TIMEFRAMES else None


def timeframe_minutes(tf: str) -> int:
    if tf == "MN":
        return 43200
    return {"M": 1, "H": 60, "D": 1440, "W": 10080}[tf[0]] * int(tf[1:])


def parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    s = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y",
                "%d.%m.%Y", "%d-%m-%Y %H:%M", "%d/%m/%Y %H:%M"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _match_option(key: str, raw: str) -> str | None:
    fd = FIELD_MAP[key]
    s = raw.strip()
    syn = _SYNONYMS.get(key, {})
    if s.lower() in syn:
        return syn[s.lower()]
    for opt in fd.options:
        if opt.lower() == s.lower():
            return opt
    if key in ("entry_timeframe", "setup_timeframe"):
        return normalize_timeframe(s)
    if key == "fib_retracement":
        n = parse_number(s)
        if n is not None:
            if n > 1:  # "61.8" -> 0.618
                n = n / 100
            for opt in fd.options:
                if opt != "Other" and abs(float(opt) - n) < 0.006:
                    return opt
            return "Other"
    if key == "ema_alignment":
        low = s.lower()
        bull, bear = "bull" in low or "up" in low, "bear" in low or "down" in low
        if "cross" in low:
            return "Bullish crossover" if bull else "Bearish crossover" if bear else None
        if bull and not bear:
            return "Bullish stack"
        if bear and not bull:
            return "Bearish stack"
        if "mixed" in low:
            return "Mixed"
        if "flat" in low:
            return "Flat"
    return None


class ValidationError(ValueError):
    pass


def normalize_value(key: str, value: Any) -> Any:
    """Coerce a raw value into the field's canonical form. Raises ValidationError if invalid.

    Returns None for empty input. Used for both AI output and user edits so they're stored identically.
    """
    fd = FIELD_MAP.get(key)
    if fd is None:
        raise ValidationError(f"Unknown field '{key}'")
    if value is None or (isinstance(value, str) and value.strip() == "") or value == []:
        return None

    if fd.type in ("number", "rating"):
        n = parse_number(value)
        if n is None:
            raise ValidationError(f"{fd.label}: '{value}' is not a number")
        if fd.min is not None and n < fd.min:
            raise ValidationError(f"{fd.label} must be ≥ {fd.min}")
        if fd.max is not None and n > fd.max:
            raise ValidationError(f"{fd.label} must be ≤ {fd.max}")
        if fd.key in ("entry_price", "exit_price", "stop_loss", "take_profit", "position_size") and n == 0:
            raise ValidationError(f"{fd.label} must be greater than 0")
        if fd.type == "rating":
            n = float(round(n))
        return n

    if fd.type == "date":
        dt = parse_datetime(value)
        if dt is None:
            raise ValidationError(f"{fd.label}: '{value}' is not a valid date")
        return dt

    if fd.type == "select":
        opt = _match_option(key, str(value))
        if opt is None:
            raise ValidationError(f"{fd.label}: '{value}' is not one of {', '.join(fd.options)}")
        return opt

    if fd.type == "multiselect":
        items = value if isinstance(value, list) else re.split(r"[,;/|+]", str(value))
        out: list[str] = []
        for item in items:
            item = str(item).strip()
            if not item:
                continue
            opt = _match_option(key, item)
            if opt is None:
                if key == "mistakes":
                    opt = item[:60]  # free-text mistakes are allowed alongside the standard tags
                else:
                    raise ValidationError(f"{fd.label}: '{item}' is not one of {', '.join(fd.options)}")
            if opt not in out:
                out.append(opt)
        return out or None

    s = str(value).strip()
    if key == "asset":
        from ..instruments import find_in_text, match_symbol

        hit = match_symbol(s) or find_in_text(s)
        return hit[0].asset if hit else s.upper().replace(" ", "")[:40]
    if key == "trading_pair":
        s = s.upper().replace(" ", "")
    return s[:2000] if fd.type == "longtext" else s[:300]


def values_equal(a: Any, b: Any) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        # 1e-5 relative is under one tick for both MNQ (0.25 @ ~21000) and gold (0.1 @ ~4000)
        return abs(a - b) <= max(abs(a), abs(b)) * 1e-5 + 1e-9
    if isinstance(a, datetime) and isinstance(b, datetime):
        return a.date() == b.date() and (a.hour, a.minute) == (b.hour, b.minute) or (
            a.date() == b.date() and (a.hour == a.minute == 0 or b.hour == b.minute == 0)
        )
    if isinstance(a, list) and isinstance(b, list):
        return sorted(a) == sorted(b)
    return a == b


def to_json(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value
