"""
Deterministic analytics (all math is done by SQL / Python, never by the LLM).

Used by both the FastAPI dashboard endpoints and the LangChain agent tools so
that the chat answers and the dashboard always agree.
"""
import math
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

import re
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from database import (
    Transaction,
    TransactionItem,
    Book,
    Budget,
    RecurringExpense,
    PendingTransaction,
    UdhaarEntry,
    Party,
    PartyTransaction,
    BusinessProfile,
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


GENERIC_STORE_NAMES = frozenset(
    {
        "",
        "general store",
        "local store",
        "local shop",
        "general",
        "store",
        "shop",
        "mart",
        "market",
        "bazaar",
        "dukan",
        "dukkan",
        "misc",
        "unknown",
        "other",
        "general purchase",
        "bank_transfer transfer",
        "bank_transfer",
        "bank transfer",
        "bank transfer transfer",
        "transfer",
        "transfer transfer",
        "easypaisa transfer",
        "jazzcash transfer",
        "raast transfer",
        "card transfer",
        "دکان",
        "جنرل سٹور",
        "جنرل اسٹور",
        "لوکل سٹور",
        "لوکل اسٹور",
        "سمار",
        "سودا",
        "خریداری",
        "متفرق",
        "بینک ٹرانسفر",
    }
)

GENERIC_SALE_NAMES = frozenset(
    {
        "customer / cash sale",
        "cash sale",
        "customer",
        "sale",
        "galla",
        "farokht",
        "فروخت",
        "گلہ",
        "گالا",
        "آج کا گلہ",
        "نقد فروخت",
        "کسٹمر",
        "گاہک",
    }
)

GENERIC_ITEM_NAMES = frozenset(
    GENERIC_STORE_NAMES
    | GENERIC_SALE_NAMES
    | {"item", "items", "general item", "misc item", "general expense", "expense", "purchase", "samann", "samaan", "سامان"}
)

_URDU_RE = re.compile(r"[\u0600-\u06FF]")

_TRANSCRIPT_HIGHLIGHTS = (
    (("بجلی", "electricity", "wapda", "lesco", "k-electric", "kelectric", "iesco"), "بجلی کا بل", "Electricity Bill"),
    (("گیس", "sui gas", "ssgc", "sngpl"), "گیس کا بل", "Gas Bill"),
    (("انٹرنیٹ", "وائی فائی", "wifi", "internet", "ptcl", "nayatel", "fiber"), "انٹرنیٹ بل", "Internet Bill"),
    (("پانی کا بل", "water bill", "wasa"), "پانی کا بل", "Water Bill"),
    (("پیٹرول", "پٹرول", "petrol", "diesel", "ڈیزل", "cng", "fuel"), "پیٹرول / ایندھن", "Fuel / Petrol"),
    (("راشن", "گھر کا راشن", "ration", "groceries", "grocery", "کریانہ"), "گھر کا راشن", "Groceries / Ration"),
    (("دوائی", "دوا", "میڈیکل", "medicine", "pharmacy", "doctor", "clinic", "hospital"), "دوائی / میڈیکل", "Medical / Pharmacy"),
    (("کرایہ", "rent", "kiraya"), "کرایہ", "Rent"),
    (("فیس", "fee", "fees", "tuition", "school"), "فیس / تعلیم", "School / Tuition Fee"),
    (("مزدوری", "تنخواہ", "wages", "salary", "labor", "mazdoori"), "مزدوری / تنخواہ", "Labor / Wages"),
    (("مال", "اسٹاک", "stock", "inventory", "wholesale", "thok"), "دکان کا مال / اسٹاک", "Shop Inventory"),
)

_CATEGORY_HIGHLIGHTS = {
    "utilities": ("یوٹیلیٹی بل", "Utility Bill"),
    "groceries": ("راشن / گروسری", "Groceries"),
    "fuel": ("پیٹرول / ایندھن", "Fuel"),
    "transport": ("سفر / کرایہ", "Transport"),
    "medical": ("دوائی / میڈیکل", "Medical"),
    "food": ("کھانا / ناشتہ", "Food & Dining"),
    "education": ("فیس / تعلیم", "Education"),
    "rent": ("کرایہ", "Rent"),
    "inventory": ("دکان کا مال", "Shop Inventory"),
    "labor": ("مزدوری / تنخواہ", "Labor & Wages"),
    "maintenance": ("مرمت", "Maintenance"),
    "clothes": ("کپڑے", "Clothing"),
    "entertainment": ("تفریح", "Entertainment"),
}


def _has_urdu_script(text: str) -> bool:
    return bool(text and _URDU_RE.search(text))


def _format_label(text: str, max_len: int = 56) -> str:
    cleaned = " ".join((text or "").strip().split())
    if not cleaned:
        return ""
    cleaned = re.sub(r'[_]+', ' ', cleaned)
    cleaned = re.sub(r'\b([A-Za-z]+)\s+\1\b', r'\1', cleaned, flags=re.I)
    cleaned = " ".join(cleaned.split())
    if not _has_urdu_script(cleaned) and cleaned == cleaned.lower():
        cleaned = cleaned.title()
    if len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 1].rstrip() + "…"
    return cleaned


def compute_transaction_title(
    store_name: str,
    items: Optional[List[Dict[str, Any]]] = None,
    raw_transcript: str = "",
    tx_type: str = "expense",
    inferred_category: str = "",
) -> str:
    """
    Computes a concise, descriptive highlight title for a transaction so users never
    see 'general store' / 'local store' everywhere in their ledger.
    Priority:
      - Sales: meaningful line items -> explicit non-generic customer/description -> transcript / Cash Sale
      - Expenses: explicit named vendor -> concise line item highlight -> keyword/category -> transcript snippet
    """
    s_clean = " ".join((store_name or "").strip().split())
    s_low = s_clean.lower()
    t_clean = " ".join((raw_transcript or "").strip().split())

    clean_items: List[str] = []
    seen_items = set()
    for it in items or []:
        if isinstance(it, dict):
            raw_name = it.get("item_name") or it.get("name") or ""
        else:
            raw_name = getattr(it, "item_name", "") or ""
        name = " ".join(str(raw_name).strip().split())
        low = name.lower()
        if not name or low in GENERIC_ITEM_NAMES or low in seen_items:
            continue
        seen_items.add(low)
        clean_items.append(_format_label(name, max_len=32))

    is_urdu = _has_urdu_script(t_clean) or _has_urdu_script(s_clean) or any(_has_urdu_script(x) for x in clean_items)

    def _join_items(names: List[str]) -> str:
        if len(names) == 1:
            return _format_label(names[0], max_len=56)
        if len(names) == 2:
            sep = " اور " if is_urdu else " & "
            return _format_label(f"{names[0]}{sep}{names[1]}", max_len=60)
        if is_urdu:
            return _format_label(f"{names[0]}، {names[1]} وغیرہ ({len(names)} اشیاء)", max_len=64)
        return _format_label(f"{names[0]}, {names[1]} (+{len(names) - 2} more)", max_len=64)

    if tx_type == "sale":
        if clean_items:
            return _join_items(clean_items)
        if s_low and s_low not in GENERIC_STORE_NAMES and s_low not in GENERIC_SALE_NAMES:
            return _format_label(s_clean, max_len=56)
        return "نقد فروخت" if is_urdu else "Cash Sale"

    # Priority 1: Explicit named vendor / shop / biller
    if s_low and s_low not in GENERIC_STORE_NAMES and s_low not in GENERIC_SALE_NAMES:
        return _format_label(s_clean, max_len=56)

    # Priority 2: Specific items bought
    if clean_items:
        return _join_items(clean_items)

    # Priority 3: Specific keyword match from transcript
    t_low = t_clean.lower()
    for keywords, ur_label, en_label in _TRANSCRIPT_HIGHLIGHTS:
        if any(kw in t_low for kw in keywords):
            return ur_label if is_urdu else en_label

    # Priority 3b: Inferred category highlight
    cat_key = (inferred_category or "").strip().lower()
    if cat_key in _CATEGORY_HIGHLIGHTS:
        ur_cat, en_cat = _CATEGORY_HIGHLIGHTS[cat_key]
        return ur_cat if is_urdu else en_cat

    # Priority 4: Meaningful transcript snippet
    if len(t_clean) >= 4:
        snippet = re.sub(r"\b\d+(?:[.,]\d+)?\s*(?:rupees|rs|pkr|روپے)?\b", "", t_clean, flags=re.I).strip(" ,.-،۔")
        if len(snippet) >= 4:
            return _format_label(snippet, max_len=44)

    return "روزمرہ خرچ" if is_urdu else "Daily Expense"


def detect_cross_book_signal(transcript: str, active_book_type: Optional[str]) -> Optional[Dict[str, str]]:
    """
    Lightweight lexical detector that spots obvious cross-book intent:
    - Household/family expense logged inside a Business book -> suggest Personal book.
    - Sales/Galla/Customer/Supplier activity logged inside a Personal book -> suggest Business book.
    Returns suggestion dict or None (never blocks or alters the primary write).
    """
    if not transcript or active_book_type not in ("business", "personal"):
        return None
    low = " ".join(transcript.strip().lower().split())
    if not low:
        return None

    personal_markers = (
        "گھر کا راشن",
        "گھر کے راشن",
        "گھریلو",
        "بچوں کی فیس",
        "اسکول فیس",
        "سکول فیس",
        "home ration",
        "home groceries",
        "household",
        "school fee",
        "tuition fee",
        "family expense",
        "personal expense",
        "ذاتی خرچ",
        "گھر کی دوائی",
    )
    business_markers = (
        "فروخت",
        "بیچا",
        "بیچی",
        "پیچا",
        "پیچھا",
        "گلہ",
        "گالا",
        "farokht",
        "galla",
        "بکری",
        "کسٹمر",
        "گاہک",
        "سپلائر",
        "کاروبار",
        "مزدوری",
        "دکان کا مال",
        "دکان کا کرایہ",
        "shop sale",
        "customer sale",
        "supplier",
        "inventory",
    )

    if active_book_type == "business":
        if any(m in low for m in personal_markers) and not any(b in low for b in business_markers):
            return {
                "suggested_book_type": "personal",
                "reason": "household_expense",
                "message_ur": "یہ گھریلو/ذاتی خرچ لگتا ہے — ذاتی حساب میں منتقل کریں؟",
                "message_en": "This looks like a personal/household expense — move to Personal book?",
            }
    elif active_book_type == "personal":
        if any(m in low for m in business_markers) and not any(p in low for p in personal_markers):
            return {
                "suggested_book_type": "business",
                "reason": "business_activity",
                "message_ur": "یہ کاروباری لین دین لگتا ہے — کاروبار کتاب میں منتقل کریں؟",
                "message_en": "This looks like a business transaction — move to Business book?",
            }
    return None


def ensure_default_books(db: Session, user_id: str) -> Dict[str, Any]:
    """
    Ensures every user has default Business and Personal books and idempotently backfills
    any legacy/orphan rows (book_id IS NULL) as well as missing highlight titles on
    generic-store transactions.
    """
    now = get_pkt_now()
    books = (
        db.query(Book)
        .filter(Book.user_id == user_id, Book.deleted_at.is_(None))
        .order_by(Book.is_default.desc(), Book.id.asc())
        .all()
    )

    changed = False
    if not books:
        biz = Book(
            user_id=user_id,
            name="کاروبار / My Business",
            book_type="business",
            is_default=True,
            created_at=now,
        )
        pers = Book(
            user_id=user_id,
            name="ذاتی حساب / Personal",
            book_type="personal",
            is_default=False,
            created_at=now,
        )
        db.add(biz)
        db.add(pers)
        db.flush()
        books = [biz, pers]
        changed = True

    biz_book = next((b for b in books if b.book_type == "business"), books[0])
    pers_book = next((b for b in books if b.book_type == "personal"), books[0])

    # Backfill orphan transactions cleanly: sales -> business, expenses -> personal
    orphan_txs = (
        db.query(Transaction)
        .filter(Transaction.user_id == user_id, Transaction.book_id.is_(None))
        .all()
    )
    for tx in orphan_txs:
        tx.book_id = biz_book.id if tx.tx_type == "sale" else pers_book.id
        if not tx.title and (
            (tx.store_name or "").strip().lower() in GENERIC_STORE_NAMES
            or tx.tx_type == "sale"
        ):
            tx.title = compute_transaction_title(
                tx.store_name,
                items=tx.items,
                raw_transcript=tx.raw_transcript or "",
                tx_type=tx.tx_type or "expense",
            )
        changed = True

    # Also backfill missing highlight titles on generic-store rows created before this feature
    generic_untitled = (
        db.query(Transaction)
        .filter(
            Transaction.user_id == user_id,
            Transaction.deleted_at.is_(None),
            Transaction.title.is_(None),
        )
        .all()
    )
    for tx in generic_untitled:
        s_low = (tx.store_name or "").strip().lower()
        if s_low in GENERIC_STORE_NAMES or tx.tx_type == "sale":
            tx.title = compute_transaction_title(
                tx.store_name,
                items=tx.items,
                raw_transcript=tx.raw_transcript or "",
                tx_type=tx.tx_type or "expense",
            )
            changed = True

    for model, target_id in (
        (PendingTransaction, pers_book.id),
        (Budget, pers_book.id),
        (RecurringExpense, pers_book.id),
        (UdhaarEntry, biz_book.id),
    ):
        updated = (
            db.query(model)
            .filter(model.user_id == user_id, model.book_id.is_(None))
            .update({model.book_id: target_id}, synchronize_session=False)
        )
        if updated:
            changed = True

    if changed:
        db.commit()
        books = (
            db.query(Book)
            .filter(Book.user_id == user_id, Book.deleted_at.is_(None))
            .order_by(Book.is_default.desc(), Book.id.asc())
            .all()
        )
        biz_book = next((b for b in books if b.book_type == "business"), books[0])
        pers_book = next((b for b in books if b.book_type == "personal"), books[0])

    default_book = next((b for b in books if b.is_default), books[0])
    return {
        "books": books,
        "business_book": biz_book,
        "personal_book": pers_book,
        "default_book": default_book,
    }


def resolve_book_for_user(
    db: Session,
    user_id: str,
    book_id: Optional[int] = None,
) -> Optional[Book]:
    """
    Ensures user books exist and resolves `book_id` with strict tenant ownership.
    - If `book_id` is None, returns the user's default active book.
    - If `book_id` is provided, returns that Book only if owned by `user_id` and active; else None.
    """
    info = ensure_default_books(db, user_id)
    if book_id is None:
        return info["default_book"]
    return (
        db.query(Book)
        .filter(
            Book.id == int(book_id),
            Book.user_id == user_id,
            Book.deleted_at.is_(None),
        )
        .first()
    )


def create_user_book(db: Session, user_id: str, name: str, book_type: str = "business") -> Book:
    """Creates a new Business or Personal book owned by `user_id`."""
    clean_name = " ".join((name or "").strip().split())
    if not clean_name:
        raise ValueError("Book name cannot be empty.")
    if len(clean_name) > 120:
        clean_name = clean_name[:120]
    clean_type = "personal" if str(book_type or "").strip().lower() == "personal" else "business"
    ensure_default_books(db, user_id)
    book = Book(
        user_id=user_id,
        name=clean_name,
        book_type=clean_type,
        is_default=False,
        created_at=get_pkt_now(),
    )
    db.add(book)
    db.commit()
    db.refresh(book)
    return book


def update_user_book(
    db: Session,
    user_id: str,
    book_id: int,
    name: Optional[str] = None,
    book_type: Optional[str] = None,
    is_default: Optional[bool] = None,
) -> Optional[Book]:
    """Renames or updates an existing Book owned by `user_id`."""
    ensure_default_books(db, user_id)
    book = (
        db.query(Book)
        .filter(Book.id == int(book_id), Book.user_id == user_id, Book.deleted_at.is_(None))
        .first()
    )
    if not book:
        return None
    if name is not None:
        clean_name = " ".join(name.strip().split())
        if not clean_name:
            raise ValueError("Book name cannot be empty.")
        book.name = clean_name[:120]
    if book_type is not None:
        book.book_type = "personal" if str(book_type).strip().lower() == "personal" else "business"
    if is_default is True:
        db.query(Book).filter(Book.user_id == user_id, Book.deleted_at.is_(None)).update(
            {Book.is_default: False}, synchronize_session=False
        )
        book.is_default = True
    db.commit()
    db.refresh(book)
    return book


def delete_user_book(db: Session, user_id: str, book_id: int) -> Dict[str, Any]:
    """Soft-deletes a Book if the user owns it and still has at least one other active Book."""
    ensure_default_books(db, user_id)
    active_books = (
        db.query(Book)
        .filter(Book.user_id == user_id, Book.deleted_at.is_(None))
        .order_by(Book.id.asc())
        .all()
    )
    target = next((b for b in active_books if b.id == int(book_id)), None)
    if not target:
        return {"status": "not_found", "message": "Book not found."}
    if len(active_books) <= 1:
        return {"status": "error", "message": "Cannot delete your only remaining book."}

    target.deleted_at = get_pkt_now()
    was_default = target.is_default
    target.is_default = False
    remaining = [b for b in active_books if b.id != target.id]
    if was_default and remaining:
        remaining[0].is_default = True
    db.commit()
    return {
        "status": "success",
        "deleted_book_id": int(book_id),
        "fallback_book_id": remaining[0].id if remaining else None,
    }


def move_transaction_to_book(
    db: Session,
    user_id: str,
    transaction_id: int,
    target_book_id: int,
) -> Dict[str, Any]:
    """Moves a transaction owned by `user_id` into another active Book owned by `user_id`."""
    ensure_default_books(db, user_id)
    tx = (
        db.query(Transaction)
        .filter(
            Transaction.id == int(transaction_id),
            Transaction.user_id == user_id,
            Transaction.deleted_at.is_(None),
        )
        .first()
    )
    if not tx:
        return {"status": "not_found", "message": "Transaction not found."}

    target_book = (
        db.query(Book)
        .filter(
            Book.id == int(target_book_id),
            Book.user_id == user_id,
            Book.deleted_at.is_(None),
        )
        .first()
    )
    if not target_book:
        return {"status": "not_found", "message": "Target book not found."}

    tx.book_id = target_book.id
    db.commit()
    db.refresh(tx)
    return {
        "status": "success",
        "transaction": tx.to_dict(),
        "book": target_book.to_dict(),
    }


def month_start(now: Optional[datetime] = None) -> datetime:
    """Returns midnight on the 1st of the current month in Pakistan Standard Time."""
    now = now or get_pkt_now()
    return datetime(now.year, now.month, 1, 0, 0, 0)


def _is_expense_clause():
    """Returns SQLAlchemy filter matching expense transactions (including legacy rows with NULL tx_type)."""
    return or_(Transaction.tx_type == "expense", Transaction.tx_type.is_(None))


def category_breakdown(
    db: Session,
    user_id: str,
    since: datetime,
    book_id: Optional[int] = None,
) -> List[Dict]:
    """SUM(price) per category since `since`, largest first, with percentage share."""
    filters = [
        Transaction.user_id == user_id,
        Transaction.transaction_date >= since,
        Transaction.deleted_at.is_(None),
        _is_expense_clause(),
    ]
    if book_id is not None:
        filters.append(Transaction.book_id == int(book_id))
    rows = (
        db.query(
            TransactionItem.category,
            func.sum(TransactionItem.price).label("total"),
        )
        .join(Transaction, Transaction.id == TransactionItem.transaction_id)
        .filter(*filters)
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


def daily_trend(
    db: Session,
    user_id: str,
    days: int = 30,
    book_id: Optional[int] = None,
) -> List[Dict]:
    """Per-day spending for the last `days` days (zero-filled so the chart has no gaps)."""
    today = get_pkt_now().replace(hour=0, minute=0, second=0, microsecond=0)
    start = today - timedelta(days=days - 1)

    bind = db.get_bind()
    if bind and "postgres" in getattr(bind.dialect, "name", ""):
        day_expr = func.to_char(Transaction.transaction_date, "YYYY-MM-DD")
    else:
        day_expr = func.strftime("%Y-%m-%d", Transaction.transaction_date)

    filters = [
        Transaction.user_id == user_id,
        Transaction.transaction_date >= start,
        Transaction.deleted_at.is_(None),
        _is_expense_clause(),
    ]
    if book_id is not None:
        filters.append(Transaction.book_id == int(book_id))

    rows = (
        db.query(
            day_expr.label("day"),
            func.sum(Transaction.total_amount).label("total"),
        )
        .filter(*filters)
        .group_by("day")
        .all()
    )
    by_day = {r.day: float(r.total) for r in rows}
    out = []
    for i in range(days):
        d = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        out.append({"date": d, "total_pkr": round(by_day.get(d, 0.0), 2)})
    return out


def payment_split(
    db: Session,
    user_id: str,
    since: datetime,
    book_id: Optional[int] = None,
) -> List[Dict]:
    """SUM(total_amount) per payment method, plus cash-vs-digital totals."""
    filters = [
        Transaction.user_id == user_id,
        Transaction.transaction_date >= since,
        Transaction.deleted_at.is_(None),
        _is_expense_clause(),
    ]
    if book_id is not None:
        filters.append(Transaction.book_id == int(book_id))

    rows = (
        db.query(
            Transaction.payment_method,
            func.sum(Transaction.total_amount).label("total"),
            func.count(Transaction.id).label("count"),
        )
        .filter(*filters)
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


def budget_status(
    db: Session,
    user_id: str,
    book_id: Optional[int] = None,
) -> List[Dict]:
    """Spent / limit / percent for every budget the user has set (current month)."""
    start = month_start()
    budget_query = db.query(Budget).filter(Budget.user_id == user_id)
    if book_id is not None:
        budget_query = budget_query.filter(Budget.book_id == int(book_id))
    budgets = budget_query.all()
    result = []
    for b in budgets:
        tx_filters = [
            Transaction.user_id == user_id,
            Transaction.transaction_date >= start,
            Transaction.deleted_at.is_(None),
            _is_expense_clause(),
        ]
        if book_id is not None:
            tx_filters.append(Transaction.book_id == int(book_id))
        elif b.book_id is not None:
            tx_filters.append(or_(Transaction.book_id == int(b.book_id), Transaction.book_id.is_(None)))

        if b.category == "total":
            spent = (
                db.query(func.coalesce(func.sum(Transaction.total_amount), 0.0))
                .filter(*tx_filters)
                .scalar()
            )
        else:
            spent = (
                db.query(func.coalesce(func.sum(TransactionItem.price), 0.0))
                .join(Transaction, Transaction.id == TransactionItem.transaction_id)
                .filter(
                    *tx_filters,
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
                "book_id": b.book_id,
                "category": b.category,
                "monthly_limit": round(b.monthly_limit, 2),
                "spent_pkr": round(spent, 2),
                "remaining_pkr": round(b.monthly_limit - spent, 2),
                "percent": pct,
                "level": level,
            }
        )
    return result


def galla_summary(
    db: Session,
    user_id: str,
    book_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Computes deterministic 'Aaj Ka Galla' (Today's Cash Drawer) and Monthly Cash-In vs Cash-Out.
    - Sales (tx_type='sale'): Cash-In / Farokht
    - Expenses (tx_type='expense'): Cash-Out / Kharcha
    """
    now = get_pkt_now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    m_start = month_start(now)

    filters = [
        Transaction.user_id == user_id,
        Transaction.transaction_date >= m_start,
        Transaction.deleted_at.is_(None),
    ]
    if book_id is not None:
        filters.append(Transaction.book_id == int(book_id))

    month_txs = db.query(Transaction).filter(*filters).all()

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


def udhaar_summary(
    db: Session,
    user_id: str,
    book_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Returns deterministic Udhaar (Lend & Borrow) Khata summary for the user:
    - total_receivable_pkr: Maine Lene Hain (Customer owes shopkeeper)
    - total_payable_pkr: Maine Dene Hain (Shopkeeper owes supplier/lender)
    - entries: active open Udhaar entries
    """
    filters = [
        UdhaarEntry.user_id == user_id,
        UdhaarEntry.deleted_at.is_(None),
    ]
    if book_id is not None:
        filters.append(UdhaarEntry.book_id == int(book_id))

    rows = (
        db.query(UdhaarEntry)
        .filter(*filters)
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
    book_id: Optional[int] = None,
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
        book_id=int(book_id) if book_id is not None else None,
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
    confirm_overpay: bool = False,
    book_id: Optional[int] = None,
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

    try:
        udhaar_filters = [
            UdhaarEntry.user_id == user_id,
            UdhaarEntry.deleted_at.is_(None),
            UdhaarEntry.status == "open",
        ]
        if book_id is not None:
            udhaar_filters.append(UdhaarEntry.book_id == int(book_id))
        entries = (
            db.query(UdhaarEntry)
            .filter(*udhaar_filters)
            .order_by(UdhaarEntry.created_at.asc(), UdhaarEntry.id.asc())
            .with_for_update()
            .all()
        )

        open_people = sorted({e.person_name for e in entries if (e.person_name or "").strip()})

        if entry_id is not None:
            matched = [e for e in entries if e.id == int(entry_id)]
            if clean_dir:
                matched = [e for e in matched if e.direction == clean_dir]
        elif person_name and _norm(person_name):
            exact = [e for e in entries if _norm(e.person_name) == _norm(person_name)]
            if exact:
                person_matched = exact
            else:
                toks = set(_norm(person_name).split())
                cands = [e for e in entries if toks and toks <= set(_norm(e.person_name).split())]  # whole words only
                names = {_norm(e.person_name) for e in cands}
                if len(names) > 1:
                    return {
                        "status": "ambiguous",
                        "candidates": sorted({e.person_name for e in cands}),
                        "message": f"Multiple open Udhaar records match '{person_name}'. Please specify which person.",
                    }
                person_matched = cands

            if not person_matched:
                return {
                    "status": "not_found",
                    "open_people": open_people,
                    "message": f"No open Udhaar record found for '{person_name}'.",
                }

            matched_person = person_matched[0].person_name
            directions_present = {e.direction for e in person_matched}
            if clean_dir is not None:
                matched = [e for e in person_matched if e.direction == clean_dir]
                if not matched:
                    return {
                        "status": "direction_mismatch",
                        "person_name": matched_person,
                        "requested_direction": clean_dir,
                        "open_directions": sorted(directions_present),
                        "message": (
                            f"'{matched_person}' has no open '{clean_dir}' Udhaar balance "
                            f"(open directions: {', '.join(sorted(directions_present))})."
                        ),
                    }
            else:
                matched = person_matched
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

        matched_person = matched[0].person_name
        final_direction = matched[0].direction
        total_open_before = round(
            sum(max(0.0, float(e.amount or 0.0) - float(e.paid_amount or 0.0)) for e in matched),
            2,
        )

        overpaid_by = 0.0
        if payment_amount is not None and float(payment_amount) > 0:
            req_amt = round(float(payment_amount), 2)
            if req_amt > total_open_before + 0.01:
                overpaid_by = round(req_amt - total_open_before, 2)
                if not confirm_overpay:
                    return {
                        "status": "overpaid",
                        "person_name": matched_person,
                        "direction": final_direction,
                        "open_balance_pkr": total_open_before,
                        "overpaid_by_pkr": overpaid_by,
                        "message": f"Payment ({req_amt} PKR) exceeds open balance ({total_open_before} PKR) by {overpaid_by} PKR.",
                    }
                remaining_to_apply = total_open_before
            else:
                remaining_to_apply = req_amt
        else:
            remaining_to_apply = total_open_before

        applied = 0.0
        for e in matched:
            if remaining_to_apply <= 0.001:
                break
            entry_rem = round(max(0.0, float(e.amount or 0.0) - float(e.paid_amount or 0.0)), 2)
            if entry_rem <= 0.001:
                continue
            chunk = round(min(entry_rem, remaining_to_apply), 2)
            will_be_settled = (float(e.paid_amount or 0.0) + chunk) >= (float(e.amount or 0.0) - 0.01)
            new_status = "settled" if will_be_settled else "open"

            n = (
                db.query(UdhaarEntry)
                .filter(
                    UdhaarEntry.id == e.id,
                    UdhaarEntry.user_id == user_id,
                    UdhaarEntry.deleted_at.is_(None),
                    UdhaarEntry.status == "open",
                    UdhaarEntry.paid_amount + chunk <= UdhaarEntry.amount + 0.01,
                )
                .update(
                    {
                        UdhaarEntry.paid_amount: UdhaarEntry.paid_amount + chunk,
                        UdhaarEntry.status: new_status,
                        UdhaarEntry.updated_at: now,
                    },
                    synchronize_session=False,
                )
            )
            if n == 0:
                db.rollback()
                return {
                    "status": "conflict",
                    "message": "Balance changed, please retry",
                }
            remaining_to_apply = round(remaining_to_apply - chunk, 2)
            applied = round(applied + chunk, 2)

        db.commit()
        db.expire_all()
        total_open_after = round(max(0.0, total_open_before - applied), 2)

        result: Dict[str, Any] = {
            "status": "success",
            "person_name": matched_person,
            "direction": final_direction,
            "settled_amount_pkr": round(applied, 2),
            "remaining_balance_pkr": total_open_after,
            "fully_settled": total_open_after <= 0.01,
        }
        if overpaid_by > 0:
            result["overpaid_by_pkr"] = overpaid_by
        return result
    except Exception:
        db.rollback()
        raise


def update_udhaar_entry(
    db: Session,
    user_id: str,
    entry_id: Optional[int] = None,
    person_name: Optional[str] = None,
    amount: Optional[float] = None,
    notes: Optional[str] = None,
    phone_number: Optional[str] = None,
    direction: Optional[str] = None,
    book_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Locates an open UdhaarEntry by entry_id or most recent open entry matching person_name.
    Validates amount, updates fields, commits, and returns the updated entry.
    """
    entry = None
    if entry_id is not None:
        entry = (
            db.query(UdhaarEntry)
            .filter(
                UdhaarEntry.user_id == user_id,
                UdhaarEntry.id == int(entry_id),
                UdhaarEntry.deleted_at.is_(None),
            )
            .first()
        )
        if not entry:
            return {"status": "not_found", "message": f"Udhaar entry #{entry_id} not found."}
    elif person_name and _norm(person_name):
        filters = [
            UdhaarEntry.user_id == user_id,
            UdhaarEntry.deleted_at.is_(None),
            UdhaarEntry.status == "open",
        ]
        if book_id is not None:
            filters.append(UdhaarEntry.book_id == int(book_id))
        open_entries = (
            db.query(UdhaarEntry)
            .filter(*filters)
            .order_by(UdhaarEntry.created_at.desc(), UdhaarEntry.id.desc())
            .all()
        )
        exact = [e for e in open_entries if _norm(e.person_name) == _norm(person_name)]
        if exact:
            entry = exact[0]
        else:
            toks = set(_norm(person_name).split())
            cands = [e for e in open_entries if toks and toks <= set(_norm(e.person_name).split())]
            names = {_norm(e.person_name) for e in cands}
            if len(names) > 1:
                return {
                    "status": "ambiguous",
                    "message": f"Multiple people match '{person_name}'.",
                    "candidates": sorted({e.person_name for e in cands}),
                }
            if cands:
                entry = cands[0]
        if not entry:
            return {"status": "not_found", "message": f"No open Udhaar entry found matching '{person_name}'."}
    else:
        return {"status": "not_found", "message": "Either entry_id or person_name is required."}

    if amount is not None:
        try:
            val = validate_transaction_amount(amount)
        except ValueError as ve:
            return {"status": "error", "message": str(ve)}
        already_paid = float(entry.paid_amount or 0.0)
        if val < already_paid - 0.01:
            return {"status": "error", "message": "New amount cannot be less than already paid amount."}
        entry.amount = val
        entry.status = "settled" if val <= already_paid + 0.01 else "open"

    if notes is not None:
        entry.notes = notes.strip() or None

    if phone_number is not None:
        entry.phone_number = phone_number.strip() or None

    if direction and str(direction).strip():
        d_low = str(direction).strip().lower()
        if d_low in ("payable", "borrow", "dene", "dene_hain", "liya", "owed", "take_credit"):
            entry.direction = "payable"
        elif d_low in ("receivable", "lend", "lene", "lene_hain", "diya", "give_credit"):
            entry.direction = "receivable"

    entry.updated_at = get_pkt_now()
    db.commit()
    db.refresh(entry)
    return {"status": "success", "entry": entry.to_dict()}


def undo_last_udhaar_entry(
    db: Session,
    user_id: str,
    book_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Soft-deletes the most recently created active UdhaarEntry for the user."""
    filters = [UdhaarEntry.user_id == user_id, UdhaarEntry.deleted_at.is_(None)]
    if book_id is not None:
        filters.append(UdhaarEntry.book_id == int(book_id))
    entry = (
        db.query(UdhaarEntry)
        .filter(*filters)
        .order_by(UdhaarEntry.id.desc())
        .first()
    )
    if not entry:
        return {"status": "not_found", "message": "No Udhaar entry found to undo."}

    entry.deleted_at = get_pkt_now()
    db.commit()
    return {"status": "success", "deleted_entry": entry.to_dict()}


def recurring_status(
    db: Session,
    user_id: str,
    book_id: Optional[int] = None,
) -> List[Dict]:
    filters = [RecurringExpense.user_id == user_id]
    if book_id is not None:
        filters.append(RecurringExpense.book_id == int(book_id))
    items = (
        db.query(RecurringExpense)
        .filter(*filters)
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


def pay_recurring(
    db: Session,
    user_id: str,
    item: RecurringExpense,
    raw_transcript: Optional[str] = None,
    book_id: Optional[int] = None,
) -> Dict:
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
    target_book_id = int(book_id) if book_id is not None else item.book_id
    tx = Transaction(
        user_id=user_id,
        book_id=target_book_id,
        store_name=item.title,
        title=_format_label(item.title, max_len=56) or item.title,
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


def export_rows(
    db: Session,
    user_id: str,
    book_id: Optional[int] = None,
) -> List[List]:
    """One CSV row per line item (with the transaction context repeated)."""
    filters = [
        Transaction.user_id == user_id,
        Transaction.deleted_at.is_(None),
    ]
    if book_id is not None:
        filters.append(Transaction.book_id == int(book_id))
    txs = (
        db.query(Transaction)
        .filter(*filters)
        .order_by(Transaction.transaction_date.asc(), Transaction.id.asc())
        .all()
    )
    rows: List[List] = []
    for tx in txs:
        date = tx.transaction_date.strftime("%Y-%m-%d %H:%M")
        label = tx.title or tx.store_name
        if not tx.items:
            raw_row = [tx.id, date, label, "", "", 1, tx.total_amount, tx.payment_method, tx.total_amount]
            rows.append([sanitize_csv_cell(c) for c in raw_row])
        for it in tx.items:
            raw_row = [
                tx.id,
                date,
                label,
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

UDHAAR_EXPORT_HEADER = [
    "ID",
    "Date",
    "Person",
    "Phone",
    "Direction",
    "Total Amount (PKR)",
    "Paid Amount (PKR)",
    "Remaining Balance (PKR)",
    "Status",
    "Notes",
]


def export_udhaar_rows(
    db: Session,
    user_id: str,
    book_id: Optional[int] = None,
) -> List[List]:
    """Generates sanitized rows for Udhaar CSV export."""
    filters = [UdhaarEntry.user_id == user_id, UdhaarEntry.deleted_at.is_(None)]
    if book_id is not None:
        filters.append(UdhaarEntry.book_id == int(book_id))
    entries = (
        db.query(UdhaarEntry)
        .filter(*filters)
        .order_by(UdhaarEntry.created_at.asc(), UdhaarEntry.id.asc())
        .all()
    )
    rows: List[List] = []
    for e in entries:
        rem = round(float(e.amount or 0.0) - float(e.paid_amount or 0.0), 2)
        date_str = e.created_at.strftime("%Y-%m-%d %H:%M") if e.created_at else ""
        raw_row = [
            e.id,
            date_str,
            e.person_name,
            e.phone_number or "",
            e.direction,
            e.amount,
            e.paid_amount or 0.0,
            rem,
            e.status,
            e.notes or "",
        ]
        rows.append([sanitize_csv_cell(c) for c in raw_row])
    return rows


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
    pending_book_id = pending.book_id
    pending_title = pending.title
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

        p_items = json.loads(items_json) if items_json else []
        final_title = pending_title or compute_transaction_title(
            store_name=store_name,
            items=p_items,
            raw_transcript=raw_transcript or "",
            tx_type="expense",
        )

        tx = Transaction(
            user_id=user_id,
            book_id=pending_book_id,
            store_name=store_name,
            title=final_title,
            total_amount=total_amount,
            transaction_date=tx_date,
            raw_transcript=raw_transcript,
            payment_method=payment_method,
        )
        db.add(tx)
        db.flush()

        logged_items = []
        if p_items:
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

        alerts = [
            b
            for b in budget_status(db, user_id, book_id=pending_book_id)
            if b["level"] in ("warning", "over")
        ]

        label = tx.title or tx.store_name
        return {
            "status": "success",
            "message": f"Confirmed and logged PKR {tx.total_amount:,.2f} ({label}).",
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


def get_party_balance(
    db: Session,
    user_id: str,
    party_name_or_id: Any,
    book_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Computes real-time party balance, running balance sequence, and due date alerts.
    If party is not in the 'parties' table, falls back to legacy 'udhaar_entries'
    to ensure 100% backward compatibility.
    """
    party: Optional[Party] = None

    # Check if party_name_or_id is numeric ID
    party_id_val = None
    if isinstance(party_name_or_id, int):
        party_id_val = party_name_or_id
    elif isinstance(party_name_or_id, str) and party_name_or_id.strip().isdigit():
        party_id_val = int(party_name_or_id.strip())

    if party_id_val is not None:
        q = db.query(Party).filter(
            Party.id == party_id_val,
            Party.user_id == user_id,
            Party.deleted_at.is_(None),
        )
        if book_id is not None:
            q = q.filter(or_(Party.book_id == book_id, Party.book_id.is_(None)))
        party = q.first()

    # Search by normalized name
    if not party and party_name_or_id:
        target_norm = _norm(str(party_name_or_id))
        all_parties = (
            db.query(Party)
            .filter(Party.user_id == user_id, Party.deleted_at.is_(None))
            .all()
        )
        # 1. Exact match
        for p in all_parties:
            if _norm(p.name) == target_norm:
                party = p
                break
        # 2. Substring match
        if not party:
            for p in all_parties:
                p_norm = _norm(p.name)
                if target_norm in p_norm or p_norm in target_norm:
                    party = p
                    break

    now_pkt = get_pkt_now().date()

    if party:
        party_type = party.party_type or "customer"
        open_bal = float(party.opening_balance or 0.0)
        open_type = party.opening_balance_type or "receivable"

        # Baseline running balance before transactions
        if party_type == "customer":
            running = open_bal if open_type == "receivable" else -open_bal
        else:  # supplier
            running = open_bal if open_type == "payable" else -open_bal

        tx_query = (
            db.query(PartyTransaction)
            .filter(
                PartyTransaction.party_id == party.id,
                PartyTransaction.deleted_at.is_(None),
            )
            .order_by(PartyTransaction.tx_date.asc(), PartyTransaction.id.asc())
        )
        txs = tx_query.all()

        total_credit = 0.0
        total_debit = 0.0
        nearest_due_date = None
        is_overdue = False
        tx_list = []

        for tx in txs:
            amt = float(tx.amount or 0.0)
            if tx.tx_type == "credit":
                total_credit += amt
                if party_type == "customer":
                    running += amt
                else:
                    running -= amt
            else:  # debit
                total_debit += amt
                if party_type == "customer":
                    running -= amt
                else:
                    running += amt

            tx_d = tx.to_dict()
            tx_d["running_balance_pkr"] = round(running, 2)
            tx_list.append(tx_d)

            # Check due date
            if tx.due_date and not tx.is_settlement:
                due_d = tx.due_date.date() if isinstance(tx.due_date, datetime) else tx.due_date
                if nearest_due_date is None or due_d < nearest_due_date:
                    nearest_due_date = due_d
                if due_d < now_pkt:
                    is_overdue = True

        net_signed = running
        if party_type == "customer":
            if net_signed > 0.001:
                status = "receivable"
            elif net_signed < -0.001:
                status = "payable"
            else:
                status = "settled"
        else:  # supplier
            if net_signed > 0.001:
                status = "payable"
            elif net_signed < -0.001:
                status = "receivable"
            else:
                status = "settled"

        if status == "settled":
            is_overdue = False

        return {
            "found": True,
            "party_id": party.id,
            "id": party.id,
            "name": party.name,
            "phone": party.phone or "",
            "address": party.address or "",
            "party_type": party_type,
            "credit_limit": party.credit_limit,
            "opening_balance": round(open_bal, 2),
            "opening_balance_type": open_type,
            "total_credit_pkr": round(total_credit, 2),
            "total_debit_pkr": round(total_debit, 2),
            "total_given_pkr": round(total_credit, 2),
            "total_received_pkr": round(total_debit, 2),
            "net_balance_pkr": round(abs(net_signed), 2),
            "signed_balance_pkr": round(net_signed, 2),
            "balance_status": status,
            "balance_text_ur": (
                "آپ نے لینے ہیں"
                if status == "receivable"
                else ("آپ نے دینے ہیں" if status == "payable" else "حساب برابر")
            ),
            "balance_text_en": (
                "You will receive"
                if status == "receivable"
                else ("You will pay" if status == "payable" else "Settled")
            ),
            "is_overdue": is_overdue,
            "nearest_due_date": nearest_due_date.strftime("%Y-%m-%d") if nearest_due_date else None,
            "tx_count": len(txs),
            "last_tx_date": txs[-1].tx_date.strftime("%Y-%m-%d %H:%M") if txs else None,
            "transactions": tx_list,
        }

    # Fallback to legacy UdhaarEntry if person_name provided
    name_str = str(party_name_or_id or "").strip()
    if name_str:
        norm_name = _norm(name_str)
        legacy_entries = (
            db.query(UdhaarEntry)
            .filter(
                UdhaarEntry.user_id == user_id,
                UdhaarEntry.deleted_at.is_(None),
            )
            .all()
        )
        matched_entries = [
            e for e in legacy_entries
            if _norm(e.person_name) == norm_name or norm_name in _norm(e.person_name)
        ]
        if matched_entries:
            tot_rec = 0.0
            tot_pay = 0.0
            phone = ""
            for e in matched_entries:
                rem = max(0.0, float(e.amount or 0.0) - float(e.paid_amount or 0.0))
                if (e.direction or "receivable") == "receivable":
                    tot_rec += rem
                else:
                    tot_pay += rem
                if e.phone_number and not phone:
                    phone = e.phone_number

            net = tot_rec - tot_pay
            if net > 0.001:
                status = "receivable"
            elif net < -0.001:
                status = "payable"
            else:
                status = "settled"

            return {
                "found": True,
                "party_id": None,
                "id": None,
                "name": matched_entries[0].person_name,
                "phone": phone,
                "address": "",
                "party_type": "customer",
                "credit_limit": None,
                "opening_balance": 0.0,
                "opening_balance_type": "receivable",
                "total_credit_pkr": round(tot_rec, 2),
                "total_debit_pkr": round(tot_pay, 2),
                "total_given_pkr": round(tot_rec, 2),
                "total_received_pkr": round(tot_pay, 2),
                "net_balance_pkr": round(abs(net), 2),
                "signed_balance_pkr": round(net, 2),
                "balance_status": status,
                "balance_text_ur": (
                    "آپ نے لینے ہیں"
                    if status == "receivable"
                    else ("آپ نے دینے ہیں" if status == "payable" else "حساب برابر")
                ),
                "balance_text_en": (
                    "You will receive"
                    if status == "receivable"
                    else ("You will pay" if status == "payable" else "Settled")
                ),
                "is_overdue": False,
                "nearest_due_date": None,
                "tx_count": len(matched_entries),
                "last_tx_date": matched_entries[-1].created_at.strftime("%Y-%m-%d %H:%M") if matched_entries else None,
                "transactions": [],
            }

    # Not found
    return {
        "found": False,
        "party_id": None,
        "id": None,
        "name": str(party_name_or_id or ""),
        "phone": "",
        "address": "",
        "party_type": "customer",
        "credit_limit": None,
        "opening_balance": 0.0,
        "opening_balance_type": "receivable",
        "total_credit_pkr": 0.0,
        "total_debit_pkr": 0.0,
        "total_given_pkr": 0.0,
        "total_received_pkr": 0.0,
        "net_balance_pkr": 0.0,
        "signed_balance_pkr": 0.0,
        "balance_status": "settled",
        "balance_text_ur": "حساب برابر",
        "balance_text_en": "Settled",
        "is_overdue": False,
        "nearest_due_date": None,
        "tx_count": 0,
        "last_tx_date": None,
        "transactions": [],
    }


def get_parties_summary(
    db: Session,
    user_id: str,
    book_id: Optional[int] = None,
    party_type: str = "all",
    search: str = "",
    status: str = "all",
) -> Dict[str, Any]:
    """
    Returns list of parties with metrics and overall receivable/payable summary.
    """
    query = db.query(Party).filter(
        Party.user_id == user_id,
        Party.deleted_at.is_(None),
    )
    if book_id is not None:
        query = query.filter(or_(Party.book_id == book_id, Party.book_id.is_(None)))
    if party_type and party_type != "all":
        query = query.filter(Party.party_type == party_type)

    parties = query.order_by(Party.name.asc()).all()

    items = []
    total_customers = 0
    total_suppliers = 0
    total_rec_pkr = 0.0
    total_pay_pkr = 0.0
    overdue_count = 0

    s_term = _norm(search) if search else ""

    for p in parties:
        # Search filter
        if s_term:
            p_name = _norm(p.name)
            p_phone = _norm(p.phone)
            if s_term not in p_name and s_term not in p_phone:
                continue

        bal_info = get_party_balance(db, user_id, p.id, book_id=book_id)

        # Status filter ('all', 'receivable', 'payable', 'settled', 'overdue')
        if status == "receivable" and bal_info["balance_status"] != "receivable":
            continue
        if status == "payable" and bal_info["balance_status"] != "payable":
            continue
        if status == "settled" and bal_info["balance_status"] != "settled":
            continue
        if status == "overdue" and not bal_info["is_overdue"]:
            continue

        if p.party_type == "customer":
            total_customers += 1
        elif p.party_type == "supplier":
            total_suppliers += 1

        if bal_info["balance_status"] == "receivable":
            total_rec_pkr += bal_info["net_balance_pkr"]
        elif bal_info["balance_status"] == "payable":
            total_pay_pkr += bal_info["net_balance_pkr"]

        if bal_info["is_overdue"]:
            overdue_count += 1

        items.append({
            "id": p.id,
            "party_id": p.id,
            "name": p.name,
            "phone": p.phone or "",
            "address": p.address or "",
            "party_type": p.party_type or "customer",
            "opening_balance": bal_info["opening_balance"],
            "opening_balance_type": bal_info["opening_balance_type"],
            "credit_limit": p.credit_limit,
            "total_credit_pkr": bal_info["total_credit_pkr"],
            "total_debit_pkr": bal_info["total_debit_pkr"],
            "net_balance_pkr": bal_info["net_balance_pkr"],
            "signed_balance_pkr": bal_info["signed_balance_pkr"],
            "balance_status": bal_info["balance_status"],
            "balance_text_ur": bal_info["balance_text_ur"],
            "balance_text_en": bal_info["balance_text_en"],
            "is_overdue": bal_info["is_overdue"],
            "nearest_due_date": bal_info["nearest_due_date"],
            "tx_count": bal_info["tx_count"],
            "last_tx_date": bal_info["last_tx_date"],
            "created_at": p.created_at.strftime("%Y-%m-%d %H:%M") if p.created_at else None,
        })

    net_balance_pkr = round(total_rec_pkr - total_pay_pkr, 2)
    return {
        "parties": items,
        "summary": {
            "total_parties": len(items),
            "total_customers": total_customers,
            "total_suppliers": total_suppliers,
            "total_receivable_pkr": round(total_rec_pkr, 2),
            "total_payable_pkr": round(total_pay_pkr, 2),
            "net_balance_pkr": net_balance_pkr,
            "overdue_count": overdue_count,
        },
    }


def get_or_create_business_profile(
    db: Session,
    user_id: str,
    book_id: Optional[int] = None,
) -> BusinessProfile:
    """
    Retrieves or initializes a business profile for the user.
    """
    profile = (
        db.query(BusinessProfile)
        .filter(BusinessProfile.user_id == user_id)
        .first()
    )
    if not profile:
        profile = BusinessProfile(
            user_id=user_id,
            book_id=book_id,
            business_name="My Business",
            currency="PKR",
            business_type="kiriyana",
        )
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return profile


def generate_party_statement(
    db: Session,
    user_id: str,
    party_id: int,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Generates a full statement for the party within date range.
    """
    party_info = get_party_balance(db, user_id, party_id)
    if not party_info.get("found"):
        return {"status": "error", "message": "Party not found."}

    biz_profile = get_or_create_business_profile(db, user_id)

    txs = party_info.get("transactions", [])
    if start_date:
        txs = [t for t in txs if (t.get("tx_date") or "")[:10] >= start_date]
    if end_date:
        txs = [t for t in txs if (t.get("tx_date") or "")[:10] <= end_date]

    return {
        "status": "success",
        "business": biz_profile.to_dict(),
        "party": party_info,
        "statement_period": {
            "start_date": start_date or (txs[0]["tx_date"][:10] if txs else None),
            "end_date": end_date or (txs[-1]["tx_date"][:10] if txs else None),
        },
        "filtered_transactions": txs,
        "statement_header": biz_profile.statement_header or f"Khata Statement - {party_info['name']}",
        "statement_footer": biz_profile.statement_footer or "Thank you for doing business with us!",
    }


def generate_party_reminder(
    db: Session,
    user_id: str,
    party_id: int,
    lang: str = "ur",
) -> Dict[str, Any]:
    """
    Generates polite bilingual WhatsApp & SMS payment reminder text and click-to-chat URL.
    """
    party_info = get_party_balance(db, user_id, party_id)
    if not party_info.get("found"):
        return {"status": "error", "message": "Party not found."}

    biz = get_or_create_business_profile(db, user_id)
    b_name = biz.business_name or "HisaabAI Store"
    p_name = party_info["name"]
    amount = f"{party_info['net_balance_pkr']:,.2f}"
    due_str = party_info["nearest_due_date"] or "جلد از جلد (Immediate)"

    raast = biz.raast_id or biz.easypaisa_number or biz.jazzcash_number or ""
    contact_phone = biz.phone_number or ""

    raw_phone = re.sub(r"\D", "", party_info["phone"] or "")
    if raw_phone.startswith("0") and len(raw_phone) == 11:
        wa_phone = "92" + raw_phone[1:]
    elif raw_phone.startswith("92"):
        wa_phone = raw_phone
    else:
        wa_phone = raw_phone

    text_ur = (
        f"محترم {p_name} صاحب!\n"
        f"{b_name} کی طرف سے سلام۔\n"
        f"آپ کے کھاتے کا بقایا حساب Rs {amount} ہے۔\n"
        f"آخری تاریخ ادائیگی: {due_str}۔\n"
    )
    if raast:
        text_ur += f"برائے مہربانی راست / ایزی پیسہ / جاز کیش ({raast}) پر رقم منتقل فرمائیں۔\n"
    if contact_phone:
        text_ur += f"کسی سوال یا تصدیق کے لیے رابطہ فرمائیں: {contact_phone}۔\n"
    text_ur += "شکریہ!"

    text_en = (
        f"Dear {p_name},\n"
        f"Greetings from {b_name}.\n"
        f"Your current outstanding khata balance is PKR {amount}.\n"
        f"Payment due: {due_str}.\n"
    )
    if raast:
        text_en += f"Please remit via Raast / Mobile Wallet: {raast}.\n"
    if contact_phone:
        text_en += f"Contact {contact_phone} for inquiries.\n"
    text_en += "Thank you for your valued business!"

    import urllib.parse
    encoded_ur = urllib.parse.quote(text_ur)
    encoded_en = urllib.parse.quote(text_en)

    wa_url_ur = f"https://wa.me/{wa_phone}?text={encoded_ur}" if wa_phone else None
    wa_url_en = f"https://wa.me/{wa_phone}?text={encoded_en}" if wa_phone else None

    return {
        "status": "success",
        "party_id": party_id,
        "customer_name": p_name,
        "phone": party_info["phone"],
        "wa_phone": wa_phone,
        "net_balance_pkr": party_info["net_balance_pkr"],
        "due_date": party_info["nearest_due_date"],
        "is_overdue": party_info["is_overdue"],
        "formatted_text_ur": text_ur,
        "formatted_text_en": text_en,
        "whatsapp_url_ur": wa_url_ur,
        "whatsapp_url_en": wa_url_en,
        "whatsapp_url": wa_url_ur if lang == "ur" else wa_url_en,
    }

