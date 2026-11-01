"""Stage 2b: classical computer vision on the chart image (OpenCV).

1. Finds a TradingView-style Long/Short Position tool: a green (profit) box stacked directly on a
   red (risk) box. Box order gives the direction, and the ratio of their heights gives the planned
   R:R. Both are pixel geometry, so they hold on any linear price scale.
2. Calibrates the price axis by fitting the OCR'd labels in the right-hand axis column (pixel
   y -> price). Combined with the box edges that yields entry, stop-loss and take-profit prices.
"""

from dataclasses import dataclass

import cv2
import numpy as np

from .normalize import parse_number
from .ocr import OcrItem
from .types import Candidate


@dataclass
class Box:
    x0: int
    y0: int
    x1: int
    y1: int
    fill: float

    @property
    def w(self) -> int:
        return self.x1 - self.x0

    @property
    def h(self) -> int:
        return self.y1 - self.y0


def _zone_mask(hsv: np.ndarray, hue_ranges: list[tuple[int, int]]) -> np.ndarray:
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    # position-tool fills are translucent, so saturation is low on light themes; the floor of 18
    # keeps grey grid/background out while still catching pale teal and pink
    tone = (s >= 18) & (v >= 35)
    hue = np.zeros_like(tone)
    for lo, hi in hue_ranges:
        hue |= (h >= lo) & (h <= hi)
    mask = (tone & hue).astype(np.uint8) * 255
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k, iterations=2)


def _boxes(mask: np.ndarray, img_w: int, img_h: int) -> list[Box]:
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if w < img_w * 0.06 or h < img_h * 0.02 or w * h < img_w * img_h * 0.004:
            continue
        fill = float(cv2.countNonZero(mask[y:y + h, x:x + w])) / (w * h)
        # candles are tall and thin; zones are wide and mostly filled
        if fill < 0.55 or w < h * 0.35:
            continue
        boxes.append(Box(x, y, x + w, y + h, fill))
    return boxes


def detect_position_tool(img: np.ndarray) -> dict | None:
    h, w = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    greens = _boxes(_zone_mask(hsv, [(70, 100)]), w, h)  # teal / green
    reds = _boxes(_zone_mask(hsv, [(0, 8), (165, 180)]), w, h)  # red / pink
    best = None
    for g in greens:
        for r in reds:
            overlap = min(g.x1, r.x1) - max(g.x0, r.x0)
            if overlap < 0.6 * min(g.w, r.w):
                continue
            tol = max(6, int(h * 0.015))
            if abs(g.y1 - r.y0) <= tol:
                direction, entry_y, tp_y, sl_y = "Long", (g.y1 + r.y0) / 2, g.y0, r.y1
            elif abs(r.y1 - g.y0) <= tol:
                direction, entry_y, tp_y, sl_y = "Short", (r.y1 + g.y0) / 2, g.y1, r.y0
            else:
                continue
            score = overlap * (g.fill + r.fill) / (1 + abs(g.w - r.w))
            if best is None or score > best["score"]:
                reward_px, risk_px = abs(entry_y - tp_y), abs(sl_y - entry_y)
                if risk_px < 3:
                    continue
                best = {
                    "score": score,
                    "direction": direction,
                    "entry_y": entry_y,
                    "tp_y": float(tp_y),
                    "sl_y": float(sl_y),
                    "rr": round(reward_px / risk_px, 2),
                    "profit_box": [g.x0, g.y0, g.x1, g.y1],
                    "risk_box": [r.x0, r.y0, r.x1, r.y1],
                    "fill": round((g.fill + r.fill) / 2, 2),
                }
    return best


def calibrate_price_axis(items: list[OcrItem], img_w: int) -> dict | None:
    """Least-squares fit of price = a*y + b over numeric labels in the right-hand axis column."""
    pts = []
    for it in items:
        if it.cx < img_w * 0.85:
            continue
        txt = it.text.replace(" ", "")
        if not any(ch.isdigit() for ch in txt) or ":" in txt or "%" in txt:
            continue
        n = parse_number(txt)
        if n is not None and n > 0:
            decimals = len(txt.split(".")[1]) if "." in txt else 0
            pts.append((it.cy, n, decimals))
    if len(pts) < 3:
        return None
    ys = np.array([p[0] for p in pts])
    ps = np.array([p[1] for p in pts])
    keep = np.ones(len(pts), bool)
    for _ in range(3):  # drop outliers (e.g. a % label or a mis-read digit) and refit
        if keep.sum() < 3:
            return None
        a, b = np.polyfit(ys[keep], ps[keep], 1)
        resid = np.abs(ps - (a * ys + b))
        med = np.median(resid[keep]) + 1e-9
        new_keep = resid <= max(med * 4, np.ptp(ps[keep]) * 0.004)
        if (new_keep == keep).all():
            break
        keep = new_keep
    if keep.sum() < 3 or a >= 0:  # price must fall as y grows downwards
        return None
    fit = ps[keep] - (a * ys[keep] + b)
    r2 = 1 - (fit ** 2).sum() / (((ps[keep] - ps[keep].mean()) ** 2).sum() + 1e-12)
    if r2 < 0.999:
        return None
    decimals = int(np.median([pts[i][2] for i in range(len(pts)) if keep[i]]))
    return {"a": float(a), "b": float(b), "r2": float(r2), "points": int(keep.sum()), "decimals": decimals,
            "labels": sorted(float(p) for p in ps[keep])}


def _snap(price: float, labels: list[float], tol: float) -> float | None:
    near = [p for p in labels if abs(p - price) <= tol]
    return min(near, key=lambda p: abs(p - price)) if near else None


def analyze_chart(img: np.ndarray, items: list[OcrItem], image_label: str) -> tuple[list[Candidate], dict]:
    h, w = img.shape[:2]
    out: list[Candidate] = []
    facts: dict = {}
    tool = detect_position_tool(img)
    axis = calibrate_price_axis(items, w)
    if axis:
        facts["price_axis"] = {k: axis[k] for k in ("a", "b", "r2", "points", "decimals")}
    if not tool:
        return out, facts

    facts["position_tool"] = {k: v for k, v in tool.items() if k != "score"}
    ev = f"{image_label}: position tool ({tool['direction'].lower()}, boxes {tool['profit_box']} / {tool['risk_box']})"
    conf = 0.85 if tool["fill"] > 0.7 else 0.75
    out.append(Candidate("direction", tool["direction"], conf, "cv", ev))
    out.append(Candidate("risk_reward", tool["rr"], round(conf - 0.05, 2), "cv", ev + f", reward/risk height {tool['rr']}"))

    if axis:
        step = (axis["labels"][-1] - axis["labels"][0]) / max(len(axis["labels"]) - 1, 1)
        for field, y in (("entry_price", tool["entry_y"]), ("take_profit", tool["tp_y"]), ("stop_loss", tool["sl_y"])):
            price = axis["a"] * y + axis["b"]
            # TradingView prints the tool's exact levels on the axis when it's selected: snap to them
            snapped = _snap(price, axis["labels"], abs(axis["a"]) * 4)
            value = round(snapped if snapped is not None else price, axis["decimals"])
            c = 0.8 if snapped is not None else 0.62  # interpolated levels always need a human check
            note = "matches axis label" if snapped is not None else f"interpolated from axis (±{abs(axis['a']) * 2:.{axis['decimals']}f})"
            out.append(Candidate(field, value, c, "cv", f"{image_label}: position-tool edge at y={y:.0f}, {note}"))
        facts["axis_label_step"] = step
    return out, facts
