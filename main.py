import os
import sys
import io
import re
import csv
import hmac
import inspect
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

import database
from database import (
    init_db,
    get_db,
    get_pkt_now,
    SessionLocal,
    Transaction,
    TransactionItem,
    Book,
    Budget,
    RecurringExpense,
    UserAccount,
    PendingTransaction,
    UdhaarEntry,
    Party,
    PartyTransaction,
    BusinessProfile,
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

# ---------------------------------------------------------------------------
# Sentry Telemetry & Error Tracking (Never emit from automated/ad-hoc test runs)
# ---------------------------------------------------------------------------
SENTRY_DSN = os.getenv("SENTRY_DSN", "").strip()
if SENTRY_DSN and APP_ENV != "test" and "pytest" not in sys.modules:
    import sentry_sdk
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        traces_sample_rate=1.0,
        environment=APP_ENV,
    )

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
    description="Multilingual (Urdu, English) Expense Tracker powered by Groq and LangChain",
    version="1.0.0",
    docs_url="/docs" if IS_DEV else None,
    redoc_url=None,
    openapi_url="/openapi.json" if IS_DEV else None,
)

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Enforces strict defense-in-depth security headers (CSP, nosniff, frame denial)."""
    if request.url.path in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
        curr_env = os.getenv("APP_ENV", APP_ENV).strip().lower()
        if curr_env not in ("dev", "development", "test"):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})

    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Cross-Origin-Opener-Policy"] = "unsafe-none"

    auth_domain = os.getenv("FIREBASE_AUTH_DOMAIN", "").strip()
    frame_src_parts = ["https://accounts.google.com"]
    if auth_domain:
        if not auth_domain.startswith("https://"):
            frame_src_parts.append(f"https://{auth_domain}")
        else:
            frame_src_parts.append(auth_domain)
    frame_src = " ".join(frame_src_parts)
    connect_extra = " ".join(["https://apis.google.com", "https://accounts.google.com", *frame_src_parts[1:]])

    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' https://apis.google.com https://cdn.tailwindcss.com https://unpkg.com https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
        "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net https://unpkg.com data:; "
        "img-src 'self' data: https:; "
        f"connect-src 'self' https://identitytoolkit.googleapis.com https://securetoken.googleapis.com https://www.googleapis.com https://unpkg.com https://cdn.jsdelivr.net {connect_extra}; "
        f"frame-src {frame_src}; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none';"
    )
    if request.url.path in ("/", "/sw.js", "/manifest.webmanifest") or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
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


_ADMIN_SDK_CREDENTIALS_MISSING = not bool(os.getenv("GOOGLE_APPLICATION_CREDENTIALS"))


def verify_firebase_token_claims(token: str) -> Dict[str, Any]:
    """
    Verifies Firebase ID token server-side.
    Uses firebase_admin with automatic fallback to certificate-cached google-auth id_token verification.
    Enforces aud, iss, email presence, email_verified, and authorized providers (google.com or password).
    """
    global _ADMIN_SDK_CREDENTIALS_MISSING
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
    is_mocked = hasattr(admin_auth.verify_id_token, "_mock_name") or "mock" in type(admin_auth.verify_id_token).__name__.lower()
    # 1. Primary verification via Firebase Admin SDK (skip unmocked call if local ADC is absent to avoid 12s GCE metadata timeout)
    if is_mocked or not _ADMIN_SDK_CREDENTIALS_MISSING:
        try:
            try:
                claims = admin_auth.verify_id_token(token.strip(), check_revoked=False, clock_skew_seconds=60)
            except TypeError:
                claims = admin_auth.verify_id_token(token.strip(), check_revoked=False)
        except Exception as admin_err:
            if "DefaultCredentialsError" in type(admin_err).__name__:
                _ADMIN_SDK_CREDENTIALS_MISSING = True
            # 2. Resilient fallback via google-auth with CacheControl session (60s clock skew tolerance for fresh tokens)
            try:
                claims = google_id_token.verify_firebase_token(
                    token.strip(),
                    _cached_request_adapter,
                    audience=project_id if project_id else None,
                    clock_skew_in_seconds=60,
                )
            except Exception as ga_err:
                logger.warning(f"Firebase token verification failed (admin: {admin_err}, fallback: {ga_err})")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid or expired authentication token.",
                    headers={"WWW-Authenticate": "Bearer"},
                )
    else:
        try:
            claims = google_id_token.verify_firebase_token(
                token.strip(),
                _cached_request_adapter,
                audience=project_id if project_id else None,
                clock_skew_in_seconds=60,
            )
        except Exception as ga_err:
            logger.warning(f"Firebase token verification failed (google_id_token: {ga_err})")
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
    raw_name = claims.get("name") or clean_uid.split("@")[0].capitalize()
    sanitized_name = re.sub(r'[<>"\'\x00-\x1f\x7f]', '', str(raw_name)).strip()[:80]
    disp_name = sanitized_name if sanitized_name else clean_uid.split("@")[0].capitalize()[:80]
    raw_photo = claims.get("picture")
    photo_url = raw_photo if (isinstance(raw_photo, str) and re.match(r"^https://", raw_photo, re.I)) else None
    firebase_uid = str(claims.get("uid") or claims.get("sub") or "")

    # Upsert user account row (skip redundant DB writes on unchanged read requests to avoid SQLite write locks)
    account = db.query(UserAccount).filter(UserAccount.user_id == clean_uid).first()
    now_pkt = get_pkt_now()
    is_new_account = account is None
    needs_commit = False
    if account:
        if firebase_uid and account.firebase_uid != firebase_uid:
            account.firebase_uid = firebase_uid
            needs_commit = True
        if disp_name and account.display_name != disp_name:
            account.display_name = disp_name
            needs_commit = True
        if photo_url and account.photo_url != photo_url:
            account.photo_url = photo_url
            needs_commit = True
        if account.auth_provider != provider_name:
            account.auth_provider = provider_name
            needs_commit = True
        if account.email != clean_uid:
            account.email = clean_uid
            needs_commit = True
        if not account.identity_verified:
            account.identity_verified = True
            needs_commit = True
        if account.password_hash is not None:
            account.password_hash = None
            needs_commit = True
        last_login = account.last_login
        if last_login is None:
            account.last_login = now_pkt
            needs_commit = True
        else:
            last_login_naive = last_login.replace(tzinfo=None) if getattr(last_login, "tzinfo", None) else last_login
            if (now_pkt - last_login_naive).total_seconds() >= 300:
                account.last_login = now_pkt
                needs_commit = True
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
            created_at=now_pkt,
            last_login=now_pkt,
        )
        db.add(account)
        needs_commit = True
    if needs_commit:
        try:
            db.commit()
        except Exception as e:
            db.rollback()
            if not is_new_account and "database is locked" in str(e).lower():
                logger.warning(f"Skipping non-critical last_login commit due to concurrent lock for {clean_uid}")
            else:
                raise

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
# Health / Liveness Probe (Google Cloud Run Readiness)
# ---------------------------------------------------------------------------
@app.get("/healthz")
async def healthz():
    """Lightweight health check endpoint for Cloud Run container probes."""
    return {"status": "ok", "app": "hisaab-ai"}


# ---------------------------------------------------------------------------
# Frontend Root Route
# ---------------------------------------------------------------------------
@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
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
    lang: Optional[str] = Form(None),
    book_id: Optional[int] = Form(None),
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Accepts text message, audio voice note, or both from the frontend.
    Runs ASR and agent asynchronously in threadpools without blocking the event loop.
    Enforces tenant identity strictly via session token.
    """
    if user_id and user_id.strip() != current_user:
        verify_user_access(user_id, current_user)

    effective_user_id = current_user
    books_info = analytics.ensure_default_books(db, effective_user_id)
    if book_id is not None:
        active_book = next((b for b in books_info["books"] if b.id == int(book_id)), None)
        if not active_book:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Book not found.",
            )
    else:
        active_book = books_info["default_book"]

    # Snapshot plain book fields and release the request DB session before running ASR/LLM
    # in a worker thread, so SQLite never holds a transaction across tool writes.
    active_book_id = active_book.id
    active_book_type = active_book.book_type
    active_book_name = active_book.name
    business_book_ref = books_info.get("business_book")
    personal_book_ref = books_info.get("personal_book")
    business_book_meta = (
        {"id": business_book_ref.id, "name": business_book_ref.name}
        if business_book_ref
        else None
    )
    personal_book_meta = (
        {"id": personal_book_ref.id, "name": personal_book_ref.name}
        if personal_book_ref
        else None
    )
    db.close()

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
                logger.info(f"Whisper transcription completed ({len(transcript_text or '')} chars) for user '{effective_user_id}'")
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
        agent_meta: Dict[str, Any] = {}
        agent_kwargs: Dict[str, Any] = {
            "user_id": effective_user_id,
            "user_text": effective_text,
            "raw_transcript": raw_transcript_record,
            "chat_history": parsed_history,
            "ui_lang": lang,
        }
        sig_params = inspect.signature(run_agent).parameters
        accepts_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig_params.values())
        if accepts_var_kw or "book_id" in sig_params:
            agent_kwargs["book_id"] = active_book_id
            agent_kwargs["active_book_type"] = active_book_type
            agent_kwargs["active_book_name"] = active_book_name
        if accepts_var_kw or "_meta_out" in sig_params:
            agent_kwargs["_meta_out"] = agent_meta

        reply = await run_in_threadpool(run_agent, **agent_kwargs)
        resp_payload: Dict[str, Any] = {
            "user_id": effective_user_id,
            "book_id": active_book_id,
            "transcript": transcript_text,
            "reply": reply,
        }
        sug = agent_meta.get("cross_book_suggestion")
        if isinstance(sug, dict):
            target_book = (
                business_book_meta
                if sug.get("suggested_book_type") == "business"
                else personal_book_meta
            )
            if target_book and target_book["id"] != active_book_id:
                resp_payload["cross_book_suggestion"] = {
                    **sug,
                    "target_book_id": target_book["id"],
                    "target_book_name": target_book["name"],
                }
        return resp_payload
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
    book_id: Optional[int] = None,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Retrieves any active pending transaction awaiting user confirmation."""
    now = get_pkt_now()
    q = db.query(PendingTransaction).filter(
        PendingTransaction.user_id == current_user,
        PendingTransaction.expires_at > now,
    )
    if book_id is not None:
        q = q.filter(PendingTransaction.book_id == int(book_id))
    pending = q.order_by(PendingTransaction.created_at.desc()).first()
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
# API: Multi-Book Management & Cross-Book Move
# ---------------------------------------------------------------------------
@app.get("/api/books/{user_id}")
async def list_books(
    user_id: str,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Lists user's active ledgers (auto-creating Business & Personal defaults + migrating orphans)."""
    verify_user_access(user_id, current_user)
    info = analytics.ensure_default_books(db, current_user)
    return {
        "books": [b.to_dict() for b in info["books"]],
        "default_book_id": info["default_book"].id if info["default_book"] else None,
    }


@app.post("/api/books")
async def create_book(
    request: Request,
    name: Optional[str] = Form(None),
    book_type: str = Form("business"),
    user_id: Optional[str] = Form(None),
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Creates a new Business or Personal book for the authenticated user."""
    if "application/json" in request.headers.get("content-type", ""):
        try:
            body = await request.json()
            if isinstance(body, dict):
                name = body.get("name", name)
                book_type = body.get("book_type", book_type)
                user_id = body.get("user_id", user_id)
        except Exception:
            pass
    if user_id and str(user_id).strip() != current_user:
        verify_user_access(str(user_id), current_user)
    try:
        book = analytics.create_user_book(db, current_user, name=name or "", book_type=book_type or "business")
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    info = analytics.ensure_default_books(db, current_user)
    return {
        "status": "success",
        "book": book.to_dict(),
        "books": [b.to_dict() for b in info["books"]],
    }


@app.patch("/api/books/{user_id}/{book_id}")
async def update_book(
    user_id: str,
    book_id: int,
    request: Request,
    name: Optional[str] = Form(None),
    book_type: Optional[str] = Form(None),
    is_default: Optional[bool] = Form(None),
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Updates an existing book owned by the authenticated user."""
    verify_user_access(user_id, current_user)
    if "application/json" in request.headers.get("content-type", ""):
        try:
            body = await request.json()
            if isinstance(body, dict):
                if "name" in body:
                    name = body["name"]
                if "book_type" in body:
                    book_type = body["book_type"]
                if "is_default" in body:
                    is_default = body["is_default"]
        except Exception:
            pass
    try:
        updated = analytics.update_user_book(
            db, current_user, book_id=book_id, name=name, book_type=book_type, is_default=is_default
        )
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found.")
    info = analytics.ensure_default_books(db, current_user)
    return {
        "status": "success",
        "book": updated.to_dict(),
        "books": [b.to_dict() for b in info["books"]],
    }


@app.delete("/api/books/{user_id}/{book_id}")
async def delete_book(
    user_id: str,
    book_id: int,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Soft-deletes a book if another active book remains."""
    verify_user_access(user_id, current_user)
    res = analytics.delete_user_book(db, current_user, book_id=book_id)
    if res["status"] == "not_found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=res["message"])
    if res["status"] == "error":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=res["message"])
    info = analytics.ensure_default_books(db, current_user)
    return {**res, "books": [b.to_dict() for b in info["books"]]}


@app.post("/api/transactions/{user_id}/{transaction_id}/move-book")
async def move_transaction_book(
    user_id: str,
    transaction_id: int,
    request: Request,
    target_book_id: Optional[int] = Form(None),
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Moves a transaction from its current book to another active book owned by the same user."""
    verify_user_access(user_id, current_user)
    if target_book_id is None and "application/json" in request.headers.get("content-type", ""):
        try:
            body = await request.json()
            if isinstance(body, dict) and body.get("target_book_id") is not None:
                target_book_id = int(body["target_book_id"])
        except Exception:
            pass
    if target_book_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="target_book_id is required.")
    res = analytics.move_transaction_to_book(db, current_user, transaction_id, int(target_book_id))
    if res["status"] == "not_found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=res["message"])
    return res


# ---------------------------------------------------------------------------
# API: User Transactions Ledger & Summary
# ---------------------------------------------------------------------------
@app.get("/api/transactions/{user_id}")
async def get_user_transactions(
    user_id: str,
    book_id: Optional[int] = None,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Returns the user's monthly spending total, store-wise breakdown,
    and recent transactions for the live ledger dashboard.
    Strictly isolated: does not merge with any other user, and scopes by `book_id` when specified.
    """
    verify_user_access(user_id, current_user)
    clean_user_id = current_user
    books_info = analytics.ensure_default_books(db, clean_user_id)
    active_book = None
    if book_id is not None:
        active_book = next((b for b in books_info["books"] if b.id == int(book_id)), None)
        if not active_book:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Book not found.")

    month_start = analytics.month_start()

    base_expense_filters = [
        Transaction.user_id == clean_user_id,
        Transaction.transaction_date >= month_start,
        Transaction.deleted_at.is_(None),
        analytics._is_expense_clause(),
    ]
    if book_id is not None:
        base_expense_filters.append(Transaction.book_id == int(book_id))

    # 1. Total monthly spending (expenses only)
    monthly_stats = (
        db.query(
            func.coalesce(func.sum(Transaction.total_amount), 0.0).label("total"),
            func.count(Transaction.id).label("count"),
        )
        .filter(*base_expense_filters)
        .first()
    )

    monthly_total = float(monthly_stats.total) if monthly_stats else 0.0
    monthly_count = int(monthly_stats.count) if monthly_stats else 0

    # 2. Store/Highlight-wise spending for this month (expenses only)
    display_label_expr = func.coalesce(Transaction.title, Transaction.store_name)
    store_stats = (
        db.query(
            display_label_expr.label("display_label"),
            func.sum(Transaction.total_amount).label("store_total"),
            func.count(Transaction.id).label("visit_count"),
        )
        .filter(*base_expense_filters)
        .group_by(display_label_expr)
        .order_by(func.sum(Transaction.total_amount).desc())
        .all()
    )

    store_breakdown = [
        {
            "store_name": (
                analytics._format_label(s.display_label)
                if s.display_label
                else "Daily Expense"
            ),
            "total_pkr": round(float(s.store_total), 2),
            "visit_count": s.visit_count,
        }
        for s in store_stats
    ]

    largest_tx = (
        db.query(Transaction)
        .filter(*base_expense_filters)
        .order_by(Transaction.total_amount.desc(), Transaction.id.desc())
        .first()
    )
    top_label = (
        (analytics._format_label(largest_tx.title or largest_tx.store_name))
        if largest_tx and (largest_tx.title or largest_tx.store_name)
        else None
    )
    top_store = f"{top_label} (PKR {largest_tx.total_amount:,.0f})" if largest_tx and top_label else None

    # 3. Recent 25 transactions with line items
    recent_filters = [
        Transaction.user_id == clean_user_id,
        Transaction.deleted_at.is_(None),
    ]
    if book_id is not None:
        recent_filters.append(Transaction.book_id == int(book_id))
    recent_txs = (
        db.query(Transaction)
        .filter(*recent_filters)
        .order_by(Transaction.transaction_date.desc(), Transaction.id.desc())
        .limit(25)
        .all()
    )

    return {
        "user_id": clean_user_id,
        "book_id": int(book_id) if book_id is not None else None,
        "active_book": active_book.to_dict() if active_book else None,
        "books": [b.to_dict() for b in books_info["books"]],
        "monthly_total": round(monthly_total, 2),
        "monthly_count": monthly_count,
        "top_store": top_store,
        "store_breakdown": store_breakdown,
        "category_breakdown": analytics.category_breakdown(db, clean_user_id, month_start, book_id=book_id),
        "daily_trend": analytics.daily_trend(db, clean_user_id, days=30, book_id=book_id),
        "payment_split": analytics.payment_split(db, clean_user_id, month_start, book_id=book_id),
        "budgets": analytics.budget_status(db, clean_user_id, book_id=book_id),
        "recurring": analytics.recurring_status(db, clean_user_id, book_id=book_id),
        "galla": analytics.galla_summary(db, clean_user_id, book_id=book_id),
        "udhaar": analytics.udhaar_summary(db, clean_user_id, book_id=book_id),
        "recent_transactions": [tx.to_dict() for tx in recent_txs],
    }


# ---------------------------------------------------------------------------
# API: Authentication & User Accounts (Firebase Auth Integration)
# ---------------------------------------------------------------------------
@app.get("/api/auth/config")
async def get_auth_config():
    """Returns public Firebase configuration for client SDK."""
    fb_key = os.getenv("FIREBASE_API_KEY", "").strip()
    auth_domain = os.getenv("FIREBASE_AUTH_DOMAIN", "").strip()
    project_id = os.getenv("FIREBASE_PROJECT_ID", "").strip()
    storage_bucket = os.getenv("FIREBASE_STORAGE_BUCKET", "").strip()
    messaging_sender_id = os.getenv("FIREBASE_MESSAGING_SENDER_ID", "").strip()
    app_id = os.getenv("FIREBASE_APP_ID", "").strip()
    cfg = {
        "apiKey": fb_key,
        "authDomain": auth_domain,
        "projectId": project_id,
        "storageBucket": storage_bucket,
        "messagingSenderId": messaging_sender_id,
        "appId": app_id,
    }
    is_configured = bool(fb_key and auth_domain and project_id and app_id)
    return {
        **cfg,
        "firebase": cfg,
        "configured": is_configured,
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


@app.delete("/api/auth/account")
async def delete_my_account(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Purges all tenant data across all tables and clears in-memory agent session history.
    """
    user_str = str(current_user).strip()
    tx_ids = [t_id for (t_id,) in db.query(Transaction.id).filter(Transaction.user_id == user_str).all()]
    if tx_ids:
        db.query(TransactionItem).filter(TransactionItem.transaction_id.in_(tx_ids)).delete(synchronize_session=False)
    db.query(Transaction).filter(Transaction.user_id == user_str).delete(synchronize_session=False)
    db.query(UdhaarEntry).filter(UdhaarEntry.user_id == user_str).delete(synchronize_session=False)
    db.query(Budget).filter(Budget.user_id == user_str).delete(synchronize_session=False)
    db.query(RecurringExpense).filter(RecurringExpense.user_id == user_str).delete(synchronize_session=False)
    db.query(PendingTransaction).filter(PendingTransaction.user_id == user_str).delete(synchronize_session=False)
    db.query(Book).filter(Book.user_id == user_str).delete(synchronize_session=False)
    db.query(UserAccount).filter(UserAccount.user_id == user_str).delete(synchronize_session=False)
    db.commit()
    clear_user_history(user_str)
    return {"status": "success", "deleted_user_id": user_str}


# ---------------------------------------------------------------------------
# API: Export (CSV opens directly in Excel; UTF-8 BOM keeps Urdu text intact)
# ---------------------------------------------------------------------------
@app.get("/api/export/{user_id}/udhaar")
async def export_udhaar_csv(
    user_id: str,
    book_id: Optional[int] = None,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    verify_user_access(user_id, current_user)
    clean_user_id = current_user
    if book_id is not None and analytics.resolve_book_for_user(db, clean_user_id, book_id) is None:
        raise HTTPException(status_code=404, detail="Book not found.")
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(analytics.UDHAAR_EXPORT_HEADER)
    writer.writerows(analytics.export_udhaar_rows(db, clean_user_id, book_id=book_id))
    filename = f"hisaabai_udhaar_{clean_user_id}_{get_pkt_now().strftime('%Y%m%d')}.csv"
    return Response(
        content="\ufeff" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/export/{user_id}/full")
async def export_full_json(
    user_id: str,
    book_id: Optional[int] = None,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    verify_user_access(user_id, current_user)
    clean_user_id = current_user
    if book_id is not None and analytics.resolve_book_for_user(db, clean_user_id, book_id) is None:
        raise HTTPException(status_code=404, detail="Book not found.")
    tx_q = db.query(Transaction).filter(
        Transaction.user_id == clean_user_id, Transaction.deleted_at.is_(None)
    )
    ud_q = db.query(UdhaarEntry).filter(
        UdhaarEntry.user_id == clean_user_id, UdhaarEntry.deleted_at.is_(None)
    )
    b_q = db.query(Budget).filter(Budget.user_id == clean_user_id)
    r_q = db.query(RecurringExpense).filter(RecurringExpense.user_id == clean_user_id)
    if book_id is not None:
        bid = int(book_id)
        tx_q = tx_q.filter(Transaction.book_id == bid)
        ud_q = ud_q.filter(UdhaarEntry.book_id == bid)
        b_q = b_q.filter(Budget.book_id == bid)
        r_q = r_q.filter(RecurringExpense.book_id == bid)
    txs = tx_q.order_by(Transaction.transaction_date.desc(), Transaction.id.desc()).all()
    udhaars = ud_q.order_by(UdhaarEntry.created_at.desc(), UdhaarEntry.id.desc()).all()
    budgets = b_q.all()
    recurring = r_q.all()
    books_info = analytics.ensure_default_books(db, clean_user_id)

    return {
        "status": "success",
        "user_id": clean_user_id,
        "book_id": int(book_id) if book_id is not None else None,
        "books": [bk.to_dict() for bk in books_info["books"]],
        "transactions": [t.to_dict() for t in txs],
        "udhaar": [u.to_dict() for u in udhaars],
        "budgets": [b.to_dict() for b in budgets],
        "recurring": [r.to_dict() for r in recurring],
    }


@app.get("/api/export/{user_id}")
async def export_csv(
    user_id: str,
    book_id: Optional[int] = None,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    verify_user_access(user_id, current_user)
    clean_user_id = current_user
    if book_id is not None and analytics.resolve_book_for_user(db, clean_user_id, book_id) is None:
        raise HTTPException(status_code=404, detail="Book not found.")
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(analytics.EXPORT_HEADER)
    writer.writerows(analytics.export_rows(db, clean_user_id, book_id=book_id))
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
    book_id: Optional[int] = None,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    verify_user_access(user_id, current_user)
    if book_id is not None and analytics.resolve_book_for_user(db, current_user, book_id) is None:
        raise HTTPException(status_code=404, detail="Book not found.")
    return {"budgets": analytics.budget_status(db, current_user, book_id=book_id)}


@app.post("/api/budgets")
async def save_budget(
    monthly_limit: float = Form(...),
    category: str = Form("total"),
    user_id: Optional[str] = Form(None),
    book_id: Optional[int] = Form(None),
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user_id and user_id.strip() != current_user:
        verify_user_access(user_id, current_user)
    uid = current_user
    cat = (category or "total").strip().lower()
    if monthly_limit <= 0:
        raise HTTPException(status_code=400, detail="Budget must be greater than 0.")
    books_info = analytics.ensure_default_books(db, uid)
    if book_id is not None:
        if not any(b.id == int(book_id) for b in books_info["books"]):
            raise HTTPException(status_code=404, detail="Book not found.")
        target_book_id = int(book_id)
    else:
        target_book_id = books_info["personal_book"].id
    b = (
        db.query(Budget)
        .filter(Budget.user_id == uid, Budget.category == cat, Budget.book_id == target_book_id)
        .first()
    )
    if b:
        b.monthly_limit = monthly_limit
    else:
        db.add(Budget(user_id=uid, book_id=target_book_id, category=cat, monthly_limit=monthly_limit))
    db.commit()
    return {"budgets": analytics.budget_status(db, uid, book_id=book_id)}


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
    book_id: Optional[int] = None,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    verify_user_access(user_id, current_user)
    if book_id is not None and analytics.resolve_book_for_user(db, current_user, book_id) is None:
        raise HTTPException(status_code=404, detail="Book not found.")
    return {"recurring": analytics.recurring_status(db, current_user, book_id=book_id)}


@app.post("/api/recurring")
async def save_recurring(
    title: str = Form(...),
    amount: float = Form(...),
    due_day: int = Form(1),
    category: str = Form("utilities"),
    user_id: Optional[str] = Form(None),
    book_id: Optional[int] = Form(None),
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
    books_info = analytics.ensure_default_books(db, uid)
    if book_id is not None:
        if not any(b.id == int(book_id) for b in books_info["books"]):
            raise HTTPException(status_code=404, detail="Book not found.")
        target_book_id = int(book_id)
    else:
        target_book_id = books_info["personal_book"].id
    item = (
        db.query(RecurringExpense)
        .filter(
            RecurringExpense.user_id == uid,
            RecurringExpense.title == clean_title,
            RecurringExpense.book_id == target_book_id,
        )
        .first()
    )
    if item:
        item.amount, item.due_day, item.category = amount, day, category.strip().lower()
    else:
        db.add(
            RecurringExpense(
                user_id=uid,
                book_id=target_book_id,
                title=clean_title,
                amount=amount,
                due_day=day,
                category=category.strip().lower(),
            )
        )
    db.commit()
    return {"recurring": analytics.recurring_status(db, uid, book_id=book_id)}


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
    return analytics.pay_recurring(db, uid, item, book_id=item.book_id)


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
# API: Udhaar / Khata Book (Lend & Borrow Money)
# ---------------------------------------------------------------------------
@app.get("/api/udhaar/{user_id}")
async def list_udhaar(
    user_id: str,
    book_id: Optional[int] = None,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    verify_user_access(user_id, current_user)
    if book_id is not None and analytics.resolve_book_for_user(db, current_user, book_id) is None:
        raise HTTPException(status_code=404, detail="Book not found.")
    return {"udhaar": analytics.udhaar_summary(db, current_user, book_id=book_id)}


@app.post("/api/udhaar")
async def create_udhaar(
    person_name: str = Form(...),
    amount: float = Form(...),
    direction: str = Form("receivable"),
    notes: Optional[str] = Form(None),
    phone_number: Optional[str] = Form(None),
    user_id: Optional[str] = Form(None),
    book_id: Optional[int] = Form(None),
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user_id and user_id.strip() != current_user:
        verify_user_access(user_id, current_user)
    books_info = analytics.ensure_default_books(db, current_user)
    if book_id is not None:
        if not any(b.id == int(book_id) for b in books_info["books"]):
            raise HTTPException(status_code=404, detail="Book not found.")
        target_book_id = int(book_id)
    else:
        target_book_id = books_info["business_book"].id
    res = analytics.add_udhaar_entry(
        db,
        user_id=current_user,
        person_name=person_name,
        amount=amount,
        direction=direction,
        notes=notes,
        phone_number=phone_number,
        book_id=target_book_id,
    )
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message", "Invalid udhaar entry"))
    return {"status": "success", "entry": res["entry"], "udhaar": analytics.udhaar_summary(db, current_user, book_id=book_id)}


@app.patch("/api/udhaar/{user_id}/{entry_id}")
async def update_udhaar_item(
    user_id: str,
    entry_id: int,
    request: Request,
    amount: Optional[float] = Form(None),
    notes: Optional[str] = Form(None),
    phone_number: Optional[str] = Form(None),
    direction: Optional[str] = Form(None),
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    verify_user_access(user_id, current_user)
    ct = request.headers.get("content-type", "")
    if "application/json" in ct:
        try:
            body = await request.json()
            if isinstance(body, dict):
                if amount is None and "amount" in body and body["amount"] is not None:
                    amount = float(body["amount"])
                if notes is None and "notes" in body:
                    notes = body["notes"]
                if phone_number is None and "phone_number" in body:
                    phone_number = body["phone_number"]
                if direction is None and "direction" in body:
                    direction = body["direction"]
        except Exception:
            pass

    res = analytics.update_udhaar_entry(
        db,
        user_id=current_user,
        entry_id=entry_id,
        amount=amount,
        notes=notes,
        phone_number=phone_number,
        direction=direction,
    )
    if res.get("status") == "not_found":
        raise HTTPException(status_code=404, detail=res.get("message", "Udhaar entry not found"))
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("message", "Invalid udhaar update"))
    return {"status": "success", "entry": res["entry"], "udhaar": analytics.udhaar_summary(db, current_user)}


@app.post("/api/udhaar/{user_id}/{entry_id}/settle")
async def settle_udhaar_item(
    user_id: str,
    entry_id: int,
    amount: Optional[float] = Form(None),
    book_id: Optional[int] = Form(None),
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    verify_user_access(user_id, current_user)
    if book_id is not None and analytics.resolve_book_for_user(db, current_user, book_id) is None:
        raise HTTPException(status_code=404, detail="Book not found.")
    res = analytics.settle_udhaar(
        db,
        user_id=current_user,
        entry_id=entry_id,
        payment_amount=amount,
    )
    if res.get("status") == "not_found":
        raise HTTPException(status_code=404, detail=res.get("message", "Udhaar entry not found"))
    return {**res, "udhaar": analytics.udhaar_summary(db, current_user, book_id=book_id)}


@app.delete("/api/udhaar/{user_id}/{entry_id}")
async def delete_udhaar_item(
    user_id: str,
    entry_id: int,
    book_id: Optional[int] = None,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    verify_user_access(user_id, current_user)
    if book_id is not None and analytics.resolve_book_for_user(db, current_user, book_id) is None:
        raise HTTPException(status_code=404, detail="Book not found.")
    entry = (
        db.query(UdhaarEntry)
        .filter(
            UdhaarEntry.user_id == current_user,
            UdhaarEntry.id == entry_id,
            UdhaarEntry.deleted_at.is_(None),
        )
        .first()
    )
    if not entry:
        raise HTTPException(status_code=404, detail="Udhaar entry not found.")
    entry.deleted_at = get_pkt_now()
    db.commit()
    return {"status": "success", "udhaar": analytics.udhaar_summary(db, current_user, book_id=book_id)}


# ---------------------------------------------------------------------------
# API: Customer & Supplier Party Profiles, Ledgers & Business Profiles
# ---------------------------------------------------------------------------
@app.get("/api/parties/{user_id}")
async def list_parties(
    user_id: str,
    book_id: Optional[int] = None,
    party_type: str = "all",
    search: str = "",
    status: str = "all",
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    verify_user_access(user_id, current_user)
    if book_id is not None and analytics.resolve_book_for_user(db, current_user, book_id) is None:
        raise HTTPException(status_code=404, detail="Book not found.")
    res = analytics.get_parties_summary(
        db,
        user_id=current_user,
        book_id=book_id,
        party_type=party_type,
        search=search,
        status=status,
    )
    return res


@app.post("/api/parties", status_code=status.HTTP_201_CREATED)
async def create_party(
    request: Request,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ct = request.headers.get("content-type", "")
    data = {}
    if "application/json" in ct:
        try:
            data = await request.json()
        except Exception:
            data = {}
    else:
        form = await request.form()
        data = dict(form)

    name = str(data.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Party name is required.")

    user_id_param = data.get("user_id")
    if user_id_param and str(user_id_param).strip() != current_user:
        verify_user_access(str(user_id_param).strip(), current_user)

    target_book_id = None
    if data.get("book_id") is not None and str(data["book_id"]).strip():
        b_id = int(data["book_id"])
        if analytics.resolve_book_for_user(db, current_user, b_id) is None:
            raise HTTPException(status_code=404, detail="Book not found.")
        target_book_id = b_id
    else:
        default_books = analytics.ensure_default_books(db, current_user)
        target_book_id = default_books["business_book"].id

    try:
        opening_balance = float(data.get("opening_balance") or 0.0)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid opening balance format.")

    if opening_balance < 0:
        raise HTTPException(status_code=400, detail="Opening balance must be non-negative.")

    opening_balance_type = str(data.get("opening_balance_type") or "receivable").strip().lower()
    if opening_balance_type not in ("receivable", "payable"):
        opening_balance_type = "receivable"

    party_type = str(data.get("party_type") or "customer").strip().lower()
    if party_type not in ("customer", "supplier", "general"):
        party_type = "customer"

    credit_limit = None
    if data.get("credit_limit") is not None and str(data["credit_limit"]).strip():
        try:
            credit_limit = float(data["credit_limit"])
        except (ValueError, TypeError):
            pass

    party = Party(
        user_id=current_user,
        book_id=target_book_id,
        name=name,
        phone=str(data.get("phone") or "").strip() or None,
        address=str(data.get("address") or "").strip() or None,
        party_type=party_type,
        opening_balance=opening_balance,
        opening_balance_type=opening_balance_type,
        credit_limit=credit_limit,
        is_active=True,
    )
    db.add(party)
    db.commit()
    db.refresh(party)

    bal_info = analytics.get_party_balance(db, current_user, party.id)
    return {
        "status": "success",
        "party": party.to_dict(),
        "balance": bal_info,
    }


@app.get("/api/parties/{user_id}/{party_id}")
async def get_party_detail(
    user_id: str,
    party_id: int,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    verify_user_access(user_id, current_user)
    party = (
        db.query(Party)
        .filter(
            Party.id == party_id,
            Party.user_id == current_user,
            Party.deleted_at.is_(None),
        )
        .first()
    )
    if not party:
        raise HTTPException(status_code=404, detail="Party not found.")

    bal_info = analytics.get_party_balance(db, current_user, party_id)
    return {
        "status": "success",
        "party": party.to_dict(),
        "metrics": {
            "opening_balance": bal_info["opening_balance"],
            "opening_balance_type": bal_info["opening_balance_type"],
            "total_credit_pkr": bal_info["total_credit_pkr"],
            "total_debit_pkr": bal_info["total_debit_pkr"],
            "total_given_pkr": bal_info["total_given_pkr"],
            "total_received_pkr": bal_info["total_received_pkr"],
            "net_balance_pkr": bal_info["net_balance_pkr"],
            "signed_balance_pkr": bal_info["signed_balance_pkr"],
            "balance_status": bal_info["balance_status"],
            "balance_text_ur": bal_info["balance_text_ur"],
            "balance_text_en": bal_info["balance_text_en"],
            "is_overdue": bal_info["is_overdue"],
            "nearest_due_date": bal_info["nearest_due_date"],
        },
        "transactions": bal_info["transactions"],
    }


@app.patch("/api/parties/{user_id}/{party_id}")
async def update_party(
    user_id: str,
    party_id: int,
    request: Request,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    verify_user_access(user_id, current_user)
    party = (
        db.query(Party)
        .filter(
            Party.id == party_id,
            Party.user_id == current_user,
            Party.deleted_at.is_(None),
        )
        .first()
    )
    if not party:
        raise HTTPException(status_code=404, detail="Party not found.")

    ct = request.headers.get("content-type", "")
    data = {}
    if "application/json" in ct:
        try:
            data = await request.json()
        except Exception:
            data = {}
    else:
        form = await request.form()
        data = dict(form)

    if "name" in data and data["name"] is not None:
        name_val = str(data["name"]).strip()
        if not name_val:
            raise HTTPException(status_code=400, detail="Party name cannot be empty.")
        party.name = name_val
    if "phone" in data:
        party.phone = str(data["phone"]).strip() if data["phone"] else None
    if "address" in data:
        party.address = str(data["address"]).strip() if data["address"] else None
    if "party_type" in data and data["party_type"]:
        p_type = str(data["party_type"]).strip().lower()
        if p_type in ("customer", "supplier", "general"):
            party.party_type = p_type
    if "opening_balance" in data and data["opening_balance"] is not None:
        op_val = float(data["opening_balance"])
        if op_val < 0:
            raise HTTPException(status_code=400, detail="Opening balance must be non-negative.")
        party.opening_balance = op_val
    if "opening_balance_type" in data and data["opening_balance_type"]:
        party.opening_balance_type = str(data["opening_balance_type"]).strip().lower()
    if "credit_limit" in data:
        party.credit_limit = float(data["credit_limit"]) if data["credit_limit"] is not None else None
    if "book_id" in data:
        b_id = int(data["book_id"]) if data["book_id"] is not None else None
        if b_id is not None and analytics.resolve_book_for_user(db, current_user, b_id) is None:
            raise HTTPException(status_code=404, detail="Book not found.")
        party.book_id = b_id

    party.updated_at = database.get_pkt_now()
    db.commit()
    db.refresh(party)
    bal_info = analytics.get_party_balance(db, current_user, party_id)
    return {"status": "success", "party": party.to_dict(), "balance": bal_info}


@app.delete("/api/parties/{user_id}/{party_id}")
async def delete_party(
    user_id: str,
    party_id: int,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    verify_user_access(user_id, current_user)
    party = (
        db.query(Party)
        .filter(
            Party.id == party_id,
            Party.user_id == current_user,
            Party.deleted_at.is_(None),
        )
        .first()
    )
    if not party:
        raise HTTPException(status_code=404, detail="Party not found.")
    now = database.get_pkt_now()
    party.deleted_at = now
    db.query(PartyTransaction).filter(
        PartyTransaction.party_id == party_id,
        PartyTransaction.user_id == current_user,
        PartyTransaction.deleted_at.is_(None),
    ).update({"deleted_at": now}, synchronize_session=False)

    db.commit()
    return {"status": "success", "message": f"Party '{party.name}' deleted successfully."}


@app.post("/api/parties/{user_id}/{party_id}/transactions", status_code=status.HTTP_201_CREATED)
async def create_party_transaction(
    user_id: str,
    party_id: int,
    request: Request,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    verify_user_access(user_id, current_user)
    party = (
        db.query(Party)
        .filter(
            Party.id == party_id,
            Party.user_id == current_user,
            Party.deleted_at.is_(None),
        )
        .first()
    )
    if not party:
        raise HTTPException(status_code=404, detail="Party not found.")

    ct = request.headers.get("content-type", "")
    data = {}
    if "application/json" in ct:
        try:
            data = await request.json()
        except Exception:
            data = {}
    else:
        form = await request.form()
        data = dict(form)

    raw_amt = data.get("amount")
    if raw_amt is None:
        raise HTTPException(status_code=400, detail="Transaction amount is required.")
    try:
        amount = float(raw_amt)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid transaction amount.")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Transaction amount must be greater than zero.")

    tx_type = str(data.get("tx_type") or "").strip().lower()
    if tx_type in ("credit", "gave", "udhaar", "diya", "out"):
        tx_type = "credit"
    elif tx_type in ("debit", "received", "jama", "wasooli", "liye", "in"):
        tx_type = "debit"
    else:
        raise HTTPException(status_code=400, detail="Transaction type must be 'credit' or 'debit'.")

    desc = str(data.get("description") or "").strip() or None
    payment_method = str(data.get("payment_method") or "cash").strip().lower()
    is_settlement = bool(data.get("is_settlement") or tx_type == "debit")

    tx_date = database.get_pkt_now()
    if data.get("tx_date"):
        try:
            tx_date_str = str(data["tx_date"]).strip()
            if len(tx_date_str) == 10:
                tx_date = datetime.strptime(tx_date_str, "%Y-%m-%d")
            else:
                tx_date = datetime.fromisoformat(tx_date_str.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            pass

    due_date = None
    if data.get("due_date"):
        try:
            due_str = str(data["due_date"]).strip()
            due_date = datetime.strptime(due_str[:10], "%Y-%m-%d")
        except Exception:
            pass

    new_tx = PartyTransaction(
        party_id=party.id,
        user_id=current_user,
        book_id=party.book_id,
        amount=amount,
        tx_type=tx_type,
        tx_date=tx_date,
        due_date=due_date,
        description=desc,
        payment_method=payment_method,
        bill_image_url=data.get("bill_image_url"),
        is_settlement=is_settlement,
    )
    db.add(new_tx)
    db.commit()
    db.refresh(new_tx)

    bal_info = analytics.get_party_balance(db, current_user, party.id)
    return {
        "status": "success",
        "transaction": new_tx.to_dict(),
        "party_summary": bal_info,
    }


@app.patch("/api/parties/{user_id}/{party_id}/transactions/{tx_id}")
async def update_party_transaction(
    user_id: str,
    party_id: int,
    tx_id: int,
    request: Request,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    verify_user_access(user_id, current_user)
    tx = (
        db.query(PartyTransaction)
        .filter(
            PartyTransaction.id == tx_id,
            PartyTransaction.party_id == party_id,
            PartyTransaction.user_id == current_user,
            PartyTransaction.deleted_at.is_(None),
        )
        .first()
    )
    if not tx:
        raise HTTPException(status_code=404, detail="Party transaction not found.")

    ct = request.headers.get("content-type", "")
    data = {}
    if "application/json" in ct:
        try:
            data = await request.json()
        except Exception:
            data = {}
    else:
        form = await request.form()
        data = dict(form)

    if "amount" in data and data["amount"] is not None:
        amt = float(data["amount"])
        if amt <= 0:
            raise HTTPException(status_code=400, detail="Amount must be greater than zero.")
        tx.amount = amt
    if "tx_type" in data and data["tx_type"]:
        t_type = str(data["tx_type"]).strip().lower()
        if t_type not in ("credit", "debit"):
            raise HTTPException(status_code=400, detail="Invalid tx_type.")
        tx.tx_type = t_type
    if "description" in data:
        tx.description = str(data["description"]).strip() if data["description"] else None
    if "payment_method" in data and data["payment_method"]:
        tx.payment_method = str(data["payment_method"]).strip().lower()
    if "due_date" in data:
        if data["due_date"]:
            try:
                tx.due_date = datetime.strptime(str(data["due_date"])[:10], "%Y-%m-%d")
            except Exception:
                pass
        else:
            tx.due_date = None
    if "is_settlement" in data:
        tx.is_settlement = bool(data["is_settlement"])

    tx.updated_at = database.get_pkt_now()
    db.commit()
    db.refresh(tx)
    bal_info = analytics.get_party_balance(db, current_user, party_id)
    return {"status": "success", "transaction": tx.to_dict(), "party_summary": bal_info}


@app.delete("/api/parties/{user_id}/{party_id}/transactions/{tx_id}")
async def delete_party_transaction(
    user_id: str,
    party_id: int,
    tx_id: int,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    verify_user_access(user_id, current_user)
    tx = (
        db.query(PartyTransaction)
        .filter(
            PartyTransaction.id == tx_id,
            PartyTransaction.party_id == party_id,
            PartyTransaction.user_id == current_user,
            PartyTransaction.deleted_at.is_(None),
        )
        .first()
    )
    if not tx:
        raise HTTPException(status_code=404, detail="Party transaction not found.")
    tx.deleted_at = database.get_pkt_now()
    db.commit()
    bal_info = analytics.get_party_balance(db, current_user, party_id)
    return {"status": "success", "message": "Transaction deleted successfully.", "party_summary": bal_info}


@app.get("/api/parties/{user_id}/{party_id}/statement")
async def get_party_statement(
    user_id: str,
    party_id: int,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    verify_user_access(user_id, current_user)
    res = analytics.generate_party_statement(
        db,
        user_id=current_user,
        party_id=party_id,
        start_date=start_date,
        end_date=end_date,
    )
    if res.get("status") == "error":
        raise HTTPException(status_code=404, detail=res.get("message", "Statement generation failed."))
    return res


@app.get("/api/parties/{user_id}/{party_id}/reminder")
async def get_party_reminder(
    user_id: str,
    party_id: int,
    lang: str = "ur",
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    verify_user_access(user_id, current_user)
    res = analytics.generate_party_reminder(
        db,
        user_id=current_user,
        party_id=party_id,
        lang=lang,
    )
    if res.get("status") == "error":
        raise HTTPException(status_code=404, detail=res.get("message", "Reminder generation failed."))
    return res


@app.get("/api/business-profile/{user_id}")
async def get_business_profile_endpoint(
    user_id: str,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    verify_user_access(user_id, current_user)
    profile = analytics.get_or_create_business_profile(db, current_user)
    return {"status": "success", "profile": profile.to_dict()}


@app.put("/api/business-profile/{user_id}")
async def update_business_profile_endpoint(
    user_id: str,
    request: Request,
    current_user: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    verify_user_access(user_id, current_user)
    profile = analytics.get_or_create_business_profile(db, current_user)

    ct = request.headers.get("content-type", "")
    data = {}
    if "application/json" in ct:
        try:
            data = await request.json()
        except Exception:
            data = {}
    else:
        form = await request.form()
        data = dict(form)

    if "business_name" in data and data["business_name"]:
        profile.business_name = str(data["business_name"]).strip()
    if "owner_name" in data:
        profile.owner_name = str(data["owner_name"]).strip() if data["owner_name"] else None
    if "phone_number" in data:
        profile.phone_number = str(data["phone_number"]).strip() if data["phone_number"] else None
    if "business_type" in data and data["business_type"]:
        profile.business_type = str(data["business_type"]).strip()
    if "address" in data:
        profile.address = str(data["address"]).strip() if data["address"] else None
    if "city" in data:
        profile.city = str(data["city"]).strip() if data["city"] else None
    if "currency" in data and data["currency"]:
        profile.currency = str(data["currency"]).strip()
    if "statement_header" in data:
        profile.statement_header = str(data["statement_header"]).strip() if data["statement_header"] else None
    if "statement_footer" in data:
        profile.statement_footer = str(data["statement_footer"]).strip() if data["statement_footer"] else None
    if "raast_id" in data:
        profile.raast_id = str(data["raast_id"]).strip() if data["raast_id"] else None
    if "easypaisa_number" in data:
        profile.easypaisa_number = str(data["easypaisa_number"]).strip() if data["easypaisa_number"] else None
    if "jazzcash_number" in data:
        profile.jazzcash_number = str(data["jazzcash_number"]).strip() if data["jazzcash_number"] else None
    if "bank_account_details" in data:
        profile.bank_account_details = str(data["bank_account_details"]).strip() if data["bank_account_details"] else None
    if "reminder_template_ur" in data:
        profile.reminder_template_ur = str(data["reminder_template_ur"]).strip() if data["reminder_template_ur"] else None
    if "reminder_template_en" in data:
        profile.reminder_template_en = str(data["reminder_template_en"]).strip() if data["reminder_template_en"] else None

    profile.updated_at = database.get_pkt_now()
    db.commit()
    db.refresh(profile)
    return {"status": "success", "profile": profile.to_dict()}



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

    deleted_store = tx.title or tx.store_name.title()
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

    host = os.getenv("APP_HOST", "0.0.0.0" if not IS_DEV else "127.0.0.1")
    port = int(os.getenv("PORT", os.getenv("APP_PORT", "8000")))
    reload = IS_DEV
    uvicorn.run("main:app", host=host, port=port, reload=reload)
