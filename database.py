import os
from datetime import datetime
from typing import Generator
from dotenv import load_dotenv
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Float,
    DateTime,
    ForeignKey,
    Text,   
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./expenses.db")

# For SQLite, check_same_thread=False allows FastAPI multi-threaded requests
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(String(100), index=True, nullable=False)
    store_name = Column(String(150), index=True, nullable=False)
    total_amount = Column(Float, nullable=False)
    transaction_date = Column(DateTime, index=True, default=datetime.utcnow, nullable=False)
    raw_transcript = Column(Text, nullable=True)
    payment_method = Column(String(30), index=True, default="cash", nullable=False)

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
        }


class TransactionItem(Base):
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
    price = Column(Float, nullable=False)
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
            "subtotal": round(self.price * self.quantity, 2),
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
        current_month = datetime.now().strftime("%Y-%m")
        return {
            "id": self.id,
            "title": self.title,
            "amount": round(self.amount, 2),
            "due_day": self.due_day,
            "category": self.category,
            "paid_this_month": self.paid_month == current_month,
        }


def _migrate_sqlite():
    """Adds columns introduced after the first release to existing SQLite files."""
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

