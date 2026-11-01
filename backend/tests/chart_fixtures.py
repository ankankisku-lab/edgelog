"""Draws synthetic TradingView-like chart screenshots with known levels, for testing OCR + CV."""

import cv2
import numpy as np


def make_chart(
    *,
    direction: str = "Long",
    entry: float = 67450.0,
    sl: float = 66800.0,
    tp: float = 68900.0,
    header: str = "BTCUSDT  ·  1  ·  BINANCE",
    note: str | None = "TP 2.2R  TP reason: previous high liquidity",
    dark: bool = False,
    size: tuple[int, int] = (1600, 900),
    p_lo: float = 66400.0,
    p_hi: float = 69400.0,
    seed: int = 7,
    step: float = 250.0,
    decimals: int = 1,
    tool: bool = True,
) -> np.ndarray:
    w, h = size
    bg = (23, 17, 19) if dark else (255, 255, 255)
    ink = (220, 220, 220) if dark else (40, 40, 40)
    grid = (45, 40, 42) if dark else (240, 240, 240)
    img = np.full((h, w, 3), bg, np.uint8)
    top, bottom, axis_x = 70, h - 50, w - 110

    def y_of(p: float) -> int:
        return int(round(top + (p_hi - p) / (p_hi - p_lo) * (bottom - top)))

    # grid + right price axis labels every 250
    p = (p_lo // step + 1) * step
    while p < p_hi:
        y = y_of(p)
        cv2.line(img, (0, y), (axis_x, y), grid, 1)
        cv2.putText(img, f"{p:.{decimals}f}", (axis_x + 10, y + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.55, ink, 1, cv2.LINE_AA)
        p += step
    cv2.line(img, (axis_x, 0), (axis_x, h), grid, 1)

    # candles (random walk)
    rng = np.random.default_rng(seed)
    price = (p_lo + p_hi) / 2
    for i, x in enumerate(range(20, axis_x - 420, 14)):
        o = price
        vol = (p_hi - p_lo) / 50
        c = o + rng.normal(0, vol)
        hi, lo = max(o, c) + abs(rng.normal(0, vol / 2)), min(o, c) - abs(rng.normal(0, vol / 2))
        col = (129, 153, 8) if c >= o else (69, 54, 242)  # BGR teal / red
        cv2.line(img, (x + 4, y_of(hi)), (x + 4, y_of(lo)), col, 1)
        cv2.rectangle(img, (x, y_of(max(o, c))), (x + 8, y_of(min(o, c))), col, -1)
        price = min(max(c, p_lo + (p_hi - p_lo) * 0.1), p_hi - (p_hi - p_lo) * 0.1)

    x0, x1 = axis_x - 400, axis_x - 40
    if not tool:
        cv2.putText(img, header, (16, 36), cv2.FONT_HERSHEY_SIMPLEX, 0.8, ink, 2, cv2.LINE_AA)
        if note:
            cv2.putText(img, note, (x0, top + 40), cv2.FONT_HERSHEY_SIMPLEX, 0.6, ink, 1, cv2.LINE_AA)
        return img

    # position tool: translucent teal profit zone + red risk zone, 0.2 alpha like TradingView
    overlay = img.copy()
    teal, red = (129, 153, 8), (69, 54, 242)
    if direction == "Long":
        cv2.rectangle(overlay, (x0, y_of(tp)), (x1, y_of(entry)), teal, -1)
        cv2.rectangle(overlay, (x0, y_of(entry)), (x1, y_of(sl)), red, -1)
    else:
        cv2.rectangle(overlay, (x0, y_of(entry)), (x1, y_of(tp)), teal, -1)
        cv2.rectangle(overlay, (x0, y_of(sl)), (x1, y_of(entry)), red, -1)
    img = cv2.addWeighted(overlay, 0.2, img, 0.8, 0)

    # the tool's exact levels highlighted on the axis, as TradingView does when it's selected
    for lvl, col in ((tp, teal), (entry, (120, 120, 120)), (sl, red)):
        y = y_of(lvl)
        cv2.rectangle(img, (axis_x + 2, y - 11), (w - 2, y + 11), col, -1)
        cv2.putText(img, f"{lvl:.{decimals}f}", (axis_x + 10, y + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)

    cv2.putText(img, header, (16, 36), cv2.FONT_HERSHEY_SIMPLEX, 0.8, ink, 2, cv2.LINE_AA)
    if note:
        cv2.putText(img, note, (x0, max(top + 20, y_of(max(tp, sl)) - 14)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, ink, 1, cv2.LINE_AA)
    return img
