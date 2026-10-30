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


def init_db():
    """Initializes SQLite database and tables."""
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency for database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

