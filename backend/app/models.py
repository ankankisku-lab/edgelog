from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # --- journal fields (keys match app.fields.FIELDS) ---
    trade_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    session: Mapped[str | None] = mapped_column(String(20))
    asset: Mapped[str | None] = mapped_column(String(40), index=True)
    trading_pair: Mapped[str | None] = mapped_column(String(40))
    market: Mapped[str | None] = mapped_column(String(30))
    exchange: Mapped[str | None] = mapped_column(String(60))
    entry_timeframe: Mapped[str | None] = mapped_column(String(10))
    setup_timeframe: Mapped[str | None] = mapped_column(String(10))
    direction: Mapped[str | None] = mapped_column(String(10))
    position_type: Mapped[str | None] = mapped_column(String(20))
    entry_price: Mapped[float | None] = mapped_column(Float)
    exit_price: Mapped[float | None] = mapped_column(Float)
    stop_loss: Mapped[float | None] = mapped_column(Float)
    take_profit: Mapped[float | None] = mapped_column(Float)
    risk_reward: Mapped[float | None] = mapped_column(Float)
    tp_r: Mapped[float | None] = mapped_column(Float)
    tp_reason: Mapped[str | None] = mapped_column(String(300))
    position_size: Mapped[float | None] = mapped_column(Float)
    leverage: Mapped[float | None] = mapped_column(Float)
    strategy_name: Mapped[str | None] = mapped_column(String(80), index=True)
    setup_type: Mapped[str | None] = mapped_column(String(80))
    market_condition: Mapped[str | None] = mapped_column(String(30))
    htf_bias: Mapped[str | None] = mapped_column(String(20))
    htf_zone: Mapped[str | None] = mapped_column(String(80))
    ema_alignment: Mapped[str | None] = mapped_column(String(30))
    impulse_move: Mapped[str | None] = mapped_column(String(10))
    fib_retracement: Mapped[str | None] = mapped_column(String(10))
    ob_fvg_sr_reaction: Mapped[list[str] | None] = mapped_column(JSON)
    pnl: Mapped[float | None] = mapped_column(Float)
    pnl_percent: Mapped[float | None] = mapped_column(Float)
    r_multiple: Mapped[float | None] = mapped_column(Float)
    risk_amount: Mapped[float | None] = mapped_column(Float)
    emotion: Mapped[str | None] = mapped_column(String(30))
    discipline: Mapped[float | None] = mapped_column(Float)
    followed_plan: Mapped[str | None] = mapped_column(String(15))
    mistakes: Mapped[list[str] | None] = mapped_column(JSON)
    outcome: Mapped[str | None] = mapped_column(String(15), index=True)
    remarks: Mapped[str | None] = mapped_column(Text)
    screenshot_link: Mapped[str | None] = mapped_column(String(500))

    # --- provenance: {field_key: {source, confidence, status, note}} ---
    # source: filename | folder | ocr | vision | computed | manual
    # status: auto (high confidence) | needs_review (medium) | verified (user-confirmed or manual)
    field_meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # --- origin: a trade is one Drive subfolder (or one upload batch) holding 1+ screenshots ---
    source: Mapped[str] = mapped_column(String(10), default="manual")  # manual | upload | drive
    drive_folder_id: Mapped[str | None] = mapped_column(String(128), unique=True, index=True)
    folder_name: Mapped[str | None] = mapped_column(String(300))
    folder_path: Mapped[str | None] = mapped_column(String(500))

    # --- extraction state ---
    processing_status: Mapped[str] = mapped_column(String(20), default="none")
    # none | queued | processing | done | partial | rate_limited | error
    processing_error: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extraction: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # raw stage outputs + annotations

    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    suggestions: Mapped[list["Suggestion"]] = relationship(back_populates="trade", cascade="all, delete-orphan")
    images: Mapped[list["TradeImage"]] = relationship(
        back_populates="trade", cascade="all, delete-orphan", order_by="TradeImage.sort_order"
    )


class TradeImage(Base):
    """A screenshot belonging to a trade, e.g. the M1 entry chart or an M5 setup chart."""

    __tablename__ = "trade_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trade_id: Mapped[int] = mapped_column(ForeignKey("trades.id", ondelete="CASCADE"), index=True)
    file_name: Mapped[str] = mapped_column(String(300))
    mime_type: Mapped[str | None] = mapped_column(String(60))
    drive_file_id: Mapped[str | None] = mapped_column(String(128), unique=True, index=True)
    web_link: Mapped[str | None] = mapped_column(String(500))
    local_path: Mapped[str | None] = mapped_column(String(500))  # uploads, or the Drive download cache
    file_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # role: entry | setup | other; timeframe as detected from the filename or by the vision model
    role: Mapped[str | None] = mapped_column(String(10))
    timeframe: Mapped[str | None] = mapped_column(String(10))
    role_source: Mapped[str | None] = mapped_column(String(15))  # filename | vision | manual
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    analysis: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # per-image OCR / CV output
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    trade: Mapped[Trade] = relationship(back_populates="images")


class Suggestion(Base):
    """An AI-proposed value awaiting the user's approve/reject decision.

    Created when extraction finds a value for a field that is already verified, or that has
    low confidence. Approving applies it; rejecting keeps the current value.
    """

    __tablename__ = "suggestions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trade_id: Mapped[int] = mapped_column(ForeignKey("trades.id", ondelete="CASCADE"), index=True)
    field: Mapped[str] = mapped_column(String(40))
    value: Mapped[Any] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(15))
    confidence: Mapped[float] = mapped_column(Float)
    evidence: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(String(30))  # low_confidence | conflicts_verified | conflict
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending | approved | rejected
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    trade: Mapped[Trade] = relationship(back_populates="suggestions")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trade_id: Mapped[int | None] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(20))  # create | edit | verify | approve | reject | extract | delete
    field: Mapped[str | None] = mapped_column(String(40))
    old_value: Mapped[Any] = mapped_column(JSON, nullable=True)
    new_value: Mapped[Any] = mapped_column(JSON, nullable=True)
    source: Mapped[str] = mapped_column(String(15))  # manual | filename | ocr | vision | computed | system
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class SeenDriveFile(Base):
    """Every Drive file ID ever imported, so a deleted trade or image is never re-created by a later sync."""

    __tablename__ = "seen_drive_files"

    file_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(300))
    trade_id: Mapped[int | None] = mapped_column(Integer)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DriveConnection(Base):
    """Single-row table holding the Google Drive connection for this (single-user) journal."""

    __tablename__ = "drive_connection"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    encrypted_credentials: Mapped[str | None] = mapped_column(Text)
    account_email: Mapped[str | None] = mapped_column(String(200))
    folder_id: Mapped[str | None] = mapped_column(String(128))
    folder_name: Mapped[str | None] = mapped_column(String(300))
    oauth_state: Mapped[str | None] = mapped_column(String(128))
    oauth_code_verifier: Mapped[str | None] = mapped_column(String(200))
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_status: Mapped[str | None] = mapped_column(String(20))
    last_sync_message: Mapped[str | None] = mapped_column(Text)
    files_seen: Mapped[int] = mapped_column(Integer, default=0)


class AccountSettings(Base):
    """Single-row table: the account the journal's P&L and balance are measured against."""

    __tablename__ = "account_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    starting_balance: Mapped[float | None] = mapped_column(Float)
    risk_per_trade: Mapped[float | None] = mapped_column(Float, default=120.0)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
