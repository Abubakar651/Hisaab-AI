import os
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Form, File, UploadFile, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import init_db, get_db, Transaction, TransactionItem
from agent import run_agent, transcribe_audio

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
):
    """
    Accepts text message, audio voice note, or both from the frontend.
    Transcribes audio using Groq whisper-large-v3, executes the LangChain agent,
    and returns the structured response.
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

    # Execute LangChain Agent
    try:
        reply = run_agent(
            user_id=clean_user_id,
            user_text=effective_text,
            raw_transcript=raw_transcript_record,
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
        "recent_transactions": [tx.to_dict() for tx in recent_txs],
    }


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
