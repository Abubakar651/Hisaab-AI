"""
Deterministic analytics (all math is done by SQL / Python, never by the LLM).

Used by both the FastAPI dashboard endpoints and the LangChain agent tools so
that the chat answers and the dashboard always agree.
"""
import math
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from database import (
    Transaction,
    TransactionItem,
    Budget,
    RecurringExpense,
    PendingTransaction,
    UdhaarEntry,
    get_pkt_now,
    PKT_TZ,
)

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


def _is_expense_clause():
    """Returns SQLAlchemy filter matching expense transactions (including legacy rows with NULL tx_type)."""
    return or_(Transaction.tx_type == "expense", Transaction.tx_type.is_(None))


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
            _is_expense_clause(),
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

    bind = db.get_bind()
    if bind and "postgres" in getattr(bind.dialect, "name", ""):
        day_expr = func.to_char(Transaction.transaction_date, "YYYY-MM-DD")
    else:
        day_expr = func.strftime("%Y-%m-%d", Transaction.transaction_date)

    rows = (
        db.query(
            day_expr.label("day"),
            func.sum(Transaction.total_amount).label("total"),
        )
        .filter(
            Transaction.user_id == user_id,
            Transaction.transaction_date >= start,
            Transaction.deleted_at.is_(None),
            _is_expense_clause(),
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
            _is_expense_clause(),
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
                    _is_expense_clause(),
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
                    _is_expense_clause(),
                    or_(
                        TransactionItem.category.ilike(b.category),
                        TransactionItem.item_name.ilike(f"%{b.category}%"),
                    ),
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


def galla_summary(db: Session, user_id: str) -> Dict[str, Any]:
    """
    Computes deterministic 'Aaj Ka Galla' (Today's Cash Drawer) and Monthly Cash-In vs Cash-Out.
    - Sales (tx_type='sale'): Cash-In / Bikri
    - Expenses (tx_type='expense'): Cash-Out / Kharcha
    """
    now = get_pkt_now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    m_start = month_start(now)

    month_txs = (
        db.query(Transaction)
        .filter(
            Transaction.user_id == user_id,
            Transaction.transaction_date >= m_start,
            Transaction.deleted_at.is_(None),
        )
        .all()
    )

    today_sales = 0.0
    today_expenses = 0.0
    today_cash_sales = 0.0
    today_digital_sales = 0.0
    today_sales_count = 0

    monthly_sales = 0.0
    monthly_expenses = 0.0
    monthly_sales_count = 0

    for tx in month_txs:
        amt = float(tx.total_amount or 0.0)
        is_sale = (tx.tx_type == "sale")
        is_today = bool(tx.transaction_date and tx.transaction_date >= today_start)
        pm = (tx.payment_method or "cash").lower()

        if is_sale:
            monthly_sales += amt
            monthly_sales_count += 1
            if is_today:
                today_sales += amt
                today_sales_count += 1
                if pm == "cash":
                    today_cash_sales += amt
                else:
                    today_digital_sales += amt
        else:
            monthly_expenses += amt
            if is_today:
                today_expenses += amt

    return {
        "today_sales_pkr": round(today_sales, 2),
        "today_expenses_pkr": round(today_expenses, 2),
        "today_net_pkr": round(today_sales - today_expenses, 2),
        "today_cash_sales_pkr": round(today_cash_sales, 2),
        "today_digital_sales_pkr": round(today_digital_sales, 2),
        "today_sales_count": today_sales_count,
        "monthly_sales_pkr": round(monthly_sales, 2),
        "monthly_expenses_pkr": round(monthly_expenses, 2),
        "monthly_net_pkr": round(monthly_sales - monthly_expenses, 2),
        "monthly_sales_count": monthly_sales_count,
    }


def udhaar_summary(db: Session, user_id: str) -> Dict[str, Any]:
    """
    Returns deterministic Udhaar (Lend & Borrow) Khata summary for the user:
    - total_receivable_pkr: Maine Lene Hain (Customer owes shopkeeper)
    - total_payable_pkr: Maine Dene Hain (Shopkeeper owes supplier/lender)
    - entries: active open Udhaar entries
    """
    rows = (
        db.query(UdhaarEntry)
        .filter(
            UdhaarEntry.user_id == user_id,
            UdhaarEntry.deleted_at.is_(None),
        )
        .order_by(UdhaarEntry.status.asc(), UdhaarEntry.updated_at.desc(), UdhaarEntry.id.desc())
        .all()
    )

    total_receivable = 0.0
    total_payable = 0.0
    open_entries = []

    for r in rows:
        d = r.to_dict()
        rem = d["remaining_pkr"]
        if d["status"] == "open" and rem > 0:
            if d["direction"] == "payable":
                total_payable += rem
            else:
                total_receivable += rem
            open_entries.append(d)

    return {
        "total_receivable_pkr": round(total_receivable, 2),
        "total_payable_pkr": round(total_payable, 2),
        "net_udhaar_pkr": round(total_receivable - total_payable, 2),
        "open_count": len(open_entries),
        "entries": open_entries,
        "open_entries": open_entries,
    }


def add_udhaar_entry(
    db: Session,
    user_id: str,
    person_name: str,
    amount: float,
    direction: str = "receivable",
    notes: Optional[str] = "",
    phone_number: Optional[str] = "",
) -> Dict[str, Any]:
    """Creates a new Udhaar entry ('receivable' = Maine Lene Hain, 'payable' = Maine Dene Hain)."""
    clean_name = (person_name or "").strip()
    if not clean_name:
        return {"status": "error", "message": "Person name is required for Udhaar entry."}
    try:
        clean_amt = validate_transaction_amount(amount)
    except ValueError as ve:
        return {"status": "error", "message": str(ve)}
    clean_dir = "payable" if str(direction or "").strip().lower() in ("payable", "borrow", "dene", "liya", "owed") else "receivable"
    now = get_pkt_now()

    entry = UdhaarEntry(
        user_id=user_id,
        person_name=clean_name,
        phone_number=(phone_number or "").strip() or None,
        direction=clean_dir,
        amount=clean_amt,
        paid_amount=0.0,
        notes=(notes or "").strip() or None,
        status="open",
        created_at=now,
        updated_at=now,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return {"status": "success", "entry": entry.to_dict()}


def _norm(s: Optional[str]) -> str:
    return " ".join((s or "").lower().split())


def settle_udhaar(
    db: Session,
    user_id: str,
    person_name: Optional[str] = None,
    entry_id: Optional[int] = None,
    payment_amount: Optional[float] = None,
    direction: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Settles full or partial Udhaar balance by entry_id or person_name (FIFO across open entries).
    """
    now = get_pkt_now()
    clean_dir: Optional[str] = None
    if direction and str(direction).strip():
        d_low = str(direction).strip().lower()
        if d_low in ("payable", "borrow", "dene", "dene_hain", "liya", "owed", "take_credit"):
            clean_dir = "payable"
        elif d_low in ("receivable", "lend", "lene", "lene_hain", "diya", "give_credit"):
            clean_dir = "receivable"
        else:
            return {"status": "error", "message": f"Invalid direction '{direction}'. Use 'receivable' or 'payable'."}

    entries = (
        db.query(UdhaarEntry)
        .filter(
            UdhaarEntry.user_id == user_id,
            UdhaarEntry.deleted_at.is_(None),
            UdhaarEntry.status == "open",
        )
        .order_by(UdhaarEntry.created_at.asc(), UdhaarEntry.id.asc())
        .all()
    )

    open_people = sorted({e.person_name for e in entries if (e.person_name or "").strip()})

    if entry_id is not None:
        matched = [e for e in entries if e.id == int(entry_id)]
        if clean_dir:
            matched = [e for e in matched if e.direction == clean_dir]
    elif person_name and _norm(person_name):
        pool = [e for e in entries if e.direction == clean_dir] if clean_dir else entries
        exact = [e for e in pool if _norm(e.person_name) == _norm(person_name)]
        if exact:
            matched = exact
        else:
            toks = set(_norm(person_name).split())
            cands = [e for e in pool if toks and toks <= set(_norm(e.person_name).split())]  # whole words only
            names = {_norm(e.person_name) for e in cands}
            if len(names) > 1:
                return {
                    "status": "ambiguous",
                    "candidates": sorted({e.person_name for e in cands}),
                    "message": f"Multiple open Udhaar records match '{person_name}'. Please specify which person.",
                }
            matched = cands
    else:
        return {"status": "error", "message": "Provide person_name or entry_id to settle Udhaar."}

    if not matched:
        return {
            "status": "not_found",
            "open_people": open_people,
            "message": f"No open Udhaar record found for '{person_name or entry_id}'.",
        }

    distinct_names = {_norm(e.person_name) for e in matched}
    if len(distinct_names) > 1:
        return {
            "status": "ambiguous",
            "candidates": sorted({e.person_name for e in matched}),
            "message": f"Multiple open Udhaar records match '{person_name}'. Please specify which person.",
        }

    directions_present = {e.direction for e in matched}
    if clean_dir is None and len(directions_present) > 1:
        receivable_pkr = round(
            sum(max(0.0, float(e.amount or 0.0) - float(e.paid_amount or 0.0)) for e in matched if e.direction == "receivable"),
            2,
        )
        payable_pkr = round(
            sum(max(0.0, float(e.amount or 0.0) - float(e.paid_amount or 0.0)) for e in matched if e.direction == "payable"),
            2,
        )
        return {
            "status": "ambiguous_direction",
            "person_name": matched[0].person_name,
            "receivable_pkr": receivable_pkr,
            "payable_pkr": payable_pkr,
            "message": f"'{matched[0].person_name}' has both receivable and payable open balances. Please specify direction.",
        }

    total_open_before = sum(max(0.0, float(e.amount or 0.0) - float(e.paid_amount or 0.0)) for e in matched)
    if payment_amount is None or float(payment_amount) <= 0 or float(payment_amount) >= total_open_before:
        # Full settlement of matching entries
        applied = total_open_before
        for e in matched:
            e.paid_amount = float(e.amount or 0.0)
            e.status = "settled"
            e.updated_at = now
    else:
        # Partial settlement FIFO
        remaining_to_apply = round(float(payment_amount), 2)
        applied = 0.0
        for e in matched:
            if remaining_to_apply <= 0:
                break
            entry_rem = round(max(0.0, float(e.amount or 0.0) - float(e.paid_amount or 0.0)), 2)
            if entry_rem <= 0:
                e.status = "settled"
                continue
            chunk = min(entry_rem, remaining_to_apply)
            e.paid_amount = round(float(e.paid_amount or 0.0) + chunk, 2)
            remaining_to_apply = round(remaining_to_apply - chunk, 2)
            applied = round(applied + chunk, 2)
            if e.paid_amount >= float(e.amount or 0.0) - 0.01:
                e.paid_amount = float(e.amount or 0.0)
                e.status = "settled"
            e.updated_at = now

    db.commit()
    total_open_after = sum(max(0.0, float(e.amount or 0.0) - float(e.paid_amount or 0.0)) for e in matched)
    matched_person = matched[0].person_name
    final_direction = matched[0].direction

    return {
        "status": "success",
        "person_name": matched_person,
        "direction": final_direction,
        "settled_amount_pkr": round(applied, 2),
        "remaining_balance_pkr": round(total_open_after, 2),
        "fully_settled": total_open_after <= 0.01,
    }


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


def commit_pending_transaction(db: Session, user_id: str) -> Dict[str, Any]:
    """
    Atomically claims and commits a server-held pending transaction.
    Whoever deletes the active pending row wins; concurrent attempts receive conflict.
    """
    now = get_pkt_now()
    pending = (
        db.query(PendingTransaction)
        .filter(PendingTransaction.user_id == user_id, PendingTransaction.expires_at > now)
        .order_by(PendingTransaction.created_at.desc())
        .first()
    )
    if not pending:
        return {
            "status": "conflict",
            "message": "Already confirmed or expired.",
        }

    pending_id = pending.id
    store_name = pending.store_name
    total_amount = round(pending.total_amount, 2)
    payment_method = pending.payment_method
    date_str = pending.date_str
    raw_transcript = pending.raw_transcript
    items_json = pending.items_json

    try:
        # Atomic claim: delete row matching id and active expiry in current transaction
        deleted = (
            db.query(PendingTransaction)
            .filter(
                PendingTransaction.id == pending_id,
                PendingTransaction.user_id == user_id,
                PendingTransaction.expires_at > now,
            )
            .delete(synchronize_session=False)
        )

        if deleted != 1:
            db.rollback()
            return {
                "status": "conflict",
                "message": "Already confirmed or expired.",
            }

        tx_date = get_pkt_now()
        if date_str:
            try:
                parsed_date = datetime.strptime(date_str.strip(), "%Y-%m-%d")
                tx_date = parsed_date.replace(hour=tx_date.hour, minute=tx_date.minute, second=tx_date.second)
            except Exception:
                pass

        tx = Transaction(
            user_id=user_id,
            store_name=store_name,
            total_amount=total_amount,
            transaction_date=tx_date,
            raw_transcript=raw_transcript,
            payment_method=payment_method,
        )
        db.add(tx)
        db.flush()

        logged_items = []
        if items_json:
            p_items = json.loads(items_json)
            for it in p_items:
                t_item = TransactionItem(
                    transaction_id=tx.id,
                    item_name=it["item_name"],
                    category=it.get("category", "groceries"),
                    price=round(it["price"], 2),
                    quantity=float(it.get("quantity", 1.0)),
                )
                db.add(t_item)
                logged_items.append(t_item.to_dict())
        else:
            t_item = TransactionItem(
                transaction_id=tx.id,
                item_name="general purchase",
                category="other",
                price=total_amount,
                quantity=1.0,
            )
            db.add(t_item)
            logged_items.append(t_item.to_dict())

        db.commit()
        db.refresh(tx)

        alerts = [b for b in budget_status(db, user_id) if b["level"] in ("warning", "over")]

        return {
            "status": "success",
            "message": f"Confirmed and logged PKR {tx.total_amount:,.2f} at {tx.store_name.title()}.",
            "transaction": tx.to_dict(),
            "budget_alerts": alerts,
        }
    except Exception as e:
        db.rollback()
        raise e


def cancel_pending_transaction(db: Session, user_id: str) -> Dict[str, Any]:
    """
    Atomically claims and cancels a server-held pending transaction.
    """
    pending = (
        db.query(PendingTransaction)
        .filter(PendingTransaction.user_id == user_id)
        .order_by(PendingTransaction.created_at.desc())
        .first()
    )
    if not pending:
        return {"status": "not_found", "message": "No pending expense found to cancel."}

    pending_id = pending.id
    cancelled_amt = pending.total_amount
    cancelled_store = pending.store_name

    deleted = (
        db.query(PendingTransaction)
        .filter(PendingTransaction.id == pending_id, PendingTransaction.user_id == user_id)
        .delete(synchronize_session=False)
    )
    if deleted != 1:
        db.rollback()
        return {"status": "conflict", "message": "Already confirmed, cancelled, or expired."}

    db.commit()
    return {
        "status": "cancelled",
        "message": f"Cancelled pending expense of PKR {cancelled_amt:,.2f} at {cancelled_store.title()}.",
    }
