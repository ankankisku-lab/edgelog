"""Instrument registry: recognises the symbols you trade however a chart, broker or filename writes them.

Add an entry here to teach the extractor a new instrument. `patterns` are matched (case-insensitive,
whole token) against filename tokens, folder names, OCR'd chart headers and the vision model's output.
"""

import re
from dataclasses import dataclass

FUT_MONTH = r"[FGHJKMNQUVXZ]"
# futures suffixes: MNQ1!, MNQ!, MNQZ6, MNQZ26, MNQZ2026, MNQ 12-26, MNQ DEC26
_FUT = rf"(?:\d?!|{FUT_MONTH}\d{{1,4}}|[\s_-]?\d{{2}}[-/]\d{{2,4}}|[\s_-]?(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\d{{2,4}})?"


@dataclass(frozen=True)
class Instrument:
    asset: str  # canonical journal value
    name: str
    family: str  # instruments that track the same underlying, e.g. MNQ and NQ
    market: str
    position_type: str | None
    patterns: tuple[str, ...]
    # long-form names as they appear in chart legends ("Micro E-mini Nasdaq-100 Futures")
    names: tuple[str, ...] = ()
    # currency per 1.0 price move per 1 unit of position size (contract / lot); None = unknown
    point_value: float | None = None
    # plausible price band, used only as a weak hint when no symbol is visible
    price_range: tuple[float, float] | None = None


INSTRUMENTS: list[Instrument] = [
    Instrument("MNQ", "Micro E-mini Nasdaq-100", "nasdaq", "Futures", "Futures",
               (rf"MNQ{_FUT}",), ("micro e-mini nasdaq", "micro nasdaq", "micro e-mini nasdaq-100"),
               point_value=2.0, price_range=(12000, 40000)),
    Instrument("NQ", "E-mini Nasdaq-100", "nasdaq", "Futures", "Futures",
               (rf"NQ{_FUT}",), ("e-mini nasdaq-100", "e-mini nasdaq"), point_value=20.0, price_range=(12000, 40000)),
    Instrument("XAUUSD", "Gold spot", "gold", "Commodities", None,
               (r"XAU[/_-]?USD[.+]?[A-Z0-9]{0,4}", r"GOLD[.+]?[A-Z0-9]{0,3}", r"XAU"),
               ("gold spot", "gold / u.s. dollar", "gold spot / u.s. dollar", "gold / us dollar"),
               point_value=100.0, price_range=(1500, 6000)),
    Instrument("MGC", "Micro Gold futures", "gold", "Commodities", "Futures",
               (rf"MGC{_FUT}",), ("micro gold",), point_value=10.0, price_range=(1500, 6000)),
    Instrument("GC", "Gold futures", "gold", "Commodities", "Futures",
               (rf"GC{_FUT}",), ("gold futures", "comex gold"), point_value=100.0, price_range=(1500, 6000)),
]

_COMPILED = [(ins, [re.compile(rf"^{p}$", re.I) for p in ins.patterns]) for ins in INSTRUMENTS]
BY_ASSET = {i.asset: i for i in INSTRUMENTS}


def match_symbol(token: str) -> tuple[Instrument, str] | None:
    """Match one token (e.g. 'MNQZ2026', 'XAUUSDm', 'GC1!'). Returns (instrument, cleaned symbol)."""
    t = token.strip().strip(".,:;()[]")
    if not t:
        return None
    for ins, rxs in _COMPILED:
        if any(rx.match(t) for rx in rxs):
            return ins, t.upper()
    return None


def find_in_text(text: str) -> tuple[Instrument, str, str] | None:
    """Search free text (OCR lines, folder names) for a known instrument. Returns (ins, symbol, how)."""
    low = text.lower()
    for ins in INSTRUMENTS:
        for n in ins.names:
            if n in low:
                return ins, ins.asset, "name"
    for tok in re.split(r"[\s·•,|_]+", text):
        if m := match_symbol(tok):
            return m[0], m[1], "symbol"
    # "MNQ 12-26" style: the contract month is a separate token
    for m in re.finditer(r"\b([A-Z]{2,4})[\s_-](\d{2}[-/]\d{2,4})\b", text.upper()):
        if mm := match_symbol(f"{m.group(1)} {m.group(2)}"):
            return mm[0], mm[1], "symbol"
    return None


def family_from_price(prices: list[float]) -> list[Instrument] | None:
    """If every price fits exactly one instrument family's usual band, return that family's members.

    A weak hint for screenshots with no visible symbol: callers must keep it below auto-fill confidence.
    """
    prices = [p for p in prices if p]
    if not prices:
        return None
    fits = [i for i in INSTRUMENTS if i.price_range and all(i.price_range[0] <= p <= i.price_range[1] for p in prices)]
    families = {i.family for i in fits}
    return fits if len(families) == 1 else None
