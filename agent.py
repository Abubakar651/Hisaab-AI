import os
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
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage

from database import SessionLocal, Transaction, TransactionItem

load_dotenv()
logger = logging.getLogger("hisaab_ai.agent")

# Initialize Groq client for Whisper ASR
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# Default model preference with fallback list
PREFERRED_MODEL = os.getenv("GROQ_LLM_MODEL", "llama-3.3-70b-versatile")
MODEL_CANDIDATES = [PREFERRED_MODEL, "qwen/qwen3.8-27b", "openai/gpt-oss-120b"]

_VALIDATED_MODEL = None


def get_chat_llm() -> ChatGroq:
    """
    Initializes Groq Chat LLM with pre-validated model caching.
    """
    global _VALIDATED_MODEL
    if _VALIDATED_MODEL:
        return ChatGroq(
            model=_VALIDATED_MODEL,
            temperature=0,
            max_tokens=800,
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
# Speech-to-Text (ASR) Pipeline
# ---------------------------------------------------------------------------
def transcribe_audio(file_bytes: bytes, filename: str = "voice_note.webm") -> str:
    """
    Transcribes audio using Groq whisper-large-v3 with domain prompt injection
    for accurate Pakistani store names, prices, and code-switched Urdu/Punjabi.
    """
    if not file_bytes or len(file_bytes) < 100:
        logger.warning("Empty or very short audio payload received.")
        return ""

    domain_prompt = (
        "Voice note in Urdu, Punjabi, Roman Urdu, or English about shopping and daily expenses "
        "in Pakistan, mentioning stores like Imtiaz, Al-Fatah, Jalal Sons, Carrefour, Metro, CSD, "
        "and prices in PKR / rupees."
    )

    try:
        transcription = groq_client.audio.transcriptions.create(
            file=(filename, file_bytes),
            model="whisper-large-v3",
            prompt=domain_prompt,
            temperature=0.0,
        )
        return transcription.text.strip()
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
        description="Total or unit price for this item in PKR (rupees)"
    )
    quantity: float = Field(
        default=1.0,
        description="Quantity or count/liters/kg of this item",
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
    Builds dynamic system prompt injecting current dates and Pakistani context.
    """
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    day_name = now.strftime("%A")
    month_start_str = now.strftime("%Y-%m-01")
    yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    two_days_ago_str = (now - timedelta(days=2)).strftime("%Y-%m-%d")
    three_days_ago_str = (now - timedelta(days=3)).strftime("%Y-%m-%d")
    seven_days_ago_str = (now - timedelta(days=7)).strftime("%Y-%m-%d")

    return f"""You are **HisaabAI**, a brilliant, polite, and accurate financial voice & text expense tracking assistant for Pakistan.

### CURRENT CALENDAR CONTEXT:
- Today's Date: {today_str} ({day_name})
- Start of This Month: {month_start_str}
- Yesterday: {yesterday_str}
- 2 Days Ago: {two_days_ago_str}
- 3 Days Ago: {three_days_ago_str}
- 7 Days Ago: {seven_days_ago_str}

### MANDATORY TOOL CALLING:
You are equipped with real SQLite database tools: `log_expense`, `query_expenses`, and `delete_last_transaction`.
1. **WHEN AN EXPENSE IS MENTIONED:**
   - Any time the user states or implies buying items, spending money, or shopping:
     **YOU MUST CALL `log_expense`**.
   - NEVER pretend to record it without executing the tool!
   - Extract `store_name` (canonical lowercase English), `items` list (with `item_name`, `price`, `quantity`, `category`), and `date_str`.
2. **WHEN SPENDING IS QUERIED:**
   - Any time the user asks how much they spent, totals, or checks their ledger:
     **YOU MUST CALL `query_expenses`** with appropriate filters.
3. **WHEN UNDO / DELETE IS REQUESTED:**
   - Any time user asks to delete their last entry ("delete my last entry", "pichla record mita do", "ghalat likha gaya"):
     **YOU MUST CALL `delete_last_transaction`**.

### SMART DEFAULTS & USER-FRIENDLY BEHAVIOR:
- **DO NOT INTERROGATE THE USER:** Never refuse to log an expense or repeatedly question the user if they did not provide a branded store name or exact item breakdown.
- **Default Store:** In Pakistan, most people buy from street vendors, open markets, or local neighbourhood shops. If the user does not specify a store name, DO NOT ask them to name a store. Instead, use a sensible default:
  * By default: `"local store"`
  * If item is vegetables/fruits ("sabzi", "phal"): `"local store"` or `"sabzi market"`
  * If item is milk/yogurt ("doodh", "dahi"): `"dairy shop"`
  * If item is fuel ("petrol"): `"petrol pump"`
- **Default Item:** If the user specifies an amount and store or general shopping without item details (e.g., "1500 da saman", "1500 ka kharcha"):
  * Default item: `"general items"` or `"groceries"`
- **Phrases like "bs enj hi add krle" / "aise hi add kar lo" / "just add it" / "record kar lo":**
  * This is an explicit command from the user to stop asking questions and immediately save the expense.
  * Combine the numbers and items mentioned in previous conversation messages and CALL `log_expense` IMMEDIATELY with sensible defaults.
- **Multi-Turn Context Retention:**
  * When reading previous conversation messages, maintain context! If the user mentioned "1500" in turn 1 and "sabzia" in turn 2, combine them to log Rs. 1500 for vegetables!

### CRITICAL RULES:

1. **MULTILINGUAL SCRIPT & LANGUAGE MIRRORING:**
   - Detect the language, dialect, and script used by the user in their request and reply in the **EXACT SAME LANGUAGE AND SCRIPT STYLE**:
     * **Urdu Script (اردو):** If the user types or speaks in Urdu script (e.g. "آج میں نے امتیاز سے 1500 کا سامان خریدا"), reply in respectful, natural Urdu script.
     * **Roman Urdu / Roman Punjabi:** If the user writes or speaks Roman Urdu or Roman Punjabi (e.g., "Main aaj Imtiaz ton 1500 da cooking oil te 450 di bread layi", "Mera is mahine Imtiaz te kinna kharcha hoya?"), reply in fluent, natural Roman Urdu/Roman Punjabi.
     * **English:** If the user speaks/writes in English, reply in clear, professional English.
     * **Punjabi (Gurmukhi or Shahmukhi):** If written in Punjabi script, reply in Punjabi.

2. **CANONICAL ENGLISH FOR DATABASE TOOLS:**
   - Even though you converse in the user's language, ALWAYS convert store names and item names to **clean, canonical lowercase English** before passing them into tools:
     * Examples of stores: "imtiaz", "al-fatah", "jalal sons", "carrefour", "metro", "csd", "shaheen", "local store".
     * Examples of items: "cooking oil", "milk", "bread", "eggs", "chicken", "flour", "rice", "sugar", "petrol", "tea", "fruits", "vegetables".
     * Categories: "groceries", "dairy", "fuel", "electronics", "dining", "utilities", "healthcare", "clothing", "other".

3. **RELATIVE DATE RESOLUTION:**
   - "aaj" / "today" -> {today_str}
   - "kal" / "yesterday" -> {yesterday_str}
   - "pichle 2 din" / "last 2 days" -> start_date: {two_days_ago_str}, end_date: {today_str}
   - "pichle 3 din" / "last 3 days" -> start_date: {three_days_ago_str}, end_date: {today_str}
   - "is hafte" / "this week" -> start_date: {seven_days_ago_str}, end_date: {today_str}
   - "is mahine" / "this month" -> start_date: {month_start_str}, end_date: {today_str}

4. **CURRENCY & TONE:**
   - Currency is always Pakistani Rupees (PKR / روپے).
   - Be concise, friendly, and helpful. Format confirmation with the store name, items breakdown, and total.
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
    ) -> str:
        """Logs an expense transaction with line items into the database for the current user."""
        db: Session = SessionLocal()
        try:
            canonical_store = (store_name or "general store").strip().lower()

            # Resolve transaction date
            tx_date = datetime.now()
            if date_str:
                try:
                    parsed_date = datetime.strptime(date_str.strip(), "%Y-%m-%d")
                    tx_date = parsed_date.replace(
                        hour=datetime.now().hour,
                        minute=datetime.now().minute,
                        second=datetime.now().second,
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

            # Compute or validate total amount
            computed_sum = sum(
                float(it["price"]) * (float(it["quantity"]) if float(it["quantity"]) > 0 else 1.0)
                for it in parsed_items
            )

            if total_amount is not None and float(total_amount) > 0:
                final_total = float(total_amount)
            elif computed_sum > 0:
                final_total = computed_sum
            else:
                final_total = 0.0

            # Create Transaction record
            tx = Transaction(
                user_id=user_id,
                store_name=canonical_store,
                total_amount=round(final_total, 2),
                transaction_date=tx_date,
                raw_transcript=raw_transcript or None,
            )
            db.add(tx)
            db.flush()  # Populates tx.id

            # Create TransactionItem records
            logged_items = []
            if parsed_items:
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

            result = {
                "status": "success",
                "message": "Expense successfully recorded.",
                "transaction_id": tx.id,
                "user_id": user_id,
                "store_name": canonical_store,
                "total_amount_pkr": round(final_total, 2),
                "date": tx.transaction_date.strftime("%Y-%m-%d"),
                "items_count": len(logged_items),
                "items": logged_items,
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
    ) -> str:
        """Queries expenses and totals for the current user with date, store, item, or category filters."""
        db: Session = SessionLocal()
        try:
            query = db.query(Transaction).filter(Transaction.user_id == user_id)

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

            transactions = query.order_by(Transaction.transaction_date.desc()).all()

            total_spent = sum(tx.total_amount for tx in transactions)
            store_totals: Dict[str, float] = {}
            for tx in transactions:
                store_totals[tx.store_name] = store_totals.get(tx.store_name, 0.0) + tx.total_amount

            # If querying for a specific item, compute the specific item spending
            item_specific_total = 0.0
            if item_name:
                cleaned_item = item_name.strip().lower()
                for tx in transactions:
                    for it in tx.items:
                        if cleaned_item in it.item_name.lower():
                            item_specific_total += it.price * it.quantity

            tx_list = []
            for tx in transactions[:15]:  # Return up to 15 recent matching transactions
                tx_list.append(
                    {
                        "id": tx.id,
                        "store_name": tx.store_name,
                        "total_amount_pkr": round(tx.total_amount, 2),
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

            payload = {
                "status": "success",
                "user_id": user_id,
                "visit_count": len(transactions),
                "total_spent_pkr": round(total_spent, 2),
                "spending_by_store": {k: round(v, 2) for k, v in store_totals.items()},
                "transactions": tx_list,
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
        """Deletes the most recent expense transaction logged for the current user."""
        db: Session = SessionLocal()
        try:
            tx = (
                db.query(Transaction)
                .filter(Transaction.user_id == user_id)
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

            db.delete(tx)
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

    return [log_expense, query_expenses, delete_last_transaction]


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

    # Construct conversation history
    messages = []
    if chat_history and len(chat_history) > 0:
        # Load from client-supplied history (most accurate for active UI tab)
        for msg in chat_history[-12:]:
            role = msg.get("role", "user")
            content = (msg.get("content") or "").strip()
            if not content:
                continue
            if role == "user":
                messages.append(HumanMessage(content=content))
            elif role == "assistant":
                from langchain_core.messages import AIMessage
                messages.append(AIMessage(content=content))
    elif clean_user_id in _IN_MEMORY_SESSIONS:
        # Fallback to server in-memory history
        messages.extend(_IN_MEMORY_SESSIONS[clean_user_id][-10:])

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

