import os
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Generator
from dotenv import load_dotenv
from sqlalchemy import (
    create_engine,
    event,
    Column,
    Integer,
    String,
    Float,
    Boolean,
    DateTime,
    ForeignKey,
    Text,
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session

load_dotenv()

# Standard Timezone for Pakistan
PKT_TZ = ZoneInfo("Asia/Karachi")


def get_pkt_now() -> datetime:
    """Returns the current Pakistan Standard Time (Asia/Karachi) as naive datetime for DB consistency."""
    return datetime.now(PKT_TZ).replace(tzinfo=None)


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./expenses.db")

# For SQLite, check_same_thread=False allows FastAPI multi-threaded requests
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    echo=False,
)


# SQLite Concurrency & Integrity: WAL mode, synchronous=NORMAL, busy_timeout=5000, foreign_keys=ON
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if DATABASE_URL.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA busy_timeout=5000;")
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


class Transaction(Base):
    """
    Main expense transaction table.
    All dates and timestamps are stored in Pakistan Standard Time (Asia/Karachi).
    """
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(String(100), index=True, nullable=False)
    store_name = Column(String(150), index=True, nullable=False)
    total_amount = Column(Float, nullable=False)
    transaction_date = Column(DateTime, index=True, default=get_pkt_now, nullable=False)
    raw_transcript = Column(Text, nullable=True)
    payment_method = Column(String(30), index=True, default="cash", nullable=False)
    deleted_at = Column(DateTime, index=True, nullable=True)  # Soft delete support

    items = relationship(
        "TransactionItem",
        back_populates="transaction",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "store_name": self.store_name,
            "total_amount": round(self.total_amount, 2),
            "transaction_date": (
                self.transaction_date.strftime("%Y-%m-%d %H:%M")
                if self.transaction_date
                else None
            ),
            "raw_transcript": self.raw_transcript,
            "payment_method": self.payment_method or "cash",
            "items": [item.to_dict() for item in self.items],
            "deleted_at": self.deleted_at.strftime("%Y-%m-%d %H:%M") if self.deleted_at else None,
        }


class TransactionItem(Base):
    """
    Line items for each transaction.
    
    IMPORTANT: `price` is designated as the LINE TOTAL for this item (not unit price).
    Example: 2 kg sugar for PKR 300 total -> price=300.0, quantity=2.0.
    Subtotal is therefore round(self.price, 2).
    """
    __tablename__ = "transaction_items"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    transaction_id = Column(
        Integer,
        ForeignKey("transactions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_name = Column(String(150), index=True, nullable=False)
    category = Column(String(100), index=True, default="other", nullable=False)
    price = Column(Float, nullable=False)  # LINE TOTAL in PKR
    quantity = Column(Float, default=1.0, nullable=False)

    transaction = relationship("Transaction", back_populates="items")

    def to_dict(self):
        return {
            "id": self.id,
            "transaction_id": self.transaction_id,
            "item_name": self.item_name,
            "category": self.category,
            "price": round(self.price, 2),
            "quantity": self.quantity,
            "subtotal": round(self.price, 2),  # price is LINE TOTAL
            "unit_price": round(self.price / self.quantity, 2) if self.quantity > 0 else round(self.price, 2),
        }


class Budget(Base):
    """Monthly spending limit per user. category='total' means overall monthly budget."""

    __tablename__ = "budgets"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(String(100), index=True, nullable=False)
    category = Column(String(100), index=True, default="total", nullable=False)
    monthly_limit = Column(Float, nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "category": self.category,
            "monthly_limit": round(self.monthly_limit, 2),
        }


class RecurringExpense(Base):
    """Monthly obligations such as rent, bills, school fees, committee (BC)."""

    __tablename__ = "recurring_expenses"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(String(100), index=True, nullable=False)
    title = Column(String(150), nullable=False)
    amount = Column(Float, nullable=False)
    due_day = Column(Integer, default=1, nullable=False)  # day of month 1-28
    category = Column(String(100), default="utilities", nullable=False)
    paid_month = Column(String(7), nullable=True)  # 'YYYY-MM' of last payment

    def to_dict(self):
        current_month = get_pkt_now().strftime("%Y-%m")
        return {
            "id": self.id,
            "title": self.title,
            "amount": round(self.amount, 2),
            "due_day": self.due_day,
            "category": self.category,
            "paid_this_month": self.paid_month == current_month,
        }


class UserAccount(Base):
    """Registered or signed-in user accounts with token versioning for instant revocation."""

    __tablename__ = "user_accounts"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(String(100), unique=True, index=True, nullable=False)
    auth_provider = Column(String(30), default="guest", nullable=False)  # 'google', 'phone', 'credentials', 'guest'
    display_name = Column(String(150), nullable=True)
    email = Column(String(150), index=True, nullable=True)
    phone_number = Column(String(50), index=True, nullable=True)
    photo_url = Column(String(500), nullable=True)
    password_hash = Column(String(255), nullable=True)
    identity_verified = Column(Boolean, default=False, nullable=False)
    token_version = Column(Integer, default=0, nullable=False)  # Incremented on password change or log out everywhere
    created_at = Column(DateTime, default=get_pkt_now, nullable=False)
    last_login = Column(DateTime, default=get_pkt_now, nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "auth_provider": self.auth_provider,
            "display_name": self.display_name or self.phone_number or self.email or self.user_id,
            "email": self.email,
            "phone_number": self.phone_number,
            "photo_url": self.photo_url,
            "identity_verified": bool(self.identity_verified),
            "token_version": self.token_version,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M") if self.created_at else None,
            "last_login": self.last_login.strftime("%Y-%m-%d %H:%M") if self.last_login else None,
        }


class LoginAttempt(Base):
    """Persistent database-backed rate limiting table for authentication and registrations."""

    __tablename__ = "login_attempts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(150), index=True, nullable=False)  # e.g., "127.0.0.1:login:victim@gmail.com"
    attempted_at = Column(DateTime, default=get_pkt_now, index=True, nullable=False)


class PendingTransaction(Base):
    """Server-held pending transactions requiring explicit user confirmation before persisting to ledger."""

    __tablename__ = "pending_transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(100), index=True, nullable=False)
    store_name = Column(String(150), nullable=False)
    total_amount = Column(Float, nullable=False)
    payment_method = Column(String(30), default="cash", nullable=False)
    date_str = Column(String(20), nullable=True)
    raw_transcript = Column(Text, nullable=True)
    items_json = Column(Text, nullable=True)  # JSON-encoded list of line items
    created_at = Column(DateTime, default=get_pkt_now, nullable=False)
    expires_at = Column(DateTime, index=True, nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "store_name": self.store_name,
            "total_amount": round(self.total_amount, 2),
            "payment_method": self.payment_method,
            "date_str": self.date_str,
            "raw_transcript": self.raw_transcript,
            "items": json.loads(self.items_json) if self.items_json else [],
            "expires_at": self.expires_at.strftime("%Y-%m-%d %H:%M:%S") if self.expires_at else None,
        }


def _migrate_sqlite():
    """Adds columns introduced after the first release to existing SQLite files using safe DDL."""
    if not DATABASE_URL.startswith("sqlite"):
        return
    from sqlalchemy import text

    with engine.begin() as conn:
        cols = [row[1] for row in conn.execute(text("PRAGMA table_info(transactions)"))]
        if cols and "payment_method" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE transactions ADD COLUMN payment_method "
                    "VARCHAR(30) NOT NULL DEFAULT 'cash'"
                )
            )
        if cols and "deleted_at" not in cols:
            conn.execute(text("ALTER TABLE transactions ADD COLUMN deleted_at DATETIME NULL"))

        user_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(user_accounts)"))]
        if user_cols and "password_hash" not in user_cols:
            conn.execute(text("ALTER TABLE user_accounts ADD COLUMN password_hash VARCHAR(255) NULL"))
        if user_cols and "identity_verified" not in user_cols:
            conn.execute(text("ALTER TABLE user_accounts ADD COLUMN identity_verified BOOLEAN NOT NULL DEFAULT 0"))
        if user_cols and "token_version" not in user_cols:
            conn.execute(text("ALTER TABLE user_accounts ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0"))


def init_db():
    """Initializes SQLite database and tables (with lightweight migration)."""
    Base.metadata.create_all(bind=engine)
    _migrate_sqlite()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency for database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
