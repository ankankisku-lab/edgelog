"""Stage 2: OCR. Reads text off the screenshot and maps labelled values to journal fields.

Uses RapidOCR (PaddleOCR models on ONNX Runtime): pip-installable, runs locally, no API calls.
"""

import logging
import re
import threading
from dataclasses import dataclass

import cv2
import numpy as np

from ..instruments import find_in_text
from .filename_parser import EXCHANGES, split_pair
from .normalize import normalize_timeframe, parse_number
from .types import Candidate

log = logging.getLogger(__name__)

_engine = None
_engine_lock = threading.Lock()
_engine_error: str | None = None


@dataclass
class OcrItem:
    text: str
    conf: float
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2


def ocr_available() -> tuple[bool, str | None]:
    _get_engine()
    return _engine is not None, _engine_error


def _get_engine():
    global _engine, _engine_error
    if _engine is None and _engine_error is None:
        with _engine_lock:
            if _engine is None and _engine_error is None:
                try:
                    from rapidocr_onnxruntime import RapidOCR

                    _engine = RapidOCR()
                except Exception as e:  # missing wheel / onnxruntime on an unsupported platform
                    _engine_error = f"OCR unavailable: {e}"
                    log.warning(_engine_error)
    return _engine


def run_ocr(img: np.ndarray) -> list[OcrItem]:
    engine = _get_engine()
    if engine is None:
        return []
    h, w = img.shape[:2]
    # small screenshots OCR far better upscaled; coordinates are mapped back to the original
    scale = 2.0 if max(h, w) < 1400 else 1.0
    work = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC) if scale != 1 else img
    with _engine_lock:
        result, _ = engine(work)
    items: list[OcrItem] = []
    for box, text, conf in result or []:
        xs, ys = [p[0] / scale for p in box], [p[1] / scale for p in box]
        items.append(OcrItem(str(text).strip(), float(conf), min(xs), min(ys), max(xs), max(ys)))
    return items


def group_lines(items: list[OcrItem]) -> list[str]:
    """Join OCR boxes that sit on the same visual row into lines, left to right."""
    rows: list[list[OcrItem]] = []
    for it in sorted(items, key=lambda i: i.cy):
        h = max(it.y1 - it.y0, 1)
        for row in rows:
            if abs(row[0].cy - it.cy) < h * 0.5:
                row.append(it)
                break
        else:
            rows.append([it])
    return [" ".join(i.text for i in sorted(r, key=lambda i: i.x0)) for r in rows]


@dataclass
class Segment:
    text: str
    x0: float
    cy: float
    h: float


def group_segments(items: list[OcrItem]) -> list[Segment]:
    """Like group_lines, but a wide horizontal gap starts a new segment: two notes side by side stay apart."""
    rows: list[list[OcrItem]] = []
    for it in sorted(items, key=lambda i: i.cy):
        h = max(it.y1 - it.y0, 1)
        for row in rows:
            if abs(row[0].cy - it.cy) < h * 0.5:
                row.append(it)
                break
        else:
            rows.append([it])
    out: list[Segment] = []
    for row in rows:
        row.sort(key=lambda i: i.x0)
        cur = [row[0]]
        for it in row[1:]:
            h = max(it.y1 - it.y0, 1)
            if it.x0 - cur[-1].x1 > max(4 * h, 60):
                out.append(Segment(" ".join(i.text for i in cur), cur[0].x0, cur[0].cy, cur[0].y1 - cur[0].y0))
                cur = []
            cur.append(it)
        out.append(Segment(" ".join(i.text for i in cur), cur[0].x0, cur[0].cy, cur[0].y1 - cur[0].y0))
    return out


# atomic group: "TP 2.2R" must not backtrack into "TP 2" + ".2R"
_N = r"((?>[+\-−]?\$?\d[\d,]*(?:\.\d+)?))"
_LABELLED: list[tuple[str, str, float]] = [
    # (regex with one number group, field, label strength)
    (rf"\b(?:avg\.?\s*)?(?:entry(?:\s*price)?|open\s*price|avg\.?\s*(?:entry|open)|entry\s*@)\s*[:=@\-]?\s*{_N}", "entry_price", 0.9),
    (rf"\b(?:exit|close|closing)\s*(?:price)?\s*[:=@\-]?\s*{_N}", "exit_price", 0.85),
    (rf"\b(?:stop\s*-?\s*loss|sl)\s*[:=@\-]?\s*{_N}(?!\s*%)", "stop_loss", 0.9),
    # OCR often drops the space after "2R" ("TP 2RTP reason"), so R/% counts even when glued to a word
    (rf"\b(?:take\s*-?\s*profit|tp\d?)\s*[:=@\-]?\s*{_N}(?!\s*(?:%|[rR](?-i:[^a-z])|[rR]$))", "take_profit", 0.85),
    (rf"\b(?:size|qty|quantity|position\s*size|contracts?)\s*[:=\-]?\s*{_N}", "position_size", 0.8),
    (rf"\b(?:realized|realised|closed)\s*(?:pnl|p&l|p/l|profit)\s*[:=\-]?\s*{_N}", "pnl", 0.85),
    (rf"\b(?:pnl|p&l|p/l|net\s*profit)\s*[:=\-]?\s*{_N}(?!\s*%)", "pnl", 0.65),
    (rf"\brisk\s*/?\s*reward(?:\s*ratio)?\s*[:=\-]?\s*{_N}", "risk_reward", 0.85),
    (rf"\b(?:rr|r:r)\s*[:=\-]?\s*(?:1\s*:\s*)?{_N}", "risk_reward", 0.75),
    # the R written on the chart is the trade's result in R (fallback when no full "TP … | xR" note is found)
    (rf"\b(?:tp|target)\s*[:=@\-]?\s*{_N}\s*[rR](?=(?-i:[^a-z])|$)", "r_multiple", 0.85),
]
# chart note "TP <reason> | 3.2R": OCR drops spaces ("TPuptom5untappedfvg|3.2R") and reads the bar as
# I or l ("upsurge I 2.91R", "1 pm ETI 1.54R" for "1 pm ET | 1.54R")
_TP_NOTE = re.compile(
    r"^\s*(?:tp|target)\s*[:\-]?\s*(?P<reason>.*?)\s*(?:[|¦/]|\bI\b|(?<=[A-Z])I|\bl\b|1(?=\s+\d))?\s*"
    r"(?P<r>[-−]?\d{1,2}(?:[.,]\d{1,2})?)\s*[rR]\W*$", re.I)
_NOTE_START = re.compile(r"^\s*(?:tp|target)", re.I)
_ENDS_WITH_R = re.compile(r"\d\s*[rR]\W*$")


def _tp_note(items: list[OcrItem], img_w: int) -> tuple[float, str, str] | None:
    """The trade's own TP note: (R, reason, raw text).

    A chart often still shows earlier trades' notes. Price runs left to right, so the rightmost note is
    the latest one, i.e. this trade's. A note that wraps onto a second line is joined back together.
    """
    segs = group_segments([i for i in items if i.cx < img_w * 0.88])
    found: list[tuple[float, float, str, str]] = []
    for k, s in enumerate(segs):
        if not _NOTE_START.match(s.text):
            continue
        text = s.text
        if not _ENDS_WITH_R.search(text):  # wrapped: the R sits on the next line, roughly left-aligned
            nxt = next((t for t in segs[k + 1:] if 0 < t.cy - s.cy < 2.6 * max(s.h, 1)
                        and abs(t.x0 - s.x0) < 8 * max(s.h, 1) and not _NOTE_START.match(t.text)), None)
            if nxt is None:
                continue
            text = f"{text} {nxt.text}"
        m = _TP_NOTE.search(text)
        if not m:
            continue
        raw = m.group("r").replace(",", ".").replace("−", "-")
        before = text[: m.start("r")]
        # the bar glued on as a "1": "…fvg11.6R" is "…fvg | 1.6R" (R values of 10+ are rare)
        if re.search(r"[A-Za-z]$", before) and re.fullmatch(r"1\d(?:\.\d+)?", raw):
            raw = raw[1:]
        r = parse_number(raw)
        if r is not None:
            found.append((s.x0, r, m.group("reason").strip(" .;:-|"), text))
    if not found:
        return None
    _, r, reason, text = max(found, key=lambda f: f[0])
    return r, reason, text
_TP_REASON = re.compile(r"(?:\b|(?<=\d[rR]))(?:tp|target)\s*(?:reason|because|why|logic)\s*[:=\-]\s*(.{3,120})", re.I)
_LEVERAGE = re.compile(r"\b(?:(cross|isolated|lev(?:erage)?)\s*[:\-]?\s*)?(\d{1,4})\s*[xX]\b")
_OPEN_CLOSE_DIR = re.compile(r"\b(?:open|close|opened|closed)\s+(long|short)\b", re.I)
# TradingView chart header, e.g. "BTCUSDT · 1 · BINANCE" or "BTCUSDT.P, 5, BYBIT"
_TV_HEADER = re.compile(r"\b([A-Z0-9]{2,15}(?:\.P|\d?!)?)\s*[·•,|]\s*(\d{1,4}[mhHDWM]?|[DWM]|\d+[sS])\s*[·•,|]\s*([A-Z0][A-Za-z0 ]{1,20})")  # OCR reads O as 0: "0ANDA"
# legend with the long name instead of a symbol: "Micro Gold Futures·5COMEX", "Futures 1 · COMEX"
_LEGEND_TF = re.compile(
    r"(?:futures|dollar|usd|index|[·•])\s*[·•]?\s*(\d{1,3}[mhHDWM]?)\s*[·•]?\s*"
    r"(?=COMEX|CME|NYMEX|CBOT|OANDA|FXCM|TVC|ICMARKETS|PEPPERSTONE|CAPITALCOM|FOREXCOM|SAXO|[·•]\s*[A-Z]{3,})", re.I)
# "H4" in "H4,012.1" is the candle's High price in the OHLC legend, not a 4-hour timeframe
_TF_WORD = re.compile(r"\b(\d{1,2}\s?(?:m|min|h|hr)|[mMhH]\d{1,2})\b(?![,.]?\d)")


def _tv_interval(raw: str) -> str | None:
    """TradingView writes intervals as minutes ('1', '5', '240') or with a unit ('1h', 'D')."""
    s = raw.strip()
    if s.isdigit():
        n = int(s)
        if n < 60:
            return normalize_timeframe(f"{n}m")
        if n % 60 == 0:
            return normalize_timeframe(f"{n // 60}h")
        return None
    if s.upper() in ("D", "1D"):
        return "D1"
    if s.upper() in ("W", "1W"):
        return "W1"
    if s.upper() in ("M", "1M") and s != "1m":
        return "MN"
    return normalize_timeframe(s)


def extract_from_ocr(items: list[OcrItem], image_label: str, img_w: int) -> tuple[list[Candidate], dict]:
    """Return field candidates plus per-image facts (timeframe, symbol) from OCR output."""
    # the right-hand price-axis column would otherwise glue axis prices onto chart notes
    lines = group_lines([i for i in items if i.cx < img_w * 0.88])
    mean_conf = float(np.mean([i.conf for i in items])) if items else 0.0
    out: list[Candidate] = []
    facts: dict = {}
    seen: set[str] = set()

    def add(field, value, strength, ev):
        if field in seen or value is None:
            return
        seen.add(field)
        out.append(Candidate(field, value, round(min(strength * max(mean_conf, 0.5), 0.9), 3), "ocr",
                             f"{image_label}: “{ev.strip()[:120]}”"))

    if note := _tp_note(items, img_w):
        r, reason, text = note
        add("r_multiple", r, 0.9, text)
        if len(reason) >= 3 and not re.match(r"(?:reason|because|why|logic)\b", reason, re.I):
            # spaces lost by OCR ("uptom5untappedfvg"): keep it, but let a readable reading win
            add("tp_reason", reason, 0.85 if " " in reason or len(reason) < 12 else 0.6, text)

    for line in lines:
        for rx, field, strength in _LABELLED:
            m = re.search(rx, line, re.I)
            if m:
                n = parse_number(m.group(1))
                if n is not None:
                    add(field, n, strength, m.group(0))
        if m := _TP_REASON.search(line):
            reason = re.sub(r"\s*(?:[|¦/I]\s*)?\d+(?:[.,]\d+)?\s*R\W*$", "", m.group(1))  # "… | 2R"
            add("tp_reason", re.sub(r"[\s\d.,]+$", "", reason).strip(" .;:-|"), 0.85, m.group(0))
        if m := _LEVERAGE.search(line):
            add("leverage", float(m.group(2)), 0.85 if m.group(1) else 0.6, m.group(0))
        if m := _OPEN_CLOSE_DIR.search(line):
            add("direction", m.group(1).capitalize(), 0.8, m.group(0))
        if "timeframe" not in facts and (m := _TV_HEADER.search(line)):
            asset, pair, market = split_pair(m.group(1))
            tf = _tv_interval(m.group(2))
            if tf:
                facts["timeframe"] = tf
                facts["timeframe_evidence"] = m.group(0)
            facts["symbol"] = m.group(1)
            add("asset", asset, 0.9, m.group(0))
            if pair:
                add("trading_pair", pair, 0.9, m.group(0))
            if market:
                add("market", market, 0.8, m.group(0))
            ex = EXCHANGES.get(m.group(3).strip().lower().replace("0", "o"))
            if ex:
                add("exchange", ex, 0.85, m.group(0))
        low = line.lower()
        for key, name in EXCHANGES.items():
            if re.search(rf"\b{key}\b", low) and key not in ("delta",):
                add("exchange", name, 0.6, line)

    if "timeframe" not in facts:
        for line in lines[:4]:
            if (m := _LEGEND_TF.search(line)) and (tf := _tv_interval(m.group(1))):
                facts["timeframe"], facts["timeframe_evidence"] = tf, m.group(0)
                break
    if "timeframe" not in facts:
        for line in lines[:6]:  # interval labels live in the toolbar at the top
            if m := _TF_WORD.search(line):
                tf = normalize_timeframe(m.group(1))
                if tf:
                    facts["timeframe"], facts["timeframe_evidence"] = tf, m.group(0)
                    break

    if "asset" not in seen:
        # chart legend / header lines: "MNQ1! · 1 · CME", "Micro E-mini Nasdaq-100 Futures", "Gold Spot / U.S. Dollar"
        for line in lines[:8]:
            if hit := find_in_text(line):
                ins, symbol, how = hit
                add("asset", ins.asset, 0.85 if how == "symbol" else 0.8, line)
                if how == "symbol":
                    add("trading_pair", symbol, 0.85, line)
                add("market", ins.market, 0.8, line)
                if ins.position_type:
                    add("position_type", ins.position_type, 0.75, line)
                break
    if "asset" not in seen:
        for it in sorted(items, key=lambda i: (i.y0, i.x0))[:25]:
            tok = re.sub(r"[^A-Z0-9.]", "", it.text.upper())
            asset, pair, market = split_pair(tok) if 5 <= len(tok) <= 15 else (None, None, None)
            if pair and market:
                add("asset", asset, 0.75, it.text)
                add("trading_pair", pair, 0.75, it.text)
                add("market", market, 0.7, it.text)
                break

    facts["text"] = "\n".join(lines)[:4000]
    facts["mean_conf"] = round(mean_conf, 3)
    return out, facts
