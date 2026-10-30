import os
import io
import csv
import hmac
import base64
import hashlib
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Form, File, UploadFile, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import init_db, get_db, Transaction, TransactionItem, Budget, RecurringExpense
from agent import run_agent, transcribe_audio, clear_user_history
import analytics

load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("hisaab_ai")

# Initialize database schema
init_db()

app = FastAPI(
    title="HisaabAI — Voice & Text Expense Tracker",
    description="Multilingual (Urdu, Punjabi, English) Expense Tracker powered by Groq and LangChain",
    version="1.0.0",
)

# Ensure templates and static directories exist
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")
os.makedirs(TEMPLATES_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)


# ---------------------------------------------------------------------------
# Frontend Root Route
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def serve_home(request: Request):
    """Serves the main single-page application."""
    return templates.TemplateResponse(request=request, name="index.html")


# ---------------------------------------------------------------------------
# API: Chat / Voice Processing
# ---------------------------------------------------------------------------
@app.post("/api/chat")
async def chat_endpoint(
    user_id: str = Form(...),
    message: Optional[str] = Form(None),
    audio: Optional[UploadFile] = File(None),
    history: Optional[str] = Form(None),
):
    """
    Accepts text message, audio voice note, or both from the frontend.
    Transcribes audio using Groq whisper-large-v3, executes the LangChain agent
    with multi-turn conversation memory, and returns the structured response.
    """
    clean_user_id = (user_id or "default").strip()
    if not clean_user_id:
        clean_user_id = "default"

    transcript_text: Optional[str] = None

    # Handle voice note upload
    if audio is not None and audio.filename:
        try:
            audio_bytes = await audio.read()
            if len(audio_bytes) > 100:
                logger.info(f"Received audio recording ({len(audio_bytes)} bytes) for user '{clean_user_id}'")
                transcript_text = transcribe_audio(
                    audio_bytes, filename=audio.filename or "recording.webm"
                )
                logger.info(f"Whisper transcript: '{transcript_text}'")
        except Exception as e:
            logger.error(f"Error reading or transcribing audio: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Audio transcription error: {str(e)}",
            )

    # Determine user's effective prompt text
    effective_text = ""
    raw_transcript_record = ""

    if transcript_text and message and message.strip():
        effective_text = f"{message.strip()}\n{transcript_text.strip()}"
        raw_transcript_record = transcript_text.strip()
    elif transcript_text:
        effective_text = transcript_text.strip()
        raw_transcript_record = transcript_text.strip()
    elif message and message.strip():
        effective_text = message.strip()
        raw_transcript_record = message.strip()
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either a text message or a valid audio recording must be provided.",
        )

    # Parse chat history if provided
    parsed_history = []
    if history:
        try:
            import json
            parsed_history = json.loads(history)
            if not isinstance(parsed_history, list):
                parsed_history = []
        except Exception:
            parsed_history = []

    # Execute LangChain Agent
    try:
        reply = run_agent(
            user_id=clean_user_id,
            user_text=effective_text,
            raw_transcript=raw_transcript_record,
            chat_history=parsed_history,
        )
        return {
            "user_id": clean_user_id,
            "transcript": transcript_text,
            "reply": reply,
        }
    except Exception as e:
        logger.error(f"Error running agent for user '{clean_user_id}': {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agent execution error: {str(e)}",
        )


@app.post("/api/chat/clear")
async def clear_chat_history(user_id: str = Form(...)):
    """Clears conversation context for user."""
    clear_user_history(user_id)
    return {"status": "success", "message": f"History cleared for user {user_id}"}


# ---------------------------------------------------------------------------
# API: User Transactions Ledger & Summary
# ---------------------------------------------------------------------------
@app.get("/api/transactions/{user_id}")
async def get_user_transactions(user_id: str, db: Session = Depends(get_db)):
    """
    Returns the user's monthly spending total, store-wise breakdown,
    and recent transactions for the live ledger dashboard.
    """
    clean_user_id = user_id.strip()
    now = datetime.now()
    month_start = datetime(now.year, now.month, 1, 0, 0, 0)

    # 1. Total monthly spending
    monthly_stats = (
        db.query(
            func.coalesce(func.sum(Transaction.total_amount), 0.0).label("total"),
            func.count(Transaction.id).label("count"),
        )
        .filter(
            Transaction.user_id == clean_user_id,
            Transaction.transaction_date >= month_start,
        )
        .first()
    )

    monthly_total = float(monthly_stats.total) if monthly_stats else 0.0
    monthly_count = int(monthly_stats.count) if monthly_stats else 0

    # 2. Store-wise spending for this month
    store_stats = (
        db.query(
            Transaction.store_name,
            func.sum(Transaction.total_amount).label("store_total"),
            func.count(Transaction.id).label("visit_count"),
        )
        .filter(
            Transaction.user_id == clean_user_id,
            Transaction.transaction_date >= month_start,
        )
        .group_by(Transaction.store_name)
        .order_by(func.sum(Transaction.total_amount).desc())
        .all()
    )

    store_breakdown = [
        {
            "store_name": s.store_name.title(),
            "total_pkr": round(float(s.store_total), 2),
            "visit_count": s.visit_count,
        }
        for s in store_stats
    ]

    top_store = store_breakdown[0]["store_name"] if store_breakdown else "N/A"

    # 3. Recent 25 transactions with line items
    recent_txs = (
        db.query(Transaction)
        .filter(Transaction.user_id == clean_user_id)
        .order_by(Transaction.transaction_date.desc(), Transaction.id.desc())
        .limit(25)
        .all()
    )

    return {
        "user_id": clean_user_id,
        "monthly_total": round(monthly_total, 2),
        "monthly_count": monthly_count,
        "top_store": top_store,
        "store_breakdown": store_breakdown,
        "category_breakdown": analytics.category_breakdown(db, clean_user_id, month_start),
        "daily_trend": analytics.daily_trend(db, clean_user_id, days=30),
        "payment_split": analytics.payment_split(db, clean_user_id, month_start),
        "budgets": analytics.budget_status(db, clean_user_id),
        "recurring": analytics.recurring_status(db, clean_user_id),
        "recent_transactions": [tx.to_dict() for tx in recent_txs],
    }


# ---------------------------------------------------------------------------
# API: Export (CSV opens directly in Excel; UTF-8 BOM keeps Urdu text intact)
# ---------------------------------------------------------------------------
@app.get("/api/export/{user_id}")
async def export_csv(user_id: str, db: Session = Depends(get_db)):
    clean_user_id = user_id.strip()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(analytics.EXPORT_HEADER)
    writer.writerows(analytics.export_rows(db, clean_user_id))
    filename = f"hisaabai_{clean_user_id}_{datetime.now().strftime('%Y%m%d')}.csv"
    return Response(
        content="\ufeff" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# API: Budgets
# ---------------------------------------------------------------------------
@app.get("/api/budgets/{user_id}")
async def list_budgets(user_id: str, db: Session = Depends(get_db)):
    return {"budgets": analytics.budget_status(db, user_id.strip())}


@app.post("/api/budgets")
async def save_budget(
    user_id: str = Form(...),
    monthly_limit: float = Form(...),
    category: str = Form("total"),
    db: Session = Depends(get_db),
):
    uid = user_id.strip()
    cat = (category or "total").strip().lower()
    if monthly_limit <= 0:
        raise HTTPException(status_code=400, detail="Budget must be greater than 0.")
    b = db.query(Budget).filter(Budget.user_id == uid, Budget.category == cat).first()
    if b:
        b.monthly_limit = monthly_limit
    else:
        db.add(Budget(user_id=uid, category=cat, monthly_limit=monthly_limit))
    db.commit()
    return {"budgets": analytics.budget_status(db, uid)}


@app.delete("/api/budgets/{user_id}/{budget_id}")
async def delete_budget(user_id: str, budget_id: int, db: Session = Depends(get_db)):
    b = db.query(Budget).filter(Budget.user_id == user_id.strip(), Budget.id == budget_id).first()
    if not b:
        raise HTTPException(status_code=404, detail="Budget not found.")
    db.delete(b)
    db.commit()
    return {"status": "success"}


# ---------------------------------------------------------------------------
# API: Recurring monthly expenses
# ---------------------------------------------------------------------------
@app.get("/api/recurring/{user_id}")
async def list_recurring(user_id: str, db: Session = Depends(get_db)):
    return {"recurring": analytics.recurring_status(db, user_id.strip())}


@app.post("/api/recurring")
async def save_recurring(
    user_id: str = Form(...),
    title: str = Form(...),
    amount: float = Form(...),
    due_day: int = Form(1),
    category: str = Form("utilities"),
    db: Session = Depends(get_db),
):
    uid = user_id.strip()
    clean_title = title.strip().lower()
    if not clean_title or amount <= 0:
        raise HTTPException(status_code=400, detail="Title and a positive amount are required.")
    day = min(max(due_day, 1), 28)
    item = (
        db.query(RecurringExpense)
        .filter(RecurringExpense.user_id == uid, RecurringExpense.title == clean_title)
        .first()
    )
    if item:
        item.amount, item.due_day, item.category = amount, day, category.strip().lower()
    else:
        db.add(
            RecurringExpense(
                user_id=uid, title=clean_title, amount=amount, due_day=day, category=category.strip().lower()
            )
        )
    db.commit()
    return {"recurring": analytics.recurring_status(db, uid)}


@app.post("/api/recurring/{user_id}/{recurring_id}/pay")
async def pay_recurring_item(user_id: str, recurring_id: int, db: Session = Depends(get_db)):
    uid = user_id.strip()
    item = (
        db.query(RecurringExpense)
        .filter(RecurringExpense.user_id == uid, RecurringExpense.id == recurring_id)
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="Recurring expense not found.")
    return analytics.pay_recurring(db, uid, item)


@app.delete("/api/recurring/{user_id}/{recurring_id}")
async def delete_recurring_item(user_id: str, recurring_id: int, db: Session = Depends(get_db)):
    item = (
        db.query(RecurringExpense)
        .filter(RecurringExpense.user_id == user_id.strip(), RecurringExpense.id == recurring_id)
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="Recurring expense not found.")
    db.delete(item)
    db.commit()
    return {"status": "success"}


# ---------------------------------------------------------------------------
# PWA: manifest + service worker (must be served from the site root scope)
# ---------------------------------------------------------------------------
@app.get("/manifest.webmanifest")
async def pwa_manifest():
    return FileResponse(
        os.path.join(STATIC_DIR, "manifest.webmanifest"), media_type="application/manifest+json"
    )


@app.get("/sw.js")
async def pwa_service_worker():
    return FileResponse(
        os.path.join(STATIC_DIR, "sw.js"),
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


# ---------------------------------------------------------------------------
# WhatsApp (Twilio) webhook: voice notes and text -> same agent pipeline
# ---------------------------------------------------------------------------
def _twiml(message: str) -> Response:
    from xml.sax.saxutils import escape

    body = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{escape(message)}</Message></Response>'
    return Response(content=body, media_type="application/xml")


def _valid_twilio_signature(url: str, params: Dict[str, str], signature: str, token: str) -> bool:
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    digest = hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, signature or "")


@app.post("/api/whatsapp")
async def whatsapp_webhook(request: Request):
    """
    Configure in Twilio: WhatsApp sandbox/sender -> 'When a message comes in' -> POST
    {PUBLIC_BASE_URL}/api/whatsapp. Requires TWILIO_AUTH_TOKEN (and TWILIO_ACCOUNT_SID
    to download voice notes). Each WhatsApp number is its own HisaabAI user.
    """
    token = os.getenv("TWILIO_AUTH_TOKEN")
    if not token:
        raise HTTPException(status_code=503, detail="WhatsApp integration is not configured.")

    form = await request.form()
    params = {k: str(v) for k, v in form.items()}

    if os.getenv("WHATSAPP_VALIDATE_SIGNATURE", "true").lower() != "false":
        base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
        url = f"{base}/api/whatsapp" if base else str(request.url)
        if not _valid_twilio_signature(url, params, request.headers.get("X-Twilio-Signature", ""), token):
            raise HTTPException(status_code=403, detail="Invalid Twilio signature.")

    sender = params.get("From", "").replace("whatsapp:", "").strip()
    if not sender:
        raise HTTPException(status_code=400, detail="Missing sender.")

    body_text = (params.get("Body") or "").strip()
    transcript: Optional[str] = None

    if int(params.get("NumMedia", "0") or 0) > 0 and params.get("MediaContentType0", "").startswith("audio"):
        sid = os.getenv("TWILIO_ACCOUNT_SID", "")
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
                resp = await client.get(params["MediaUrl0"], auth=(sid, token))
                resp.raise_for_status()
            transcript = await run_in_threadpool(transcribe_audio, resp.content, "whatsapp_voice.ogg")
        except Exception as e:
            logger.error(f"WhatsApp voice download/transcription failed: {e}")
            return _twiml("Voice note samajh nahi aaya, dobara bhejein ya likh kar bataen. / Could not process the voice note.")

    text = transcript or body_text
    if not text:
        return _twiml("Please send a text or a voice note. / ٹیکسٹ یا وائس نوٹ بھیجیں۔")

    try:
        reply = await run_in_threadpool(run_agent, user_id=f"wa:{sender}", user_text=text, raw_transcript=text)
    except Exception as e:
        logger.error(f"WhatsApp agent error for {sender}: {e}")
        return _twiml("Server busy, please retry in a moment. / ابھی مصروف ہے، دوبارہ کوشش کریں۔")

    # WhatsApp uses *bold*; Markdown uses **bold**. Cap at the 1600-char limit.
    reply = reply.replace("**", "*")
    return _twiml(reply[:1500])


# ---------------------------------------------------------------------------
# API: Delete Specific Transaction
# ---------------------------------------------------------------------------
@app.delete("/api/transactions/{user_id}/{transaction_id}")
async def delete_transaction(
    user_id: str,
    transaction_id: int,
    db: Session = Depends(get_db),
):
    """
    Deletes a specific transaction belonging strictly to user_id.
    Cascade deletes all associated transaction_items.
    """
    clean_user_id = user_id.strip()
    tx = (
        db.query(Transaction)
        .filter(
            Transaction.user_id == clean_user_id,
            Transaction.id == transaction_id,
        )
        .first()
    )

    if not tx:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transaction #{transaction_id} not found for user '{clean_user_id}'.",
        )

    deleted_store = tx.store_name.title()
    deleted_amount = tx.total_amount

    db.delete(tx)
    db.commit()

    return {
        "status": "success",
        "deleted_id": transaction_id,
        "message": f"Deleted transaction #{transaction_id} ({deleted_store} - PKR {deleted_amount})",
    }


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("APP_HOST", "0.0.0.0")
    port = int(os.getenv("APP_PORT", "8000"))
    uvicorn.run("main:app", host=host, port=port, reload=True)
