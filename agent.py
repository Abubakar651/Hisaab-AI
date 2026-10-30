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

from database import SessionLocal, Transaction, TransactionItem, Budget, RecurringExpense
from analytics import (
    normalize_payment_method,
    detect_payment_method,
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
        "پاکستان میں روزمرہ کے اخراجات، گروسری، خریداری، راشن اور بل کا وائس نوٹ۔ "
        "Voice note about daily expenses, shopping, groceries, and bills in Pakistan. "
        "Mentions stores like Imtiaz, Al-Fatah, Jalal Sons, Carrefour, Metro, CSD, Chase Up, "
        "Save Mart, Bin Hashim, Shaheen Chemist, Servaid, Fazal Din. Spoken in Urdu, Punjabi, "
        "or English. Common words: ton, lita, layi, le aya, khareedya, ditte, mangwaya, "
        "hoya, kinna, kharcha, hisaab, rashan, sabzi, doodh, dahi, anday, double roti, cooking oil, "
        "ghee, aloo, pyaz, tamatar, gosht, chicken, qeema, chawal, aata, cheeni, chai patti, daal, "
        "petrol, diesel, easyload, bill. Spoken amounts: sau, hazaar, dedh hazaar, dhai hazaar, "
        "sadhe teen hazaar, pandra sau, baara sau, ath sau, pachaas, satthar, lakh, rupees, PKR. "
        "Always transcribe Urdu and Punjabi in Urdu script, never in Gurmukhi."
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
        description="Total cost in PKR (rupees) for this entire line item (e.g. 300 for 2 kg sugar, 400 for 12 eggs). NOT unit price."
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
You are equipped with real SQLite database tools: `log_expense`, `query_expenses`, `delete_last_transaction`, `set_budget`, `get_budget_status`, `add_recurring_expense`, and `mark_recurring_paid`.
1. **WHEN AN EXPENSE IS MENTIONED:**
   - Any time the user states or implies buying items, spending money, or shopping:
     **YOU MUST CALL `log_expense`**.
   - NEVER pretend to record it without executing the tool!
   - Extract `store_name` (canonical lowercase English), `items` list (with `item_name`, `price`, `quantity`, `category`), and `date_str`.
2. **WHEN SPENDING IS QUERIED (STRICT REQUIREMENT):**
   - Any time the user asks how much they spent, totals, asks for hisaab, or checks their ledger (e.g., "is mahine ka hisaab", "kinna kharcha hoya", "how much did I spend?"):
     **YOU MUST ALWAYS CALL `query_expenses`**!
   - **NEVER** calculate or state spending totals from the conversation history!
   - The conversation history only contains the last few chat messages, NOT the user's ledger!
   - The SQLite database is the ONLY source of truth. Always query it!
3. **WHEN UNDO / DELETE IS REQUESTED:**
   - Any time user asks to delete their last entry ("delete my last entry", "pichla record mita do", "ghalat likha gaya"):
     **YOU MUST CALL `delete_last_transaction`**.
4. **PAYMENT METHOD (always pass `payment_method` to `log_expense`):**
   - Default is `"cash"`. Detect: "card se" / "debit card" / "SadaPay" / "NayaPay" -> `card`; "easypaisa" -> `easypaisa`; "jazzcash" / "jazz cash" -> `jazzcash`; "bank transfer" / "raast" / "IBFT" -> `bank_transfer`.
   - For questions like "card se kitna kharcha hua" call `query_expenses` with `payment_method`.
5. **BUDGETS & ALERTS:**
   - "mera mahana budget 50000 hai" / "fuel ka budget 15000" -> **CALL `set_budget`** (category `total` for the overall budget).
   - "mera budget kitna bacha hai" / "budget status" -> **CALL `get_budget_status`**.
   - After `log_expense`, if the result contains `budget_alerts`, ALWAYS warn the user in their language, quoting the exact `percent`, `spent_pkr` and `monthly_limit` values from the tool (e.g. "fuel ka budget 82% khatam ho chuka hai").
6. **RECURRING MONTHLY BILLS (rent, electricity bill, school fee, committee):**
   - "har mahine 1 tareekh ko 35000 kiraya" -> **CALL `add_recurring_expense`**.
   - "is mahine ka kiraya / bill ada kar diya" / "paid electricity bill" -> **CALL `mark_recurring_paid`** with `title` (e.g. `title="electricity bill"` or `title="kiraya"`). **NEVER ask the user for the amount** because the recurring amount is already saved in the database! `mark_recurring_paid` automatically retrieves the saved amount and logs the expense.
   - "kaun se bills baqi hain" -> **CALL `get_budget_status`** and list recurring items where `paid_this_month` is false.

### SMART DEFAULTS & TRANSACTION ISOLATION:
- **DO NOT INTERROGATE THE USER:** Never refuse to log an expense or repeatedly question the user if they did not provide a branded store name or exact item breakdown.
- **Default Store:** In Pakistan, most people buy from street vendors, open markets, or local neighbourhood shops. If the user does not specify a store name, DO NOT ask them to name a store. Instead, use a sensible default:
  * By default: `"local store"`
  * If item is vegetables/fruits ("sabzi", "phal"): `"local store"` or `"sabzi market"`
  * If item is milk/yogurt ("doodh", "dahi"): `"dairy shop"`
  * If item is fuel ("petrol"): `"petrol pump"`
- **Default Item:** If the user specifies an amount and store or general shopping without item details (e.g., "1500 da saman", "1500 ka kharcha"):
  * Default item: `"general items"` or `"groceries"`
- **TRANSACTION ISOLATION (DO NOT ACCUMULATE PAST ITEMS):**
  * Every new expense statement is a fresh, independent transaction.
  * **NEVER copy items from previous transactions or earlier chat messages into the new transaction!**
  * If the user already recorded eggs and bread in Turn 1, and now says "1500 da rashan" in Turn 2, log ONLY the new 1500 grocery expense! NEVER attach eggs and bread to Turn 2.
  * Only combine information across messages if the user is directly answering an incomplete clarification question from the assistant.
- **MULTI-STORE PURCHASES IN ONE MESSAGE:**
  * If the user mentions buying items from multiple distinct stores in a single message (e.g. "Imtiaz se 1500 ka oil liya aur Metro se 3000 ka rashan"):
  * YOU MUST EMIT MULTIPLE `log_expense` TOOL CALLS — one for each store!
- **ABSOLUTE ZERO MENTAL ARITHMETIC MANDATE (DATABASE DOES ALL MATH):**
  * YOU MUST NEVER DO MENTAL ARITHMETIC OR GUESS SUMS IN YOUR HEAD.
  * All calculations, summations, line item additions, and totals are computed deterministically by SQLite and Python.
  * For queries: ALWAYS call `query_expenses`. The database executes SQL `SUM()` and returns the 100% mathematically exact numbers.
  * ALWAYS report the exact numbers returned by the tool to the user — NEVER recalculate or alter them!

### CRITICAL RULES:

1. **LANGUAGE ENFORCEMENT & STRICT PROHIBITION OF GURMUKHI:**
   - **Urdu or Punjabi Inputs (Voice or Text):**
     * Whenever a user sends a message or voice note in Urdu or Punjabi (spoken, Urdu script, or Roman Urdu/Punjabi), ALWAYS respond in **polite, natural Urdu script (اردو)**.
     * **ABSOLUTE BAN ON GURMUKHI SCRIPT:** NEVER output Gurmukhi script (e.g., ਮੈਂ ਤੁਹਾਡਾ ਖਰਚਾ ਦਰਜ ਕਰ ਰਿਹਾ ਹਾਂ...). Gurmukhi is not used or understood in Pakistan. Even if the voice transcription produced Gurmukhi characters, YOU MUST ALWAYS RESPOND IN STANDARD URDU SCRIPT (اردو رسم الخط).
     * Example Urdu reply:
       "جی بالکل! آپ کا خرچہ درج کر لیا گیا ہے: الفتح سے 3,400 روپے کا سامان (انڈے، بریڈ، پانی اور کیچپ)۔"
   - **English Inputs (Voice or Text):**
     * Whenever the user inputs in English, ALWAYS respond in clear, professional English.
     * Example English reply:
       "Recorded your expense: PKR 3,400 at Al-Fatah for eggs, bread, water, and ketchup."

2. **SPOKEN PAKISTANI NUMBERS RESOLUTION (URDU & PUNJABI):**
   Users frequently state amounts colloquially in Urdu and Punjabi. You MUST accurately resolve these spoken numbers to exact numeric values:
   * **Hundreds (Sau / So / سَو):**
     - "dedh sau" / "dehr sau" / "ڈیڑھ سو" -> 150
     - "paune do sau" / "پونے دو سو" -> 175
     - "dhai sau" / "ڈھائی سو" -> 250
     - "sadhe teen sau" / "ساڑھے تین سو" -> 350
     - "sadhe chaar sau" / "ساڑھے چار سو" -> 450
     - "ath sau" / "اٹھ سو" -> 800
     - "nau sau" / "نو سو" -> 900
     - "baara sau" / "بارہ سو" -> 1200
     - "pandra sau" / "پندرہ سو" -> 1500
   * **Thousands (Hazaar / ہزار):**
     - "hazaar" / "ik hazaar" / "اک ہزار" -> 1000
     - "dedh hazaar" / "dehr hazaar" / "ڈیڑھ ہزار" -> 1500 (same as "pandra sau")
     - "paune do hazaar" / "پونے دو ہزار" -> 1750
     - "dhai hazaar" / "ڈھائی ہزار" -> 2500 (same as "pachees sau")
     - "sadhe teen hazaar" / "ساڑھے تین ہزار" -> 3500 (same as "paitees sau")
     - "sadhe chaar hazaar" / "ساڑھے چار ہزار" -> 4500 (same as "paintaalis sau")
     - "sadhe paanch hazaar" / "ساڑھے پانچ ہزار" -> 5500
     - "das hazaar" / "دس ہزار" -> 10000
     - "veeh hazaar" / "ویہہ ہزار" -> 20000
     - "panjah hazaar" / "پنجاہ ہزار" -> 50000
   * **Lakhs (Lakh / لاکھ):**
     - "ik lakh" / "ek lakh" -> 100,000
     - "dedh lakh" -> 150,000
     - "dhai lakh" -> 250,000
   * **Colloquial Quantities & Counts:**
     - Punjabi numbers: ikk (1), do (2), tinn (3), chaar (4), panj (5), chhe (6), satt (7), atth (8), nau (9), das (10), yaaran (11), baaran (12), tehran (13), chaudan (14), pandran (15), veeh (20), panjah (50), sattar (70), assi (80), sau (100).
     - Units: "paa" / "pao" = 250g, "adha kilo" = 0.5kg, "dedh kilo" = 1.5kg, "darjan" = 12 pcs, "adha darjan" = 6 pcs.

3. **100 PAKISTANI USER SCENARIOS & VOCABULARY MAPPING:**
   Always map Pakistani daily life terms to clean, canonical lowercase English store names, items, and categories:
   * **Kiryaana & Rashan (Category: "groceries"):**
     aata (flour), daal (lentils), cheeni (sugar), chai patti (tea), cooking oil, ghee, chawal (rice), masalay (spices), surf (detergent), sabun (soap). Stores: "imtiaz", "al-fatah", "save mart", "csd", "bin hashim", "local store".
   * **Sabzi & Phal Mandi (Category: "groceries"):**
     aloo (potatoes), pyaz (onions), tamatar (tomatoes), lehsan adrak (garlic ginger), hari mirch (chilies), kela (bananas), seb (apples), aam (mangoes). Stores: "sabzi market", "local store".
   * **Gosht / Meat (Category: "meat"):**
     chicken / murghi, broil, desi murgh, qeema (minced meat), bara gosht (beef), chota gosht (mutton), machli (fish). Store: "butcher shop", "meat shop".
   * **Dairy & Bakery (Category: "dairy" or "bakery"):**
     doodh (milk), dahi (yogurt), makkhan (butter), anday (eggs), double roti (bread), rus, naan, khameeri, roti, paratha. Stores: "dairy shop", "bakery", "tandoor".
   * **Fuel & Transport (Category: "fuel" or "transport"):**
     petrol, diesel, hi-octane, mobile oil, bike tuning, puncture, car wash, rickshaw fare, metro card, Indrive, Bykea, Careem, Yango. Stores: "petrol pump", "mechanic", "ride hailing".
   * **Bills & Telecom (Category: "utilities"):**
     bijli ka bill (electricity - LESCO, K-Electric, FESCO, IESCO), sui gas, pani bill (WASA), internet (PTCL, Nayatel, StormFiber), easyload, Jazz/Telenor/Zong package. Stores: "bill payment", "jazzcash", "easypaisa", "telecom".
   * **Dining & Dhaba (Category: "dining"):**
     chai hotel, pathan hotel, samosay, pakoray, biryani, karahi, nihari, haleem, burger, pizza, shawarma. Stores: "restaurant", "dhaba", "cafe".
   * **Healthcare & Pharmacy (Category: "healthcare"):**
     dawai (medicine), panadol, syrup, doctor fee, lab test. Stores: "shaheen chemist", "servaid", "fazal din", "pharmacy", "clinic".
   * **Personal & Services (Category: "personal" or "household"):**
     darzi silai (tailor), haircut / salon, kapray (clothes), bijli wala / plumber. Stores: "tailor", "salon", "local service".

4. **PUNJABI & URDU DIALECT & GRAMMAR NUANCES:**
   * Understand Pakistani Punjabi verbs & prepositions:
     - "ton" = from ("Imtiaz ton lita" = bought from Imtiaz)
     - "lita" / "le aya" / "khareedya" = bought
     - "layi" / "le li" = bought / took
     - "ditte" = paid / gave ("petrol pump te 500 ditte")
     - "pwaya" / "dalwaya" = poured / refilled ("bike ch petrol pwaya")
     - "peeti" / "khada" = drank / ate ("chai peeti", "khana khada")
     - "hoya" / "banya" = totaled / amounted to ("kinna bill hoya?")
     - "kharcha" / "hisaab" = expense / ledger
   * **Language Output Rules:**
     - For ANY Urdu or Punjabi input (voice or text, whether in Urdu script, Roman Urdu/Punjabi, or Punjabi phrasing): ALWAYS respond in natural, polite **Urdu script (اردو)**. NEVER use Gurmukhi!
     - For ANY English input (voice or text): ALWAYS respond in clear, professional **English**.

5. **CANONICAL ENGLISH FOR DATABASE TOOLS:**
   - Even though you converse in the user's language, ALWAYS convert store names and item names to **clean, canonical lowercase English** before passing them into tools:
     * Examples of stores: "imtiaz", "al-fatah", "jalal sons", "carrefour", "metro", "csd", "shaheen", "local store", "petrol pump", "dairy shop", "sabzi market", "butcher shop".
     * Examples of items: "cooking oil", "milk", "bread", "eggs", "chicken", "flour", "rice", "sugar", "petrol", "tea", "fruits", "vegetables", "electricity bill".
     * Categories: "groceries", "dairy", "fuel", "electronics", "dining", "utilities", "healthcare", "clothing", "transport", "other".

6. **RELATIVE DATE RESOLUTION:**
   - "aaj" / "today" -> {today_str}
   - "kal" / "yesterday" -> {yesterday_str}
   - "pichle 2 din" / "last 2 days" -> start_date: {two_days_ago_str}, end_date: {today_str}
   - "pichle 3 din" / "last 3 days" -> start_date: {three_days_ago_str}, end_date: {today_str}
   - "is hafte" / "this week" -> start_date: {seven_days_ago_str}, end_date: {today_str}
   - "is mahine" / "this month" -> start_date: {month_start_str}, end_date: {today_str}

7. **MANDATORY TOOL INVOCATION ORDER:**
   - Step 1: When an expense, query, or undo is mentioned, YOUR VERY FIRST ACTION MUST BE TO INVOKE THE REAL TOOL (`log_expense`, `query_expenses`, or `delete_last_transaction`).
   - Step 2: NEVER write conversational text claiming an expense was recorded or confirmed without the tool executing first.
   - Step 3: If no store was mentioned by the user, immediately use the default store (`local store`) and proceed to call `log_expense`. Do NOT interrogate the user!
   - Step 4: Only after the tool successfully executes and returns its result do you reply to the user, confirming with the store name, item list, and total amount in the user's language and script.

8. **CURRENCY & TONE:**
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
        payment_method: Optional[str] = "cash",
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

            # Create Transaction record
            tx = Transaction(
                user_id=user_id,
                store_name=canonical_store,
                total_amount=round(final_total, 2),
                transaction_date=tx_date,
                raw_transcript=raw_transcript or None,
                payment_method=detect_payment_method(payment_method, raw_transcript),
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

            payload = {
                "status": "success",
                "user_id": user_id,
                "visit_count": len(transactions),
                "total_spent_pkr": round(total_spent, 2),
                "spending_by_store": {k: round(v, 2) for k, v in store_totals.items()},
                "spending_by_category": {k: round(v, 2) for k, v in category_totals.items()},
                "spending_by_payment_method": {k: round(v, 2) for k, v in payment_totals.items()},
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

    return [
        log_expense,
        query_expenses,
        delete_last_transaction,
        set_budget,
        get_budget_status,
        add_recurring_expense,
        mark_recurring_paid,
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

    # Construct conversation history
    messages = []
    if chat_history and len(chat_history) > 0:
        # Load from client-supplied history (keep last 4 messages for tight context & low token consumption)
        for msg in chat_history[-4:]:
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

