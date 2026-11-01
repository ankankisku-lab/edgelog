"""Journal field registry: the single source of truth for every journal column.

Each field maps 1:1 to a column on the Trade model. The frontend fetches this registry from
/api/fields to render the journal table and editor, so adding a field here (plus its column)
is all that's needed to surface it everywhere.
"""

from dataclasses import asdict, dataclass, field
from typing import Literal

FieldType = Literal["text", "number", "date", "select", "multiselect", "rating", "longtext"]


@dataclass(frozen=True)
class FieldDef:
    key: str
    label: str
    category: str
    type: FieldType
    options: list[str] = field(default_factory=list)
    # extractable: the AI pipeline may propose a value for this field
    extractable: bool = True
    # computed: derived from other fields (R:R, R-multiple, P&L %) unless the user overrides it
    computed: bool = False
    # required for a trade to count as "complete" (missing ones are highlighted)
    required: bool = False
    min: float | None = None
    max: float | None = None
    help: str = ""


MISTAKE_TAGS = [
    "Overtrading",
    "Revenge trade",
    "Early exit",
    "Late entry",
    "Stop-loss violation",
    "Outside plan",
    "Emotional decision",
    "Moved stop loss",
    "Oversized position",
    "FOMO entry",
]

TIMEFRAMES = ["M1", "M3", "M5", "M15", "M30", "H1", "H2", "H4", "H6", "H12", "D1", "W1", "MN"]

FIELDS: list[FieldDef] = [
    # Identification
    FieldDef("trade_date", "Date", "Identification", "date", required=True),
    # read from the day folder's tallies ("2A | 1NY"), never from the chart itself
    FieldDef("session", "Session", "Identification", "select", options=["Asia", "London", "New York"],
             extractable=False, help="Trading session; from the day folder, e.g. '2A | 1NY' = trades 1-2 Asia, 3 New York"),
    # Instrument
    FieldDef("asset", "Asset", "Instrument", "text", required=True, help="e.g. BTC, EURUSD, NIFTY"),
    FieldDef("trading_pair", "Trading Pair", "Instrument", "text", help="e.g. BTCUSDT"),
    FieldDef("market", "Market", "Instrument", "select",
             options=["Crypto", "Forex", "Stocks", "Indices", "Commodities", "Futures", "Options"]),
    FieldDef("exchange", "Exchange / Broker", "Instrument", "text"),
    FieldDef("entry_timeframe", "Entry Timeframe", "Instrument", "select", options=TIMEFRAMES,
             help="Chart the entry was taken on (e.g. M1)"),
    FieldDef("setup_timeframe", "Setup Timeframe", "Instrument", "select", options=TIMEFRAMES,
             help="Chart the setup formed on (e.g. M5)"),
    # Position
    FieldDef("direction", "Direction", "Position", "select", options=["Long", "Short"], required=True),
    FieldDef("position_type", "Position Type", "Position", "select",
             options=["Spot", "Perpetual", "Futures", "Margin", "Options", "CFD"]),
    # Execution
    FieldDef("entry_price", "Entry Price", "Execution", "number", min=0),
    FieldDef("exit_price", "Exit Price", "Execution", "number", min=0),
    # Risk Management
    FieldDef("stop_loss", "Stop Loss", "Risk Management", "number", min=0),
    FieldDef("take_profit", "Take Profit", "Risk Management", "number", min=0),
    FieldDef("risk_reward", "Risk-to-Reward", "Risk Management", "number", computed=True, min=0,
             help="Planned reward ÷ risk from entry, SL and TP"),
    # the "… | 3.2R" written on charts is the trade's result and goes to r_multiple; this one is manual only
    FieldDef("tp_r", "TP (R)", "Risk Management", "number", min=0, extractable=False,
             help="Planned target in R, if you track it separately from the result"),
    FieldDef("tp_reason", "TP Reason", "Risk Management", "text",
             help="Why the target was placed there, e.g. 'previous high liquidity'"),
    FieldDef("risk_amount", "Risk ($)", "Risk Management", "number", extractable=False, min=0,
             help="Money risked on this trade. Filled from the account's risk per trade; P&L = R × risk"),
    # Position Details
    FieldDef("position_size", "Position Size", "Position Details", "number", min=0,
             help="Quantity in base units (e.g. 0.5 BTC)"),
    FieldDef("leverage", "Leverage", "Position Details", "number", min=1, max=1000),
    # Strategy
    FieldDef("strategy_name", "Strategy Name", "Strategy", "text", extractable=False),
    FieldDef("setup_type", "Setup Type", "Strategy", "text"),
    FieldDef("market_condition", "Market Condition", "Strategy", "select",
             options=["Trending", "Ranging", "Volatile", "Choppy", "News-driven"]),
    # Technical Analysis
    FieldDef("htf_bias", "HTF Bias", "Technical Analysis", "select", options=["Bullish", "Bearish", "Neutral"]),
    FieldDef("htf_zone", "HTF Zone", "Technical Analysis", "text", help="e.g. H4 demand, D1 premium"),
    FieldDef("ema_alignment", "EMA Alignment", "Technical Analysis", "select",
             options=["Bullish stack", "Bearish stack", "Bullish crossover", "Bearish crossover", "Mixed", "Flat"]),
    # Entry Confirmation
    FieldDef("impulse_move", "Impulse Move", "Entry Confirmation", "select", options=["Yes", "No"]),
    FieldDef("fib_retracement", "Fibonacci Retracement", "Entry Confirmation", "select",
             options=["0.236", "0.382", "0.5", "0.618", "0.705", "0.786", "Other"]),
    FieldDef("ob_fvg_sr_reaction", "OB / FVG / SR Reaction", "Entry Confirmation", "multiselect",
             options=["Order Block", "Fair Value Gap", "Support", "Resistance", "None"]),
    # Performance
    FieldDef("pnl", "P&L", "Performance", "number", help="Realised profit/loss in account currency"),
    FieldDef("pnl_percent", "P&L %", "Performance", "number", computed=True,
             help="Price move × leverage, signed by direction"),
    FieldDef("r_multiple", "R-Multiple", "Performance", "number", computed=True,
             help="The trade's result in R, as written on the chart (e.g. '| 3.2R'), or computed from prices"),
    # Psychology (never extracted from images; the user's own record)
    FieldDef("emotion", "Emotion", "Psychology", "select", extractable=False,
             options=["Calm", "Confident", "Anxious", "Fearful", "Greedy", "Frustrated", "Bored", "Euphoric"]),
    FieldDef("discipline", "Discipline", "Psychology", "rating", extractable=False, min=1, max=5,
             help="1 = broke every rule, 5 = textbook execution"),
    FieldDef("followed_plan", "Followed Plan", "Psychology", "select", extractable=False, options=["Yes", "Partially", "No"]),
    FieldDef("mistakes", "Mistakes", "Psychology", "multiselect", extractable=False, options=MISTAKE_TAGS),
    # Review
    FieldDef("outcome", "Trade Outcome", "Review", "select", options=["Win", "Loss", "Breakeven", "Open"]),
    FieldDef("remarks", "Remarks", "Review", "longtext", extractable=False),
    FieldDef("screenshot_link", "Screenshot Link", "Review", "text", extractable=False),
]

FIELD_MAP: dict[str, FieldDef] = {f.key: f for f in FIELDS}
EXTRACTABLE_KEYS = [f.key for f in FIELDS if f.extractable]
NUMERIC_KEYS = {f.key for f in FIELDS if f.type in ("number", "rating")}
COMPUTED_KEYS = {f.key for f in FIELDS if f.computed}
CATEGORIES = list(dict.fromkeys(f.category for f in FIELDS))


def registry_payload() -> dict:
    return {"categories": CATEGORIES, "fields": [asdict(f) for f in FIELDS]}
