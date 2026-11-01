"""Stage 1: rule-based parsing of the screenshot's filename and folder path.

Handles names like:
    BTCUSDT_Long_Entry_67450_SL_66800_TP_68900_01-11-2026.png
    ETHUSDT short e3450 sl3510 tp3300 15m 2026-03-04.jpg
    EURUSD-Buy-H1-Entry-1.0850-SL-1.0820-TP-1.0910.png
Folder paths like "Trading Journal/2026/March" give a year/month that disambiguates dates.
"""

import calendar
import re
from datetime import datetime, timezone
from pathlib import PurePosixPath

from ..instruments import BY_ASSET, match_symbol
from .normalize import normalize_timeframe
from .types import Candidate

QUOTES = ("USDT", "USDC", "BUSD", "FDUSD", "USD", "PERP", "BTC", "ETH", "EUR", "INR")
FIAT = {"EUR", "USD", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD", "SGD", "HKD", "INR", "ZAR", "MXN", "SEK", "NOK"}
METALS = {"XAU", "XAG", "XPT", "XPD"}
COMMODITIES = {"USOIL", "UKOIL", "WTI", "BRENT", "NATGAS", "XAUUSD", "XAGUSD"}
INDICES = {"US30", "US100", "US500", "NAS100", "NQ", "ES", "SPX", "SPX500", "GER40", "DE40", "UK100", "JP225",
           "NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "HK50", "DJI", "NDX"}
EXCHANGES = {"binance": "Binance", "bybit": "Bybit", "okx": "OKX", "bitget": "Bitget", "mexc": "MEXC",
             "kucoin": "KuCoin", "coinbase": "Coinbase", "kraken": "Kraken", "deribit": "Deribit",
             "hyperliquid": "Hyperliquid", "bingx": "BingX", "delta": "Delta Exchange", "mt4": "MT4", "mt5": "MT5",
             "zerodha": "Zerodha", "ftmo": "FTMO", "oanda": "OANDA", "ibkr": "IBKR", "tradovate": "Tradovate",
             "cme": "CME", "comex": "COMEX", "cbot": "CBOT", "tvc": "TVC", "fxcm": "FXCM", "pepperstone": "Pepperstone",
             "icmarkets": "IC Markets", "exness": "Exness", "topstep": "Topstep", "apex": "Apex",
             "ninjatrader": "NinjaTrader", "rithmic": "Rithmic", "tradestation": "TradeStation"}
MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})

_NUM = r"(\d+(?:[.,]\d+)?)"
# keyword -> field, matched as "<kw><sep?><number>" within a token or across adjacent tokens
NUMERIC_KEYWORDS: list[tuple[str, str]] = [
    (r"entry|entr|ep|en|e|open|buyat|sellat", "entry_price"),
    (r"stoploss|stop|sl", "stop_loss"),
    (r"takeprofit|target|tgt|tp1|tp", "take_profit"),
    (r"exit|close|cl|ex", "exit_price"),
    (r"size|qty|quantity|lots|lot|amount", "position_size"),
    (r"pnl|profit|pl", "pnl"),
]
_DIRECTION = {"long": "Long", "buy": "Long", "short": "Short", "sell": "Short"}
_OUTCOME = {"win": "Win", "won": "Win", "loss": "Loss", "lost": "Loss", "be": "Breakeven",
            "breakeven": "Breakeven", "tphit": "Win", "slhit": "Loss"}
_POSITION_TYPE = {"perp": "Perpetual", "perpetual": "Perpetual", "futures": "Futures", "spot": "Spot",
                  "margin": "Margin"}
_REACTION = {"ob": "Order Block", "fvg": "Fair Value Gap", "imbalance": "Fair Value Gap", "sr": "Support"}

_DATE_PATTERNS = [
    (re.compile(r"(?<!\d)(20\d{2})[-_.](\d{1,2})[-_.](\d{1,2})(?!\d)"), "ymd"),
    (re.compile(r"(?<!\d)(\d{1,2})[-_.](\d{1,2})[-_.](20\d{2})(?!\d)"), "xy_y"),
    (re.compile(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)"), "ymd"),
]
_TIME_RE = re.compile(r"(?<!\d)([01]?\d|2[0-3])[:hH.]([0-5]\d)(?:[:.]([0-5]\d))?(?![\d])")
_COMPACT_TIME_RE = re.compile(r"^[\s_T-]{1,3}([01]\d|2[0-3])([0-5]\d)([0-5]\d)?(?!\d)")
STOPWORDS = {"screenshot", "screen", "image", "img", "chart", "trade", "trades", "capture", "snip", "snapshot",
             "photo", "pic", "untitled", "tradingview", "tv", "copy", "final", "new", "setup", "journal", "whatsapp"}


def split_pair(symbol: str) -> tuple[str, str | None, str | None]:
    """Return (asset, pair, market) for a symbol like MNQZ2026, XAUUSD, BTCUSDT, EURUSD, NAS100."""
    if m := match_symbol(symbol):  # instruments in the registry win (MNQ, gold, ...)
        ins, sym = m
        # a bare alias ("gold", "mnq") names the instrument but isn't a tradable symbol
        return ins.asset, (None if sym in (ins.asset, "GOLD", "XAU") else sym), ins.market
    s = symbol.upper().replace("/", "").replace("-", "")
    s = s.removesuffix(".P").removesuffix("PERP") if s.endswith(("PERP", ".P")) and len(s) > 6 else s
    if s in INDICES:
        return s, None, "Indices"
    if s in COMMODITIES:
        return s[:3] if s[:3] in METALS else s, s, "Commodities"
    if len(s) == 6 and s[:3] in METALS and s[3:] in FIAT:
        return s[:3], s, "Commodities"
    if len(s) == 6 and s[:3] in FIAT and s[3:] in FIAT:
        return s, s, "Forex"
    for q in QUOTES:
        if s.endswith(q) and len(s) > len(q) + 1:
            base = s[: -len(q)]
            if base.isalnum() and not base.isdigit():
                return base, s, "Crypto"
    return s, None, None


def parse_folder(folder_path: str | None) -> dict:
    """Extract year / month hints from a path like 'Trading Journal/2026/March'."""
    hints: dict = {}
    if not folder_path:
        return hints
    for part in PurePosixPath(folder_path.replace("\\", "/")).parts:
        p = part.strip().lower()
        if re.fullmatch(r"20\d{2}", p):
            hints["year"] = int(p)
        m = re.fullmatch(r"(?:\d{1,2}[\s._-]*)?([a-z]+)(?:[\s._-]*(20\d{2}))?", p)
        if m and m.group(1) in MONTHS:
            hints["month"] = MONTHS[m.group(1)]
            if m.group(2):
                hints["year"] = int(m.group(2))
        if re.fullmatch(r"(20\d{2})[-_](\d{2})", p):
            y, mo = p.replace("_", "-").split("-")
            hints["year"], hints["month"] = int(y), int(mo)
    return hints


def _parse_date(text: str, folder: dict) -> Candidate | None:
    for rx, kind in _DATE_PATTERNS:
        m = rx.search(text)
        if not m:
            continue
        a, b, c = (int(g) for g in m.groups())
        if kind == "ymd":
            y, mo, d, conf = a, b, c, 0.95
        else:
            y = c
            # ambiguous dd-mm vs mm-dd: prefer day-first (the common convention outside the US),
            # unless the folder month or the numbers themselves say otherwise.
            if a > 12 and b <= 12:
                d, mo, conf = a, b, 0.95
            elif b > 12 and a <= 12:
                d, mo, conf = b, a, 0.95
            elif folder.get("month") == b:
                d, mo, conf = a, b, 0.95
            elif folder.get("month") == a:
                d, mo, conf = b, a, 0.95
            else:
                d, mo, conf = a, b, 0.75
        try:
            dt = datetime(y, mo, d, tzinfo=timezone.utc)
        except ValueError:
            continue
        rest = text[m.end():]
        end = m.end()
        tm = _COMPACT_TIME_RE.search(rest) or _TIME_RE.match(rest.lstrip(" _T-"))
        if tm:
            dt = dt.replace(hour=int(tm.group(1)), minute=int(tm.group(2)))
            end += tm.end() + (len(rest) - len(rest.lstrip(" _T-")) if tm.re is _TIME_RE else 0)
        if folder.get("year") and folder["year"] != y:
            conf = min(conf, 0.6)
        # evidence spans the date and any time that followed it, so the caller can strip both
        return Candidate("trade_date", dt, conf, "filename", text[m.start():end])
    return None


def parse_filename(file_name: str, folder_path: str | None = None) -> list[Candidate]:
    stem = re.sub(r"\.(png|jpe?g|webp|gif|bmp|heic)$", "", file_name, flags=re.I)
    folder = parse_folder(folder_path)
    out: list[Candidate] = []
    found: set[str] = set()

    def add(field: str, value, conf: float, ev: str) -> None:
        if field not in found:
            found.add(field)
            out.append(Candidate(field, value, conf, "filename", ev))

    date_c = _parse_date(stem, folder)
    if date_c:
        if TV_CAPTURE_NAME.match(file_name):  # only says when the screenshot was taken: a weak hint
            add(date_c.field, date_c.value, 0.45, f"{date_c.evidence} (TradingView screenshot time)")
        else:
            add(date_c.field, date_c.value, date_c.confidence, date_c.evidence)
        stem_wo_date = stem.replace(date_c.evidence, " ")
        if date_c.value.hour or date_c.value.minute:  # drop the consumed time token too
            stem_wo_date = re.sub(r"^([\s_T-]*)(\d{1,2}[:hH.]\d{2}(?:[:.]\d{2})?|\d{4,6})", r"",
                                  stem_wo_date.split(" ", 1)[-1]) if False else re.sub(
                r"(?<=\s)[\s_T-]*(\d{1,2}[:hH.]\d{2}(?:[:.]\d{2})?|\d{4,6})(?!\d)", " ", stem_wo_date, count=1)
    else:
        stem_wo_date = stem

    # journal folders: "1 | W" (trade 1, a win) or a day folder "19th | Mon | 2W | 1BE | 3NY" whose tallies
    # (2 wins, 1 breakeven, 3 in the NY session) must not be read as timeframes ("1W" = one win, not weekly)
    segments = [seg.strip() for seg in stem.split("|")]
    day_m = _DAY_FOLDER.match(stem)
    is_day_folder = bool(day_m and (day_m.group(2) or day_m.group(3)))
    if len(segments) > 1:
        for seg in segments:
            if seg.upper() in ("W", "L", "BE"):
                add("outcome", {"W": "Win", "L": "Loss", "BE": "Breakeven"}[seg.upper()], 0.95, f"“{seg}” in “{stem}”")
        tally = {k.upper(): int(n) for n, k in (re.fullmatch(r"(\d+)\s*(W|L|BE)", s, re.I).groups()
                                                for s in segments if re.fullmatch(r"\d+\s*(W|L|BE)", s, re.I))}
        if is_day_folder and sum(tally.values()) == 1:  # a day with one trade: its tally is that trade's result
            k = next(iter(tally))
            add("outcome", {"W": "Win", "L": "Loss", "BE": "Breakeven"}[k], 0.85, f"day tally “1{k}”")

    tokens = [t for t in re.split(r"[_\s|]+|(?<=[A-Za-z])-|-(?=[A-Za-z])", stem_wo_date) if t]
    lowered = [t.lower() for t in tokens]

    # keyword + number, either fused ("SL66800") or split across tokens ("SL", "66800")
    for i, tok in enumerate(lowered):
        for kw, field in NUMERIC_KEYWORDS:
            m = re.fullmatch(rf"(?:{kw})[:=@]?{_NUM}", tok)
            if m:
                add(field, float(m.group(1).replace(",", ".")), 0.95, tokens[i])
                break
            if re.fullmatch(rf"(?:{kw})", tok) and i + 1 < len(lowered):
                nm = re.fullmatch(rf"[-+]?{_NUM}", lowered[i + 1])
                if nm:
                    sign = -1 if lowered[i + 1].startswith("-") else 1
                    add(field, sign * float(nm.group(1).replace(",", ".")), 0.95, f"{tokens[i]} {tokens[i + 1]}")
                    break

    for i, tok in enumerate(lowered):
        if tok in _DIRECTION:
            add("direction", _DIRECTION[tok], 0.95, tokens[i])
        elif tok in _OUTCOME:
            add("outcome", _OUTCOME[tok], 0.9, tokens[i])
        elif tok in _POSITION_TYPE:
            add("position_type", _POSITION_TYPE[tok], 0.9, tokens[i])
        elif tok in EXCHANGES:
            add("exchange", EXCHANGES[tok], 0.9, tokens[i])
        elif m := re.fullmatch(r"(?:x(\d{1,4})|(\d{1,4})x|lev(\d{1,4}))", tok):
            add("leverage", float(next(g for g in m.groups() if g)), 0.9, tokens[i])
        elif m := re.fullmatch(r"(?:rr|r)[:]?(\d+(?:[.,]\d+)?)|1[:](\d+(?:[.,]\d+)?)", tok):
            add("risk_reward", float((m.group(1) or m.group(2)).replace(",", ".")), 0.85, tokens[i])
        elif not is_day_folder and (tf := normalize_timeframe(tok)) and not re.fullmatch(r"\d+", tok):
            add("timeframe", tf, 0.9, tokens[i])

    reactions = [_REACTION[t] for t in lowered if t in _REACTION]
    if reactions:
        add("ob_fvg_sr_reaction", list(dict.fromkeys(reactions)), 0.8, ",".join(reactions))

    # symbol: first token that looks like a tradable instrument
    reserved = {kw for kw in ("long", "short", "buy", "sell", "win", "loss", "be")} | set(MONTHS) | set(EXCHANGES)
    for i, tok in enumerate(tokens):
        t = tok.upper()
        if t.lower() in reserved or t.lower() in STOPWORDS or not re.fullmatch(r"[A-Z0-9./!+]{2,15}", t) or re.fullmatch(r"[\d.]+", t):
            continue
        if any(re.fullmatch(rf"(?:{kw}){_NUM}?", t.lower()) for kw, _ in NUMERIC_KEYWORDS):
            continue
        asset, pair, market = split_pair(t)
        if pair or market:
            add("asset", asset, 0.95, tok)
            if pair:
                add("trading_pair", pair, 0.95, tok)
            add("market", market, 0.85, tok)
            if (ins := BY_ASSET.get(asset)) and ins.position_type:
                add("position_type", ins.position_type, 0.85, tok)
            break
        if i == 0 and re.fullmatch(r"[A-Z]{2,10}", t) and normalize_timeframe(tok) is None:
            add("asset", t, 0.6, tok)  # a bare leading ticker is plausible but unconfirmed
            break

    return out


WEEKDAYS = {d.lower(): i for i, d in enumerate(calendar.day_abbr)} | {d.lower(): i for i, d in enumerate(calendar.day_name)}
# day folder: "19th | Mon | 2W | 1BE | 3NY", "21st Wed", "05 | Friday"
_DAY_FOLDER = re.compile(
    r"^\s*(\d{1,2})\s*(st|nd|rd|th)?\b(?:\s*[|,._-]?\s*(" + "|".join(sorted(WEEKDAYS, key=len, reverse=True)) + r")\b)?", re.I)
# TradingView's own screenshot names, e.g. "MGC1!_2026-10-24_21-38-00.png": the capture time, not the trade's
TV_CAPTURE_NAME = re.compile(r"^[A-Z0-9!.]+_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(?:\.\w+)?$", re.I)


def folder_day_date(folder_path: str | None, captured: datetime | None) -> Candidate | None:
    """Trade date from a folder tree like "GOLD/OCT/19th | Mon | 2W/1 | W".

    Month comes from a month folder, day (and weekday) from a day folder. Without a year in the path, the
    year is the one, at or before the screenshot's capture time, in which that day falls on the stated weekday.
    """
    if not folder_path:
        return None
    hints = parse_folder(folder_path)
    month = hints.get("month")
    day = weekday = None
    day_part = ""
    for part in PurePosixPath(folder_path.replace("\\", "/")).parts:
        m = _DAY_FOLDER.match(part)
        if m and (m.group(2) or m.group(3)):  # "1 | W" (a trade number) has neither suffix nor weekday
            day, day_part = int(m.group(1)), part
            weekday = WEEKDAYS[m.group(3).lower()] if m.group(3) else None
    if not (month and day):
        return None
    ref = captured or datetime.now(timezone.utc)
    if hints.get("year"):
        year = hints["year"]
    else:  # the latest such date not after the screenshot was taken (a trade is screenshotted after it happens)
        year = ref.year if (month, day) <= (ref.month, ref.day) else ref.year - 1
    try:
        dt = datetime(year, month, day, tzinfo=timezone.utc)
    except ValueError:
        return None
    ev = f"folder “{day_part.strip()}” in {calendar.month_abbr[month]}" + ("" if hints.get("year") else f", year {year} from screenshot time")
    if weekday is None:
        return Candidate("trade_date", dt, 0.9 if hints.get("year") else 0.85, "folder", ev)
    if dt.weekday() == weekday:
        return Candidate("trade_date", dt, 0.95, "folder", f"{ev}; weekday matches")
    # a mislabelled weekday: keep the date but have the user check it
    return Candidate("trade_date", dt, 0.6, "folder",
                     f"{ev}; ⚠ folder says {calendar.day_abbr[weekday]} but it was a {calendar.day_abbr[dt.weekday()]}")


_SESSIONS = [  # in the order they happen within a trading day
    ("Asia", ("a", "as", "asia", "asian", "tokyo")),
    ("London", ("ldn", "lon", "london", "eu")),  # not "L": "1L" is one loss
    ("New York", ("ny", "nyc", "newyork", "us")),
]
_SESSION_OF = {alias: name for name, aliases in _SESSIONS for alias in aliases}
_TALLY = re.compile(r"^(\d+)\s*([a-z]+)$", re.I)


def folder_session(folder_path: str | None) -> Candidate | None:
    """Session of one trade from its day folder's tallies.

    "28th | Wed | 3W | 2A | 1NY / 3 | W": 2 Asia trades then 1 New York. Trades are numbered in time
    order and Asia comes before London before New York, so trades 1-2 are Asia and trade 3 is New York.
    """
    if not folder_path:
        return None
    parts = [p.strip() for p in PurePosixPath(folder_path.replace("\\", "/")).parts]
    day_i = next((i for i, p in enumerate(parts)
                  if (m := _DAY_FOLDER.match(p)) and (m.group(2) or m.group(3))), None)
    if day_i is None:
        return None
    counts: dict[str, int] = {}
    results = 0
    for seg in parts[day_i].split("|"):
        m = _TALLY.match(seg.strip().replace(" ", ""))
        if not m:
            continue
        n, key = int(m.group(1)), m.group(2).lower()
        if key in ("w", "l", "be"):
            results += n
        elif key in _SESSION_OF:
            counts[_SESSION_OF[key]] = counts.get(_SESSION_OF[key], 0) + n
    if not counts:
        return None
    if day_i == len(parts) - 1:
        number = 1  # the day folder holds the screenshots itself: a single trade
    elif m := re.match(r"^(\d+)\s*(?:\||$)", parts[day_i + 1]):
        number = int(m.group(1))
    else:
        return None
    slots = [name for name, _ in _SESSIONS for _ in range(counts.get(name, 0))]
    ev = f"day folder “{parts[day_i]}”, trade {number}"
    if len(counts) == 1:  # one session that day: every trade was in it
        return Candidate("session", slots[0], 0.95, "folder", ev)
    if number > len(slots):
        return None
    conf = 0.95 if results == len(slots) else 0.7  # tallies that don't add up get a human check
    return Candidate("session", slots[number - 1], conf, "folder",
                     ev + ("" if conf > 0.9 else f" (⚠ {len(slots)} session trades vs {results} results)"))


def folder_date_fallback(created: datetime | None, folder_path: str | None) -> Candidate | None:
    """When nothing in the filename or image dates the trade, the upload time is a weak hint.

    It's only offered at medium confidence when it falls inside the folder's month, otherwise low
    (which means it's suggested, never auto-filled).
    """
    if created is None:
        return None
    folder = parse_folder(folder_path)
    in_month = folder.get("month") == created.month and folder.get("year", created.year) == created.year
    conf = 0.65 if in_month else 0.45
    return Candidate("trade_date", created, conf, "folder", "file upload time" + (" (matches folder month)" if in_month else ""))
