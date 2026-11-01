from datetime import datetime, timezone

import pytest

from app.extraction.cv_chart import analyze_chart
from app.extraction.filename_parser import folder_day_date, folder_session, parse_filename, parse_folder
from app.extraction.merge import merge_candidates, validate_levels
from app.extraction.normalize import ValidationError, normalize_value
from app.extraction.ocr import OcrItem, extract_from_ocr, run_ocr
from app.extraction.types import Candidate
from app.instruments import family_from_price, match_symbol
from app.services.drive import _is_calendar_folder
from chart_fixtures import make_chart


def by_field(cands):
    return {c.field: c for c in cands}


def test_spec_filename_example():
    c = by_field(parse_filename("BTCUSDT_Long_Entry_67450_SL_66800_TP_68900_01-11-2026.png"))
    assert c["asset"].value == "BTC" and c["trading_pair"].value == "BTCUSDT"
    assert c["direction"].value == "Long"
    assert (c["entry_price"].value, c["stop_loss"].value, c["take_profit"].value) == (67450, 66800, 68900)
    assert c["trade_date"].value == datetime(2026, 11, 1, tzinfo=timezone.utc)
    assert c["trade_date"].confidence < 0.85  # 01-11 is ambiguous without a folder month


def test_folder_month_disambiguates_date():
    c = by_field(parse_filename("MNQ_long_01-11-2026.png", "Trading Journal/2026/January"))
    assert c["trade_date"].value == datetime(2026, 1, 11, tzinfo=timezone.utc)
    assert c["trade_date"].confidence >= 0.95
    assert parse_folder("Trading Journal/2026/March") == {"year": 2026, "month": 3}


@pytest.mark.parametrize("name,asset,pair", [
    ("MNQ short 03-11-2026", "MNQ", None),
    ("MNQZ2026_long_m1.png", "MNQ", "MNQZ2026"),
    ("gold_sell_m5_setup.png", "XAUUSD", None),
    ("XAUUSDm_buy_M1.png", "XAUUSD", "XAUUSDM"),
    ("MGC1!_long.png", "MGC", "MGC1!"),
])
def test_instruments_in_filenames(name, asset, pair):
    c = by_field(parse_filename(name))
    assert c["asset"].value == asset
    assert ("trading_pair" in c and c["trading_pair"].value) == (pair or False)


def test_image_timeframe_from_filename():
    assert by_field(parse_filename("m1 entry.png"))["timeframe"].value == "M1"
    assert by_field(parse_filename("MNQ 5m setup.png"))["timeframe"].value == "M5"


def test_symbol_registry():
    assert match_symbol("MNQ1!")[0].asset == "MNQ"
    assert match_symbol("GC1!")[0].asset == "GC"
    assert match_symbol("BTCUSDT") is None
    assert {i.family for i in family_from_price([4120.0])} == {"gold"}
    assert {i.family for i in family_from_price([21450.25])} == {"nasdaq"}


def test_normalize_validation():
    assert normalize_value("asset", "mnq1!") == "MNQ"
    assert normalize_value("direction", "buy") == "Long"
    assert normalize_value("entry_timeframe", "1m") == "M1"
    assert normalize_value("fib_retracement", "61.8%") == "0.618"
    assert normalize_value("ob_fvg_sr_reaction", "OB, fvg") == ["Order Block", "Fair Value Gap"]
    with pytest.raises(ValidationError):
        normalize_value("entry_price", "-5")
    with pytest.raises(ValidationError):
        normalize_value("leverage", "abc")


def test_merge_agreement_and_conflict():
    m = merge_candidates([Candidate("entry_price", 100.0, 0.8, "ocr"), Candidate("entry_price", 100.0, 0.8, "cv")])
    assert m["entry_price"].confidence > 0.95 and not m["entry_price"].conflict
    m = merge_candidates([Candidate("entry_price", 100.0, 0.95, "filename"), Candidate("entry_price", 105.0, 0.8, "vision")])
    assert m["entry_price"].conflict and m["entry_price"].confidence < 0.85


def test_level_validation_flags_wrong_side_stop():
    m = merge_candidates([Candidate("direction", "Long", 0.95, "filename"), Candidate("entry_price", 100.0, 0.95, "filename"),
                          Candidate("stop_loss", 105.0, 0.95, "filename")])
    warnings = validate_levels(m)
    assert warnings and m["stop_loss"].confidence < 0.6


def test_calendar_folders():
    assert _is_calendar_folder("2026") and _is_calendar_folder("January") and _is_calendar_folder("01 January")
    assert not _is_calendar_folder("MNQ short 03-11-2026")


@pytest.mark.parametrize("dark,direction", [(False, "Long"), (True, "Short")])
def test_cv_reads_position_tool_and_axis(dark, direction):
    levels = dict(entry=21450.0, sl=21420.0, tp=21510.0) if direction == "Long" else dict(entry=21450.0, sl=21480.0, tp=21390.0)
    img = make_chart(direction=direction, dark=dark, p_lo=21350, p_hi=21560, step=25, decimals=2,
                     header="MNQ1!, 1, CME", **levels)
    items = run_ocr(img)
    ocr = by_field(extract_from_ocr(items, "m1.png", img.shape[1])[0])
    cands, facts = analyze_chart(img, items, "m1.png")
    cv = by_field(cands)
    assert cv["direction"].value == direction
    assert cv["risk_reward"].value == pytest.approx(2.0, abs=0.1)
    assert cv["entry_price"].value == levels["entry"]
    assert cv["stop_loss"].value == levels["sl"]
    assert cv["take_profit"].value == levels["tp"]
    assert ocr["asset"].value == "MNQ"


def test_ocr_reads_tp_r_and_reason():
    img = make_chart(note="TP 2R  TP reason: previous high liquidity", header="XAUUSD, 5, OANDA",
                     p_lo=4050, p_hi=4180, step=10, decimals=2, entry=4100, sl=4090, tp=4120)
    cands, facts = extract_from_ocr(run_ocr(img), "m5.png", img.shape[1])
    c = by_field(cands)
    assert c["r_multiple"].value == 2.0
    assert "liquidity" in c["tp_reason"].value.lower()
    assert "take_profit" not in c or c["take_profit"].value != 2.0
    assert facts["timeframe"] == "M5"
    assert c["asset"].value == "XAUUSD"


# ---- the user's real journal layout: ASSET/MONTH/<day> | <weekday> | <tallies>/<n> | <result> ----

CAPTURED = datetime(2026, 10, 24, 21, 38, tzinfo=timezone.utc)


@pytest.mark.parametrize("path,expected,conf", [
    ("GOLD/OCT/19th | Mon | 2W | 1BE | 3NY/1 | W", datetime(2026, 10, 19, tzinfo=timezone.utc), 0.95),
    ("MNQ/OCT/22nd | Thu | 3W | 3A", datetime(2026, 10, 22, tzinfo=timezone.utc), 0.95),
    ("GOLD/OCT/20th | Mon/1 | W", datetime(2026, 10, 20, tzinfo=timezone.utc), 0.6),  # wrong weekday: flagged
    ("GOLD/DEC/30th | Tue/1 | W", datetime(2025, 12, 30, tzinfo=timezone.utc), 0.95),  # can't be after capture
])
def test_date_from_day_folder(path, expected, conf):
    c = folder_day_date(path, CAPTURED)
    assert c.value == expected and c.confidence == conf


def test_trade_folder_results_and_tallies():
    assert by_field(parse_filename("1 | W"))["outcome"].value == "Win"
    assert by_field(parse_filename("3 | L"))["outcome"].value == "Loss"
    assert by_field(parse_filename("2 | BE"))["outcome"].value == "Breakeven"
    one = by_field(parse_filename("19th | Mon | 1W | 1NY"))  # a day with a single trade
    assert one["outcome"].value == "Win" and "timeframe" not in one  # "1W" is one win, not weekly
    assert "outcome" not in by_field(parse_filename("23rd | Fri | 2L | 1BE | 3NY"))


def test_tradingview_capture_name_is_weak_date():
    c = by_field(parse_filename("MGC1!_2026-10-24_21-38-00.png"))
    assert c["trade_date"].confidence < 0.6  # when the screenshot was taken, not when the trade was


@pytest.mark.parametrize("line,r,reason", [
    ("TPuptom5untappedfvg|3.2R", 3.2, "uptom5untappedfvg"),
    ("TP upto swing high that swept and gave rapid upsurge I 2.91R", 2.91,
     "upto swing high that swept and gave rapid upsurge"),
    ("TP upto swing high that swept and gave rapid upsurge but TP session ends at 1 pm ETI 1.54R", 1.54,
     "upto swing high that swept and gave rapid upsurge but TP session ends at 1 pm ET"),
])
def test_ocr_tp_note_lines(line, r, reason):
    c = by_field(extract_from_ocr([OcrItem(line, 0.95, 100, 400, 900, 420)], "m5.png", 1600)[0])
    assert c["r_multiple"].value == r and c["tp_reason"].value == reason


def test_ocr_legend_timeframe_not_ohlc_high():
    legend = "Micro Gold Futures·5COMEX 04,011.5 H4,012.1L4,009.0 C4,009.5 -2.2 (-0.05%)"
    _, facts = extract_from_ocr([OcrItem(legend, 0.95, 10, 10, 900, 30)], "x.png", 1600)
    assert facts["timeframe"] == "M5"  # not H4 from the High price "H4,012.1"


def _item(text, x, y, h=18):
    return OcrItem(text, 0.95, x, y, x + len(text) * 9, y + h)


def test_rightmost_note_is_this_trades_r():
    # earlier trades' notes stay on the chart; price runs left to right, so the latest note is rightmost
    items = [_item("TP upto untapped m30 fvg since chart can't go to m5 in free mode I 7.47R", 50, 200),
             _item("TP upto untappedm5 fvg1 2.77R", 900, 260),
             _item("TP uptountappedm30 since chart can't go tom5 in freemode| 2.74R", 300, 420)]
    c = by_field(extract_from_ocr(items, "m5.png", 1600)[0])
    assert c["r_multiple"].value == 2.77


def test_glued_bar_and_wrapped_note():
    c = by_field(extract_from_ocr([_item("TPuptountappedm5fvg11.6R", 600, 300)], "x.png", 1600)[0])
    assert c["r_multiple"].value == 1.6  # "fvg | 1.6R" with the bar read as "1"
    c = by_field(extract_from_ocr([_item("TPuptountappedm5fvgI15.7R", 600, 300)], "x.png", 1600)[0])
    assert c["r_multiple"].value == 15.7  # the bar was read as "I": the 1 belongs to the number
    wrapped = [_item("TP upto untapped H1 fvg since chart can't go to m5 in free mode", 700, 300),
               _item("butfP session ended sobooking at1pm ETI 1.7R", 705, 322)]
    c = by_field(extract_from_ocr(wrapped, "x.png", 1600)[0])
    assert c["r_multiple"].value == 1.7 and c["tp_reason"].value.startswith("upto untapped H1 fvg")


def test_r_must_fit_the_folder_result():
    res = merge_candidates([Candidate("outcome", "Loss", 0.95, "folder"), Candidate("r_multiple", 2.5, 0.9, "ocr")])
    warnings = validate_levels(res)
    assert warnings and res["r_multiple"].confidence < 0.6  # offered as a suggestion, not filled in


@pytest.mark.parametrize("path,session", [
    ("MNQ/OCT/19th | Mon | 1W | 1NY", "New York"),  # single-trade day folder
    ("GOLD/OCT/28th | Wed | 3W | 2A | 1NY/1 | W", "Asia"),  # trades run Asia -> London -> New York
    ("GOLD/OCT/28th | Wed | 3W | 2A | 1NY/2 | W", "Asia"),
    ("GOLD/OCT/28th | Wed | 3W | 2A | 1NY/3 | W", "New York"),
    ("MNQ/OCT/22nd | Thu | 3W | 3A/2 | W", "Asia"),
    ("GOLD/OCT/20th | Tue | 1L | 1W | 2NY/1 | L", "New York"),  # "1L" is a loss, not London
    ("X/OCT/5th | Mon | 1W | 1LDN | 1NY/1 | W", "London"),
])
def test_session_from_day_folder(path, session):
    assert folder_session(path).value == session


def test_session_tallies_that_dont_add_up_need_review():
    c = folder_session("X/OCT/5th | Mon | 2W | 1A | 2NY/2 | W")  # 2 results, 3 session trades
    assert c.confidence < 0.85
