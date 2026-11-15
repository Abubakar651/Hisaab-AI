"""
Structured (fixed-schema JSON) responses for the default chat prompt chips (GitHub issue #2).

Chips send an `intent_id`; the backend builds a compact, validated JSON envelope straight from the
database (no LLM call for read/action intents, no mock data) and the client renders it through a
localized template. For `budget_set` amount parsing, deterministic numeric extraction runs first and
falls back to schema-constrained LLM output (`with_structured_output`) with Pydantic validation and a
single retry before falling back to the free-text agent.

Envelope: {"intent": <id>, "status": "ok|empty|need_input|error", "data": {...}}
"""
import re
from datetime import timedelta
from typing import Any, Dict, List, Literal, Optional, Type

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func
from sqlalchemy.orm import Session

import analytics
from database import Budget, Transaction, get_pkt_now

CURRENCY = "PKR"
SUPPORTED_INTENTS = (
    "spend_summary",
    "spend_summary_3d",
    "galla_summary",
    "udhaar_summary",
    "budget_set",
    "undo_last",
)
TOP_N = 5


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------
class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CategoryAmount(_Strict):
    name: str
    amount: float


class BudgetInfo(_Strict):
    limit: float
    remaining: float


class SpendSummaryData(_Strict):
    period: Literal["month", "last_3_days"]
    total: float
    currency: str = CURRENCY
    txn_count: int
    top_categories: List[CategoryAmount] = Field(default_factory=list)
    budget: Optional[BudgetInfo] = None


class GallaSummaryData(_Strict):
    date: str
    sales_total: float
    expenses_total: float
    cash_in_drawer: float
    currency: str = CURRENCY


class PartyAmount(_Strict):
    party: str
    amount: float


class UdhaarSummaryData(_Strict):
    receivable_total: float
    payable_total: float
    receivables: List[PartyAmount] = Field(default_factory=list)
    payables: List[PartyAmount] = Field(default_factory=list)
    currency: str = CURRENCY


class BudgetNeedInputData(_Strict):
    missing: List[str]


class BudgetSetData(_Strict):
    month: str
    amount: float
    previous_amount: Optional[float] = None
    currency: str = CURRENCY


class BudgetAmountParse(_Strict):
    """Schema for LLM structured extraction of a monthly budget amount from user text."""

    amount: Optional[float] = Field(
        default=None,
        description="Positive monthly budget amount in PKR if user is specifying a budget, else null.",
    )


class DeletedEntry(_Strict):
    type: str
    description: str
    amount: float
    date: str


class UndoLastData(_Strict):
    deleted: DeletedEntry


class EmptyData(_Strict):
    pass


class Envelope(_Strict):
    intent: Literal["spend_summary", "galla_summary", "udhaar_summary", "budget_set", "undo_last"]
    status: Literal["ok", "empty", "need_input", "error"]
    data: Dict[str, Any]


def validate_envelope(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validates both the outer envelope and the intent/status-specific `data` schema."""
    env = Envelope.model_validate(payload)
    intent, st, raw_data = env.intent, env.status, env.data
    if intent == "spend_summary":
        validated_data = SpendSummaryData.model_validate(raw_data)
    elif intent == "galla_summary":
        validated_data = GallaSummaryData.model_validate(raw_data)
    elif intent == "udhaar_summary":
        validated_data = UdhaarSummaryData.model_validate(raw_data)
    elif intent == "budget_set":
        if st == "need_input":
            validated_data = BudgetNeedInputData.model_validate(raw_data)
        else:
            validated_data = BudgetSetData.model_validate(raw_data)
    elif intent == "undo_last":
        if st == "empty":
            validated_data = EmptyData.model_validate(raw_data)
        else:
            validated_data = UndoLastData.model_validate(raw_data)
    else:
        raise ValueError(f"Unsupported intent: {intent}")
    return {
        "intent": intent,
        "status": st,
        "data": validated_data.model_dump(exclude_none=True),
    }


def _make(intent: str, status: str, data: BaseModel) -> Dict[str, Any]:
    return validate_envelope(
        {
            "intent": intent,
            "status": status,
            "data": data.model_dump(exclude_none=True),
        }
    )


# ---------------------------------------------------------------------------
# Schema-constrained LLM helper with Pydantic validation + single retry
# ---------------------------------------------------------------------------
def _coerce_model(schema_model: Type[BaseModel], raw: Any) -> BaseModel:
    if isinstance(raw, schema_model):
        return schema_model.model_validate(raw.model_dump())
    if isinstance(raw, BaseModel):
        return schema_model.model_validate(raw.model_dump())
    if isinstance(raw, dict):
        return schema_model.model_validate(raw)
    if isinstance(raw, str):
        return schema_model.model_validate_json(raw)
    raise TypeError(f"Cannot coerce {type(raw).__name__} to {schema_model.__name__}")


def _bind_structured_llm(llm: Any, schema_model: Type[BaseModel]) -> Any:
    if not hasattr(llm, "with_structured_output"):
        return llm
    try:
        return llm.with_structured_output(schema_model, method="json_schema", strict=True)
    except TypeError:
        return llm.with_structured_output(schema_model)


def invoke_structured_llm_with_retry(
    intent_id: str,
    user_text: str,
    schema_model: Type[BaseModel],
    llm: Any = None,
) -> Optional[BaseModel]:
    """
    Invokes `llm.with_structured_output(schema_model)` with the minimal chip instruction:
      "Return ONLY the JSON for intent <X>. No prose."
    Validates with Pydantic; on failure retries once with the validation error appended;
    returns None if the retry also fails so the caller falls back to free-text.
    """
    if llm is None:
        try:
            import agent as _agent

            keys = _agent.get_groq_api_keys()
            if keys:
                llm = _agent._build_groq_llm(_agent.GROQ_PREFERRED_MODEL, keys[0], max_tokens=160)
        except Exception:
            llm = None
    if llm is None:
        return None

    sys_prompt = f"Return ONLY the JSON for intent {intent_id}. No prose."
    bound = _bind_structured_llm(llm, schema_model)
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_text or ""},
    ]

    last_err: Optional[Exception] = None
    for attempt in range(2):
        try:
            if attempt == 1 and last_err is not None:
                messages = [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_text or ""},
                    {
                        "role": "user",
                        "content": (
                            f"Validation error: {last_err}. "
                            f"Return ONLY valid JSON matching schema for intent {intent_id}. No prose."
                        ),
                    },
                ]
            raw = bound.invoke(messages)
            return _coerce_model(schema_model, raw)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
    return None


# ---------------------------------------------------------------------------
# Builders (DB-backed)
# ---------------------------------------------------------------------------
def _total_budget(db: Session, user_id: str, book_id: Optional[int]) -> Optional[BudgetInfo]:
    for b in analytics.budget_status(db, user_id, book_id=book_id):
        if b["category"] == "total":
            return BudgetInfo(limit=b["monthly_limit"], remaining=b["remaining_pkr"])
    return None


def build_spend_summary(db: Session, user_id: str, book_id: Optional[int], period: str) -> Dict[str, Any]:
    now = get_pkt_now()
    if period == "last_3_days":
        since = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=2)
    else:
        period = "month"
        since = analytics.month_start(now)

    filters = [
        Transaction.user_id == user_id,
        Transaction.transaction_date >= since,
        Transaction.deleted_at.is_(None),
        analytics._is_expense_clause(),
    ]
    if book_id is not None:
        filters.append(Transaction.book_id == int(book_id))
    total, count = (
        db.query(
            func.coalesce(func.sum(Transaction.total_amount), 0.0),
            func.count(Transaction.id),
        )
        .filter(*filters)
        .one()
    )

    if not count:
        return _make("spend_summary", "empty", SpendSummaryData(period=period, total=0, txn_count=0))

    cats = analytics.category_breakdown(db, user_id, since, book_id=book_id)[:TOP_N]
    data = SpendSummaryData(
        period=period,
        total=round(float(total), 2),
        txn_count=int(count),
        top_categories=[CategoryAmount(name=c["category"], amount=c["total_pkr"]) for c in cats],
        budget=_total_budget(db, user_id, book_id) if period == "month" else None,
    )
    return _make("spend_summary", "ok", data)


def build_galla_summary(db: Session, user_id: str, book_id: Optional[int]) -> Dict[str, Any]:
    g = analytics.galla_summary(db, user_id, book_id=book_id)
    data = GallaSummaryData(
        date=get_pkt_now().strftime("%Y-%m-%d"),
        sales_total=g["today_sales_pkr"],
        expenses_total=g["today_expenses_pkr"],
        cash_in_drawer=g["today_net_pkr"],
    )
    status = "ok" if (data.sales_total or data.expenses_total) else "empty"
    return _make("galla_summary", status, data)


def build_udhaar_summary(db: Session, user_id: str, book_id: Optional[int]) -> Dict[str, Any]:
    u = analytics.udhaar_summary(db, user_id, book_id=book_id)
    recv_by_party: Dict[str, List[Any]] = {}
    pay_by_party: Dict[str, List[Any]] = {}
    for e in u["open_entries"]:
        name = (e.get("person_name") or "").strip() or "Unknown"
        key = name.lower()
        rem = float(e.get("remaining_pkr") or 0.0)
        bucket = pay_by_party if e.get("direction") == "payable" else recv_by_party
        if key in bucket:
            bucket[key][1] = round(bucket[key][1] + rem, 2)
        else:
            bucket[key] = [name, round(rem, 2)]

    recv = [PartyAmount(party=v[0], amount=v[1]) for v in recv_by_party.values()]
    pay = [PartyAmount(party=v[0], amount=v[1]) for v in pay_by_party.values()]
    recv.sort(key=lambda r: -r.amount)
    pay.sort(key=lambda r: -r.amount)
    data = UdhaarSummaryData(
        receivable_total=u["total_receivable_pkr"],
        payable_total=u["total_payable_pkr"],
        receivables=recv[:TOP_N],
        payables=pay[:TOP_N],
    )
    return _make("udhaar_summary", "ok" if u["open_count"] else "empty", data)


def build_budget_need_input() -> Dict[str, Any]:
    return _make("budget_set", "need_input", BudgetNeedInputData(missing=["amount"]))


_URDU_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_STANDALONE_AMOUNT_RE = re.compile(
    r"^\s*(?:pkr|rs\.?|روپے|روپیہ)?\s*(\d[\d,]*(?:\.\d+)?)\s*(k|ہزار|hazar|hazaar|لاکھ|lakh|lac)?\s*(?:pkr|rs\.?|روپے|روپیہ)?\s*$",
    re.IGNORECASE,
)
_BUDGET_CONTEXT_RE = re.compile(
    r"(?:budget|limit|monthly|بجٹ|ماہانہ|حد)",
    re.IGNORECASE,
)
_AMOUNT_WITH_UNIT_RE = re.compile(
    r"(\d[\d,]*(?:\.\d+)?)\s*(k|ہزار|hazar|hazaar|لاکھ|lakh|lac)?",
    re.IGNORECASE,
)


def _scale_amount(num_str: str, unit: Optional[str]) -> Optional[float]:
    try:
        val = float(num_str.replace(",", ""))
    except ValueError:
        return None
    if unit:
        u = unit.lower()
        if u in ("k", "ہزار", "hazar", "hazaar"):
            val *= 1000.0
        elif u in ("لاکھ", "lakh", "lac"):
            val *= 100000.0
    return val if val > 0 else None


def parse_amount(text: Optional[str], strict_context: bool = False) -> Optional[float]:
    """
    Extracts a positive budget amount from text (supports Urdu digits, commas, k/ہزار/لاکھ).
    When `strict_context=True`, only matches standalone numeric replies or strings that mention budget
    keywords, preventing unrelated expenses like 'Spent 500 on chai' from being treated as budgets.
    """
    if not text:
        return None
    norm = text.translate(_URDU_DIGITS).replace("٬", ",").replace("٫", ".").strip()
    m_standalone = _STANDALONE_AMOUNT_RE.match(norm)
    if m_standalone:
        return _scale_amount(m_standalone.group(1), m_standalone.group(2))
    if strict_context and not _BUDGET_CONTEXT_RE.search(norm):
        return None
    m = _AMOUNT_WITH_UNIT_RE.search(norm)
    if not m:
        return None
    return _scale_amount(m.group(1), m.group(2))


def build_budget_set(db: Session, user_id: str, book_id: Optional[int], amount: float) -> Dict[str, Any]:
    if amount is None or float(amount) <= 0:
        raise ValueError("Budget amount must be positive")
    q = db.query(Budget).filter(Budget.user_id == user_id, Budget.category == "total")
    if book_id is not None:
        q = q.filter(Budget.book_id == int(book_id))
    b = q.first()
    previous = float(b.monthly_limit) if b else None
    if b:
        b.monthly_limit = float(amount)
    else:
        db.add(
            Budget(
                user_id=user_id,
                book_id=int(book_id) if book_id is not None else None,
                category="total",
                monthly_limit=float(amount),
            )
        )
    db.commit()
    data = BudgetSetData(
        month=get_pkt_now().strftime("%Y-%m"),
        amount=round(float(amount), 2),
        previous_amount=round(previous, 2) if previous is not None else None,
    )
    return _make("budget_set", "ok", data)


def build_undo_last(db: Session, user_id: str, book_id: Optional[int]) -> Dict[str, Any]:
    q = db.query(Transaction).filter(Transaction.user_id == user_id, Transaction.deleted_at.is_(None))
    if book_id is not None:
        q = q.filter(Transaction.book_id == int(book_id))
    tx = q.order_by(Transaction.transaction_date.desc(), Transaction.id.desc()).first()
    if not tx:
        return _make("undo_last", "empty", EmptyData())
    deleted = DeletedEntry(
        type=tx.tx_type or "expense",
        description=tx.title or tx.store_name or "",
        amount=round(float(tx.total_amount or 0.0), 2),
        date=tx.transaction_date.strftime("%Y-%m-%d"),
    )
    tx.deleted_at = get_pkt_now()
    db.commit()
    return _make("undo_last", "ok", UndoLastData(deleted=deleted))


def _is_3day_query(message: Optional[str]) -> bool:
    if not message:
        return False
    norm = message.translate(_URDU_DIGITS).lower()
    return bool(re.search(r"\b3\s*days?\b|3\s*دن", norm))


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------
def build_intent_response(
    db: Session,
    user_id: str,
    book_id: Optional[int],
    intent_id: str,
    message: Optional[str] = None,
    awaiting_amount: bool = False,
    llm: Any = None,
) -> Optional[Dict[str, Any]]:
    """
    Returns a validated envelope dict, or None when the request must fall back to the free-text agent
    (unknown intent, unparseable budget amount, or any validation/DB failure).
    """
    try:
        if intent_id == "spend_summary":
            period = "last_3_days" if _is_3day_query(message) else "month"
            return build_spend_summary(db, user_id, book_id, period)
        if intent_id == "spend_summary_3d":
            return build_spend_summary(db, user_id, book_id, "last_3_days")
        if intent_id == "galla_summary":
            return build_galla_summary(db, user_id, book_id)
        if intent_id == "udhaar_summary":
            return build_udhaar_summary(db, user_id, book_id)
        if intent_id == "undo_last":
            return build_undo_last(db, user_id, book_id)
        if intent_id == "budget_set":
            amount = parse_amount(message, strict_context=True)
            if amount:
                return build_budget_set(db, user_id, book_id, amount)
            if awaiting_amount:
                parsed = invoke_structured_llm_with_retry("budget_set", message or "", BudgetAmountParse, llm=llm)
                if parsed and parsed.amount and parsed.amount > 0:
                    return build_budget_set(db, user_id, book_id, float(parsed.amount))
                return None
            return build_budget_need_input()
    except (ValidationError, Exception):  # noqa: BLE001 - validation/DB failure → free-text fallback
        db.rollback()
        return None
    return None
