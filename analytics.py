"""
Deterministic analytics (all math is done by SQL / Python, never by the LLM).

Used by both the FastAPI dashboard endpoints and the LangChain agent tools so
that the chat answers and the dashboard always agree.
"""
import math
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from database import Transaction, TransactionItem, Budget, RecurringExpense, get_pkt_now, PKT_TZ

PAYMENT_METHODS = ["cash", "easypaisa", "jazzcash", "card", "bank_transfer"]

_PAYMENT_ALIASES = {
    "cash": "cash",
    "naqd": "cash",
    "نقد": "cash",
    "کیش": "cash",
    "easypaisa": "easypaisa",
    "easy paisa": "easypaisa",
    "ایزی پیسہ": "easypaisa",
    "ایزیپیسہ": "easypaisa",
    "jazzcash": "jazzcash",
    "jazz cash": "jazzcash",
    "جاز کیش": "jazzcash",
    "جازکیش": "jazzcash",
    "card": "card",
    "کاٹ": "card",
    "کارڈ": "card",
    "debit card": "card",
    "credit card": "card",
    "sadapay": "card",
    "sada pay": "card",
    "سادہ پے": "card",
    "nayapay": "card",
    "naya pay": "card",
    "نیا پے": "card",
    "bank": "bank_transfer",
    "بینک": "bank_transfer",
    "bank_transfer": "bank_transfer",
    "bank transfer": "bank_transfer",
    "raast": "bank_transfer",
    "راست": "bank_transfer",
    "ibft": "bank_transfer",
}


def normalize_payment_method(value: Optional[str]) -> str:
    """Maps any user/LLM phrasing to one of PAYMENT_METHODS (default: cash)."""
    if not value:
        return "cash"
    key = str(value).strip().lower().replace("-", " ")
    if key in _PAYMENT_ALIASES:
        return _PAYMENT_ALIASES[key]
    key_us = key.replace(" ", "_")
    return key_us if key_us in PAYMENT_METHODS else "cash"


def detect_payment_method(value: Optional[str] = None, text: Optional[str] = None) -> str:
    """
    Resolves payment method deterministically:
    1. If `value` is explicitly non-cash (e.g. 'card', 'easypaisa'), normalize and return it.
    2. If `value` is missing or 'cash', scan `text` (transcript / prompt) for explicit payment keywords.
    3. Defaults to 'cash'.
    """
    if value:
        norm = normalize_payment_method(value)
        if norm != "cash":
            return norm

    if text:
        t = str(text).lower()
        for alias in sorted(_PAYMENT_ALIASES.keys(), key=len, reverse=True):
            if _PAYMENT_ALIASES[alias] == "cash":
                continue
            if alias in t:
                return _PAYMENT_ALIASES[alias]

    return normalize_payment_method(value)


def validate_transaction_amount(amount: Any) -> float:
    """
    Validates that the transaction amount is positive, finite, and not NaN.
    Raises ValueError on invalid input.
    """
    try:
        val = float(amount)
    except (TypeError, ValueError):
        raise ValueError("Transaction amount must be a valid number.")
    if math.isnan(val) or math.isinf(val) or val <= 0:
        raise ValueError("Transaction amount must be a positive, finite number.")
    return round(val, 2)


def check_amount_plausibility(amount: float, payment_method: str = "cash") -> Dict[str, Any]:
    """
    Checks if an amount is unusually large or exceeds wallet limits:
    - > 10,000,000 PKR (general threshold)
    - > 1,000,000 PKR for Easypaisa or JazzCash (regulatory wallet limit)
    Returns: {"needs_confirmation": bool, "reason": str or None}
    """
    pm = normalize_payment_method(payment_method)
    if pm in ("easypaisa", "jazzcash") and amount > 1_000_000:
        return {
            "needs_confirmation": True,
            "reason": f"Amount of PKR {amount:,.2f} exceeds standard {pm.title()} single transaction limits (PKR 1,000,000).",
        }
    if amount > 10_000_000:
        return {
            "needs_confirmation": True,
            "reason": f"Amount of PKR {amount:,.2f} is unusually large (> 10,000,000 PKR).",
        }
    return {"needs_confirmation": False, "reason": None}


def sanitize_transaction_amount(
    amount: float,
    raw_text: Optional[str] = None,
    payment_method: Optional[str] = "cash",
) -> float:
    """
    Validation only: ensures amount is positive, finite, and not NaN.
    Never overwrites or corrupts numeric values.
    """
    try:
        return validate_transaction_amount(amount)
    except ValueError:
        return 0.0


def month_start(now: Optional[datetime] = None) -> datetime:
    """Returns midnight on the 1st of the current month in Pakistan Standard Time."""
    now = now or get_pkt_now()
    return datetime(now.year, now.month, 1, 0, 0, 0)


def category_breakdown(db: Session, user_id: str, since: datetime) -> List[Dict]:
    """SUM(price) per category since `since`, largest first, with percentage share."""
    rows = (
        db.query(
            TransactionItem.category,
            func.sum(TransactionItem.price).label("total"),
        )
        .join(Transaction, Transaction.id == TransactionItem.transaction_id)
        .filter(
            Transaction.user_id == user_id,
            Transaction.transaction_date >= since,
            Transaction.deleted_at.is_(None),
        )
        .group_by(TransactionItem.category)
        .order_by(func.sum(TransactionItem.price).desc())
        .all()
    )
    grand = sum(float(r.total) for r in rows) or 0.0
    return [
        {
            "category": r.category or "other",
            "total_pkr": round(float(r.total), 2),
            "percent": round(float(r.total) / grand * 100, 1) if grand else 0.0,
        }
        for r in rows
    ]


def daily_trend(db: Session, user_id: str, days: int = 30) -> List[Dict]:
    """Per-day spending for the last `days` days (zero-filled so the chart has no gaps)."""
    today = get_pkt_now().replace(hour=0, minute=0, second=0, microsecond=0)
    start = today - timedelta(days=days - 1)
    rows = (
        db.query(
            func.strftime("%Y-%m-%d", Transaction.transaction_date).label("day"),
            func.sum(Transaction.total_amount).label("total"),
        )
        .filter(
            Transaction.user_id == user_id,
            Transaction.transaction_date >= start,
            Transaction.deleted_at.is_(None),
        )
        .group_by("day")
        .all()
    )
    by_day = {r.day: float(r.total) for r in rows}
    out = []
    for i in range(days):
        d = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        out.append({"date": d, "total_pkr": round(by_day.get(d, 0.0), 2)})
    return out


def payment_split(db: Session, user_id: str, since: datetime) -> List[Dict]:
    """SUM(total_amount) per payment method, plus cash-vs-digital totals."""
    rows = (
        db.query(
            Transaction.payment_method,
            func.sum(Transaction.total_amount).label("total"),
            func.count(Transaction.id).label("count"),
        )
        .filter(
            Transaction.user_id == user_id,
            Transaction.transaction_date >= since,
            Transaction.deleted_at.is_(None),
        )
        .group_by(Transaction.payment_method)
        .order_by(func.sum(Transaction.total_amount).desc())
        .all()
    )
    return [
        {
            "method": r.payment_method or "cash",
            "total_pkr": round(float(r.total), 2),
            "count": int(r.count),
        }
        for r in rows
    ]


def budget_status(db: Session, user_id: str) -> List[Dict]:
    """Spent / limit / percent for every budget the user has set (current month)."""
    start = month_start()
    budgets = db.query(Budget).filter(Budget.user_id == user_id).all()
    result = []
    for b in budgets:
        if b.category == "total":
            spent = (
                db.query(func.coalesce(func.sum(Transaction.total_amount), 0.0))
                .filter(
                    Transaction.user_id == user_id,
                    Transaction.transaction_date >= start,
                    Transaction.deleted_at.is_(None),
                )
                .scalar()
            )
        else:
            spent = (
                db.query(func.coalesce(func.sum(TransactionItem.price), 0.0))
                .join(Transaction, Transaction.id == TransactionItem.transaction_id)
                .filter(
                    Transaction.user_id == user_id,
                    Transaction.transaction_date >= start,
                    Transaction.deleted_at.is_(None),
                    TransactionItem.category.ilike(b.category),
                )
                .scalar()
            )
        spent = float(spent or 0.0)
        pct = round(spent / b.monthly_limit * 100, 1) if b.monthly_limit else 0.0
        if pct >= 100:
            level = "over"
        elif pct >= 80:
            level = "warning"
        else:
            level = "ok"
        result.append(
            {
                "id": b.id,
                "category": b.category,
                "monthly_limit": round(b.monthly_limit, 2),
                "spent_pkr": round(spent, 2),
                "remaining_pkr": round(b.monthly_limit - spent, 2),
                "percent": pct,
                "level": level,
            }
        )
    return result


def recurring_status(db: Session, user_id: str) -> List[Dict]:
    items = (
        db.query(RecurringExpense)
        .filter(RecurringExpense.user_id == user_id)
        .order_by(RecurringExpense.due_day.asc())
        .all()
    )
    today = get_pkt_now().day
    out = []
    for r in items:
        d = r.to_dict()
        d["overdue"] = (not d["paid_this_month"]) and r.due_day < today
        out.append(d)
    return out


def pay_recurring(db: Session, user_id: str, item: RecurringExpense, raw_transcript: Optional[str] = None) -> Dict:
    """
    Marks a recurring obligation paid for the current month and logs it as a real
    transaction so every total (chat + dashboard) stays consistent.
    Enforces idempotency: will not log a duplicate transaction for the same month.
    """
    current_month = get_pkt_now().strftime("%Y-%m")
    if item.paid_month == current_month:
        return {
            "status": "already_paid",
            "message": f"'{item.title}' is already marked paid for {current_month}.",
            "title": item.title,
            "paid_month": current_month,
        }
    now_pkt = get_pkt_now()
    tx = Transaction(
        user_id=user_id,
        store_name=item.title,
        total_amount=round(item.amount, 2),
        transaction_date=now_pkt,
        raw_transcript=raw_transcript or None,
        payment_method="cash",
    )
    db.add(tx)
    db.flush()
    db.add(
        TransactionItem(
            transaction_id=tx.id,
            item_name=item.title,
            category=item.category,
            price=round(item.amount, 2),
            quantity=1.0,
        )
    )
    item.paid_month = current_month
    db.commit()
    return {
        "status": "success",
        "message": f"Marked '{item.title}' as paid (PKR {round(item.amount, 2):,}) and logged as an expense.",
        "title": item.title,
        "amount_pkr": round(item.amount, 2),
        "transaction_id": tx.id,
    }


def sanitize_csv_cell(value: Any) -> Any:
    """
    Mitigates CSV Formula Injection (CWE-1236) by prepending a single quote (')
    if the string begins with =, +, -, @, tab, or carriage return.
    """
    if isinstance(value, str):
        if value and value[0] in ("=", "+", "-", "@", "\t", "\r"):
            return f"'{value}"
    return value


def export_rows(db: Session, user_id: str) -> List[List]:
    """One CSV row per line item (with the transaction context repeated)."""
    txs = (
        db.query(Transaction)
        .filter(
            Transaction.user_id == user_id,
            Transaction.deleted_at.is_(None),
        )
        .order_by(Transaction.transaction_date.asc(), Transaction.id.asc())
        .all()
    )
    rows: List[List] = []
    for tx in txs:
        date = tx.transaction_date.strftime("%Y-%m-%d %H:%M")
        if not tx.items:
            raw_row = [tx.id, date, tx.store_name, "", "", 1, tx.total_amount, tx.payment_method, tx.total_amount]
            rows.append([sanitize_csv_cell(c) for c in raw_row])
        for it in tx.items:
            raw_row = [
                tx.id,
                date,
                tx.store_name,
                it.item_name,
                it.category,
                it.quantity,
                it.price,  # LINE TOTAL
                tx.payment_method,
                tx.total_amount,
            ]
            rows.append([sanitize_csv_cell(c) for c in raw_row])
    return rows


EXPORT_HEADER = [
    "Transaction ID",
    "Date",
    "Store",
    "Item",
    "Category",
    "Quantity",
    "Item Total (PKR)",
    "Payment Method",
    "Transaction Total (PKR)",
]
