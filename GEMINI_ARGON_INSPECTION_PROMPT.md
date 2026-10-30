# 🤖 Gemini Inspection & Oversight Prompt: HisaabAI Application Audit

> **Instructions for the User:**  
> Copy and paste the entire prompt below into **Google Gemini (Gemini Advanced / Gemini 1.5 Pro / Gemini 2.0 / Gemini Argon)**.  
> It gives Gemini complete visibility over the HisaabAI codebase, architecture, sign-in mechanisms (Google, Phone Number, Email/Password, WhatsApp), database schema, AI voice pipeline, and testing tools.

---

```markdown
You are acting as **Senior Software Architect, Security Auditor, and Financial AI Evaluator** inspecting **HisaabAI** — a production-grade, multilingual (Urdu, Punjabi, English) voice and text expense tracker built specifically for Pakistan.

Your task is to oversee, inspect, and evaluate the entire application: its authentication & sign-in architecture, voice AI pipeline, database integrity, deterministic financial math, and API endpoints.

Here is the complete architectural blueprint and implementation specification of HisaabAI:

---

## 1. HIGH-LEVEL APPLICATION ARCHITECTURE

* **Backend Framework:** FastAPI (Python 3.12, async endpoints, threadpool execution for AI/ASR).
* **Database & ORM:** SQLAlchemy with SQLite (`expenses.db`), using automatic schema migrations (`_migrate_sqlite`).
* **AI & Language Models:**
  - **Speech-to-Text (ASR):** Groq Cloud Whisper (`whisper-large-v3`) with Web Audio API recording.
  - **Conversational Intelligence:** LangChain agent powered by Groq (`openai/gpt-oss-20b`) with multi-tier fallback (`qwen/qwen3.8-27b`, `openai/gpt-oss-120b`).
  - **Deterministic Math Engine:** `analytics.py` (all sums, categories, daily trends, and budget percentages are computed via SQL/Python, **never** by LLM mental math).
* **Frontend:**
  - Modern dual-pane web application (Left: Chat & Voice Interface, Right: Live Expense Ledger).
  - Vanilla JS, Tailwind CSS, Lucide icons, Chart.js for data visualization.
  - **PWA (Progressive Web App):** Installable on Android & iOS (`/manifest.webmanifest`, `/sw.js`, 192px/512px icons).
* **External Integrations:**
  - Twilio WhatsApp Webhook (`/api/whatsapp`) processing text and voice notes with multi-tenant isolation.
  - Firebase Auth SDK (Google Sign-In, Phone Number SMS OTP, Email/Password).

---

## 2. COMPLETE AUTHENTICATION & SIGN-IN ARCHITECTURE

HisaabAI features a unified, multi-provider authentication system centered on the `user_accounts` table:

### Table Schema: `user_accounts`
| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PRIMARY KEY | Auto-incrementing identifier |
| `user_id` | VARCHAR(100) UNIQUE INDEX | Universal user key (e.g. `+923001234567`, `user@gmail.com`, `bakar`) |
| `auth_provider` | VARCHAR(30) | `google`, `phone`, `credentials`, `whatsapp`, or `guest` |
| `display_name` | VARCHAR(150) | User's full name or phone number |
| `email` | VARCHAR(150) INDEX | Google or registered email address |
| `phone_number` | VARCHAR(50) INDEX | Standardized E.164 phone (`+923001234567`) |
| `photo_url` | VARCHAR(500) | Avatar image URL |
| `password_hash` | VARCHAR(255) | Salted PBKDF2-HMAC-SHA256 hash (100,000 iterations) |
| `created_at` | DATETIME | Account creation timestamp |
| `last_login` | DATETIME | Last activity timestamp |

### Supported Sign-In Flows:
1. **🌐 Google Account Sign-In:**
   - Powered by Firebase Auth SDK (`firebase.auth.GoogleAuthProvider`).
   - One-click popup extracts verified email, display name, and avatar URL.
   - Endpoint: `POST /api/auth/login` (records or updates `user_accounts`).
   - Persists verified profile and syncs user expenses across all devices.

2. **📱 Pakistani Phone Number Sign-In (`+92 300 1234567`):**
   - Automatically normalizes Pakistani inputs (`0300...` or `300...` $\rightarrow$ `+92300...`).
   - Uses Firebase Phone Auth with invisible reCAPTCHA and 6-digit SMS OTP.
   - **Cross-Channel WhatsApp Sync:** Because the WhatsApp bot scopes users as `wa:+923...`, signing in with your phone number on the web app automatically links and surfaces all transactions recorded through WhatsApp!

3. **🔑 Email & Password Account Creation (Sign Up & Login):**
   - Direct account registration without third-party dependencies.
   - `POST /api/auth/register`: Validates name, email/phone, and hashes password using standard-library PBKDF2-HMAC-SHA256 with 16-byte cryptographically random salt.
   - `POST /api/auth/password-login`: Verifies constant-time HMAC digest.

4. **👤 Local / Guest Mode:**
   - Allows instant switching between local usernames (e.g., `bakar`, `ali`, `home`) for offline or demo use.

---

## 3. CORE APPLICATION MODULES & PIPELINES

### A. Voice & Natural Language Pipeline (`agent.py`)
* Users speak or write in **Urdu (اردو)**, **Punjabi (پنجابی)**, or **English**.
* Language Rules:
  - Urdu/Punjabi inputs **always** receive responses in polite Nastaleeq Urdu script. Absolute ban on Gurmukhi script.
  - English inputs receive English responses.
* Pakistani Spoken Number Parsing: Resolves colloquial terms (`sau`, `dedh sau`, `dhai sau`, `hazaar`, `dedh hazaar`, `lakh`, Punjabi counts).
* **Unit Collision Guard (`sanitize_transaction_amount` in `analytics.py`):**
  - Prevents double-scaling typos (e.g. typing `20000 lakh` previously caused $20,000 \times 100,000 = 2,000,000,000$).
  - Detects digit-multiplier collisions and bounds wallet transactions to Pakistani regulatory limits.
* **Modern LangChain Tool Calling (7 Database Tools):**
  1. `log_expense`: Inserts transaction & items with payment method (`cash`, `card`, `easypaisa`, `jazzcash`, `bank_transfer`).
  2. `query_expenses`: Deterministic filtering by date range, store, item, category, and payment channel.
  3. `delete_last_transaction`: Instant undo functionality.
  4. `set_budget`: Sets overall or category monthly spending limits.
  5. `get_budget_status`: Returns live spending, limits, remaining amounts, and percentage levels (`ok`, `warning`, `over`).
  6. `add_recurring_expense`: Registers monthly obligations (rent, LESCO bill, school fee, committee) with due days.
  7. `mark_recurring_paid`: Automatically fetches the saved recurring amount, marks it paid for the current month, and creates the ledger transaction.

### B. Deterministic Analytics Engine (`analytics.py`)
* **Category Breakdown:** SQL `SUM()` grouped by category with exact percentages.
* **Daily Trend:** 30-day zero-filled daily spending trend (no date gaps for Chart.js).
* **Payment Split:** Cash vs. Digital breakdown with transaction counts.
* **CSV Export:** `GET /api/export/{user_id}` with UTF-8 Byte Order Mark (`\ufeff`) ensuring Urdu Nastaleeq text opens properly in Microsoft Excel.

### C. Live Ledger Dashboard (`templates/index.html`)
* Left Pane: Conversational chat stream with voice wave recorder and suggestions.
* Right Pane:
  - Monthly spending KPI card & total visits.
  - Store breakdown pills.
  - Monthly Budget progress bars (Green $<80\%$, Amber $80-99\%$, Red $\ge 100\%$).
  - Chart.js Category Doughnut Chart & 30-Day Daily Spending Bar Chart.
  - Cash vs. Digital payment breakdown.
  - Recurring Monthly Bills tracker with one-click "Mark Paid" buttons.
  - Live reverse-chronological transaction cards with payment method badges and delete buttons.

---

## 4. API SPECIFICATION TABLE

| Route | Method | Purpose |
|---|---|---|
| `/` | `GET` | Main UI dual-pane application |
| `/api/chat` | `POST` | Process text message through LangChain agent |
| `/api/voice` | `POST` | Process audio file (WAV/WEBM/OGG) via Groq Whisper + Agent |
| `/api/transactions/{user_id}` | `GET` | Complete ledger, charts, budgets, and recurring data |
| `/api/transactions/{user_id}/{id}`| `DELETE` | Delete specific transaction record |
| `/api/export/{user_id}` | `GET` | Download Excel-ready CSV with UTF-8 BOM |
| `/api/budgets` | `POST` | Create or update monthly budget limit |
| `/api/budgets/{user_id}/{id}` | `DELETE` | Remove budget |
| `/api/recurring` | `POST` | Register recurring bill (rent, electricity, committee) |
| `/api/recurring/{user_id}/{id}/pay` | `POST` | Mark recurring bill paid & log expense |
| `/api/auth/config` | `GET` | Public Firebase client configuration |
| `/api/auth/login` | `POST` | Unified login (Google, Phone Number, or Guest) |
| `/api/auth/register` | `POST` | Create account with Name, Email/Phone, and Password |
| `/api/auth/password-login` | `POST` | Authenticate with Password |
| `/api/auth/profile/{user_id}` | `GET` | Get user account metadata |
| `/api/whatsapp` | `POST` | Twilio WhatsApp text and voice note webhook |
| `/manifest.webmanifest` | `GET` | Progressive Web App manifest |
| `/sw.js` | `GET` | Service worker for offline shell caching |

---

## 5. YOUR OVERSIGHT & EVALUATION TASKS

Please perform a comprehensive review of this application across the following dimensions and provide your feedback:

1. **Authentication Flow Integrity:**
   - Review the interaction between Google OAuth, Phone Number OTP, and Email/Password credentials.
   - Confirm multi-tenant data isolation (`user_id` filtering across transactions, budgets, and recurring bills).
   - Evaluate the security of password hashing (PBKDF2-HMAC-SHA256, 100,000 rounds).

2. **WhatsApp Bot & Web Dashboard Convergence:**
   - Verify how a user logging in with `+92300...` on the web accesses transactions logged via `wa:+92300...` on WhatsApp.

3. **Financial Mathematics & AI Isolation:**
   - Confirm that the LLM is strictly prohibited from doing mental math, and verify that all calculations remain 100% deterministic via SQL/Python.

4. **Pakistani Localization & Edge Cases:**
   - Review number parsing for colloquial Urdu/Punjabi terms and the unit collision guard (e.g. `20000 lakh` resolving to PKR 20,000 rather than 2 Billion).

5. **Recommendations for Future Scalability:**
   - Suggest production hardening steps (PostgreSQL migration, JWT session cookies, rate-limiting, Redis caching).

Provide a structured, executive audit report summarizing the strengths, architecture score, and actionable enhancements.
```
