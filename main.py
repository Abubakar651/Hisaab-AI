import os
import sys
import io
import re
import csv
import hmac
import time
import json
import uuid
import base64
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any, Tuple

import httpx
import requests
from cachecontrol import CacheControl
from dotenv import load_dotenv
from pydantic import BaseModel
from fastapi import (
    FastAPI,
    Request,
    Form,
    File,
    UploadFile,
    Depends,
    HTTPException,
    status,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import func
from sqlalchemy.orm import Session

import firebase_admin
from firebase_admin import auth as admin_auth
from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests

from database import (
    init_db,
    get_db,
    get_pkt_now,
    SessionLocal,
    Transaction,
    TransactionItem,
    Budget,
    RecurringExpense,
    UserAccount,
    PendingTransaction,
)
from agent import run_agent, transcribe_audio, clear_user_history
import analytics

load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("hisaab_ai")

# ---------------------------------------------------------------------------
# Environment & Firebase Configuration (Fail-Closed Default)
# ---------------------------------------------------------------------------
APP_ENV = os.getenv("APP_ENV", "").strip().lower()
if not APP_ENV:
    # Fail-closed default: if unspecified, detect local dev flags (--reload or test harness)
    if any(arg in sys.argv for arg in ("--reload", "--reload-dir", "--reload-include")) or "pytest" in sys.modules or (sys.argv and "pytest" in sys.argv[0]):
        APP_ENV = "development"
    else:
        APP_ENV = "production"

IS_DEV = APP_ENV in ("dev", "development", "test")

FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "").strip()
if not IS_DEV and not FIREBASE_PROJECT_ID:
    raise RuntimeError("FIREBASE_PROJECT_ID environment variable is required in production mode.")

# Initialize Firebase Admin SDK once at startup
try:
    if not firebase_admin._apps:
        if FIREBASE_PROJECT_ID:
            firebase_admin.initialize_app(options={"projectId": FIREBASE_PROJECT_ID})
        else:
            firebase_admin.initialize_app()
except Exception as e:
    logger.warning(f"firebase_admin initialization note: {e}")

# In-memory certificate-cached request adapter for fallback verification
_cached_session = CacheControl(requests.Session())
_cached_request_adapter = google_requests.Request(session=_cached_session)

MAX_AUDIO_SIZE = 5 * 1024 * 1024  # 5 MB
VALID_AUDIO_EXTENSIONS = (".webm", ".ogg", ".mp3", ".wav", ".m4a", ".aac")

# Initialize database schema
init_db()

app = FastAPI(
    title="HisaabAI — Voice & Text Expense Tracker",
    description="Multilingual (Urdu, Punjabi, English) Expense Tracker powered by Groq and LangChain",
    version="1.0.0",
)

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Enforces strict defense-in-depth security headers (CSP, nosniff, frame denial)."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin-allow-popups"

    auth_domain = os.getenv("FIREBASE_AUTH_DOMAIN", "").strip()
    frame_src_parts = ["https://accounts.google.com"]
    if auth_domain:
        if not auth_domain.startswith("https://"):
            frame_src_parts.append(f"https://{auth_domain}")
        else:
            frame_src_parts.append(auth_domain)
    frame_src = " ".join(frame_src_parts)

    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' https://apis.google.com https://cdn.tailwindcss.com https://unpkg.com https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
        "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net https://unpkg.com data:; "
        "img-src 'self' data: https:; "
        "connect-src 'self' https://identitytoolkit.googleapis.com https://securetoken.googleapis.com https://www.googleapis.com; "
        f"frame-src {frame_src}; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none';"
    )
    return response

# Global 500 error handler returning sanitized generic response with request ID
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    req_id = str(uuid.uuid4())
    logger.exception(f"Unhandled server error [request_id={req_id}] on {request.method} {request.url.path}: {exc}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "An internal server error occurred.", "request_id": req_id},
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
# Firebase Authentication & FastAPI Dependency
# ---------------------------------------------------------------------------
class AuthenticatedUser(str):
    """
    Represents an authenticated user verified via Firebase ID Token.
    Subclasses str so it can be passed interchangeably anywhere a user_id string is expected,
    while also exposing tenant attributes (firebase_uid, display_name, email, photo_url, auth_provider).
    """
    firebase_uid: str
    display_name: str
    photo_url: Optional[str]
    auth_provider: str
    email: str

    def __new__(
        cls,
        user_id: str,
        firebase_uid: str = "",
        display_name: str = "",
        photo_url: Optional[str] = None,
        auth_provider: str = "google",
        email: Optional[str] = None,
    ):
        instance = super().__new__(cls, user_id)
        instance.user_id = user_id
        instance.firebase_uid = firebase_uid
        instance.display_name = display_name
        instance.photo_url = photo_url
        instance.auth_provider = auth_provider
        instance.email = email or user_id
        return instance


def verify_firebase_token_claims(token: str) -> Dict[str, Any]:
    """
    Verifies Firebase ID token server-side.
    Uses firebase_admin with automatic fallback to certificate-cached google-auth id_token verification.
    Enforces aud, iss, email presence, email_verified, and authorized providers (google.com or password).
    """
    project_id = os.getenv("FIREBASE_PROJECT_ID", "").strip()
    if not IS_DEV and not project_id:
        raise RuntimeError("FIREBASE_PROJECT_ID environment variable is required in production mode.")

    if not token or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please sign in.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    claims = None
    # 1. Primary verification via Firebase Admin SDK
    try:
        claims = admin_auth.verify_id_token(token.strip(), check_revoked=False)
    except Exception as admin_err:
        # 2. Resilient fallback via google-auth with CacheControl session
        try:
            claims = google_id_token.verify_firebase_token(
                token.strip(),
                _cached_request_adapter,
                audience=project_id if project_id else None,
            )
        except Exception as ga_err:
            logger.warning(f"Firebase token verification failed (admin: {admin_err}, fallback: {ga_err})")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired authentication token.",
                headers={"WWW-Authenticate": "Bearer"},
            )

    if not isinstance(claims, dict):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token claims.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Strictly assert audience and issuer
    if project_id:
        aud = claims.get("aud")
        iss = claims.get("iss")
        expected_iss = f"https://securetoken.google.com/{project_id}"
        if aud != project_id:
            logger.warning(f"Firebase token audience mismatch: got '{aud}', expected '{project_id}'")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Firebase token audience mismatch.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if iss != expected_iss:
            logger.warning(f"Firebase token issuer mismatch: got '{iss}', expected '{expected_iss}'")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Firebase token issuer mismatch.",
                headers={"WWW-Authenticate": "Bearer"},
            )

    # Provider enforcement: allow ONLY google.com and password
    fb_claims = claims.get("firebase", {})
    provider = fb_claims.get("sign_in_provider")
    if provider not in ("google.com", "password"):
        logger.warning(f"Unsupported sign-in provider attempted: '{provider}'")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unsupported authentication provider.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Email verification requirements
    if provider == "password" and not claims.get("email_verified"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="email_not_verified",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if provider == "google.com" and not claims.get("email_verified"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google account email is not verified.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Non-empty email required
    email = (claims.get("email") or "").strip()
    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token does not contain a verified email.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return claims


security_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    auth_header: Optional[HTTPAuthorizationCredentials] = Depends(security_bearer),
    db: Session = Depends(get_db),
) -> AuthenticatedUser:
    """
    FastAPI dependency that reads Bearer token from Authorization header.
    Verifies Firebase token server-side, ensures a UserAccount row exists,
    and returns AuthenticatedUser.
    """
    token = None
    if auth_header and auth_header.credentials:
        token = auth_header.credentials.strip()
    elif "Authorization" in request.headers:
        hdr = request.headers["Authorization"].strip()
        if hdr.lower().startswith("bearer "):
            token = hdr[7:].strip()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please sign in.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    claims = verify_firebase_token_claims(token)
    clean_uid = claims["email"].strip().lower()
    provider_name = "google" if claims.get("firebase", {}).get("sign_in_provider") == "google.com" else "password"
    disp_name = claims.get("name") or clean_uid.split("@")[0].capitalize()
    photo_url = claims.get("picture")
    firebase_uid = str(claims.get("uid") or claims.get("sub") or "")

    # Upsert user account row
    account = db.query(UserAccount).filter(UserAccount.user_id == clean_uid).first()
    if account:
        account.last_login = get_pkt_now()
        if firebase_uid:
            account.firebase_uid = firebase_uid
        if disp_name and (not account.display_name or account.display_name == clean_uid):
            account.display_name = disp_name
        if photo_url:
            account.photo_url = photo_url
        account.auth_provider = provider_name
        account.email = clean_uid
        account.identity_verified = True
        account.password_hash = None
    else:
        account = UserAccount(
            user_id=clean_uid,
            firebase_uid=firebase_uid,
            auth_provider=provider_name,
            display_name=disp_name,
            email=clean_uid,
            photo_url=photo_url,
            identity_verified=True,
            password_hash=None,
            token_version=0,
            created_at=get_pkt_now(),
            last_login=get_pkt_now(),
        )
        db.add(account)
    db.commit()

    return AuthenticatedUser(
        user_id=clean_uid,
        firebase_uid=firebase_uid,
        display_name=account.display_name or disp_name,
        photo_url=account.photo_url or photo_url,
        auth_provider=provider_name,
        email=clean_uid,
    )


def verify_user_access(requested_user_id: str, authenticated_user_id: str):
    """
    Enforces multi-tenant isolation.
    Rejects any cross-user data access with HTTP 403 Forbidden.
    """
    if requested_user_id.strip() != authenticated_user_id.strip():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you do not have permission to access another user's data.",
        )



# ---------------------------------------------------------------------------
# Frontend Root Route
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def serve_home(request: Request):
    """Serves the main single-page application."""
    return templates.TemplateResponse(request=request, name="index.html")


# ---------------------------------------------------------------------------
# API: Chat / Voice Processing (Threadpooled & Authenticated)
# ---------------------------------------------------------------------------
@app.post("/api/chat")
async def chat_endpoint(
    request: Request,
    user_id: Optional[str] = Form(None),
    message: Optional[str] = Form(None),
    audio: Optional[UploadFile] = File(None),
    history: Optional[str] = Form(None),
    current_user: str = Depends(get_current_user),
):
    """
    Accepts text message, audio voice note, or both from the frontend.
    Runs ASR and agent asynchronously in threadpools without blocking the event loop.
    Enforces tenant identity strictly via session token.
    """
    if user_id and user_id.strip() != current_user:
        verify_user_access(user_id, current_user)

    effective_user_id = current_user
    transcript_text: Optional[str] = None

    # Handle voice note upload
    if audio is not None and audio.filename:
        # Validate MIME-type and extension
        ext = os.path.splitext(audio.filename.lower())[1]
        ct = (audio.content_type or "").lower()
        if not (ct.startswith("audio/") or ext in VALID_AUDIO_EXTENSIONS):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid audio file format. Only audio files are allowed.",
            )

        try:
            audio_bytes = await audio.read()
            if len(audio_bytes) > MAX_AUDIO_SIZE:
                raise HTTPException(
                    status_code=getattr(status, "HTTP_413_CONTENT_TOO_LARGE", 413),
                    detail="Audio file exceeds maximum allowed size (5 MB).",
                )

            if len(audio_bytes) > 100:
                logger.info(f"Received audio recording ({len(audio_bytes)} bytes) for user '{effective_user_id}'")
                transcript_text = await run_in_threadpool(
                    transcribe_audio,
                    audio_bytes,
                    filename=audio.filename or "recording.webm",
                )
                logger.info(f"Whisper transcript: '{transcript_text}'")
        except HTTPException:
            raise
        except Exception as e:
            req_id = str(uuid.uuid4())
            logger.error(f"Error reading or transcribing audio [request_id={req_id}]: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Audio transcription error. Request ID: {req_id}",
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

    # Sanitize client-supplied history (untrusted input)
    parsed_history = []
    if history:
        try:
            import json
            raw_hist = json.loads(history)
            if isinstance(raw_hist, list):
                for item in raw_hist:
                    if isinstance(item, dict):
                        role = item.get("role")
                        content = item.get("content")
                        if role in ("user", "assistant") and isinstance(content, str):
                            clean_c = content.strip()[:1000]
                            if clean_c:
                                parsed_history.append({"role": role, "content": clean_c})
                parsed_history = parsed_history[-4:]
        except Exception:
            parsed_history = []

    # Execute LangChain Agent in threadpool
    try:
        reply = await run_in_threadpool(
            run_agent,
            user_id=effective_user_id,
            user_text=effective_text,
            raw_transcript=raw_transcript_record,
            chat_history=parsed_history,
        )
        return {
            "user_id": effective_user_id,
            "transcript": transcript_text,
            "reply": reply,
        }
    except Exception as e:
        req_id = str(uuid.uuid4())
        logger.error(f"Error running agent for user '{effective_user_id}' [request_id={req_id}]: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Agent execution error. Request ID: {req_id}",
        )


@app.post("/api/transcribe")
async def transcribe_endpoint(
    audio: UploadFile = File(...),
    current_user: str = Depends(get_current_user),
):
    """
    Accepts an audio recording, verifies MIME/extension and file size,
    and returns its transcription via Whisper without executing transaction logic.
    Enforces authentication via session token.
    """
    if not audio.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing audio file filename.",
        )

    ext = os.path.splitext(audio.filename.lower())[1]
    ct = (audio.content_type or "").lower()
    if not (ct.startswith("audio/") or ext in VALID_AUDIO_EXTENSIONS):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid audio file format. Only audio files are allowed.",
        )

    try:
        audio_bytes = await audio.read()
        if len(audio_bytes) > MAX_AUDIO_SIZE:
            raise HTTPException(
                status_code=getattr(status, "HTTP_413_CONTENT_TOO_LARGE", 413),
                detail="Audio file exceeds maximum allowed size (5 MB).",
            )

        if len(audio_bytes) < 100:
            return {"transcript": ""}

        transcript_text = await run_in_threadpool(
            transcribe_audio,
            audio_bytes,
            filename=audio.filename or "recording.webm",
        )
        return {"transcript": transcript_text.strip() if transcript_text else ""}
    except HTTPException:
        raise
    except Exception as e:
        req_id = str(uuid.uuid4())
        logger.error(f"Error reading or transcribing audio [request_id={req_id}]: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Audio transcription error. Request ID: {req_id}",
        )


@app.post("/api/chat/clear")
async def clear_chat_history(
    user_id: Optional[str] = Form(None),
    current_user: str = Depends(get_current_user),
):
    """Clears conversation context for authenticated user."""
    if user_id and user_id.strip() != current_user:
        verify_user_access(user_id, current_user)
    clear_user_history(current_user)
    return {"status": "success", "message": f"History cleared for user {current_user}"}


# ---------------------------------------------------------------------------
# API: Server-Held Pending Large Expenses (UI Confirmation & Cancellation)
# ---------------------------------------------------------------------------
@app.get("/api/transactions/pending")
async def get_active_pending_transaction(
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Retrieves any active pending transaction awaiting user confirmation."""
    now = get_pkt_now()
    pending = (
        db.query(PendingTransaction)
        .filter(PendingTransaction.user_id == current_user, PendingTransaction.expires_at > now)
        .order_by(PendingTransaction.created_at.desc())
        .first()
    )
    return {"pending": pending.to_dict() if pending else None}


@app.post("/api/transactions/pending/confirm")
async def confirm_pending_transaction(
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Commits active server-held pending transaction atomically. Rejects concurrent confirms with 409 Conflict."""
    res = analytics.commit_pending_transaction(db, current_user)
    if res["status"] == "not_found":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=res["message"],
        )
    if res["status"] == "conflict":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=res["message"],
        )
    return res


@app.post("/api/transactions/pending/cancel")
async def cancel_pending_transaction(
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Cancels and discards active pending expense."""
    res = analytics.cancel_pending_transaction(db, current_user)
    if res["status"] == "not_found":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=res["message"],
        )
    if res["status"] == "conflict":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=res["message"],
        )
    return res


# ---------------------------------------------------------------------------
# API: User Transactions Ledger & Summary
# ---------------------------------------------------------------------------
@app.get("/api/transactions/{user_id}")
async def get_user_transactions(
    user_id: str,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Returns the user's monthly spending total, store-wise breakdown,
    and recent transactions for the live ledger dashboard.
    Strictly isolated: does not merge with any other user or WhatsApp ledger.
    """
    verify_user_access(user_id, current_user)
    clean_user_id = current_user

    month_start = analytics.month_start()

    # 1. Total monthly spending
    monthly_stats = (
        db.query(
            func.coalesce(func.sum(Transaction.total_amount), 0.0).label("total"),
            func.count(Transaction.id).label("count"),
        )
        .filter(
            Transaction.user_id == clean_user_id,
            Transaction.transaction_date >= month_start,
            Transaction.deleted_at.is_(None),
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
            Transaction.deleted_at.is_(None),
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
        .filter(
            Transaction.user_id == clean_user_id,
            Transaction.deleted_at.is_(None),
        )
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
# API: Authentication & User Accounts (Firebase Auth Integration)
# ---------------------------------------------------------------------------
@app.get("/api/auth/config")
async def get_auth_config():
    """Returns public Firebase configuration for client SDK."""
    fb_key = os.getenv("FIREBASE_API_KEY", "")
    cfg = {
        "apiKey": fb_key,
        "authDomain": os.getenv("FIREBASE_AUTH_DOMAIN", ""),
        "projectId": os.getenv("FIREBASE_PROJECT_ID", ""),
        "storageBucket": os.getenv("FIREBASE_STORAGE_BUCKET", ""),
        "messagingSenderId": os.getenv("FIREBASE_MESSAGING_SENDER_ID", ""),
        "appId": os.getenv("FIREBASE_APP_ID", ""),
    }
    return {
        **cfg,
        "firebase": cfg,
        "configured": bool(fb_key),
    }


@app.post("/api/auth/me")
async def auth_me(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Returns the authenticated user's profile, upserting their account in SQLite.
    Guarantees user ledger continuity with verified email.
    """
    account = db.query(UserAccount).filter(UserAccount.user_id == str(current_user)).first()
    if not account:
        raise HTTPException(status_code=404, detail="User account not found.")
    return {
        "status": "success",
        "user": account.to_dict(),
    }


@app.get("/api/auth/profile/{user_id}")
async def get_user_profile(
    user_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Retrieves user profile under multi-tenant isolation."""
    verify_user_access(user_id, current_user)
    account = db.query(UserAccount).filter(UserAccount.user_id == str(current_user)).first()
    if account:
        return {"user": account.to_dict()}
    return {
        "user": {
            "user_id": str(current_user),
            "auth_provider": current_user.auth_provider,
            "display_name": current_user.display_name,
            "email": current_user.email,
            "phone_number": None,
            "photo_url": current_user.photo_url,
        }
    }


# ---------------------------------------------------------------------------
# API: Export (CSV opens directly in Excel; UTF-8 BOM keeps Urdu text intact)
# ---------------------------------------------------------------------------
@app.get("/api/export/{user_id}")
async def export_csv(
    user_id: str,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    verify_user_access(user_id, current_user)
    clean_user_id = current_user
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(analytics.EXPORT_HEADER)
    writer.writerows(analytics.export_rows(db, clean_user_id))
    filename = f"hisaabai_{clean_user_id}_{get_pkt_now().strftime('%Y%m%d')}.csv"
    return Response(
        content="\ufeff" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# API: Budgets
# ---------------------------------------------------------------------------
@app.get("/api/budgets/{user_id}")
async def list_budgets(
    user_id: str,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    verify_user_access(user_id, current_user)
    return {"budgets": analytics.budget_status(db, current_user)}


@app.post("/api/budgets")
async def save_budget(
    monthly_limit: float = Form(...),
    category: str = Form("total"),
    user_id: Optional[str] = Form(None),
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user_id and user_id.strip() != current_user:
        verify_user_access(user_id, current_user)
    uid = current_user
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
async def delete_budget(
    user_id: str,
    budget_id: int,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    verify_user_access(user_id, current_user)
    b = db.query(Budget).filter(Budget.user_id == current_user, Budget.id == budget_id).first()
    if not b:
        raise HTTPException(status_code=404, detail="Budget not found.")
    db.delete(b)
    db.commit()
    return {"status": "success"}


# ---------------------------------------------------------------------------
# API: Recurring monthly expenses
# ---------------------------------------------------------------------------
@app.get("/api/recurring/{user_id}")
async def list_recurring(
    user_id: str,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    verify_user_access(user_id, current_user)
    return {"recurring": analytics.recurring_status(db, current_user)}


@app.post("/api/recurring")
async def save_recurring(
    title: str = Form(...),
    amount: float = Form(...),
    due_day: int = Form(1),
    category: str = Form("utilities"),
    user_id: Optional[str] = Form(None),
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user_id and user_id.strip() != current_user:
        verify_user_access(user_id, current_user)
    uid = current_user
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
                user_id=uid,
                title=clean_title,
                amount=amount,
                due_day=day,
                category=category.strip().lower(),
            )
        )
    db.commit()
    return {"recurring": analytics.recurring_status(db, uid)}


@app.post("/api/recurring/{user_id}/{recurring_id}/pay")
async def pay_recurring_item(
    user_id: str,
    recurring_id: int,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    verify_user_access(user_id, current_user)
    uid = current_user
    item = (
        db.query(RecurringExpense)
        .filter(RecurringExpense.user_id == uid, RecurringExpense.id == recurring_id)
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="Recurring expense not found.")
    return analytics.pay_recurring(db, uid, item)


@app.delete("/api/recurring/{user_id}/{recurring_id}")
async def delete_recurring_item(
    user_id: str,
    recurring_id: int,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    verify_user_access(user_id, current_user)
    item = (
        db.query(RecurringExpense)
        .filter(RecurringExpense.user_id == current_user, RecurringExpense.id == recurring_id)
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="Recurring expense not found.")
    db.delete(item)
    db.commit()
    return {"status": "success"}


# ---------------------------------------------------------------------------
# PWA: manifest + service worker
# ---------------------------------------------------------------------------
@app.get("/manifest.webmanifest")
async def pwa_manifest():
    return FileResponse(
        os.path.join(STATIC_DIR, "manifest.webmanifest"),
        media_type="application/manifest+json",
    )


@app.get("/sw.js")
async def pwa_service_worker():
    return FileResponse(
        os.path.join(STATIC_DIR, "sw.js"),
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


# ---------------------------------------------------------------------------
# WhatsApp Webhook (Isolated wa: namespace)
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
    token = os.getenv("TWILIO_AUTH_TOKEN")
    if not token:
        raise HTTPException(status_code=503, detail="WhatsApp integration is not configured.")

    form = await request.form()
    params = {k: str(v) for k, v in form.items()}

    validate_sig = os.getenv("WHATSAPP_VALIDATE_SIGNATURE", "true").lower() != "false"
    if not validate_sig and not IS_DEV:
        # Signature bypass is strictly prohibited outside development
        validate_sig = True

    if validate_sig:
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

    reply = reply.replace("**", "*")
    return _twiml(reply[:1500])


# ---------------------------------------------------------------------------
# API: Soft-Delete Specific Transaction
# ---------------------------------------------------------------------------
@app.delete("/api/transactions/{user_id}/{transaction_id}")
async def delete_transaction(
    user_id: str,
    transaction_id: int,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    verify_user_access(user_id, current_user)
    clean_user_id = current_user
    tx = (
        db.query(Transaction)
        .filter(
            Transaction.user_id == clean_user_id,
            Transaction.id == transaction_id,
            Transaction.deleted_at.is_(None),
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

    # Soft-delete preserves data for undo buffer
    tx.deleted_at = get_pkt_now()
    db.commit()

    return {
        "status": "success",
        "deleted_id": transaction_id,
        "message": f"Deleted transaction #{transaction_id} ({deleted_store} - PKR {deleted_amount})",
    }


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("APP_HOST", "127.0.0.1")
    port = int(os.getenv("APP_PORT", "8000"))
    reload = IS_DEV
    uvicorn.run("main:app", host=host, port=port, reload=reload)
