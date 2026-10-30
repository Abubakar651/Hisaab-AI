import os
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

import jwt
import httpx
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
    LoginAttempt,
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
# Environment & Session Security Verification (Fail-Closed Default)
# ---------------------------------------------------------------------------
APP_ENV = os.getenv("APP_ENV", "production").strip().lower()
IS_DEV = APP_ENV in ("dev", "development", "test")
SESSION_SECRET = os.getenv("SESSION_SECRET")

if not SESSION_SECRET or len(SESSION_SECRET) < 32:
    if IS_DEV:
        SESSION_SECRET = (
            SESSION_SECRET
            if (SESSION_SECRET and len(SESSION_SECRET) >= 32)
            else "hisaab-ai-dev-secret-key-do-not-use-in-production-1234567890"
        )
        logger.warning("Using dev fallback SESSION_SECRET (min 32 chars).")
    else:
        raise RuntimeError(
            "SESSION_SECRET environment variable is required and must be at least 32 characters in production mode."
        )

FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "").strip()
if not IS_DEV and not FIREBASE_PROJECT_ID:
    raise RuntimeError("FIREBASE_PROJECT_ID environment variable is required in production mode.")

JWT_ALGORITHM = "HS256"
JWT_EXPIRY_DAYS = 7
PBKDF2_ITERATIONS = 600_000
OLD_PBKDF2_ITERATIONS = 100_000
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
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' https://cdn.tailwindcss.com https://unpkg.com https://cdn.jsdelivr.net https://www.gstatic.com https://apis.google.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
        "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net https://unpkg.com data:; "
        "img-src 'self' data: https:; "
        "connect-src 'self' https://*.firebaseio.com https://identitytoolkit.googleapis.com https://securetoken.googleapis.com https://*.googleapis.com; "
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
# Session JWT Helpers & FastAPI Authentication Dependency
# ---------------------------------------------------------------------------
def create_session_token(user_id: str, provider: str = "credentials", token_version: int = 0) -> str:
    """Generates signed HS256 JWT session token with 7-day expiry and token version claim."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "provider": provider,
        "ver": int(token_version or 0),
        "iat": now,
        "exp": now + timedelta(days=JWT_EXPIRY_DAYS),
    }
    return jwt.encode(payload, SESSION_SECRET, algorithm=JWT_ALGORITHM)


def decode_session_token(token: str) -> Dict[str, Any]:
    """Validates signature and claims of session JWT (strictly HS256, requires exp, sub, iat)."""
    try:
        return jwt.decode(
            token,
            SESSION_SECRET,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["exp", "sub", "iat"]},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session has expired. Please sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


security_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    auth_header: Optional[HTTPAuthorizationCredentials] = Depends(security_bearer),
    db: Session = Depends(get_db),
) -> str:
    """
    FastAPI dependency that reads Bearer token from Authorization header.
    Validates token signature, expiration, account existence, and token_version match.
    Guarantees tenant identity and provides instant token revocation.
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

    payload = decode_session_token(token)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token claims.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    clean_uid = str(user_id).strip()
    token_ver = payload.get("ver", 0)

    # Validate against DB: account must exist and token_version must match
    account = db.query(UserAccount).filter(UserAccount.user_id == clean_uid).first()
    if not account:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account does not exist or has been removed.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if (account.token_version or 0) != token_ver:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session has been revoked. Please sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return clean_uid


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
# Password Hashing & Persistent Rate Limiting Helpers
# ---------------------------------------------------------------------------
def get_client_ip(request: Request) -> str:
    """
    Safely extracts client IP.
    By default (TRUSTED_PROXY=false), ignores X-Forwarded-For to prevent spoofing.
    Only trusts X-Forwarded-For when TRUSTED_PROXY is explicitly enabled.
    """
    trusted_proxy = os.getenv("TRUSTED_PROXY", "false").strip().lower() in ("true", "1", "yes")
    if trusted_proxy:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host.strip()
    return "127.0.0.1"


def check_rate_limit(
    db: Session,
    client_ip: str,
    action: str,
    identifier: str,
    max_attempts: int = 5,
    window_seconds: int = 60,
):
    """
    Enforces persistent database-backed rate limiting across multiple workers.
    Raises HTTP 429 Too Many Requests if threshold is exceeded.
    Also automatically prunes stale attempt records older than 1 hour.
    """
    now = get_pkt_now()
    # Prune stale records older than 1 hour to prevent unbounded growth of login_attempts table
    prune_cutoff = now - timedelta(seconds=3600)
    db.query(LoginAttempt).filter(LoginAttempt.attempted_at < prune_cutoff).delete(synchronize_session=False)

    cutoff = now - timedelta(seconds=window_seconds)
    key = f"{client_ip}:{action}:{identifier.lower()}"

    count = (
        db.query(func.count(LoginAttempt.id))
        .filter(LoginAttempt.key == key, LoginAttempt.attempted_at >= cutoff)
        .scalar()
    ) or 0

    if count >= max_attempts:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Please wait a minute and try again.",
        )


def record_failed_attempt(db: Session, client_ip: str, action: str, identifier: str):
    """Records an attempt in the persistent login_attempts table."""
    now = get_pkt_now()
    prune_cutoff = now - timedelta(seconds=3600)
    db.query(LoginAttempt).filter(LoginAttempt.attempted_at < prune_cutoff).delete(synchronize_session=False)

    key = f"{client_ip}:{action}:{identifier.lower()}"
    attempt = LoginAttempt(key=key, attempted_at=now)
    db.add(attempt)
    db.commit()


def clear_failed_attempts(db: Session, client_ip: str, action: str, identifier: str):
    """Resets failed attempt counter upon successful authentication."""
    key = f"{client_ip}:{action}:{identifier.lower()}"
    db.query(LoginAttempt).filter(LoginAttempt.key == key).delete(synchronize_session=False)
    db.commit()


def clear_login_rate_limit(client_ip: str, identifier: str):
    """Convenience helper to clear login rate limit for tests and administrative resets."""
    is_email = "@" in identifier
    uid = identifier.lower() if is_email else normalize_pakistan_phone(identifier)
    db: Session = SessionLocal()
    try:
        clear_failed_attempts(db, client_ip, "login", uid)
        clear_failed_attempts(db, "global", "acct_login", uid)
    finally:
        db.close()


def hash_password(password: str, iterations: int = PBKDF2_ITERATIONS) -> str:
    """PBKDF2-HMAC-SHA256 password hash with 600,000 iterations and 16-byte random salt."""
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    salt_b64 = base64.b64encode(salt).decode("utf-8")
    dk_b64 = base64.b64encode(dk).decode("utf-8")
    return f"pbkdf2_sha256${iterations}${salt_b64}${dk_b64}"


def verify_and_check_rehash(password: str, hashed: str) -> Tuple[bool, bool]:
    """
    Verifies password against stored hash.
    Transparently supports legacy 100,000 iteration hashes and signals rehash on login.
    Does NOT strip password.
    """
    if not hashed:
        return False, False
    try:
        if hashed.startswith("pbkdf2_sha256$"):
            parts = hashed.split("$")
            if len(parts) == 4:
                iters = int(parts[1])
                salt = base64.b64decode(parts[2].encode("utf-8"))
                expected_dk = base64.b64decode(parts[3].encode("utf-8"))
                dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters)
                valid = hmac.compare_digest(expected_dk, dk)
                needs_rehash = valid and (iters < PBKDF2_ITERATIONS)
                return valid, needs_rehash

        # Legacy format: base64(salt + dk) with 100,000 iterations
        raw = base64.b64decode(hashed.encode("utf-8"))
        if len(raw) == 48:
            salt = raw[:16]
            expected_dk = raw[16:]
            dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, OLD_PBKDF2_ITERATIONS)
            if hmac.compare_digest(expected_dk, dk):
                return True, True  # Needs upgrade to 600,000 iterations
        return False, False
    except Exception:
        return False, False


# Precomputed constant-time dummy hash to eliminate timing oracle on non-existent accounts
DUMMY_PBKDF2_HASH = hash_password("dummy_password_timing_oracle", iterations=PBKDF2_ITERATIONS)



def normalize_pakistan_phone(phone: str) -> str:
    """Normalizes Pakistani phone numbers to standard E.164: +923001234567."""
    digits = re.sub(r"[^\d+]", "", phone.strip())
    if digits.startswith("03") and len(digits) == 11:
        return "+92" + digits[1:]
    if digits.startswith("923") and len(digits) == 12:
        return "+" + digits
    if digits.startswith("+923") and len(digits) == 13:
        return digits
    return digits


def verify_firebase_id_token(id_token_str: str) -> Dict[str, Any]:
    """
    Verifies Firebase ID token server-side using google-auth.
    Enforces non-empty project_id, matching audience, and valid issuer.
    """
    project_id = os.getenv("FIREBASE_PROJECT_ID", "").strip()
    if not id_token_str or not id_token_str.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Firebase id_token is required.",
        )

    if not project_id:
        if not IS_DEV:
            raise RuntimeError("FIREBASE_PROJECT_ID is not configured in production mode.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Firebase authentication requires FIREBASE_PROJECT_ID to be configured.",
        )

    try:
        request_adapter = google_requests.Request()
        claims = google_id_token.verify_firebase_token(
            id_token_str.strip(),
            request_adapter,
            audience=project_id,
        )
    except Exception as e:
        logger.warning(f"Firebase token verification failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired Firebase authentication token.",
        )

    # Strictly verify audience and issuer
    aud = claims.get("aud")
    iss = claims.get("iss")
    expected_iss = f"https://securetoken.google.com/{project_id}"

    if aud != project_id:
        logger.warning(f"Firebase token audience mismatch: got '{aud}', expected '{project_id}'")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Firebase token audience mismatch (token issued for different project).",
        )

    if iss != expected_iss:
        logger.warning(f"Firebase token issuer mismatch: got '{iss}', expected '{expected_iss}'")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Firebase token issuer mismatch.",
        )

    return claims


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
# API: Authentication & User Accounts (Google, Phone, Credentials & Guests)
# ---------------------------------------------------------------------------
class RegisterPayload(BaseModel):
    name: str
    email_or_phone: str
    password: str


class PasswordLoginPayload(BaseModel):
    email_or_phone: str
    password: str


class LoginPayload(BaseModel):
    provider: str = "guest"  # 'google', 'phone', 'guest'
    user_id: Optional[str] = None
    email: Optional[str] = None
    phone_number: Optional[str] = None
    display_name: Optional[str] = None
    photo_url: Optional[str] = None
    id_token: Optional[str] = None


@app.get("/api/auth/config")
async def get_auth_config():
    """Returns public Firebase configuration for client SDK."""
    fb_key = os.getenv("FIREBASE_API_KEY", "")
    return {
        "firebase": {
            "apiKey": fb_key,
            "authDomain": os.getenv("FIREBASE_AUTH_DOMAIN", ""),
            "projectId": os.getenv("FIREBASE_PROJECT_ID", ""),
            "storageBucket": os.getenv("FIREBASE_STORAGE_BUCKET", ""),
            "messagingSenderId": os.getenv("FIREBASE_MESSAGING_SENDER_ID", ""),
            "appId": os.getenv("FIREBASE_APP_ID", ""),
        },
        "configured": bool(fb_key),
    }


@app.post("/api/auth/login")
async def auth_login(
    request: Request,
    payload: LoginPayload,
    db: Session = Depends(get_db),
):
    """
    Logs in or registers a user via Google, Phone Number, or Guest mode.
    Enforces server-side token verification, pre-hijacking prevention, and issues signed JWT session tokens.
    """
    client_ip = get_client_ip(request)
    provider = payload.provider.strip().lower()

    if provider == "google":
        # Strictly verify Firebase ID token server-side
        if not payload.id_token:
            raise HTTPException(status_code=400, detail="Firebase ID token is required for Google sign-in.")
        claims = verify_firebase_id_token(payload.id_token)

        # Check email_verified is True
        if not claims.get("email_verified", False):
            raise HTTPException(status_code=401, detail="Google account email is not verified.")

        # Check sign_in_provider is google.com
        sign_in_provider = claims.get("firebase", {}).get("sign_in_provider", "")
        if sign_in_provider != "google.com":
            raise HTTPException(status_code=401, detail="Token was not issued by Google sign-in provider.")

        verified_email = claims.get("email")
        if not verified_email:
            raise HTTPException(status_code=401, detail="Google token does not contain a verified email.")
        uid = verified_email.strip().lower()
        display_name = claims.get("name") or uid.split("@")[0].title()
        photo_url = claims.get("picture")
        email = uid
        phone_number = None

    elif provider == "phone":
        if payload.id_token:
            # Verified via Firebase SMS OTP
            claims = verify_firebase_id_token(payload.id_token)
            verified_phone = claims.get("phone_number")
            if not verified_phone:
                raise HTTPException(status_code=401, detail="Phone token does not contain a verified phone number.")
            uid = normalize_pakistan_phone(verified_phone)
            display_name = uid
            photo_url = None
            email = None
            phone_number = uid
        elif IS_DEV and (payload.phone_number or payload.user_id):
            # Development-only instant verification fallback for Pakistani numbers
            raw_phone = (payload.phone_number or payload.user_id or "").strip()
            uid = normalize_pakistan_phone(raw_phone)
            display_name = uid
            photo_url = None
            email = None
            phone_number = uid
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Firebase ID token is required for phone sign-in in this environment.",
            )

    else:
        # Rate limit guest account generation
        check_rate_limit(db, client_ip, "guest", client_ip, max_attempts=10, window_seconds=60)
        record_failed_attempt(db, client_ip, "guest", client_ip)

        # Secure Guest Mode: generate random unguessable guest ID
        guest_uuid = uuid.uuid4().hex[:12]
        uid = f"guest_{guest_uuid}"
        display_name = f"Guest {guest_uuid[:4].upper()}"
        email = None
        phone_number = None
        photo_url = None
        provider = "guest"

    account = db.query(UserAccount).filter(UserAccount.user_id == uid).first()
    is_verified_provider = provider in ("google", "phone")

    if account:
        # Pre-hijacking mitigation: if account was registered with credentials without verification,
        # or has an untrusted password_hash when claimed by a verified provider (Google or Phone OTP):
        if is_verified_provider and (not account.identity_verified or account.password_hash or account.auth_provider == "credentials"):
            logger.warning(
                f"Verified {provider} sign-in for {uid}. Clearing unverified credentials "
                "and revoking old sessions while preserving existing ledger data."
            )
            account.password_hash = None
            account.identity_verified = True
            account.auth_provider = provider
            account.token_version = (account.token_version or 0) + 1

        account.last_login = get_pkt_now()
        if display_name:
            account.display_name = display_name
        if photo_url:
            account.photo_url = photo_url
        if email:
            account.email = email
        if phone_number:
            account.phone_number = phone_number
        if provider != "guest":
            account.auth_provider = provider
        if is_verified_provider:
            account.identity_verified = True
    else:
        account = UserAccount(
            user_id=uid,
            auth_provider=provider,
            display_name=display_name,
            email=email,
            phone_number=phone_number,
            photo_url=photo_url,
            identity_verified=is_verified_provider,
            token_version=0,
            created_at=get_pkt_now(),
            last_login=get_pkt_now(),
        )
        db.add(account)

    db.commit()
    session_token = create_session_token(uid, provider, token_version=account.token_version or 0)
    return {
        "status": "success",
        "token": session_token,
        "user": account.to_dict(),
    }


@app.get("/api/auth/profile/{user_id}")
async def get_user_profile(
    user_id: str,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    verify_user_access(user_id, current_user)
    clean_uid = current_user
    account = db.query(UserAccount).filter(UserAccount.user_id == clean_uid).first()
    if account:
        return {"user": account.to_dict()}
    return {
        "user": {
            "user_id": clean_uid,
            "auth_provider": "guest",
            "display_name": clean_uid.capitalize(),
            "email": None,
            "phone_number": clean_uid if clean_uid.startswith("+") else None,
            "photo_url": None,
        }
    }


@app.post("/api/auth/register")
async def auth_register(
    request: Request,
    payload: RegisterPayload,
    db: Session = Depends(get_db),
):
    """Creates a new user account with Name, Email/Phone, and Password. Prevents account squatting and rate limits registrations."""
    client_ip = get_client_ip(request)
    check_rate_limit(db, client_ip, "register", client_ip, max_attempts=5, window_seconds=60)

    ident = payload.email_or_phone.strip()
    name = payload.name.strip()
    password = payload.password  # Do NOT strip password

    if not name or len(name) < 2:
        raise HTTPException(status_code=400, detail="Name must be at least 2 characters.")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters.")

    is_email = "@" in ident
    if is_email:
        email = ident.lower()
        phone = None
        uid = email
    else:
        email = None
        phone = normalize_pakistan_phone(ident)
        uid = phone

    existing = db.query(UserAccount).filter(UserAccount.user_id == uid).first()
    if existing:
        # Never silently take over an existing account row
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An account with this email/phone already exists. Please sign in.",
        )

    pwd_hash = hash_password(password)

    account = UserAccount(
        user_id=uid,
        auth_provider="credentials",
        display_name=name,
        email=email,
        phone_number=phone,
        password_hash=pwd_hash,
        identity_verified=False,
        token_version=0,
        created_at=get_pkt_now(),
        last_login=get_pkt_now(),
    )
    db.add(account)
    db.commit()

    record_failed_attempt(db, client_ip, "register", client_ip)
    session_token = create_session_token(uid, "credentials", token_version=0)
    return {
        "status": "success",
        "message": "Account created successfully!",
        "token": session_token,
        "user": account.to_dict(),
    }


@app.post("/api/auth/password-login")
async def auth_password_login(
    request: Request,
    payload: PasswordLoginPayload,
    db: Session = Depends(get_db),
):
    """Signs in user with Email/Phone and Password with rate limiting and uniform error reporting."""
    ident = payload.email_or_phone.strip()
    password = payload.password  # Do NOT strip password

    client_ip = get_client_ip(request)

    # Normalize identifier BEFORE rate limiting so 0300..., +92300..., 0300-... share the same bucket
    is_email = "@" in ident
    uid = ident.lower() if is_email else normalize_pakistan_phone(ident)

    # Enforce 5 failed attempts/min per IP+normalized identifier with 429
    check_rate_limit(db, client_ip, "login", uid, max_attempts=5, window_seconds=60)
    # Also enforce 10 failed attempts/15min globally per account (distributed brute-force protection)
    check_rate_limit(db, "global", "acct_login", uid, max_attempts=10, window_seconds=900)

    account = db.query(UserAccount).filter(UserAccount.user_id == uid).first()
    if not account or not account.password_hash:
        # Eliminate timing oracle: perform dummy PBKDF2 verification with equal iterations
        verify_and_check_rehash(password, DUMMY_PBKDF2_HASH)
        record_failed_attempt(db, client_ip, "login", uid)
        record_failed_attempt(db, "global", "acct_login", uid)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email/phone or password.",
        )

    is_valid, needs_rehash = verify_and_check_rehash(password, account.password_hash)
    if not is_valid:
        record_failed_attempt(db, client_ip, "login", uid)
        record_failed_attempt(db, "global", "acct_login", uid)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email/phone or password.",
        )

    # Transparently upgrade 100k hashes to 600k on successful login
    if needs_rehash:
        account.password_hash = hash_password(password)

    account.last_login = get_pkt_now()
    db.commit()

    clear_failed_attempts(db, client_ip, "login", uid)
    clear_failed_attempts(db, "global", "acct_login", uid)
    session_token = create_session_token(
        uid,
        account.auth_provider or "credentials",
        token_version=account.token_version or 0,
    )
    return {
        "status": "success",
        "token": session_token,
        "user": account.to_dict(),
    }


@app.post("/api/auth/logout-all")
async def logout_all_sessions(
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Increments user's token_version, immediately revoking all issued JWT session tokens."""
    account = db.query(UserAccount).filter(UserAccount.user_id == current_user).first()
    if account:
        account.token_version = (account.token_version or 0) + 1
        db.commit()
    return {"status": "success", "message": "All sessions revoked successfully. Please sign in again."}


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
