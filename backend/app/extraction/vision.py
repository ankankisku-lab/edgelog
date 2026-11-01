"""Stage 3: multimodal model interpretation of all of a trade's screenshots in one request."""

import base64

import cv2
import numpy as np

from ..config import get_settings
from ..fields import EXTRACTABLE_KEYS, FIELD_MAP
from ..instruments import INSTRUMENTS
from ..llm import chat_json
from .normalize import ValidationError, normalize_value
from .types import Candidate

# fields that describe what was actually executed; a value only drawn as analysis must not fill them
EXECUTION_FIELDS = {"entry_price", "exit_price", "stop_loss", "take_profit", "position_size", "leverage", "pnl",
                    "direction", "outcome"}
ANNOTATION_TYPES = ["entry_line", "stop_loss_line", "take_profit_line", "position_tool", "ema", "order_block",
                    "fair_value_gap", "support", "resistance", "fibonacci", "impulse", "retracement", "liquidity",
                    "text_note", "other"]

_field_obj = {
    "type": "object",
    "properties": {
        "value": {"type": ["string", "null"]},
        "confidence": {"type": "number"},
        "basis": {"type": "string", "enum": ["execution", "annotation", "inferred", "none"]},
        "image": {"type": ["integer", "null"]},
        "evidence": {"type": "string"},
    },
    "required": ["value", "confidence", "basis", "image", "evidence"],
    "additionalProperties": False,
}

SCHEMA = {
    "type": "object",
    "properties": {
        "is_trading_chart": {"type": "boolean"},
        "images": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "image": {"type": "integer"},
                    "timeframe": {"type": ["string", "null"]},
                    "role": {"type": "string", "enum": ["entry", "setup", "other"]},
                    "description": {"type": "string"},
                },
                "required": ["image", "timeframe", "role", "description"],
                "additionalProperties": False,
            },
        },
        "fields": {
            "type": "object",
            "properties": {k: _field_obj for k in EXTRACTABLE_KEYS},
            "required": list(EXTRACTABLE_KEYS),
            "additionalProperties": False,
        },
        "annotations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "image": {"type": "integer"},
                    "type": {"type": "string", "enum": ANNOTATION_TYPES},
                    "label": {"type": "string"},
                    "price": {"type": ["string", "null"]},
                    "is_trade_level": {"type": "boolean"},
                },
                "required": ["image", "type", "label", "price", "is_trade_level"],
                "additionalProperties": False,
            },
        },
        "notes": {"type": "string"},
    },
    "required": ["is_trading_chart", "images", "fields", "annotations", "notes"],
    "additionalProperties": False,
}


def _field_guide() -> str:
    lines = []
    for k in EXTRACTABLE_KEYS:
        fd = FIELD_MAP[k]
        extra = f" one of: {', '.join(fd.options)}" if fd.options else ""
        hint = f" ({fd.help})" if fd.help else ""
        lines.append(f"- {k}: {fd.label}{hint}{extra}")
    return "\n".join(lines)


SYSTEM_PROMPT = f"""You read trading chart screenshots and extract journal data. All images belong to ONE trade:
typically a lower-timeframe entry chart (e.g. M1) and one or more higher-timeframe setup charts (e.g. M5).

Rules:
1. Only report what is visible. If a value is not shown, set value=null, confidence=0, basis="none". Never guess prices.
2. basis tells where a value came from:
   - "execution": an order/position panel, a filled order marker, or the position tool marking the trade actually taken.
   - "annotation": an analytical drawing or note (hypothetical level, zone, fib, idea) - not proof of execution.
   - "inferred": read from chart structure (e.g. EMA order, impulse leg, HTF bias) rather than printed text.
3. confidence is 0-1: 0.9+ only when the value is printed clearly; 0.6-0.8 when readable but small or partly hidden;
   below 0.5 when you are unsure.
4. image is the 1-based index of the image the value was read from. evidence quotes the text or describes the mark.
5. The trade's result and TP reason are written as a note on either image, e.g. "TP upto m5 untapped fvg | 3.2R"
   (reason, a bar, then the R). r_multiple is the number before R (keep a minus sign); tp_reason is the text.
   Charts often still show notes from EARLIER trades: use the rightmost (most recent) note only.
   trade_date: the "created with TradingView.com, <date>" watermark is when the SCREENSHOT was taken, not the
   trade date. Leave trade_date null unless the chart itself marks the entry date/time.
6. Numbers: plain digits with '.' as decimal separator, no thousands separators or currency symbols.
   For multi-select fields return a comma-separated list. Dates as YYYY-MM-DD or YYYY-MM-DD HH:MM.
7. asset: use one of these canonical names when the chart shows that instrument:
   {", ".join(f"{i.asset} ({i.name})" for i in INSTRUMENTS)}. trading_pair: the exact symbol printed on the chart
   (e.g. MNQZ2026, MNQ1!, XAUUSD, MGC1!). Read it from the chart header/legend, never from price levels alone.
8. images: for every image give its chart timeframe (as shown in the chart header/toolbar) and role
   ("entry" = the chart the entry was executed on, "setup" = higher-timeframe context).

Fields:
{_field_guide()}

Respond with a single JSON object matching the schema."""


def _encode(img: np.ndarray, max_side: int = 1600) -> str:
    h, w = img.shape[:2]
    if max(h, w) > max_side:
        f = max_side / max(h, w)
        img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        raise ValueError("could not encode image")
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()


def interpret(images: list[tuple[str, np.ndarray]], folder_name: str | None) -> tuple[list[Candidate], dict]:
    """images: [(file_name, bgr_array)]. Returns (candidates, raw_result)."""
    s = get_settings()
    content: list[dict] = [{
        "type": "text",
        "text": f"Trade folder: {folder_name or '(none)'}. {len(images)} screenshot(s) follow, in order.",
    }]
    for i, (name, img) in enumerate(images, 1):
        content.append({"type": "text", "text": f"Image {i}: {name}"})
        content.append({"type": "image_url", "image_url": {"url": _encode(img)}})
    data, model = chat_json(
        [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": content}],
        SCHEMA,
        s.vision_model_list,
        max_tokens=12000,  # reasoning models think before writing ~30 fields of JSON
    )

    out: list[Candidate] = []
    fields = data.get("fields") or {}
    for key in EXTRACTABLE_KEYS:
        f = fields.get(key)
        if not isinstance(f, dict) or f.get("value") in (None, "", "null"):
            continue
        try:
            value = normalize_value(key, f["value"])
        except ValidationError:
            continue
        if value is None:
            continue
        conf = min(float(f.get("confidence") or 0), 0.9)  # a model's self-rating is never "certain"
        basis = f.get("basis", "inferred")
        if key in EXECUTION_FIELDS and basis == "annotation":
            conf = min(conf, 0.5)  # hypothetical level: suggest it, never auto-fill it
        elif basis == "inferred":
            conf = min(conf, 0.8)
        img_no = f.get("image")
        label = images[img_no - 1][0] if isinstance(img_no, int) and 0 < img_no <= len(images) else "chart"
        out.append(Candidate(key, value, round(conf, 3), "vision", f"{label}: {f.get('evidence', '')[:200]} [{basis}]"))

    data["_model"] = model
    return out, data
