# HisaabAI — Multilingual Voice & Text Expense Tracker

**HisaabAI** is an intelligent, multi-user web application designed for expense logging and querying in **Urdu, Punjabi, Roman Urdu, and English**. It combines speech recognition with natural language reasoning to extract Pakistani stores, line items, prices, and quantities directly from natural speech or text.

Powered by:
- **FastAPI** backend & asynchronous server
- **Groq Cloud API** (`whisper-large-v3` for Automatic Speech Recognition)
- **LangChain & Groq LLM** (`llama-3.3-70b-versatile` / `qwen/qwen3.8-27b`)
- **SQLAlchemy & SQLite** with strict multi-user tenant isolation
- **Responsive Web UI** built with TailwindCSS, browser `MediaRecorder` API, and dynamic live ledger

---

## Key Features

1. **Strict Multi-User Isolation:**
   - Users switch accounts via the top navigation bar (`user_id`).
   - Every database query and agent tool is bound to `user_id` via closures in Python. No user can see or alter another user's financial records.
2. **Multilingual Voice & Text:**
   - Direct microphone recording in your browser using the Web Audio `MediaRecorder` API.
   - Code-switched Punjabi, Urdu, Roman Urdu, and English speech transcribed via `whisper-large-v3` with domain-prompt injection for Pakistani stores (`Imtiaz`, `Al-Fatah`, `Jalal Sons`, `Carrefour`, `Metro`, `CSD`).
3. **Structured Expense Extraction:**
   - Extracts store name, item names, categories, quantities, item prices, and date.
   - Normalizes stores and items to canonical lowercase English in the database while communicating back in the user's dialect.
4. **Natural Expense Querying:**
   - Inquire about store totals, item spending, and date ranges (e.g. *"What did I spend in the last 3 days?"* or *"Mera is mahine Imtiaz te kinna kharcha hoya?"*).
5. **Live Expense Ledger Dashboard:**
   - Live KPI cards: Monthly Total (PKR), Total Visits, Top Store.
   - Store-wise breakdown pills.
   - Expandable recent transaction cards with line items and one-click delete.

---

## Project Structure

```
Hisaab_Ai/
├── .env                  # Your Groq API key and environment config
├── .env.example          # Environment template
├── .gitignore            # Git exclusion rules
├── database.py           # SQLAlchemy models (transactions, transaction_items) & engine
├── agent.py              # Groq Whisper ASR, LangChain agent, and user-scoped tools
├── main.py               # FastAPI web server and REST endpoints
├── requirements.txt      # Project dependencies
├── templates/
│   └── index.html        # Single-page responsive Web UI with dark mode
└── static/               # Static assets
```

---

## Installation & Setup

### 1. Prerequisites
- Python 3.10+
- Groq Cloud API key ([console.groq.com](https://console.groq.com))

### 2. Create Virtual Environment & Install Dependencies

```bash
# Clone the repository
git clone https://github.com/Abubakar651/Hisaab-AI.git
cd Hisaab-AI

# Create virtual environment
python3 -m venv .venv

# Activate virtual environment
source .venv/bin/activate  # On Linux/macOS
# or .venv\Scripts\activate on Windows

# Install dependencies
pip install -r requirements.txt
```

### 3. Configure Environment Variables

Create or edit `.env`:

```env
GROQ_API_KEY=your_groq_api_key_here
GROQ_LLM_MODEL=llama-3.3-70b-versatile
DATABASE_URL=sqlite:///./expenses.db
APP_HOST=0.0.0.0
APP_PORT=8000
```

### 4. Start the Application

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Open your browser at **`http://localhost:8000`**.

---

## Example Prompts & Voice Notes

### Expense Logging (Write Path):
- **Punjabi (Roman):** *"Main aaj Imtiaz ton 1500 da cooking oil te 450 di bread layi"*
- **Urdu Script (اردو):** *"آج میں نے الفتح سے 3200 روپے کا راشن خریدا"*
- **Roman Urdu:** *"Aaj maine Jalal Sons se 1200 ka doodh aur anday liye"*
- **English:** *"Spent 2500 PKR at Carrefour on chicken and rice today"*

### Expense Querying (Read Path):
- **Punjabi:** *"Mera is mahine Imtiaz te kinna kharcha hoya?"*
- **English:** *"What did I spend in the last 3 days?"*
- **Item Query:** *"How much have I spent on milk?"*
- **Category Query:** *"Show me my grocery expenses for this month."*

### Undo / Delete:
- *"Delete my last entry"*
- *"Ghalat likha gaya, pichla record mita do"*

---

## API Documentation

- `GET /` — Serves the interactive Web UI.
- `POST /api/chat` — Accepts `multipart/form-data` with `user_id`, optional text `message`, and optional `audio` recording.
- `GET /api/transactions/{user_id}` — Returns monthly total, store breakdown, and recent transactions.
- `DELETE /api/transactions/{user_id}/{transaction_id}` — Deletes a specific transaction for that user.
- Interactive Swagger docs are available at **`http://localhost:8000/docs`**.

