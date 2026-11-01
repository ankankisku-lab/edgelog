"""The trading account: starting balance and default risk per trade, used to turn R into money."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AccountSettings, Trade

DEFAULT_RISK = 120.0


def get_account(db: Session) -> AccountSettings:
    acc = db.get(AccountSettings, 1)
    if acc is None:
        acc = AccountSettings(id=1, starting_balance=None, risk_per_trade=DEFAULT_RISK, currency="USD")
        db.add(acc)
        db.flush()
    return acc


def account_dict(acc: AccountSettings) -> dict:
    return {"starting_balance": acc.starting_balance, "risk_per_trade": acc.risk_per_trade, "currency": acc.currency}


def update_account(db: Session, starting_balance: float | None, risk_per_trade: float | None,
                   apply_to_existing: bool) -> int:
    """Save the settings and recompute P&L. Returns how many trades' P&L changed.

    A new risk only reaches trades that already have one when apply_to_existing is set, and never
    a risk the user typed on a trade: past trades keep the risk they were taken with.
    """
    from .journal import get_meta, is_locked, recompute_derived, set_field

    acc = get_account(db)
    acc.starting_balance = starting_balance
    acc.risk_per_trade = risk_per_trade
    changed = 0
    for t in db.scalars(select(Trade)).all():
        before = t.pnl
        if apply_to_existing and t.risk_amount is not None and not is_locked(t, "risk_amount") \
                and get_meta(t, "risk_amount").get("source") == "computed":
            set_field(db, t, "risk_amount", None, source="computed", confidence=1.0, status="auto",
                      evidence="account risk changed", action="compute")
        recompute_derived(db, t)
        changed += before != t.pnl
    return changed
