# HisaabAI — Multilingual Voice & Text Expense Tracker

**HisaabAI** is an intelligent, multi-user web application designed for financial expense logging and querying in **Urdu, Punjabi, Roman Urdu, and English**. It combines speech recognition with natural language reasoning to extract Pakistani stores, line items, prices, and quantities directly from natural speech or text, with deterministic arithmetic calculated completely by SQLite and Python.

Powered by:
- **FastAPI** backend & asynchronous server
- **Groq Cloud API** (`whisper-large-v3` for Automatic Speech Recognition)
- **LangChain & Groq LLM** (`openai/gpt-oss-20b` / `qwen/qwen3.8-27b`)
- **SQLAlchemy & SQLite** with WAL mode, foreign keys, and strict multi-user tenant isolation
- **JWT Session Tokens** with PBKDF2-HMAC-SHA256 (600,000 iterations) and Firebase server-side verification
- **Responsive Web UI** built with TailwindCSS, Web Audio `MediaRecorder` API, Chart.js, and PWA capabilities

---

## Security Architecture & Multi-Tenant IDOR Protection

1. **Session JWT & Bearer Auth:**
   - Every authenticated user receives a signed HS256 JWT session token with a 7-day expiry.
   - All data endpoints (`/api/chat`, `/api/transactions/*`, `/api/budgets*`, `/api/recurring*`, `/api/export/*`) require valid `Authorization: Bearer <token>` or session cookies.
   - Cross-user data access is rejected with `HTTP 403 Forbidden`.
2. **Server-Side Firebase Token Verification:**
   - Google and Phone authentication verifies Firebase ID tokens cryptographically using Google OAuth2 certificates.
   - Identity claims (email, phone number) are extracted exclusively from verified server-side claims, preventing identity forging.
3. **Password Security & Rate Limiting:**
   - Upgraded to 600,000 iterations of PBKDF2-HMAC-SHA256 with 16-byte random salts.
   - Backward-compatible verification for existing 100,000 iteration hashes with transparent auto-rehashing on login.
   - Failed login attempts are rate limited (5 attempts per minute per IP + identifier) returning `HTTP 429 Too Many Requests`.
   - Uniform error messages prevent username enumeration.
4. **Deterministic Math & Sanity Bounds:**
   - Zero LLM mental math: all sums, category breakdowns, and budgets are executed via SQL `SUM()`.
   - `price` in `transaction_items` is formally designated as the **LINE TOTAL**.
   - Large or implausible expenses (> 10,000,000 PKR, or > 1,000,000 PKR on mobile wallets) trigger structured confirmation before persisting to the ledger.
   - Mitigates CSV Formula Injection (CWE-1236) by escaping formula prefixes (`=`, `+`, `-`, `@`).

---

## Required Environment Variables

Configure these in your `.env` file:

| Variable | Description | Required / Default |
|---|---|---|
| `GROQ_API_KEY` | API key from [console.groq.com](https://console.groq.com) | **Required** |
| `SESSION_SECRET` | Secret key used to sign HS256 session JWTs | **Required in production** (dev fallback provided) |
| `APP_ENV` | Environment mode (`development`, `dev`, `production`) | Default: `development` |
| `DATABASE_URL` | SQLAlchemy database URL | Default: `sqlite:///./expenses.db` |
| `FIREBASE_PROJECT_ID` | Firebase project ID for server-side token verification | Required for Firebase token verification |
| `FIREBASE_API_KEY` | Firebase Web API key for client SDK initialization | Optional (for Firebase UI) |
| `FIREBASE_AUTH_DOMAIN` | Firebase Auth domain | Optional |
| `FIREBASE_STORAGE_BUCKET` | Firebase storage bucket | Optional |
| `FIREBASE_MESSAGING_SENDER_ID` | Firebase sender ID | Optional |
| `FIREBASE_APP_ID` | Firebase Application ID | Optional |
| `APP_HOST` | FastAPI bind address | Default: `127.0.0.1` |
| `APP_PORT` | FastAPI port | Default: `8000` |

---

## Installation & Setup

### 1. Create Virtual Environment & Install Dependencies

```bash
git clone https://github.com/Abubakar651/Hisaab-AI.git
cd Hisaab-AI

python3 -m venv .venv
source .venv/bin/activate  # On Linux/macOS

pip install -r requirements.txt
```

### 2. Configure Environment

Copy `.env.example` to `.env` and set your credentials:

```bash
cp .env.example .env
```

### 3. Run Automated Tests

```bash
pytest -v tests/
```

### 4. Start the Application

```bash
python3 main.py
```

Open your browser at **`http://127.0.0.1:8000`**.
