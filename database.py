import os
import re
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
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

if DATABASE_URL.startswith("sqlite:///"):
    sqlite_file = DATABASE_URL.replace("sqlite:///", "", 1)
    db_dir = os.path.dirname(os.path.abspath(sqlite_file))
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

# For SQLite, check_same_thread=False allows FastAPI multi-threaded requests; timeout=30 prevents transient lock errors
connect_args = {"check_same_thread": False, "timeout": 30} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    echo=False,
)


# SQLite Concurrency & Integrity: WAL mode, synchronous=NORMAL, busy_timeout=30000, foreign_keys=ON
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if DATABASE_URL.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA busy_timeout=30000;")
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


class Book(Base):
    """
    Ledger / Business Book profile per user (Multi-Book Pattern).
    - book_type='business': Daily Cashbook (Galla), Sales, Customer & Supplier Udhaar Khata.
    - book_type='personal': Household & personal expense ledger, monthly budgets, personal bills.
    """

    __tablename__ = "books"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(String(100), index=True, nullable=False)
    name = Column(String(100), nullable=False)
    book_type = Column(String(20), index=True, default="personal", nullable=False)  # 'business' or 'personal'
    is_default = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=get_pkt_now, nullable=False)
    deleted_at = Column(DateTime, index=True, nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "name": self.name,
            "book_type": self.book_type or "personal",
            "is_default": bool(self.is_default),
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M") if self.created_at else None,
        }


class Transaction(Base):
    """
    Main expense & sale transaction table.
    All dates and timestamps are stored in Pakistan Standard Time (Asia/Karachi).
    """
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(String(100), index=True, nullable=False)
    book_id = Column(Integer, ForeignKey("books.id", ondelete="SET NULL"), index=True, nullable=True)
    store_name = Column(String(150), index=True, nullable=False)
    title = Column(String(200), index=True, nullable=True)
    total_amount = Column(Float, nullable=False)
    transaction_date = Column(DateTime, index=True, default=get_pkt_now, nullable=False)
    raw_transcript = Column(Text, nullable=True)
    payment_method = Column(String(30), index=True, default="cash", nullable=False)
    tx_type = Column(String(20), index=True, default="expense", nullable=False)  # 'expense' or 'sale'
    deleted_at = Column(DateTime, index=True, nullable=True)  # Soft delete support

    items = relationship(
        "TransactionItem",
        back_populates="transaction",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def to_dict(self):
        raw_title = self.title or self.store_name or "Expense"
        clean_title = re.sub(r'[_]+', ' ', raw_title)
        clean_title = re.sub(r'\b(transfer)\s+\1\b', r'\1', clean_title, flags=re.I)
        clean_title = " ".join(clean_title.split())
        if clean_title.lower() == "bank transfer" and self.raw_transcript and any('\u0600' <= ch <= '\u06FF' for ch in self.raw_transcript):
            clean_title = "بینک ٹرانسفر"
        elif not any('\u0600' <= ch <= '\u06FF' for ch in clean_title) and clean_title == clean_title.lower():
            clean_title = clean_title.title()

        return {
            "id": self.id,
            "user_id": self.user_id,
            "book_id": self.book_id,
            "store_name": self.store_name,
            "title": self.title,
            "display_title": clean_title,
            "total_amount": round(self.total_amount, 2),
            "transaction_date": (
                self.transaction_date.strftime("%Y-%m-%d %H:%M")
                if self.transaction_date
                else None
            ),
            "raw_transcript": self.raw_transcript,
            "payment_method": self.payment_method or "cash",
            "tx_type": self.tx_type or "expense",
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
    book_id = Column(Integer, ForeignKey("books.id"), index=True, nullable=True)
    category = Column(String(100), index=True, default="total", nullable=False)
    monthly_limit = Column(Float, nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "book_id": self.book_id,
            "category": self.category,
            "monthly_limit": round(self.monthly_limit, 2),
        }


class RecurringExpense(Base):
    """Monthly obligations such as rent, bills, school fees, committee (BC)."""

    __tablename__ = "recurring_expenses"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(String(100), index=True, nullable=False)
    book_id = Column(Integer, ForeignKey("books.id"), index=True, nullable=True)
    title = Column(String(150), nullable=False)
    amount = Column(Float, nullable=False)
    due_day = Column(Integer, default=1, nullable=False)  # day of month 1-28
    category = Column(String(100), default="utilities", nullable=False)
    paid_month = Column(String(7), nullable=True)  # 'YYYY-MM' of last payment

    def to_dict(self):
        current_month = get_pkt_now().strftime("%Y-%m")
        return {
            "id": self.id,
            "book_id": self.book_id,
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
    firebase_uid = Column(String(128), index=True, nullable=True)
    auth_provider = Column(String(30), default="google", nullable=False)  # 'google', 'password'
    display_name = Column(String(150), nullable=True)
    email = Column(String(150), index=True, nullable=True)
    phone_number = Column(String(50), index=True, nullable=True)
    photo_url = Column(String(500), nullable=True)
    password_hash = Column(String(255), nullable=True)
    identity_verified = Column(Boolean, default=False, nullable=False)
    token_version = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=get_pkt_now, nullable=False)
    last_login = Column(DateTime, default=get_pkt_now, nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "firebase_uid": self.firebase_uid,
            "auth_provider": self.auth_provider,
            "display_name": self.display_name or (self.email.split("@")[0] if self.email else self.user_id),
            "email": self.email,
            "phone_number": self.phone_number,
            "photo_url": self.photo_url,
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
    book_id = Column(Integer, ForeignKey("books.id"), index=True, nullable=True)
    store_name = Column(String(150), nullable=False)
    title = Column(String(200), nullable=True)
    total_amount = Column(Float, nullable=False)
    payment_method = Column(String(30), default="cash", nullable=False)
    date_str = Column(String(20), nullable=True)
    raw_transcript = Column(Text, nullable=True)
    items_json = Column(Text, nullable=True)  # JSON-encoded list of line items
    created_at = Column(DateTime, default=get_pkt_now, nullable=False)
    expires_at = Column(DateTime, index=True, nullable=False)

    def to_dict(self):
        raw_title = self.title or self.store_name or "Expense"
        clean_title = re.sub(r'[_]+', ' ', raw_title)
        clean_title = re.sub(r'\b(transfer)\s+\1\b', r'\1', clean_title, flags=re.I)
        clean_title = " ".join(clean_title.split())
        if clean_title.lower() == "bank transfer" and self.raw_transcript and any('\u0600' <= ch <= '\u06FF' for ch in self.raw_transcript):
            clean_title = "بینک ٹرانسفر"
        elif not any('\u0600' <= ch <= '\u06FF' for ch in clean_title) and clean_title == clean_title.lower():
            clean_title = clean_title.title()

        return {
            "id": self.id,
            "user_id": self.user_id,
            "book_id": self.book_id,
            "store_name": self.store_name,
            "title": self.title,
            "display_title": clean_title,
            "total_amount": round(self.total_amount, 2),
            "payment_method": self.payment_method,
            "date_str": self.date_str,
            "raw_transcript": self.raw_transcript,
            "items": json.loads(self.items_json) if self.items_json else [],
            "expires_at": self.expires_at.strftime("%Y-%m-%d %H:%M:%S") if self.expires_at else None,
        }


class UdhaarEntry(Base):
    """
    Shopkeeper & Personal Udhaar (Credit / Lend & Borrow) Khata Book.
    - direction='receivable': Maine Lene Hain / Udhaar Diya (Customer owes user)
    - direction='payable': Maine Dene Hain / Udhaar Liya (User owes supplier/lender)
    """

    __tablename__ = "udhaar_entries"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(String(100), index=True, nullable=False)
    book_id = Column(Integer, ForeignKey("books.id"), index=True, nullable=True)
    person_name = Column(String(150), index=True, nullable=False)
    phone_number = Column(String(50), nullable=True)
    direction = Column(String(20), index=True, default="receivable", nullable=False)  # 'receivable' or 'payable'
    amount = Column(Float, nullable=False)
    paid_amount = Column(Float, default=0.0, nullable=False)
    notes = Column(String(255), nullable=True)
    status = Column(String(20), index=True, default="open", nullable=False)  # 'open' or 'settled'
    created_at = Column(DateTime, default=get_pkt_now, nullable=False)
    updated_at = Column(DateTime, default=get_pkt_now, nullable=False)
    deleted_at = Column(DateTime, index=True, nullable=True)

    def to_dict(self):
        total = float(self.amount or 0.0)
        paid = float(self.paid_amount or 0.0)
        rem = round(max(0.0, total - paid), 2)
        return {
            "id": self.id,
            "user_id": self.user_id,
            "book_id": self.book_id,
            "person_name": self.person_name,
            "phone_number": self.phone_number or "",
            "direction": self.direction or "receivable",
            "amount": round(total, 2),
            "paid_amount": round(paid, 2),
            "remaining_pkr": rem,
            "notes": self.notes or "",
            "status": "settled" if rem <= 0.0 else (self.status or "open"),
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M") if self.created_at else None,
            "updated_at": self.updated_at.strftime("%Y-%m-%d %H:%M") if self.updated_at else None,
        }


class Party(Base):
    """
    Customer, Supplier, or General Business Party Profile.
    Holds party metadata, contact numbers, and opening balance configurations.
    """
    __tablename__ = "parties"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(String(100), index=True, nullable=False)
    book_id = Column(Integer, ForeignKey("books.id", ondelete="SET NULL"), index=True, nullable=True)
    name = Column(String(150), index=True, nullable=False)
    phone = Column(String(50), index=True, nullable=True)
    address = Column(Text, nullable=True)
    party_type = Column(String(20), index=True, default="customer", nullable=False)  # 'customer', 'supplier', 'general'
    opening_balance = Column(Float, default=0.0, nullable=False)
    opening_balance_type = Column(String(20), default="receivable", nullable=False)  # 'receivable' (lene) or 'payable' (dene)
    credit_limit = Column(Float, nullable=True)  # Optional maximum credit limit
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=get_pkt_now, nullable=False)
    updated_at = Column(DateTime, default=get_pkt_now, onupdate=get_pkt_now, nullable=False)
    deleted_at = Column(DateTime, index=True, nullable=True)

    transactions = relationship(
        "PartyTransaction",
        back_populates="party",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="PartyTransaction.tx_date.asc(), PartyTransaction.id.asc()",
    )

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "book_id": self.book_id,
            "name": self.name,
            "phone": self.phone or "",
            "address": self.address or "",
            "party_type": self.party_type or "customer",
            "opening_balance": round(float(self.opening_balance or 0.0), 2),
            "opening_balance_type": self.opening_balance_type or "receivable",
            "credit_limit": round(float(self.credit_limit), 2) if self.credit_limit is not None else None,
            "is_active": bool(self.is_active),
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M") if self.created_at else None,
            "updated_at": self.updated_at.strftime("%Y-%m-%d %H:%M") if self.updated_at else None,
            "deleted_at": self.deleted_at.strftime("%Y-%m-%d %H:%M") if self.deleted_at else None,
        }


class PartyTransaction(Base):
    """
    Individual Khata ledger entry for a Party.
    tx_type:
      - 'credit' ('gave' / 'diye'): In customer ledger, items/money given on credit (increases customer debt).
      - 'debit' ('received' / 'liye' / 'jama'): Payment received / wasooli (decreases customer debt).
    """
    __tablename__ = "party_transactions"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    party_id = Column(Integer, ForeignKey("parties.id", ondelete="CASCADE"), index=True, nullable=False)
    user_id = Column(String(100), index=True, nullable=False)
    book_id = Column(Integer, ForeignKey("books.id", ondelete="SET NULL"), index=True, nullable=True)
    amount = Column(Float, nullable=False)
    tx_type = Column(String(20), index=True, nullable=False)  # 'credit' (gave / دیے) or 'debit' (received / لیے)
    tx_date = Column(DateTime, default=get_pkt_now, index=True, nullable=False)
    due_date = Column(DateTime, index=True, nullable=True)  # Payment promise / due date
    description = Column(String(255), nullable=True)
    payment_method = Column(String(30), default="cash", nullable=False)
    bill_image_url = Column(String(500), nullable=True)
    is_settlement = Column(Boolean, default=False, nullable=False)  # True if wasooli/payment
    created_at = Column(DateTime, default=get_pkt_now, nullable=False)
    updated_at = Column(DateTime, default=get_pkt_now, onupdate=get_pkt_now, nullable=False)
    deleted_at = Column(DateTime, index=True, nullable=True)

    party = relationship("Party", back_populates="transactions")

    def to_dict(self):
        return {
            "id": self.id,
            "party_id": self.party_id,
            "user_id": self.user_id,
            "book_id": self.book_id,
            "amount": round(float(self.amount or 0.0), 2),
            "tx_type": self.tx_type,
            "tx_date": self.tx_date.strftime("%Y-%m-%d %H:%M") if self.tx_date else None,
            "due_date": self.due_date.strftime("%Y-%m-%d") if self.due_date else None,
            "description": self.description or "",
            "payment_method": self.payment_method or "cash",
            "bill_image_url": self.bill_image_url,
            "is_settlement": bool(self.is_settlement),
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M") if self.created_at else None,
            "updated_at": self.updated_at.strftime("%Y-%m-%d %H:%M") if self.updated_at else None,
            "deleted_at": self.deleted_at.strftime("%Y-%m-%d %H:%M") if self.deleted_at else None,
        }


class BusinessProfile(Base):
    """
    Shop & Business Profile Settings for invoices, statement exports, and payment reminders.
    """
    __tablename__ = "business_profiles"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(String(100), unique=True, index=True, nullable=False)
    book_id = Column(Integer, ForeignKey("books.id", ondelete="SET NULL"), index=True, nullable=True)
    business_name = Column(String(150), nullable=False)
    owner_name = Column(String(150), nullable=True)
    phone_number = Column(String(50), nullable=True)
    business_type = Column(String(50), default="kiriyana", nullable=False)
    address = Column(Text, nullable=True)
    city = Column(String(100), nullable=True)
    currency = Column(String(10), default="PKR", nullable=False)
    statement_header = Column(Text, nullable=True)
    statement_footer = Column(Text, nullable=True)
    raast_id = Column(String(50), nullable=True)
    easypaisa_number = Column(String(50), nullable=True)
    jazzcash_number = Column(String(50), nullable=True)
    bank_account_details = Column(Text, nullable=True)
    reminder_template_ur = Column(Text, nullable=True)
    reminder_template_en = Column(Text, nullable=True)
    created_at = Column(DateTime, default=get_pkt_now, nullable=False)
    updated_at = Column(DateTime, default=get_pkt_now, onupdate=get_pkt_now, nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "book_id": self.book_id,
            "business_name": self.business_name,
            "owner_name": self.owner_name or "",
            "phone_number": self.phone_number or "",
            "business_type": self.business_type or "kiriyana",
            "address": self.address or "",
            "city": self.city or "",
            "currency": self.currency or "PKR",
            "statement_header": self.statement_header or "",
            "statement_footer": self.statement_footer or "",
            "raast_id": self.raast_id or "",
            "easypaisa_number": self.easypaisa_number or "",
            "jazzcash_number": self.jazzcash_number or "",
            "bank_account_details": self.bank_account_details or "",
            "reminder_template_ur": self.reminder_template_ur or "",
            "reminder_template_en": self.reminder_template_en or "",
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M") if self.created_at else None,
            "updated_at": self.updated_at.strftime("%Y-%m-%d %H:%M") if self.updated_at else None,
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
        if cols and "tx_type" not in cols:
            conn.execute(
                text(
                    "ALTER TABLE transactions ADD COLUMN tx_type "
                    "VARCHAR(20) NOT NULL DEFAULT 'expense'"
                )
            )
        if cols and "book_id" not in cols:
            conn.execute(text("ALTER TABLE transactions ADD COLUMN book_id INTEGER NULL"))
        if cols and "title" not in cols:
            conn.execute(text("ALTER TABLE transactions ADD COLUMN title VARCHAR(200) NULL"))

        pending_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(pending_transactions)"))]
        if pending_cols and "book_id" not in pending_cols:
            conn.execute(text("ALTER TABLE pending_transactions ADD COLUMN book_id INTEGER NULL"))
        if pending_cols and "title" not in pending_cols:
            conn.execute(text("ALTER TABLE pending_transactions ADD COLUMN title VARCHAR(200) NULL"))

        budget_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(budgets)"))]
        if budget_cols and "book_id" not in budget_cols:
            conn.execute(text("ALTER TABLE budgets ADD COLUMN book_id INTEGER NULL"))

        rec_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(recurring_expenses)"))]
        if rec_cols and "book_id" not in rec_cols:
            conn.execute(text("ALTER TABLE recurring_expenses ADD COLUMN book_id INTEGER NULL"))

        udhaar_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(udhaar_entries)"))]
        if udhaar_cols and "book_id" not in udhaar_cols:
            conn.execute(text("ALTER TABLE udhaar_entries ADD COLUMN book_id INTEGER NULL"))

        user_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(user_accounts)"))]
        if user_cols and "firebase_uid" not in user_cols:
            conn.execute(text("ALTER TABLE user_accounts ADD COLUMN firebase_uid VARCHAR(128) NULL"))
        if user_cols and "password_hash" not in user_cols:
            conn.execute(text("ALTER TABLE user_accounts ADD COLUMN password_hash VARCHAR(255) NULL"))
        if user_cols and "identity_verified" not in user_cols:
            conn.execute(text("ALTER TABLE user_accounts ADD COLUMN identity_verified BOOLEAN NOT NULL DEFAULT 0"))
        if user_cols and "token_version" not in user_cols:
            conn.execute(text("ALTER TABLE user_accounts ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0"))
        # Idempotent migration: null out all legacy password hashes so no stored password hashes remain
        if user_cols and "password_hash" in user_cols:
            conn.execute(text("UPDATE user_accounts SET password_hash = NULL WHERE password_hash IS NOT NULL"))

        # Customer & Supplier Parties, Transactions, and Business Profiles tables
        Base.metadata.create_all(
            bind=engine,
            tables=[Party.__table__, PartyTransaction.__table__, BusinessProfile.__table__],
        )
        party_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(parties)"))]
        if party_cols and "credit_limit" not in party_cols:
            conn.execute(text("ALTER TABLE parties ADD COLUMN credit_limit FLOAT NULL"))
        if party_cols and "deleted_at" not in party_cols:
            conn.execute(text("ALTER TABLE parties ADD COLUMN deleted_at DATETIME NULL"))

        tx_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(party_transactions)"))]
        if tx_cols and "due_date" not in tx_cols:
            conn.execute(text("ALTER TABLE party_transactions ADD COLUMN due_date DATETIME NULL"))
        if tx_cols and "bill_image_url" not in tx_cols:
            conn.execute(text("ALTER TABLE party_transactions ADD COLUMN bill_image_url VARCHAR(500) NULL"))


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
