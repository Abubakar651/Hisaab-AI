import os
import re
import time
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

from database import SessionLocal, Transaction, TransactionItem, Budget, RecurringExpense, PendingTransaction, UdhaarEntry, get_pkt_now
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
    galla_summary,
    udhaar_summary,
    add_udhaar_entry,
    settle_udhaar,
)

load_dotenv()
logger = logging.getLogger("hisaab_ai.agent")

LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "30.0"))
AGENT_RECURSION_LIMIT = int(os.getenv("AGENT_RECURSION_LIMIT", "6"))

def get_groq_api_keys() -> List[str]:
    """
    Returns ordered, deduplicated list of active Groq API keys.
    Order: GROQ_API_KEY, GROQ_API_KEY_2, followed by any comma-separated keys in GROQ_API_KEYS.
    Empty/whitespace strings are discarded.
    """
    keys = []
    seen = set()

    k1 = (os.getenv("GROQ_API_KEY") or "").strip()
    if k1 and k1 not in seen:
        keys.append(k1)
        seen.add(k1)

    k2 = (os.getenv("GROQ_API_KEY_2") or "").strip()
    if k2 and k2 not in seen:
        keys.append(k2)
        seen.add(k2)

    extra_str = os.getenv("GROQ_API_KEYS", "") or ""
    if extra_str.strip():
        for piece in extra_str.split(","):
            p = piece.strip()
            if p and p not in seen:
                keys.append(p)
                seen.add(p)

    return keys


def get_groq_client(api_key: Optional[str] = None):
    if api_key is None:
        keys = get_groq_api_keys()
        api_key = keys[0] if keys else ""
    return Groq(api_key=api_key.strip())

# Initialize Groq client for Whisper ASR
groq_client = get_groq_client()

# Default model preference with fallback list matching active Groq account models
PREFERRED_MODEL = os.getenv("GROQ_LLM_MODEL", "qwen/qwen3.8-27b")
MODEL_CANDIDATES = [PREFERRED_MODEL, "qwen/qwen3.8-27b", "openai/gpt-oss-120b", "openai/gpt-oss-20b"]

_VALIDATED_MODEL = None
_BEDROCK_THROTTLED_UNTIL = 0.0


def get_chat_llm(api_key: Optional[str] = None):
    """
    Initializes Chat LLM.
    Priority 1: AWS Bedrock (if configured, not throttled, LLM_PROVIDER != 'groq', and api_key is None).
    Priority 2: Groq Chat LLM with pre-validated model fallback.
    """
    global _BEDROCK_THROTTLED_UNTIL
    provider = os.getenv("LLM_PROVIDER", "").strip().lower()

    if provider != "groq" and api_key is None:
        aws_key = os.getenv("AWS_ACCESS_KEY_ID", "").strip()
        aws_secret = os.getenv("AWS_SECRET_ACCESS_KEY", "").strip()
        bedrock_model = os.getenv("BEDROCK_MODEL_ID", "").strip()

        now = time.time()
        if ((aws_key and aws_secret) or bedrock_model) and now >= _BEDROCK_THROTTLED_UNTIL:
            if ChatBedrockConverse is not None:
                try:
                    from botocore.config import Config
                    # Fast failover config to avoid 15s retry hang when AWS throttles
                    cfg = Config(
                        retries={"max_attempts": 1, "mode": "standard"},
                        connect_timeout=10,
                        read_timeout=int(LLM_TIMEOUT_SECONDS),
                    )
                    model_id = bedrock_model or "us.meta.llama3-3-70b-instruct-v1:0"
                    region = os.getenv("AWS_REGION", "us-east-1").strip() or "us-east-1"
                    kwargs = {
                        "model": model_id,
                        "region_name": region,
                        "temperature": 0,
                        "max_tokens": 1000,
                        "config": cfg,
                    }
                    if aws_key and aws_secret:
                        kwargs["aws_access_key_id"] = aws_key
                        kwargs["aws_secret_access_key"] = aws_secret

                    return ChatBedrockConverse(**kwargs)
                except Exception as e:
                    logger.warning(f"Failed to initialize AWS Bedrock LLM ({bedrock_model}): {e}. Falling back to Groq.")

    effective_groq_key = api_key
    if effective_groq_key is None:
        groq_keys = get_groq_api_keys()
        effective_groq_key = groq_keys[0] if groq_keys else ""

    global _VALIDATED_MODEL
    if _VALIDATED_MODEL:
        return ChatGroq(
            model=_VALIDATED_MODEL,
            temperature=0,
            max_tokens=800,
            max_retries=3,
            timeout=LLM_TIMEOUT_SECONDS,
            groq_api_key=effective_groq_key,
        )

    for candidate in MODEL_CANDIDATES:
        if not candidate:
            continue
        try:
            test_llm = ChatGroq(
                model=candidate,
                temperature=0,
                max_tokens=10,
                timeout=LLM_TIMEOUT_SECONDS,
                groq_api_key=effective_groq_key,
            )
            test_llm.invoke("ping")
            _VALIDATED_MODEL = candidate
            logger.info(f"Active Groq LLM model verified: {_VALIDATED_MODEL}")
            return ChatGroq(
                model=_VALIDATED_MODEL,
                temperature=0,
                max_tokens=800,
                max_retries=3,
                timeout=LLM_TIMEOUT_SECONDS,
                groq_api_key=effective_groq_key,
            )
        except Exception as e:
            logger.info(f"Groq model candidate '{candidate}' not available: {e}")
            continue

    _VALIDATED_MODEL = "qwen/qwen3.8-27b"
    return ChatGroq(
        model=_VALIDATED_MODEL,
        temperature=0,
        max_tokens=800,
        timeout=LLM_TIMEOUT_SECONDS,
        groq_api_key=effective_groq_key,
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
                logger.info("Converted Hindi/Gurmukhi text to Urdu script successfully")
                return converted
        except Exception as e:
            logger.warning(f"Error converting Hindi/Gurmukhi text to Urdu script: {e}")

    return text


ARABIC_SCRIPT_REGEX = re.compile(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")
REGIONAL_LANGS = {"ur", "pa", "hi", "sd", "ps", "fa", "ar"}


def transcribe_audio(file_bytes: bytes, filename: str = "voice_note.webm") -> str:
    """
    Transcribes audio using Groq whisper-large-v3 with domain prompt injection
    for accurate Pakistani store names, prices, and code-switched Urdu/Punjabi.
    Enforces Pakistani Urdu script (Nastaliq), preventing Hindi Devanagari, Gurmukhi, or Latin transliteration.
    Automatically rotates to secondary Groq API key upon 429 rate limit.
    """
    if not file_bytes or len(file_bytes) < 100:
        logger.warning("Empty or very short audio payload received.")
        return ""

    # Vocabulary-only domain prompt (no instruction sentences that degrade ASR)
    domain_prompt = (
        "امتیاز، الفتح، جلال سنز، کیارفور، میٹرو، چیس اپ، شاہین کیمسٹ، "
        "پٹرول، ڈیزل، دودھ، دہی، انڈے، ڈبل روٹی، چینی، چاول، دال، آٹا، "
        "راشن، کریانہ، گوشت، سبزی، پھل، بل، بجلی، گیس، پانی، فیس، کرایہ، تنخواہ، "
        "ادھار، کھاتہ، کھاتے میں، وصولی، ادھار دیا، ادھار لیا، گاہک، فروخت، گلا، کیش، بینک ٹرانسفر، روپے، PKR"
    )

    keys = get_groq_api_keys() or [""]
    for idx, k in enumerate(keys):
        try:
            try:
                client = get_groq_client(api_key=k)
            except TypeError:
                client = get_groq_client()
            transcription = client.audio.transcriptions.create(
                file=(filename, file_bytes),
                model="whisper-large-v3",
                prompt=domain_prompt,
                temperature=0.0,
                response_format="verbose_json",
            )
            raw_text = getattr(transcription, "text", "") or (transcription.get("text", "") if isinstance(transcription, dict) else "")
            raw_text = raw_text.strip()
            detected_lang = getattr(transcription, "language", "") or (transcription.get("language", "") if isinstance(transcription, dict) else "")

            # If spoken Urdu/Punjabi/regional dialect was detected but transcribed in non-Arabic script (e.g. Latin transliteration or Devanagari),
            # perform at most ONE retry with explicit language="ur".
            if detected_lang in REGIONAL_LANGS and not bool(ARABIC_SCRIPT_REGEX.search(raw_text)):
                try:
                    retry_transcription = client.audio.transcriptions.create(
                        file=(filename, file_bytes),
                        model="whisper-large-v3",
                        prompt=domain_prompt,
                        temperature=0.0,
                        language="ur",
                        response_format="verbose_json",
                    )
                    retry_text = getattr(retry_transcription, "text", "") or (retry_transcription.get("text", "") if isinstance(retry_transcription, dict) else "")
                    if retry_text and retry_text.strip():
                        raw_text = retry_text.strip()
                except Exception as retry_err:
                    # On 429 or any retry error, safely fall back to the first transcript
                    logger.warning(f"Whisper Urdu retry failed, using initial transcript: {retry_err}")

            # Final guarantee that Urdu/Punjabi text is formatted in authentic Urdu script, never Hindi Devanagari
            return ensure_urdu_script(raw_text)
        except Exception as e:
            err_msg = str(e)
            is_429 = "429" in err_msg or "rate_limit" in err_msg.lower()
            if is_429 and idx + 1 < len(keys):
                logger.warning(f"Whisper transcription rate limit (429) on key index {idx}; rotating to next Groq key...")
                continue
            logger.error(f"Whisper transcription failed: {e}")
            raise RuntimeError(f"Speech transcription failed: {str(e)}")



# ---------------------------------------------------------------------------
# Recipient & Vendor Extraction Helpers
# ---------------------------------------------------------------------------
def extract_recipient_or_vendor(text: str) -> str:
    """
    Extracts a person/recipient or vendor name from transfer and expense phrases.
    Supports English ('to Abid Hussain', 'sent ... to Ali', 'add name Abid Hussain', 'recipient is Tariq'),
    and Urdu / Roman Urdu ('Abid Hussain ko bheje', 'Ali ko transfer kiye', 'naam Bilal likh do').
    Returns canonical lowercase string or empty string if not found.
    """
    if not text or not text.strip():
        return ""
    raw = text.strip()

    # 1. Clarification / explicit name declaration ("add name X", "name is X", "recipient is X", "naam X hai", "naam X likho")
    m_add = re.search(r"\b(?:add(?:\s+the)?\s+name|name\s+is|recipient\s+is|recipient:?)\s+([A-Za-z\s]+?)(?:\s*(?:please|plz|\.|$))", raw, re.I)
    if m_add:
        name = m_add.group(1).strip()
        if name and name.lower() not in ("bank", "account", "the name", "my bank"):
            return name.lower()

    m_add_ur = re.search(r"(?:نام|naam)\s+([A-Za-z\u0600-\u06FF\s]+?)(?:\s*(?:hai|rakh|likh|daal|karein|ہے|لکھ|ڈال|\.|$))", raw, re.I)
    if m_add_ur:
        name = m_add_ur.group(1).strip()
        if name and name.lower() not in ("bank", "account", "un", "in"):
            return name.lower()

    # 2. Transfer recipient in English ("to X", "sent to X", "paid to X", "transfer to X")
    m_to = re.search(r"\b(?:to|paid to|sent to|transfer to)\s+([A-Za-z\s]+?)(?:\s*(?:via|through|per|par|from|for|using|with|\.|$))", raw, re.I)
    if m_to:
        name = m_to.group(1).strip()
        if name.lower() not in ("bank", "account", "the bank", "my bank", "him", "her", "them", "someone", "local store"):
            return name.lower()

    # 3. Transfer recipient in Urdu / Roman Urdu ("<name> ko", "ko <name>")
    m_ko = re.search(r"\b([A-Za-z\u0600-\u06FF\s]+?)\s+(?:ko|کو)\b", raw, re.I)
    if m_ko:
        name = m_ko.group(1).strip()
        name = re.sub(r"^[\d\s,]+", "", name).strip()
        name = re.sub(r"^(?:bheje|bheja|diye|diya|sent|transfer|pay|paid)\s+", "", name, flags=re.I).strip()
        if name and name.lower() not in ("bank", "account", "un", "in", "us", "is"):
            return name.lower()

    m_ko_rev = re.search(r"\b(?:ko|کو)\s+([A-Za-z\u0600-\u06FF\s]+?)(?:\s*(?:bheje|bheja|diye|diya|per|par|\.|$))", raw, re.I)
    if m_ko_rev:
        name = m_ko_rev.group(1).strip()
        if name and name.lower() not in ("bank", "account"):
            return name.lower()

    return ""


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
        description="Canonical lowercase English store, vendor, shop, brand, or person/recipient name for money transfers/payments, e.g. 'imtiaz', 'al-fatah', 'abid hussain', 'ali', 'petrol pump', 'local store'"
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


class UpdateLastExpenseInput(BaseModel):
    model_config = {"extra": "ignore"}

    store_name: Optional[str] = Field(
        default=None,
        description="Updated store name or recipient/vendor person name (e.g. 'Abid Hussain', 'Al-Fatah', 'Ali', 'Landlord').",
    )
    category: Optional[str] = Field(
        default=None,
        description="Updated category (groceries, dairy, fuel, dining, utilities, rent, healthcare, business, other).",
    )
    notes_or_purpose: Optional[str] = Field(
        default=None,
        description="Specific purpose, description, or item name for this expense (e.g. 'house rent', 'shop stock', 'monthly fee', 'school fee').",
    )
    payment_method: Optional[str] = Field(
        default=None,
        description="Updated payment method: 'cash', 'easypaisa', 'jazzcash', 'card', 'bank_transfer'.",
    )
    total_amount: Optional[float] = Field(
        default=None,
        description="Corrected total amount in PKR if the user corrected the cost.",
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


class LogSaleInput(BaseModel):
    model_config = {"extra": "ignore"}

    customer_or_note: str = Field(
        default="daily sale",
        description="Customer name or sale description, e.g. 'daily sale', 'walk-in customer', 'sugar and oil sale'",
    )
    items: Optional[Any] = Field(
        default=None,
        description="Sold items list with item_name, price, quantity, or text summary",
    )
    total_amount: Optional[float] = Field(
        default=None,
        description="Total sale amount in PKR",
    )
    date_str: Optional[str] = Field(
        default=None,
        description="Sale date in YYYY-MM-DD format. Defaults to today.",
    )
    payment_method: Optional[str] = Field(
        default="cash",
        description="How customer paid: 'cash', 'easypaisa', 'jazzcash', 'card', 'bank_transfer'",
    )


class ManageUdhaarInput(BaseModel):
    model_config = {"extra": "ignore"}

    action: str = Field(
        description="One of: 'give_credit' (customer owes user / maine lene hain / udhaar diya), 'take_credit' (user owes supplier / maine dene hain / udhaar liya), 'settle' (wasooli / payment received or paid back), 'list' (show active udhaar khata summary)"
    )
    person_name: Optional[str] = Field(
        default=None,
        description="Customer or supplier name, e.g. 'Ali Bhai', 'Tariq Kiryana'",
    )
    amount: Optional[float] = Field(
        default=None,
        description="Amount in PKR (for give_credit, take_credit, or partial/full settle; omit on settle to settle full balance)",
    )
    direction: Optional[str] = Field(
        default=None,
        description="Optional direction for settle: 'receivable' (customer paid user / wasooli) or 'payable' (user paid supplier / udhaar chukaya)",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Optional items or note, e.g. 'atta aur cheeni'",
    )
    phone_number: Optional[str] = Field(
        default=None,
        description="Optional phone number for WhatsApp reminder",
    )


# ---------------------------------------------------------------------------
# Dynamic System Prompt
# ---------------------------------------------------------------------------
def build_system_prompt(ui_lang: Optional[str] = None) -> str:
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

    lang_pref_clause = ""
    if ui_lang == "en":
        lang_pref_clause = (
            "\n### UI LANGUAGE OVERRIDE: ACTIVE INTERFACE IS ENGLISH\n"
            "- Active UI is English. Respond in 100% clear English unless the user explicitly types in Urdu script (Nastaliq).\n"
            "- Use English headers ('Date', 'Store', 'Amount (PKR)', 'Payment') and English categories/payment methods.\n"
        )
    elif ui_lang == "ur":
        lang_pref_clause = (
            "\n### UI LANGUAGE OVERRIDE: ACTIVE INTERFACE IS URDU\n"
            "- Active UI is Urdu. Respond in natural Pakistani Urdu script (Nastaliq) unless the user explicitly communicated in English.\n"
        )

    return f"""You are **HisaabAI**, a polite, accurate financial voice & text ledger assistant for Pakistan.

### STRICT DOMAIN BOUNDARY & OUT-OF-DOMAIN REFUSAL:
Exclusively a Pakistan finance, expense, daily sales (farokht/galla), and udhaar khata assistant.
1. **IN-DOMAIN:** Expenses, sales (farokht), galla, udhaar/khata, bills, budgets, greetings, creator questions.
2. **CREATOR:** If asked who created/built HisaabAI ("Who created this app?", "Yeh app kis ne banai hai?", "یہ ایپ کس نے بنائی ہے؟"), credit **Abu Bakar Iqbal** (ابوبکر اقبال):
   - **English:** "HisaabAI was designed and developed by **Abu Bakar Iqbal**, built as a smart multilingual voice and text expense ledger tailored for Pakistan."
   - **Urdu:** "حساب اے آئی (HisaabAI) کو **ابوبکر اقبال (Abu Bakar Iqbal)** نے ڈیزائن اور ڈویلپ کیا ہے، تاکہ پاکستان بھر کے صارفین اپنے روزمرہ اخراجات، بجٹ اور مالی حساب کتاب باآسانی بول کر یا لکھ کر منظم کر سکیں۔"
3. **OUT-OF-DOMAIN:** Never answer trivia, coding, poems, stories, jokes, recipes, politics, sports, or homework. Decline:
   - **English:** "I am HisaabAI, your dedicated personal expense and financial ledger assistant. I can only help you record expenses, check your budgets, and track your finances. Please share an expense to record (e.g. 'Spent 2,500 on groceries') or ask about your spending."
   - **Urdu:** "میں حساب اے آئی ہوں، آپ کا ذاتی مالیاتی اور اخراجات کا اسسٹنٹ۔ میں صرف روزمرہ اخراجات درج کرنے، بجٹ سیٹ کرنے اور مالی حساب کتاب دیکھنے میں آپ کی مدد کر سکتا ہوں۔ براہ کرم اپنا کوئی خرچہ بتائیں (مثلاً '2500 کا پیٹرول ڈلوایا') یا اپنے حساب کتاب کے بارے میں پوچھیں۔"

### PRIMARY LANGUAGE MANDATE (STRICT):
- **English Input:** Reply in 100% clear **English**.
- **Urdu / Punjabi Input:** Reply in natural **Pakistani Urdu script (اردو / Nastaliq)**.
- **PROHIBITIONS:** NEVER output Hindi/Devanagari (अच्छा, खर्च), Arabic (تم تسجيل), or Gurmukhi (ਮੈਂ ਤੁਹਾਡਾ).

### REPORT & TABLE FORMATTING:
State summary first (Total PKR, count, dates). Tables:
- **English:** `| Date | Store | Amount (PKR) | Payment |` ('Cash', 'Card', 'EasyPaisa', 'JazzCash', 'Bank Transfer').
- **Urdu:** `| تاریخ | دکان | رقم (PKR) | ادائیگی |` (نقد، کارڈ، ایزی پیسہ، جاز کیش، بینک ٹرانسفر; 'local store'->'لوکل اسٹور', 'fuel'->'پیٹرول پمپ'). No raw keys.

### MANDATORY TOOL CALLING & DATABASE INTEGRITY:
1. **EXPENSE (`log_expense`):** Expenses/purchases/transfers ("spent", "kharcha", "Ali ko 5000 bheje") -> lowercase `store_name` (vendor/recipient), `items`, `date_str`, `payment_method`.
2. **UPDATE RECENT (`update_last_expense`):** Clarifying/answering follow-up ("add name Abid Hussain", "naam Tariq hai", "3500 not 3000") -> CALL `update_last_expense`, NEVER `log_expense`!
3. **DAILY SALES (`log_sale`):** Sold items ("sold 2 bags flour for 5400", "aaj 15000 ki farokht hui") -> CALL `log_sale`, never `log_expense`!
4. **UDHAAR (`manage_udhaar`):**
   - **MANDATORY COMPLETE DETAILS REQUIREMENT:** Both `person_name` AND `amount` are required. If missing/ambiguous or direction unclear, DO NOT call `manage_udhaar` or guess! Ask first (Urdu: "براہ کرم بتائیں کہ یہ ادھار کس کو دیا/کس سے لیا ہے اور کتنی رقم ہے؟", English: "Please specify who this credit was given to / taken from and the amount in PKR.").
   - **CUSTOMER CREDIT SALES VS EXPENSES:** Goods taken on credit ("mere se saaman/maal le gaya", "maal udhaar diya", "khate mein likh do", or STT aliases "ادار دیا", "خاتم لکھ دو", "خطہ میں لکھ دو") -> ALWAYS `manage_udhaar(action='give_credit')`, NEVER `log_expense`!
   - Actions: `give_credit` (lene hain), `take_credit` (dene hain), `settle` (wasooli; clarify on `ambiguous`/`direction_mismatch`/`overpaid`), `list`.
5. **QUERIES (`query_expenses`):** Call for totals/galla; no mental math.
6. **UNDO & RECLASSIFICATION (CONFIRM BEFORE DELETING):** Deleting is permanent. If entry was logged in wrong ledger ("ye kharche me nahi udhaar me tha", "ye khata me add karna tha"), DO NOT call `update_last_expense`! Ask confirmation first (Urdu: "کیا میں پچھلا [رقم] روپے کا خرچہ منسوخ کر کے [شخص] کے نام ادھار کھاتے میں درج کر دوں؟", English: "Would you like me to cancel the previous PKR [amount] expense and record it under [Person] in Udhaar Khata?"). On confirmation ("ہاں", "جی", "کر دو", "yes") or explicit command ("pichla kharcha delete kar ke udhaar me likho"), call `delete_last_transaction` then `manage_udhaar`. For "undo", call `delete_last_transaction`.
7. **BUDGETS:** `set_budget` / `get_budget_status`. 8. **RECURRING:** `add_recurring_expense` / `mark_recurring_paid`. 9. **LARGE EXPENSE:** On `needs_confirmation`, ask user to click 'Confirm' (تصدیق کریں) or call `cancel_pending_expense`. 10. **ISOLATION:** Independent turns.

### PROACTIVE FOLLOW-UP QUESTIONS:
After logging an expense, ask at most ONE brief follow-up if details are missing (use `update_last_expense` on reply):
- Missing recipient: "Who did you send this transfer to?" / "یہ رقم آپ نے کس کو بھیجی ہے؟"
- Generic large spend: "Would you like to add a purpose or category (e.g. rent, stock, bills) for this payment?" / "کیا آپ اس ادائیگی کا مقصد یا کیٹیگری (مثلاً کرایہ، سامان، بل) درج کرنا چاہتے ہیں؟"
- Ambiguous payment: "Was this paid in cash, or via bank / online transfer?" / "کیا یہ نقد ادائیگی تھی یا بینک / آن لائن ٹرانسفر کے ذریعے؟"
- **COMPLAINTS & CORRECTIONS:** NEVER ask follow-ups on complaints, corrections, or Udhaar.
- **NO PARROTING:** NEVER repeat or parrot user text.

### PAKISTANI NUMBER & PHRASE RESOLUTION:
- **STT Aliases:** 'ادار'/'ادارہ دیا'='ادھار', 'خاتم'/'خطہ'='کھاتہ'. Reconstruct spelled names ('دال الف واو حمزہ دال'->داؤد).
- **Numbers:** dedh sau=150, paune do sau=175, dhai sau=250, sadhe teen sau=350, sadhe chaar sau=450, baara sau=1200, pandra sau=1500, hazaar=1000, dedh hazaar=1500, paune do hazaar=1750, dhai hazaar=2500, sadhe teen hazaar=3500, sadhe chaar hazaar=4500, das hazaar=10000, panjah hazaar=50000, ik lakh=100000, dedh lakh=150000, dhai lakh=250000, crore=10000000.
- **Terms:** farokht/sale=daily sale (`log_sale`), becha/vechya=sold (`log_sale`), galla=cash drawer, udhaar/khata=credit (`manage_udhaar`), lene hain=receivable (`give_credit`), dene hain=payable (`take_credit`), wasooli/jama=settled (`settle`), lita/layi/khareedya=bought (`log_expense`). Currency: PKR / روپے.

### CALENDAR CONTEXT:
- Today ("aaj"): {today_str} ({day_name}) | Yesterday ("kal"): {yesterday_str} | Month Start ("is mahine"): {month_start_str}
- 2d Ago ("pichle 2 din"): {two_days_ago_str} | 3d Ago ("pichle 3 din"): {three_days_ago_str} | 7d Ago ("is hafte"): {seven_days_ago_str}{lang_pref_clause}
"""


# ---------------------------------------------------------------------------
# Tool Factory Function (Tenant Isolation via Closure)
# ---------------------------------------------------------------------------
def get_tools_for_user(
    user_id: str,
    raw_transcript: str = "",
    turn_state: Optional[Dict[str, Any]] = None,
) -> list:
    """
    Creates user-scoped LangChain tools bound via Python closure to user_id.
    Guarantees strict tenant isolation: the LLM never supplies or controls user_id.
    """

    def check_idempotent_cache(tool_name: str) -> Optional[str]:
        if turn_state is not None and turn_state.get("attempt", 0) > 0:
            cached = turn_state.get("mutations_by_tool", {}).get(tool_name)
            if cached:
                logger.info(f"Returning cached mutation result for {tool_name} on attempt {turn_state.get('attempt')}")
                return cached
        return None

    def record_idempotent_result(tool_name: str, res_str: str) -> str:
        if turn_state is not None and isinstance(res_str, str):
            try:
                parsed = json.loads(res_str)
                if isinstance(parsed, dict) and parsed.get("status") in ("success", "needs_confirmation", "cancelled"):
                    turn_state.setdefault("mutations_by_tool", {})[tool_name] = res_str
            except Exception:
                pass
        return res_str

    @tool("log_expense", args_schema=LogExpenseInput)
    def log_expense(
        store_name: str,
        items: Optional[Any] = None,
        total_amount: Optional[float] = None,
        date_str: Optional[str] = None,
        payment_method: Optional[str] = "cash",
    ) -> str:
        """Logs an expense transaction with line items into the database for the current user."""
        cached = check_idempotent_cache("log_expense")
        if cached:
            return cached

        # Complaint / Reclassification Guard:
        # If user is reporting a misclassification, requesting udhaar or deletion,
        # log_expense must NOT execute or insert a new transaction row.
        raw_lower = (raw_transcript or "").lower()
        complaint_signals = (
            "ادھار", "ادار", "خطہ", "کھاتہ", "غلط", "wrong", "mistake",
            "udhaar", "khata", "not an expense", "delete", "remove", "undo", "مٹا", "منسوخ"
        )
        cash_overrides = ("نقد", "naqd", "کیش")
        has_complaint = any(sig in raw_lower for sig in complaint_signals)
        has_cash_override = any(co in raw_lower for co in cash_overrides)
        if has_complaint and not has_cash_override:
            return json.dumps(
                {
                    "status": "rejected_complaint",
                    "message": "User is reporting a misclassification or requesting Udhaar/deletion. Do not call log_expense; follow the confirmation/reclassification flow.",
                },
                ensure_ascii=False,
            )

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
                return record_idempotent_result(
                    "log_expense",
                    json.dumps({
                        "status": "needs_confirmation",
                        "pending_id": pending.id,
                        "amount": round(final_total, 2),
                        "payment_method": final_pm,
                        "store_name": canonical_store,
                        "message": plausibility["reason"],
                        "expires_in_seconds": 300,
                        "instruction": "This expense is held in pending status on the server. Tell the user in their language that this large amount is pending confirmation and they must press the 'Confirm' button on their screen to record it, or say cancel. If they decline or say cancel, execute cancel_pending_expense. Do NOT attempt to confirm via chat."
                    }, ensure_ascii=False),
                )

            # If canonical_store is generic or default, check if recipient or vendor is mentioned in raw_transcript
            extracted_recip = extract_recipient_or_vendor(raw_transcript)
            if extracted_recip:
                if canonical_store in ("local store", "general store", "general purchase", "bank transfer", "bank_transfer", "bank_transfer transfer", "transfer", "cash", "easypaisa", "jazzcash", "card"):
                    canonical_store = extracted_recip
            elif raw_transcript and any(w in raw_transcript.lower() for w in ("transfer", "bheje", "bheja", "send", "sent")):
                if canonical_store in ("local store", "general store", "general purchase"):
                    clean_pm = final_pm.replace("_", " ")
                    canonical_store = clean_pm if "transfer" in clean_pm else f"{clean_pm} transfer"

            # Duplicate / Clarification Guard:
            # If the user recently recorded a transaction (within last 15 minutes), and this turn is a clarification,
            # addition of a recipient name/purpose, or answering a follow-up question with identical amount,
            # update the existing transaction instead of creating a duplicate row!
            now_pkt = get_pkt_now()
            recent_tx = (
                db.query(Transaction)
                .filter(Transaction.user_id == user_id, Transaction.deleted_at == None)
                .order_by(Transaction.id.desc())
                .first()
            )

            is_clarification = False
            if recent_tx:
                tx_age_seconds = (now_pkt - recent_tx.transaction_date).total_seconds()
                amount_matches = (final_total == 0 or abs(recent_tx.total_amount - final_total) < 1.0)
                
                raw_lower = (raw_transcript or "").lower()
                clarify_phrases = (
                    "add name", "name is", "naam", "recipient", "actually", "correct",
                    "change", "update", "likh do", "daal do", "bhi add", "bhi likh",
                    "yes", "haaji", "jee", "haan", "for rent", "purpose"
                )
                complaint_phrases = (
                    "ادھار", "ادار", "خطہ", "کھاتہ", "غلط", "نہیں", "wrong", "mistake",
                    "udhaar", "khata", "not an expense", "delete", "remove", "undo", "مٹا", "منسوخ"
                )
                has_complaint_signal = any(p in raw_lower for p in complaint_phrases)
                has_clarify_signal = any(p in raw_lower for p in clarify_phrases) and not has_complaint_signal
                
                recent_was_generic = recent_tx.store_name in (
                    "local store", "general store", "general purchase", "bank transfer",
                    "bank_transfer transfer", "bank_transfer", "easypaisa transfer", "jazzcash transfer", "transfer"
                )
                new_is_specific = canonical_store not in (
                    "local store", "general store", "general purchase", "bank transfer",
                    "bank_transfer transfer", "bank_transfer", "easypaisa transfer", "jazzcash transfer", "transfer"
                )

                if (tx_age_seconds <= 900) and amount_matches and not has_complaint_signal and (has_clarify_signal or (recent_was_generic and new_is_specific)):
                    is_clarification = True

            if is_clarification and recent_tx:
                # Update existing transaction in place
                # Never accept long sentences or complaint text as a store name
                if canonical_store and canonical_store != recent_tx.store_name:
                    if len(canonical_store.split()) <= 4 and not any(cp in canonical_store for cp in ("غلط", "نہیں", "بولا", "ایڈ کر", "ٹرانزیکشن")):
                        recent_tx.store_name = canonical_store
                if final_pm and final_pm != "cash" and recent_tx.payment_method == "cash":
                    recent_tx.payment_method = final_pm
                if parsed_items:
                    for it in parsed_items:
                        t_item = db.query(TransactionItem).filter(TransactionItem.transaction_id == recent_tx.id).first()
                        if t_item:
                            t_item.item_name = it["item_name"]
                            t_item.category = it["category"]
                            t_item.price = round(it["price"], 2)
                db.commit()
                db.refresh(recent_tx)

                alerts = [
                    b for b in budget_status(db, user_id) if b["level"] in ("warning", "over")
                ]

                return record_idempotent_result(
                    "log_expense",
                    json.dumps({
                        "status": "success",
                        "updated_existing": True,
                        "message": "Updated previous expense with recipient and details (no duplicate created).",
                        "transaction_id": recent_tx.id,
                        "user_id": user_id,
                        "store_name": recent_tx.store_name,
                        "total_amount_pkr": round(recent_tx.total_amount, 2),
                        "payment_method": recent_tx.payment_method,
                        "date": recent_tx.transaction_date.strftime("%Y-%m-%d"),
                        "budget_alerts": alerts,
                    }, ensure_ascii=False),
                )

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
            return record_idempotent_result("log_expense", json.dumps(result, ensure_ascii=False))
        except Exception as e:
            db.rollback()
            logger.error(f"Error logging expense for user {user_id}: {e}")
            return json.dumps({"status": "error", "message": str(e)})
        finally:
            db.close()

    @tool("update_last_expense", args_schema=UpdateLastExpenseInput)
    def update_last_expense(
        store_name: Optional[str] = None,
        category: Optional[str] = None,
        notes_or_purpose: Optional[str] = None,
        payment_method: Optional[str] = None,
        total_amount: Optional[float] = None,
    ) -> str:
        """Updates the user's most recent active expense transaction (e.g. adding recipient name, purpose/category, or correcting payment method/amount) without creating a duplicate entry."""
        cached = check_idempotent_cache("update_last_expense")
        if cached:
            return cached

        db: Session = SessionLocal()
        try:
            latest_tx = (
                db.query(Transaction)
                .filter(Transaction.user_id == user_id, Transaction.deleted_at == None)
                .order_by(Transaction.id.desc())
                .first()
            )
            if not latest_tx:
                return json.dumps({"status": "error", "message": "No active recent transaction found to update."})

            # Check if store_name / recipient was passed or can be extracted from raw_transcript
            clean_store = (store_name or "").strip().lower()
            if not clean_store and raw_transcript:
                clean_store = extract_recipient_or_vendor(raw_transcript)

            if clean_store:
                latest_tx.store_name = clean_store

            # Payment method
            if payment_method and payment_method.strip():
                latest_tx.payment_method = detect_payment_method(payment_method, raw_transcript)

            # Amount
            if total_amount is not None and float(total_amount) > 0:
                try:
                    val_total = validate_transaction_amount(float(total_amount))
                    latest_tx.total_amount = round(val_total, 2)
                except ValueError as ve:
                    return json.dumps({"status": "error", "message": str(ve)})

            # Update TransactionItem records (category, purpose/notes)
            items = db.query(TransactionItem).filter(TransactionItem.transaction_id == latest_tx.id).all()
            if category or notes_or_purpose or total_amount:
                if items:
                    for it in items:
                        if category and category.strip():
                            it.category = category.strip().lower()
                        if notes_or_purpose and notes_or_purpose.strip():
                            it.item_name = notes_or_purpose.strip().lower()
                        if total_amount is not None and len(items) == 1:
                            it.price = round(latest_tx.total_amount, 2)
                else:
                    new_item = TransactionItem(
                        transaction_id=latest_tx.id,
                        item_name=(notes_or_purpose or "general purchase").strip().lower(),
                        category=(category or "other").strip().lower(),
                        price=latest_tx.total_amount,
                        quantity=1.0,
                    )
                    db.add(new_item)

            db.commit()
            db.refresh(latest_tx)

            alerts = [
                b for b in budget_status(db, user_id) if b["level"] in ("warning", "over")
            ]

            return record_idempotent_result(
                "update_last_expense",
                json.dumps({
                    "status": "success",
                    "message": "Expense transaction updated successfully.",
                    "transaction_id": latest_tx.id,
                    "store_name": latest_tx.store_name,
                    "total_amount_pkr": round(latest_tx.total_amount, 2),
                    "payment_method": latest_tx.payment_method,
                    "date": latest_tx.transaction_date.strftime("%Y-%m-%d"),
                    "budget_alerts": alerts,
                }, ensure_ascii=False),
            )
        except Exception as e:
            db.rollback()
            logger.error(f"Error updating last expense for user {user_id}: {e}")
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
                analytics._is_expense_clause(),
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
                        "store_name": (tx.store_name.replace("_", " ").title() if tx.store_name else "Local Store").replace("Transfer Transfer", "Transfer"),
                        "total_amount_pkr": round(tx.total_amount, 2),
                        "payment_method": (tx.payment_method or "cash").replace("_", " ").title(),
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
                "galla_summary": galla_summary(db, user_id),
                "udhaar_summary": udhaar_summary(db, user_id),
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
        cached = check_idempotent_cache("delete_last_transaction")
        if cached:
            return cached

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

            return record_idempotent_result(
                "delete_last_transaction",
                json.dumps(
                    {
                        "status": "success",
                        "message": "Most recent transaction was successfully deleted.",
                        "deleted_transaction": deleted_info,
                    },
                    ensure_ascii=False,
                ),
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
        cached = check_idempotent_cache("set_budget")
        if cached:
            return cached

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
            return record_idempotent_result(
                "set_budget",
                json.dumps(
                    {"status": "success", "message": "Budget saved.", "budget": status[0] if status else None},
                    ensure_ascii=False,
                ),
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
        cached = check_idempotent_cache("add_recurring_expense")
        if cached:
            return cached

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
            return record_idempotent_result(
                "add_recurring_expense",
                json.dumps(
                    {"status": "success", "message": "Recurring expense saved.", "recurring": recurring_status(db, user_id)},
                    ensure_ascii=False,
                ),
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
        cached = check_idempotent_cache("mark_recurring_paid")
        if cached:
            return cached

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
            return record_idempotent_result(
                "mark_recurring_paid",
                json.dumps(
                    pay_recurring(db, user_id, item, raw_transcript=raw_transcript),
                    ensure_ascii=False,
                ),
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
        cached = check_idempotent_cache("cancel_pending_expense")
        if cached:
            return cached

        db: Session = SessionLocal()
        try:
            res = analytics.cancel_pending_transaction(db, user_id)
            return record_idempotent_result("cancel_pending_expense", json.dumps(res, ensure_ascii=False))
        except Exception as e:
            logger.error(f"Error cancelling pending expense for {user_id}: {e}")
            return json.dumps({"status": "error", "message": "Failed to cancel pending expense."})
        finally:
            db.close()

    @tool("log_sale", args_schema=LogSaleInput)
    def log_sale(
        customer_or_note: str = "daily sale",
        items: Optional[Any] = None,
        total_amount: Optional[float] = None,
        date_str: Optional[str] = None,
        payment_method: Optional[str] = "cash",
    ) -> str:
        """Logs a daily shop sale (farokht / cash-in) into the database for the current user and updates Aaj Ka Galla."""
        cached = check_idempotent_cache("log_sale")
        if cached:
            return cached

        db: Session = SessionLocal()
        try:
            canonical_store = (customer_or_note or "daily sale").strip().lower()
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

            parsed_items = []
            if isinstance(items, str):
                try:
                    loaded = json.loads(items)
                    items = loaded
                except Exception:
                    parts = re.split(r"[,;\n]+", items)
                    for p in parts:
                        p = p.strip()
                        if not p:
                            continue
                        m = re.search(
                            r"([a-zA-Z\s]+?)(?:[:=\-]?\s*(?:rs|pkr)?\s*(\d+(?:\.\d+)?))?$",
                            p,
                            re.I,
                        )
                        if m and m.group(1).strip():
                            p_name = m.group(1).strip().lower()
                            p_price = float(m.group(2)) if m.group(2) else 0.0
                            parsed_items.append(
                                {
                                    "item_name": p_name,
                                    "price": p_price,
                                    "quantity": 1.0,
                                    "category": "sale",
                                }
                            )

            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        p_name = str(item.get("item_name") or item.get("name") or "sale item").strip().lower()
                        p_price = float(item.get("price") or 0.0)
                        p_qty = float(item.get("quantity") or 1.0)
                        p_cat = str(item.get("category") or "sale").strip().lower()
                        parsed_items.append(
                            {
                                "item_name": p_name,
                                "price": p_price,
                                "quantity": p_qty,
                                "category": p_cat,
                            }
                        )
                    elif hasattr(item, "item_name"):
                        parsed_items.append(
                            {
                                "item_name": item.item_name.strip().lower(),
                                "price": float(item.price),
                                "quantity": float(item.quantity) if float(item.quantity) > 0 else 1.0,
                                "category": str(item.category or "sale").strip().lower(),
                            }
                        )

            computed_sum = sum(float(it["price"]) for it in parsed_items)
            if total_amount is not None and float(total_amount) > 0:
                user_total = float(total_amount)
                if computed_sum > 0 and abs(computed_sum - user_total) <= 0.05 * user_total:
                    final_total = computed_sum
                else:
                    final_total = user_total
            elif computed_sum > 0:
                final_total = computed_sum
            else:
                final_total = 0.0

            final_pm = detect_payment_method(payment_method, raw_transcript)
            try:
                final_total = validate_transaction_amount(final_total)
            except ValueError as ve:
                return json.dumps({"status": "error", "message": str(ve)})

            tx = Transaction(
                user_id=user_id,
                store_name=canonical_store,
                total_amount=round(final_total, 2),
                transaction_date=tx_date,
                raw_transcript=raw_transcript or None,
                payment_method=final_pm,
                tx_type="sale",
            )
            db.add(tx)
            db.flush()

            logged_items = []
            if parsed_items:
                if len(parsed_items) == 1 and abs(parsed_items[0]["price"] - final_total) > 1.0:
                    parsed_items[0]["price"] = final_total
                for it in parsed_items:
                    t_item = TransactionItem(
                        transaction_id=tx.id,
                        item_name=it["item_name"],
                        category=it["category"] or "sale",
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
            else:
                t_item = TransactionItem(
                    transaction_id=tx.id,
                    item_name=canonical_store,
                    category="sale",
                    price=round(final_total, 2),
                    quantity=1.0,
                )
                db.add(t_item)
                logged_items.append(
                    {
                        "item": canonical_store,
                        "price": round(final_total, 2),
                        "quantity": 1.0,
                        "category": "sale",
                    }
                )

            db.commit()
            db.refresh(tx)

            result = {
                "status": "success",
                "message": "Daily sale (farokht) recorded.",
                "transaction_id": tx.id,
                "tx_type": "sale",
                "customer_or_note": canonical_store,
                "total_amount_pkr": round(final_total, 2),
                "payment_method": tx.payment_method,
                "date": tx.transaction_date.strftime("%Y-%m-%d"),
                "items": logged_items,
                "galla": galla_summary(db, user_id),
            }
            return record_idempotent_result("log_sale", json.dumps(result, ensure_ascii=False))
        except Exception as e:
            db.rollback()
            logger.error(f"Error logging sale for user {user_id}: {e}")
            return json.dumps({"status": "error", "message": str(e)})
        finally:
            db.close()

    @tool("manage_udhaar", args_schema=ManageUdhaarInput)
    def manage_udhaar(
        action: str,
        person_name: Optional[str] = None,
        amount: Optional[float] = None,
        direction: Optional[str] = None,
        notes: Optional[str] = None,
        phone_number: Optional[str] = None,
    ) -> str:
        """Manages the shopkeeper's Udhaar / Khata book: give_credit (lene hain), take_credit (dene hain), settle (wasooli), or list."""
        clean_action = (action or "list").strip().lower()
        if clean_action != "list":
            cached = check_idempotent_cache("manage_udhaar")
            if cached:
                return cached

        db: Session = SessionLocal()
        try:
            if clean_action in ("give_credit", "receivable", "lend", "lene_hain", "udhaar_diya", "credit", "udhaar", "khata", "saaman_diya", "maal_diya"):
                if not person_name or amount is None:
                    return json.dumps({"status": "error", "message": "person_name and amount are required to record udhaar."})
                res = add_udhaar_entry(
                    db,
                    user_id=user_id,
                    person_name=person_name,
                    amount=float(amount),
                    direction="receivable",
                    notes=notes,
                    phone_number=phone_number,
                )
                res["summary"] = udhaar_summary(db, user_id)
                return record_idempotent_result("manage_udhaar", json.dumps(res, ensure_ascii=False))
            elif clean_action in ("take_credit", "payable", "borrow", "dene_hain", "udhaar_liya"):
                if not person_name or amount is None:
                    return json.dumps({"status": "error", "message": "person_name and amount are required to record udhaar."})
                res = add_udhaar_entry(
                    db,
                    user_id=user_id,
                    person_name=person_name,
                    amount=float(amount),
                    direction="payable",
                    notes=notes,
                    phone_number=phone_number,
                )
                res["summary"] = udhaar_summary(db, user_id)
                return record_idempotent_result("manage_udhaar", json.dumps(res, ensure_ascii=False))
            elif clean_action in ("settle", "wasooli", "pay", "paid"):
                if not person_name:
                    return json.dumps({"status": "error", "message": "person_name is required to settle udhaar."})
                res = settle_udhaar(
                    db,
                    user_id=user_id,
                    person_name=person_name,
                    payment_amount=float(amount) if amount is not None and float(amount) > 0 else None,
                    direction=direction,
                    confirm_overpay=False,
                )
                res["summary"] = udhaar_summary(db, user_id)
                return record_idempotent_result("manage_udhaar", json.dumps(res, ensure_ascii=False))
            else:
                summary = udhaar_summary(db, user_id)
                return json.dumps({"status": "success", "udhaar": summary}, ensure_ascii=False)
        except Exception as e:
            db.rollback()
            logger.error(f"Error in manage_udhaar for user {user_id}: {e}")
            return json.dumps({"status": "error", "message": str(e)})
        finally:
            db.close()

    return [
        log_expense,
        update_last_expense,
        log_sale,
        manage_udhaar,
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
    ui_lang: Optional[str] = None,
) -> str:
    """
    Executes the LangChain agent for the given user, scoping tools to user_id.
    Maintains and respects multi-turn conversation history across turns.
    """
    if not user_text or not user_text.strip():
        return "Please provide an expense message or voice note."

    clean_user_id = user_id.strip() or "default"
    turn_state = {"attempt": 0, "mutations_by_tool": {}}
    tools = get_tools_for_user(clean_user_id, raw_transcript=raw_transcript, turn_state=turn_state)
    system_prompt = build_system_prompt(ui_lang=ui_lang)
    groq_keys = get_groq_api_keys() or [""]
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
        response = None
        for attempt in range(2):
            turn_state["attempt"] = attempt
            try:
                response = agent.invoke({"messages": messages}, config={"recursion_limit": AGENT_RECURSION_LIMIT})
                break
            except Exception as invoke_err:
                err_text = str(invoke_err)
                if ("429" in err_text or "rate_limit" in err_text.lower() or "too many tokens" in err_text.lower()) and attempt == 0:
                    turn_state["attempt"] = attempt + 1
                    if len(groq_keys) > 1:
                        logger.warning("Groq rate limit 429 encountered, rotating to secondary key immediately...")
                        llm = get_chat_llm(api_key=groq_keys[1])
                        agent = create_agent(model=llm, tools=tools, system_prompt=system_prompt)
                    else:
                        logger.warning(f"Groq rate limit 429 encountered, backing off 3.5s before retry (attempt {attempt+1})...")
                        time.sleep(3.5)
                    continue
                raise invoke_err

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
        global _BEDROCK_THROTTLED_UNTIL, _VALIDATED_MODEL
        logger.error(f"Agent execution error: {e}")
        err_str = str(e)
        if "429" in err_str or "rate_limit" in err_str.lower() or "too many tokens" in err_str.lower():
            _VALIDATED_MODEL = None
        # Trip Bedrock circuit breaker on throttling/validation error so subsequent turns don't hang
        if "ThrottlingException" in err_str or "ValidationException" in err_str or "Too many tokens" in err_str:
            _BEDROCK_THROTTLED_UNTIL = time.time() + 300.0
            logger.warning("AWS Bedrock throttled. Fast fallback to Groq active for 5 minutes.")

        # Graceful fallback attempt with alternate candidate model
        try:
            turn_state["attempt"] += 1
            curr_model = getattr(llm, "model_name", getattr(llm, "model", ""))
            alt_candidates = [m for m in MODEL_CANDIDATES if m and m != curr_model]
            fallback_model = alt_candidates[0] if alt_candidates else "qwen/qwen3.8-27b"
            fallback_groq_key = groq_keys[1] if len(groq_keys) > 1 else (groq_keys[0] if groq_keys else "")
            llm_fallback = ChatGroq(
                model=fallback_model,
                temperature=0,
                max_tokens=600,
                timeout=LLM_TIMEOUT_SECONDS,
                groq_api_key=fallback_groq_key,
            )
            fallback_agent = create_agent(
                model=llm_fallback,
                tools=tools,
                system_prompt=system_prompt,
            )
            fallback_res = fallback_agent.invoke({"messages": messages}, config={"recursion_limit": AGENT_RECURSION_LIMIT})
            fallback_reply = fallback_res["messages"][-1].content
            return ensure_urdu_script(fallback_reply)
        except Exception as e2:
            logger.error(f"Fallback agent execution error: {e2}")
            err_str2 = str(e2)
            if "429" in err_str2 or "rate_limit" in err_str2.lower() or "tokens" in err_str2.lower():
                if ui_lang == "en":
                    return "The AI assistant is temporarily busy with high request volume. Please wait a few seconds and try again."
                return "سسٹم پر اس وقت زیادہ رش ہے۔ براہ کرم چند سیکنڈ بعد دوبارہ کوشش کریں۔"
            if ui_lang == "en":
                return "We encountered an issue processing your request. Please try again."
            return "درخواست پر کارروائی کے دوران مسئلہ پیش آیا۔ براہ کرم دوبارہ کوشش کریں۔"

