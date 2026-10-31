import os
import re
import json
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

from dotenv import load_dotenv
from groq import Groq
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from langchain_core.tools import tool
from langchain_groq import ChatGroq

try:
    from langchain_aws import ChatBedrockConverse
except ImportError:
    ChatBedrockConverse = None

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage

from database import SessionLocal, Transaction, TransactionItem, Budget, RecurringExpense, PendingTransaction, get_pkt_now
import analytics
from analytics import (
    normalize_payment_method,
    detect_payment_method,
    validate_transaction_amount,
    check_amount_plausibility,
    sanitize_transaction_amount,
    budget_status,
    recurring_status,
    month_start,
    pay_recurring,
)

load_dotenv()
logger = logging.getLogger("hisaab_ai.agent")

# Initialize Groq client for Whisper ASR
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# Default model preference with fallback list matching active Groq account models
PREFERRED_MODEL = os.getenv("GROQ_LLM_MODEL", "openai/gpt-oss-20b")
MODEL_CANDIDATES = [PREFERRED_MODEL, "openai/gpt-oss-20b", "openai/gpt-oss-120b", "qwen/qwen3.8-27b"]

_VALIDATED_MODEL = None


def get_chat_llm():
    """
    Initializes Chat LLM.
    Priority 1: AWS Bedrock (if AWS credentials and/or BEDROCK_MODEL_ID are set).
    Priority 2: Groq Chat LLM with pre-validated model fallback.
    """
    aws_key = os.getenv("AWS_ACCESS_KEY_ID", "").strip()
    aws_secret = os.getenv("AWS_SECRET_ACCESS_KEY", "").strip()
    bedrock_model = os.getenv("BEDROCK_MODEL_ID", "").strip()

    if (aws_key and aws_secret) or bedrock_model:
        if ChatBedrockConverse is not None:
            try:
                model_id = bedrock_model or "us.meta.llama3-3-70b-instruct-v1:0"
                region = os.getenv("AWS_REGION", "us-east-1").strip() or "us-east-1"
                kwargs = {
                    "model": model_id,
                    "region_name": region,
                    "temperature": 0,
                    "max_tokens": 1000,
                }
                if aws_key and aws_secret:
                    kwargs["aws_access_key_id"] = aws_key
                    kwargs["aws_secret_access_key"] = aws_secret

                return ChatBedrockConverse(**kwargs)
            except Exception as e:
                logger.warning(f"Failed to initialize AWS Bedrock LLM ({bedrock_model}): {e}. Falling back to Groq.")

    global _VALIDATED_MODEL
    if _VALIDATED_MODEL:
        return ChatGroq(
            model=_VALIDATED_MODEL,
            temperature=0,
            max_tokens=800,
            max_retries=3,
            groq_api_key=os.getenv("GROQ_API_KEY"),
        )

    for candidate in MODEL_CANDIDATES:
        if not candidate:
            continue
        try:
            test_llm = ChatGroq(
                model=candidate,
                temperature=0,
                max_tokens=10,
                groq_api_key=os.getenv("GROQ_API_KEY"),
            )
            test_llm.invoke("ping")
            _VALIDATED_MODEL = candidate
            logger.info(f"Active Groq LLM model verified: {_VALIDATED_MODEL}")
            return ChatGroq(
                model=_VALIDATED_MODEL,
                temperature=0,
                max_tokens=800,
                max_retries=3,
                groq_api_key=os.getenv("GROQ_API_KEY"),
            )
        except Exception as e:
            logger.info(f"Groq model candidate '{candidate}' not available: {e}")
            continue

    _VALIDATED_MODEL = "qwen/qwen3.8-27b"
    return ChatGroq(
        model=_VALIDATED_MODEL,
        temperature=0,
        max_tokens=800,
        groq_api_key=os.getenv("GROQ_API_KEY"),
    )


# ---------------------------------------------------------------------------
# Speech-to-Text (ASR) Pipeline & Script Normalization
# ---------------------------------------------------------------------------
DEV_GUR_REGEX = re.compile(r"[\u0900-\u097F\u0A00-\u0A7F]")


def ensure_urdu_script(text: str) -> str:
    """
    Guarantees that speech in Urdu or Punjabi uses authentic Pakistani Urdu script
    (Perso-Arabic alphabet / Nastaliq), never Hindi Devanagari or Indian Gurmukhi.
    Converts any detected Devanagari or Gurmukhi text accurately to standard Urdu script.
    """
    if not text or not text.strip():
        return ""

    # Detect Devanagari (Hindi) or Gurmukhi (Eastern Punjabi) characters
    if DEV_GUR_REGEX.search(text):
        try:
            llm = get_chat_llm()
            prompt = (
                "You are an expert Pakistani Urdu linguist for HisaabAI. "
                "Convert the following Hindi/Devanagari or Gurmukhi text into natural Pakistani Urdu Nastaliq script "
                "(Arabic/Perso-Arabic alphabet). Keep numbers and English store/brand names intact or in standard Urdu. "
                "Output ONLY the Urdu script text, with absolutely no preamble, quotes, or explanation:\n\n"
                f"{text.strip()}"
            )
            res = llm.invoke(prompt)
            converted = (res.content or "").strip()
            if converted:
                logger.info(f"Converted Hindi/Gurmukhi text to Urdu script: '{text}' -> '{converted}'")
                return converted
        except Exception as e:
            logger.warning(f"Error converting Hindi/Gurmukhi text to Urdu script: {e}")

    return text


def transcribe_audio(file_bytes: bytes, filename: str = "voice_note.webm") -> str:
    """
    Transcribes audio using Groq whisper-large-v3 with domain prompt injection
    for accurate Pakistani store names, prices, and code-switched Urdu/Punjabi.
    Enforces Pakistani Urdu script (Nastaliq), preventing Hindi Devanagari or Gurmukhi.
    """
    if not file_bytes or len(file_bytes) < 100:
        logger.warning("Empty or very short audio payload received.")
        return ""

    domain_prompt = (
        "یہ پاکستان میں روزمرہ کے اخراجات، گروسری، خریداری، راشن اور بل کا وائس نوٹ ہے۔ "
        "اچھا میرا اس مہینے کا ٹوٹل جو خرچ ہے وہ کتنا ہے؟ "
        "میں آج امتیاز توں 1500 دا کوکنگ آئل تے 450 دی بریڈ لئی۔ "
        "الفتح، جلال سنز، کیارفور، میٹرو، چیس اپ، شاہین کیمسٹ، پٹرول، ڈیزل، دودھ، دہی، انڈے، ڈبل روٹی، چینی، چاول، دال، آٹا۔ "
        "ہمیشہ اردو رسم الخط (Nastaliq script) میں لکھیں، ہندی (Devanagari) یا گرمکھی (Gurmukhi) میں ہرگز نہیں لکھنا۔ "
        "Voice note about daily expenses in Pakistan. Always transcribe Urdu and Punjabi in Urdu script, never in Devanagari or Gurmukhi."
    )

    try:
        transcription = groq_client.audio.transcriptions.create(
            file=(filename, file_bytes),
            model="whisper-large-v3",
            prompt=domain_prompt,
            temperature=0.0,
        )
        raw_text = transcription.text.strip()
        # Guarantee that Urdu/Punjabi text is formatted in authentic Urdu script, never Hindi Devanagari
        return ensure_urdu_script(raw_text)
    except Exception as e:
        logger.error(f"Whisper transcription failed: {e}")
        raise RuntimeError(f"Speech transcription failed: {str(e)}")


# ---------------------------------------------------------------------------
# Pydantic Schemas for Tool Arguments
# ---------------------------------------------------------------------------
class ExpenseItemSchema(BaseModel):
    model_config = {"extra": "ignore"}

    item_name: str = Field(
        description="Canonical lowercase English name of item, e.g. 'milk', 'cooking oil', 'bread', 'petrol', 'eggs'"
    )
    category: str = Field(
        default="groceries",
        description="Category: groceries, dairy, fuel, electronics, dining, utilities, healthcare, clothing, other",
    )
    price: float = Field(
        description="Total cost in PKR (rupees) for this entire line item (e.g. 300 for 2 kg sugar, 400 for 12 eggs). THIS IS THE LINE TOTAL, NOT UNIT PRICE."
    )
    quantity: float = Field(
        default=1.0,
        description="Quantity or count/liters/kg of this item (e.g. 2 for 2 kg)",
    )


class LogExpenseInput(BaseModel):
    model_config = {"extra": "ignore"}

    store_name: str = Field(
        description="Canonical lowercase English store name, e.g. 'imtiaz', 'al-fatah', 'jalal sons', 'carrefour', 'metro', 'csd', 'local store'"
    )
    items: Optional[Any] = Field(
        default=None,
        description="List of purchased items with item_name, price, quantity, category, or a text summary like 'cooking oil: 1500, bread: 450'",
    )
    total_amount: Optional[float] = Field(
        default=None,
        description="Total transaction amount in PKR. If omitted or 0, it will be calculated as sum of items.",
    )
    date_str: Optional[str] = Field(
        default=None,
        description="Date of transaction in YYYY-MM-DD format. Resolves relative terms like 'aaj' (today), 'kal' (yesterday). Defaults to today.",
    )
    payment_method: Optional[str] = Field(
        default="cash",
        description="How it was paid: 'cash' (default), 'easypaisa', 'jazzcash', 'card', 'bank_transfer'. Use 'card' for debit/credit card, 'bank_transfer' for IBFT/Raast/bank app, SadaPay/NayaPay count as 'card'.",
    )


class CancelPendingInput(BaseModel):
    model_config = {"extra": "ignore"}


class SetBudgetInput(BaseModel):
    model_config = {"extra": "ignore"}

    monthly_limit: float = Field(description="Monthly budget limit in PKR")
    category: Optional[str] = Field(
        default="total",
        description="Category the budget applies to (groceries, fuel, dining, utilities...) or 'total' for the overall monthly budget",
    )


class AddRecurringInput(BaseModel):
    model_config = {"extra": "ignore"}

    title: str = Field(description="Name of the obligation, e.g. 'house rent', 'lesco bill', 'bc committee', 'school fee'")
    amount: float = Field(description="Monthly amount in PKR")
    due_day: int = Field(default=1, description="Day of month (1-28) it is due")
    category: Optional[str] = Field(default="utilities", description="Category, e.g. 'utilities', 'rent', 'education', 'other'")


class MarkRecurringPaidInput(BaseModel):
    model_config = {"extra": "ignore"}

    title: str = Field(description="Title (or part of it) of the recurring obligation that was paid this month")


class GetBudgetStatusInput(BaseModel):
    model_config = {"extra": "ignore"}

    refresh: Optional[bool] = Field(
        default=True,
        description="Whether to fetch the latest budget and recurring bill status",
    )


class QueryExpensesInput(BaseModel):
    model_config = {"extra": "ignore"}

    start_date: Optional[str] = Field(
        default=None,
        description="Start date filter in YYYY-MM-DD format (inclusive)",
    )
    end_date: Optional[str] = Field(
        default=None,
        description="End date filter in YYYY-MM-DD format (inclusive)",
    )
    store_name: Optional[str] = Field(
        default=None,
        description="Store name filter (canonical lowercase English, e.g. 'imtiaz')",
    )
    item_name: Optional[str] = Field(
        default=None,
        description="Specific item name to query (canonical lowercase English, e.g. 'milk', 'bread', 'oil')",
    )
    category: Optional[str] = Field(
        default=None,
        description="Category filter, e.g. 'groceries', 'fuel', 'dining'",
    )
    payment_method: Optional[str] = Field(
        default=None,
        description="Payment method filter: 'cash', 'easypaisa', 'jazzcash', 'card', 'bank_transfer'",
    )


class DeleteLastTransactionInput(BaseModel):
    model_config = {"extra": "ignore"}

    confirm: bool = Field(
        default=True,
        description="Must be true to confirm deletion of the most recent transaction",
    )


# ---------------------------------------------------------------------------
# Dynamic System Prompt
# ---------------------------------------------------------------------------
def build_system_prompt() -> str:
    """
    Builds dynamic system prompt injecting current dates and Pakistani context in PKT.
    Optimized for compact token footprint to strictly respect Groq ITPM rate limits.
    """
    now = get_pkt_now()
    today_str = now.strftime("%Y-%m-%d")
    day_name = now.strftime("%A")
    month_start_str = now.strftime("%Y-%m-01")
    yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    two_days_ago_str = (now - timedelta(days=2)).strftime("%Y-%m-%d")
    three_days_ago_str = (now - timedelta(days=3)).strftime("%Y-%m-%d")
    seven_days_ago_str = (now - timedelta(days=7)).strftime("%Y-%m-%d")

    return f"""You are **HisaabAI**, a brilliant, polite, and accurate financial voice & text expense tracking assistant for Pakistan.

### PRIMARY LANGUAGE MANDATE (STRICT):
1. **English Input:** If user communicates in English (voice or text), ALWAYS reply in 100% clear English.
2. **Urdu Input:** If user communicates in Urdu (script or Roman), ALWAYS reply in polite, natural Pakistani Urdu script (اردو رسم الخط / Nastaliq font).
3. **Punjabi Input:** If user communicates in Punjabi (spoken, Roman, or Shahmukhi), ALWAYS reply in polite, natural Pakistani Urdu script (اردو رسم الخط / Nastaliq font).
4. **ABSOLUTE PROHIBITIONS:**
   - NEVER output Hindi / Devanagari script (e.g., अच्छा, खर्च, कितना...).
   - NEVER output Arabic vocabulary/grammar (e.g., تم تسجيل المصروفات, شكراً).
   - NEVER output Gurmukhi script (e.g., ਮੈਂ ਤੁਹਾਡا ਖਰਚਾ ਦਰਜ ਕਰ ਰਿਹਾ ਹਾਂ...).
   - Hindi, Arabic, and Gurmukhi are strictly forbidden in assistant responses.
   - For ANY Urdu or Punjabi input (voice or text, whether in Urdu script, Roman Urdu/Punjabi, or Punjabi phrasing): ALWAYS respond in natural, polite **Pakistani Urdu script (اردو)**. NEVER use Hindi, Arabic, or Gurmukhi!
   - For ANY English input (voice or text): ALWAYS respond in clear, professional **English**.

### CALENDAR CONTEXT:
- Today: {today_str} ({day_name}) | Month Start: {month_start_str} | Yesterday: {yesterday_str}
- 2 Days Ago: {two_days_ago_str} | 3 Days Ago: {three_days_ago_str} | 7 Days Ago: {seven_days_ago_str}

### MANDATORY TOOL CALLING & DATABASE INTEGRITY:
You are equipped with real SQLite database tools: `log_expense`, `query_expenses`, `delete_last_transaction`, `set_budget`, `get_budget_status`, `add_recurring_expense`, `mark_recurring_paid`, and `cancel_pending_expense`.
1. **EXPENSE LOGGING:** When expense is stated/implied, YOUR FIRST ACTION MUST BE TO CALL `log_expense`. Never confirm without calling it! Extract `store_name` (canonical lowercase English, default 'local store'), `items` list (with `item_name`, `price`, `quantity`, `category`), `date_str`, `payment_method` (default 'cash'; detect 'card', 'easypaisa', 'jazzcash', 'bank_transfer'). For multi-store messages, call `log_expense` once for each store.
2. **SPENDING QUERIES (ZERO MENTAL MATH):** When asked for totals or hisaab, ALWAYS call `query_expenses`. NEVER calculate sums from chat history. Report exact tool numbers.
3. **UNDO / DELETE:** "delete my last entry" / "undo" -> CALL `delete_last_transaction`.
4. **BUDGETS:** "mera mahana budget 50000 hai" -> CALL `set_budget`. "kitna bacha hai" -> CALL `get_budget_status`. Quote tool percentage alerts.
5. **RECURRING BILLS:** "har mahine kiraya" -> CALL `add_recurring_expense`. "bill ada kar diya" -> CALL `mark_recurring_paid`. Do not ask for amount when paying; tool has it.
6. **LARGE EXPENSE CONFIRMATION:** If `log_expense` returns status `needs_confirmation`, transaction is NOT saved. Ask user to click the on-screen 'Confirm' (تصدیق کریں) button. If user says cancel/nahi, call `cancel_pending_expense`.
7. **TRANSACTION ISOLATION:** Every message is independent. NEVER copy items from past messages into new transactions.

### PAKISTANI NUMBER & PHRASE RESOLUTION:
- **Numbers:** dedh sau=150, paune do sau=175, dhai sau=250, sadhe teen sau=350, sadhe chaar sau=450, ath sau=800, nau sau=900, baara sau=1200, pandra sau=1500, hazaar=1000, dedh hazaar=1500, paune do hazaar=1750, dhai hazaar=2500, sadhe teen hazaar=3500, sadhe chaar hazaar=4500, sadhe paanch hazaar=5500, das hazaar=10000, veeh hazaar=20000, panjah hazaar=50000, ik lakh=100000, dedh lakh=150000, dhai lakh=250000, 60 lakh=6000000, 200 lakh=20000000, 6000 hazaar=6000000, 20000 lakh=2000000000, crore=10000000.
- **Punjabi:** ton=from ("Imtiaz ton lita"), lita/layi/khareedya=bought, ditte=paid, pwaya=refilled, peeti/khada=consumed, hoya/banya=totaled ("kinna bill hoya?"), kharcha/hisaab=expense.
- **Stores/Categories:** imtiaz, al-fatah, jalal sons, carrefour, metro, csd, shaheen, local store, petrol pump, dairy shop, sabzi market, butcher shop. Categories: groceries, dairy, fuel, utilities, dining, healthcare, clothing, transport, personal, transfer.
- **Relative Dates:** "aaj" / "today" -> {today_str}, "kal" / "yesterday" -> {yesterday_str}, "pichle 2 din" -> start_date: {two_days_ago_str}, "pichle 3 din" -> start_date: {three_days_ago_str}, "is hafte" -> start_date: {seven_days_ago_str}, "is mahine" -> start_date: {month_start_str}, end_date: {today_str}.
- **Currency & Tone:** Always Pakistani Rupees (PKR / روپے). Concise, polite, friendly confirmation.
"""


# ---------------------------------------------------------------------------
# Tool Factory Function (Tenant Isolation via Closure)
# ---------------------------------------------------------------------------
def get_tools_for_user(user_id: str, raw_transcript: str = "") -> list:
    """
    Creates user-scoped LangChain tools bound via Python closure to user_id.
    Guarantees strict tenant isolation: the LLM never supplies or controls user_id.
    """

    @tool("log_expense", args_schema=LogExpenseInput)
    def log_expense(
        store_name: str,
        items: Optional[Any] = None,
        total_amount: Optional[float] = None,
        date_str: Optional[str] = None,
        payment_method: Optional[str] = "cash",
    ) -> str:
        """Logs an expense transaction with line items into the database for the current user."""
        db: Session = SessionLocal()
        try:
            canonical_store = (store_name or "general store").strip().lower()

            # Resolve transaction date in PKT
            tx_date = get_pkt_now()
            if date_str:
                try:
                    parsed_date = datetime.strptime(date_str.strip(), "%Y-%m-%d")
                    now_pkt = get_pkt_now()
                    tx_date = parsed_date.replace(
                        hour=now_pkt.hour,
                        minute=now_pkt.minute,
                        second=now_pkt.second,
                    )
                except Exception:
                    pass

            # Ingest and normalize items from diverse formats (list of dicts, list of objects, json string, or text summary)
            parsed_items = []
            if isinstance(items, str):
                try:
                    loaded = json.loads(items)
                    items = loaded
                except Exception:
                    # Parse as text summary e.g. "cooking oil: 1500, bread: 450"
                    import re
                    parts = re.split(r'[,;\n]+', items)
                    for p in parts:
                        p = p.strip()
                        if not p:
                            continue
                        m = re.search(r'([a-zA-Z\s]+?)(?:[:=\-]?\s*(?:rs|pkr)?\s*(\d+(?:\.\d+)?))?$', p, re.I)
                        if m and m.group(1).strip():
                            p_name = m.group(1).strip().lower()
                            p_price = float(m.group(2)) if m.group(2) else 0.0
                            parsed_items.append({
                                "item_name": p_name,
                                "price": p_price,
                                "quantity": 1.0,
                                "category": "groceries"
                            })

            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        p_name = str(item.get("item_name") or item.get("name") or "item").strip().lower()
                        p_price = float(item.get("price") or 0.0)
                        p_qty = float(item.get("quantity") or 1.0)
                        p_cat = str(item.get("category") or "groceries").strip().lower()
                        parsed_items.append({
                            "item_name": p_name,
                            "price": p_price,
                            "quantity": p_qty,
                            "category": p_cat
                        })
                    elif hasattr(item, "item_name"):
                        parsed_items.append({
                            "item_name": item.item_name.strip().lower(),
                            "price": float(item.price),
                            "quantity": float(item.quantity) if float(item.quantity) > 0 else 1.0,
                            "category": str(item.category or "groceries").strip().lower()
                        })

            # Compute exact sum of parsed line items deterministically (100% precision)
            computed_sum = sum(
                float(it["price"])
                for it in parsed_items
            )

            # Reconcile total_amount and parsed_items
            if total_amount is not None and float(total_amount) > 0:
                user_total = float(total_amount)
                # If parsed_items sum differs drastically from user_total (e.g. ghost items from prior turns):
                if computed_sum > 0 and abs(computed_sum - user_total) > 0.05 * user_total:
                    matching_items = [it for it in parsed_items if abs(it["price"] - user_total) < 1.0]
                    if matching_items:
                        parsed_items = matching_items
                        final_total = user_total
                    else:
                        final_total = user_total
                elif computed_sum > 0:
                    final_total = computed_sum
                else:
                    final_total = user_total
            elif computed_sum > 0:
                final_total = computed_sum
            else:
                final_total = 0.0

            # Resolve payment method & validate amount deterministically
            final_pm = detect_payment_method(payment_method, raw_transcript)
            try:
                final_total = validate_transaction_amount(final_total)
            except ValueError as ve:
                return json.dumps({"status": "error", "message": str(ve)})

            # Check plausibility: flag values > 10,000,000 PKR, or > 1,000,000 for easypaisa/jazzcash
            # Server-held state: store in PendingTransaction table so LLM cannot bypass via hallucinated arguments
            plausibility = check_amount_plausibility(final_total, final_pm)
            if plausibility["needs_confirmation"]:
                db.query(PendingTransaction).filter(PendingTransaction.user_id == user_id).delete()
                expires_at = get_pkt_now() + timedelta(seconds=300)
                pending = PendingTransaction(
                    user_id=user_id,
                    store_name=canonical_store,
                    total_amount=round(final_total, 2),
                    payment_method=final_pm,
                    date_str=date_str,
                    raw_transcript=raw_transcript or None,
                    items_json=json.dumps(parsed_items) if parsed_items else None,
                    created_at=get_pkt_now(),
                    expires_at=expires_at,
                )
                db.add(pending)
                db.commit()
                return json.dumps({
                    "status": "needs_confirmation",
                    "pending_id": pending.id,
                    "amount": round(final_total, 2),
                    "payment_method": final_pm,
                    "store_name": canonical_store,
                    "message": plausibility["reason"],
                    "expires_in_seconds": 300,
                    "instruction": "This expense is held in pending status on the server. Tell the user in their language that this large amount is pending confirmation and they must press the 'Confirm' button on their screen to record it, or say cancel. If they decline or say cancel, execute cancel_pending_expense. Do NOT attempt to confirm via chat."
                }, ensure_ascii=False)

            # If it was a money transfer and store defaulted to 'local store', assign clean transfer store
            if raw_transcript and any(w in raw_transcript.lower() for w in ("transfer", "bheje", "bheja", "send")):
                if canonical_store in ("local store", "general store", "general purchase"):
                    canonical_store = f"{final_pm} transfer" if final_pm != "cash" else "money transfer"

            # Create Transaction record
            tx = Transaction(
                user_id=user_id,
                store_name=canonical_store,
                total_amount=round(final_total, 2),
                transaction_date=tx_date,
                raw_transcript=raw_transcript or None,
                payment_method=final_pm,
            )
            db.add(tx)
            db.flush()  # Populates tx.id

            # Create TransactionItem records
            logged_items = []
            if parsed_items:
                # If single item and price was distorted by unit collision, sync with final_total
                if len(parsed_items) == 1 and abs(parsed_items[0]["price"] - final_total) > 1.0:
                    parsed_items[0]["price"] = final_total
                for it in parsed_items:
                    t_item = TransactionItem(
                        transaction_id=tx.id,
                        item_name=it["item_name"],
                        category=it["category"],
                        price=round(it["price"], 2),
                        quantity=it["quantity"],
                    )
                    db.add(t_item)
                    logged_items.append(
                        {
                            "item": t_item.item_name,
                            "price": t_item.price,
                            "quantity": t_item.quantity,
                            "category": t_item.category,
                        }
                    )
            elif final_total > 0:
                # User logged total without specifying line items
                t_item = TransactionItem(
                    transaction_id=tx.id,
                    item_name="general purchase",
                    category="other",
                    price=round(final_total, 2),
                    quantity=1.0,
                )
                db.add(t_item)
                logged_items.append(
                    {
                        "item": "general purchase",
                        "price": final_total,
                        "quantity": 1.0,
                        "category": "other",
                    }
                )

            db.commit()
            db.refresh(tx)

            # Deterministic budget alerts (computed by SQL, reported verbatim by the agent)
            alerts = [
                b for b in budget_status(db, user_id) if b["level"] in ("warning", "over")
            ]

            result = {
                "status": "success",
                "message": "Expense successfully recorded.",
                "transaction_id": tx.id,
                "user_id": user_id,
                "store_name": canonical_store,
                "total_amount_pkr": round(final_total, 2),
                "payment_method": tx.payment_method,
                "date": tx.transaction_date.strftime("%Y-%m-%d"),
                "items_count": len(logged_items),
                "items": logged_items,
                "budget_alerts": alerts,
            }
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            db.rollback()
            logger.error(f"Error logging expense for user {user_id}: {e}")
            return json.dumps({"status": "error", "message": str(e)})
        finally:
            db.close()

    @tool("query_expenses", args_schema=QueryExpensesInput)
    def query_expenses(
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        store_name: Optional[str] = None,
        item_name: Optional[str] = None,
        category: Optional[str] = None,
        payment_method: Optional[str] = None,
    ) -> str:
        """Queries expenses and totals for the current user with date, store, item, category, or payment-method filters."""
        db: Session = SessionLocal()
        try:
            query = db.query(Transaction).filter(
                Transaction.user_id == user_id,
                Transaction.deleted_at.is_(None),
            )

            # Apply date filters
            if start_date:
                try:
                    start_dt = datetime.strptime(start_date.strip(), "%Y-%m-%d").replace(
                        hour=0, minute=0, second=0
                    )
                    query = query.filter(Transaction.transaction_date >= start_dt)
                except Exception:
                    pass

            if end_date:
                try:
                    end_dt = datetime.strptime(end_date.strip(), "%Y-%m-%d").replace(
                        hour=23, minute=59, second=59
                    )
                    query = query.filter(Transaction.transaction_date <= end_dt)
                except Exception:
                    pass

            # Store filter
            if store_name:
                cleaned_store = store_name.strip().lower()
                query = query.filter(Transaction.store_name.ilike(f"%{cleaned_store}%"))

            # Item or Category filter
            if item_name:
                cleaned_item = item_name.strip().lower()
                query = query.filter(
                    Transaction.items.any(
                        TransactionItem.item_name.ilike(f"%{cleaned_item}%")
                    )
                )

            if category:
                cleaned_cat = category.strip().lower()
                query = query.filter(
                    Transaction.items.any(
                        TransactionItem.category.ilike(f"%{cleaned_cat}%")
                    )
                )

            if payment_method:
                query = query.filter(
                    Transaction.payment_method == normalize_payment_method(payment_method)
                )

            transactions = query.order_by(Transaction.transaction_date.desc()).all()

            total_spent = sum(tx.total_amount for tx in transactions)
            store_totals: Dict[str, float] = {}
            for tx in transactions:
                store_totals[tx.store_name] = store_totals.get(tx.store_name, 0.0) + tx.total_amount

            # Compute deterministic category totals
            category_totals: Dict[str, float] = {}
            for tx in transactions:
                for it in tx.items:
                    cat = it.category or "other"
                    category_totals[cat] = category_totals.get(cat, 0.0) + it.price

            # If querying for a specific item, compute deterministic item-specific spending
            item_specific_total = 0.0
            if item_name:
                cleaned_item = item_name.strip().lower()
                for tx in transactions:
                    for it in tx.items:
                        if cleaned_item in it.item_name.lower():
                            item_specific_total += it.price

            # Deterministic spending split per payment method
            payment_totals: Dict[str, float] = {}
            for tx in transactions:
                pm = tx.payment_method or "cash"
                payment_totals[pm] = payment_totals.get(pm, 0.0) + tx.total_amount

            tx_list = []
            for tx in transactions[:15]:  # Return up to 15 recent matching transactions
                tx_list.append(
                    {
                        "id": tx.id,
                        "store_name": tx.store_name,
                        "total_amount_pkr": round(tx.total_amount, 2),
                        "payment_method": tx.payment_method or "cash",
                        "date": tx.transaction_date.strftime("%Y-%m-%d"),
                        "items": [
                            {
                                "item": item.item_name,
                                "price": item.price,
                                "quantity": item.quantity,
                                "category": item.category,
                            }
                            for item in tx.items
                        ],
                    }
                )

            recent_tx_list = tx_list[-6:] if len(tx_list) > 6 else tx_list
            payload = {
                "status": "success",
                "user_id": user_id,
                "visit_count": len(transactions),
                "total_spent_pkr": round(total_spent, 2),
                "spending_by_store": {k: round(v, 2) for k, v in store_totals.items()},
                "spending_by_category": {k: round(v, 2) for k, v in category_totals.items()},
                "spending_by_payment_method": {k: round(v, 2) for k, v in payment_totals.items()},
                "transactions": recent_tx_list,
            }

            if item_name:
                payload["queried_item"] = item_name
                payload["item_specific_total_pkr"] = round(item_specific_total, 2)

            return json.dumps(payload, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error querying expenses for user {user_id}: {e}")
            return json.dumps({"status": "error", "message": str(e)})
        finally:
            db.close()

    @tool("delete_last_transaction", args_schema=DeleteLastTransactionInput)
    def delete_last_transaction(confirm: bool = True) -> str:
        """Soft-deletes the most recent expense transaction logged for the current user."""
        db: Session = SessionLocal()
        try:
            tx = (
                db.query(Transaction)
                .filter(Transaction.user_id == user_id, Transaction.deleted_at.is_(None))
                .order_by(Transaction.transaction_date.desc(), Transaction.id.desc())
                .first()
            )

            if not tx:
                return json.dumps(
                    {
                        "status": "not_found",
                        "message": "No transaction records found to delete for this user.",
                    }
                )

            deleted_info = {
                "id": tx.id,
                "store_name": tx.store_name,
                "total_amount_pkr": round(tx.total_amount, 2),
                "date": tx.transaction_date.strftime("%Y-%m-%d"),
            }

            tx.deleted_at = get_pkt_now()
            db.commit()

            return json.dumps(
                {
                    "status": "success",
                    "message": "Most recent transaction was successfully deleted.",
                    "deleted_transaction": deleted_info,
                },
                ensure_ascii=False,
            )
        except Exception as e:
            db.rollback()
            logger.error(f"Error deleting last transaction for user {user_id}: {e}")
            return json.dumps({"status": "error", "message": str(e)})
        finally:
            db.close()

    @tool("set_budget", args_schema=SetBudgetInput)
    def set_budget(monthly_limit: float, category: Optional[str] = "total") -> str:
        """Sets (or updates) the monthly budget for the current user, overall ('total') or for one category, and returns live usage."""
        db: Session = SessionLocal()
        try:
            cat = (category or "total").strip().lower()
            if cat in ("overall", "all", "kul", "monthly"):
                cat = "total"
            if monthly_limit is None or float(monthly_limit) <= 0:
                return json.dumps({"status": "error", "message": "Budget must be greater than 0."})
            b = (
                db.query(Budget)
                .filter(Budget.user_id == user_id, Budget.category == cat)
                .first()
            )
            if b:
                b.monthly_limit = float(monthly_limit)
            else:
                db.add(Budget(user_id=user_id, category=cat, monthly_limit=float(monthly_limit)))
            db.commit()
            status = [s for s in budget_status(db, user_id) if s["category"] == cat]
            return json.dumps(
                {"status": "success", "message": "Budget saved.", "budget": status[0] if status else None},
                ensure_ascii=False,
            )
        except Exception as e:
            db.rollback()
            logger.error(f"Error setting budget for user {user_id}: {e}")
            return json.dumps({"status": "error", "message": str(e)})
        finally:
            db.close()

    @tool("get_budget_status", args_schema=GetBudgetStatusInput)
    def get_budget_status(refresh: bool = True) -> str:
        """Returns spent / limit / remaining / percent for every budget and the status of recurring monthly bills (paid or pending)."""
        db: Session = SessionLocal()
        try:
            return json.dumps(
                {
                    "status": "success",
                    "budgets": budget_status(db, user_id),
                    "recurring": recurring_status(db, user_id),
                },
                ensure_ascii=False,
            )
        except Exception as e:
            logger.error(f"Error reading budgets for user {user_id}: {e}")
            return json.dumps({"status": "error", "message": str(e)})
        finally:
            db.close()

    @tool("add_recurring_expense", args_schema=AddRecurringInput)
    def add_recurring_expense(
        title: str,
        amount: float,
        due_day: int = 1,
        category: Optional[str] = "utilities",
    ) -> str:
        """Registers a monthly recurring obligation (rent, bill, school fee, BC committee) with its due day."""
        db: Session = SessionLocal()
        try:
            clean_title = (title or "").strip().lower()
            if not clean_title or amount is None or float(amount) <= 0:
                return json.dumps({"status": "error", "message": "Title and a positive amount are required."})
            day = min(max(int(due_day or 1), 1), 28)
            existing = (
                db.query(RecurringExpense)
                .filter(RecurringExpense.user_id == user_id, RecurringExpense.title == clean_title)
                .first()
            )
            if existing:
                existing.amount = float(amount)
                existing.due_day = day
                existing.category = (category or "utilities").strip().lower()
            else:
                db.add(
                    RecurringExpense(
                        user_id=user_id,
                        title=clean_title,
                        amount=float(amount),
                        due_day=day,
                        category=(category or "utilities").strip().lower(),
                    )
                )
            db.commit()
            return json.dumps(
                {"status": "success", "message": "Recurring expense saved.", "recurring": recurring_status(db, user_id)},
                ensure_ascii=False,
            )
        except Exception as e:
            db.rollback()
            logger.error(f"Error adding recurring expense for user {user_id}: {e}")
            return json.dumps({"status": "error", "message": str(e)})
        finally:
            db.close()

    @tool("mark_recurring_paid", args_schema=MarkRecurringPaidInput)
    def mark_recurring_paid(title: str) -> str:
        """Marks a recurring obligation as paid for the current month AND automatically logs it as an expense using its saved amount. NEVER ask the user for the amount."""
        db: Session = SessionLocal()
        try:
            needle = (title or "").strip().lower()
            item = (
                db.query(RecurringExpense)
                .filter(RecurringExpense.user_id == user_id, RecurringExpense.title.ilike(f"%{needle}%"))
                .first()
            )
            if not item:
                return json.dumps({"status": "not_found", "message": f"No recurring expense matching '{title}'."})
            return json.dumps(
                pay_recurring(db, user_id, item, raw_transcript=raw_transcript),
                ensure_ascii=False,
            )
        except Exception as e:
            db.rollback()
            logger.error(f"Error marking recurring paid for user {user_id}: {e}")
            return json.dumps({"status": "error", "message": str(e)})
        finally:
            db.close()

    @tool("cancel_pending_expense", args_schema=CancelPendingInput)
    def cancel_pending_expense() -> str:
        """Cancels and discards the currently pending expense transaction for the user."""
        db: Session = SessionLocal()
        try:
            res = analytics.cancel_pending_transaction(db, user_id)
            return json.dumps(res, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error cancelling pending expense for {user_id}: {e}")
            return json.dumps({"status": "error", "message": "Failed to cancel pending expense."})
        finally:
            db.close()

    return [
        log_expense,
        query_expenses,
        delete_last_transaction,
        set_budget,
        get_budget_status,
        add_recurring_expense,
        mark_recurring_paid,
        cancel_pending_expense,
    ]


# ---------------------------------------------------------------------------
# Agent Invocation Runner with Multi-Turn Memory
# ---------------------------------------------------------------------------
_IN_MEMORY_SESSIONS: Dict[str, List[Any]] = {}


def clear_user_history(user_id: str):
    """Clears in-memory session history for user."""
    clean_id = (user_id or "default").strip()
    _IN_MEMORY_SESSIONS.pop(clean_id, None)


def run_agent(
    user_id: str,
    user_text: str,
    raw_transcript: str = "",
    chat_history: Optional[List[Dict[str, str]]] = None,
) -> str:
    """
    Executes the LangChain agent for the given user, scoping tools to user_id.
    Maintains and respects multi-turn conversation history across turns.
    """
    if not user_text or not user_text.strip():
        return "Please provide an expense message or voice note."

    clean_user_id = user_id.strip() or "default"
    tools = get_tools_for_user(clean_user_id, raw_transcript=raw_transcript)
    system_prompt = build_system_prompt()
    llm = get_chat_llm()

    # Construct conversation history (treat client-supplied history as untrusted)
    messages = []
    if chat_history and isinstance(chat_history, list):
        sanitized_history = []
        for msg in chat_history:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            content = msg.get("content")
            if role not in ("user", "assistant") or not isinstance(content, str):
                continue
            cleaned_content = content.strip()[:1000]
            if not cleaned_content:
                continue
            sanitized_history.append((role, cleaned_content))
        
        # Enforce max 4 messages
        for role, content in sanitized_history[-4:]:
            if role == "user":
                messages.append(HumanMessage(content=content))
            elif role == "assistant":
                from langchain_core.messages import AIMessage
                messages.append(AIMessage(content=content))
    elif clean_user_id in _IN_MEMORY_SESSIONS:
        # Fallback to server in-memory history (last 4 turns)
        messages.extend(_IN_MEMORY_SESSIONS[clean_user_id][-4:])

    # Append current turn
    messages.append(HumanMessage(content=user_text))

    # Create agent using modern LangChain agent API
    agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=system_prompt,
    )

    try:
        response = agent.invoke({"messages": messages})
        final_message = response["messages"][-1]
        reply_text = final_message.content if hasattr(final_message, "content") else str(final_message)
        reply_text = ensure_urdu_script(reply_text)

        # Update in-memory session history
        from langchain_core.messages import AIMessage
        if clean_user_id not in _IN_MEMORY_SESSIONS:
            _IN_MEMORY_SESSIONS[clean_user_id] = []
        _IN_MEMORY_SESSIONS[clean_user_id].append(HumanMessage(content=user_text))
        _IN_MEMORY_SESSIONS[clean_user_id].append(AIMessage(content=reply_text))
        if len(_IN_MEMORY_SESSIONS[clean_user_id]) > 20:
            _IN_MEMORY_SESSIONS[clean_user_id] = _IN_MEMORY_SESSIONS[clean_user_id][-20:]

        return reply_text
    except Exception as e:
        logger.error(f"Agent execution error: {e}")
        # Graceful fallback attempt
        try:
            llm_fallback = ChatGroq(
                model="qwen/qwen3.8-27b",
                temperature=0,
                max_tokens=600,
                groq_api_key=os.getenv("GROQ_API_KEY"),
            )
            fallback_agent = create_agent(
                model=llm_fallback,
                tools=tools,
                system_prompt=system_prompt,
            )
            fallback_res = fallback_agent.invoke({"messages": messages})
            fallback_reply = fallback_res["messages"][-1].content
            return fallback_reply
        except Exception as e2:
            logger.error(f"Fallback agent execution error: {e2}")
            return f"Maafi chahte hain, processing ke dauraan masla pesh aya: {str(e2)}"

