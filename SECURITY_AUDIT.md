# HisaabAI — Security Audit & Codebase Health Report

> **Classification:** Read-Only Audit  
> **Date:** 2026-10-31  
> **Scope:** Full repository (`main.py`, `database.py`, `agent.py`, `analytics.py`, `static/`, `templates/`, configuration & git history)  
> **Standard:** Findings categorized by severity with file:line citations, evidence, and remediation plans. Labelled with verification status (`VERIFIED`, `REPORTED`, `UNVERIFIED`).

---

## Executive Summary

HisaabAI has completed its migration from legacy custom JWT/PBKDF2 session tokens to Google Firebase Authentication (ID token claims verification via Firebase Admin SDK with cached public certificate fallback). 

Multi-tenant isolation is enforced across all financial endpoints via `verify_user_access(requested_user_id, authenticated_user_id)`, rejecting any cross-user data access with HTTP 403 Forbidden. Client-side storage of authentication tokens in `localStorage` has been eliminated in favor of memory-held Firebase credentials (`currentUser.getIdToken()`).

This audit documents vulnerabilities, configuration hazards, and performance/security improvements identified during code inspection.

---

## Detailed Findings

### 1. [HIGH] Dev-Mode Firebase Token Fallback Permits Project Audience Bypass (`audience=None`)
- **Status:** `VERIFIED`
- **Location:** [`main.py:205-207`](file:///home/bakar/Desktop/Hisaab_Ai/main.py#L205-L207), [`main.py:223-227`](file:///home/bakar/Desktop/Hisaab_Ai/main.py#L223-L227)
- **Evidence:**
  ```python
  project_id = os.getenv("FIREBASE_PROJECT_ID", "").strip()
  if not IS_DEV and not project_id:
      raise RuntimeError("FIREBASE_PROJECT_ID environment variable is required in production mode.")
  ...
  claims = google_id_token.verify_firebase_token(
      token.strip(),
      _cached_request_adapter,
      audience=project_id if project_id else None,
  )
  ```
- **Vulnerability Mechanism:**  
  When `APP_ENV=development` (`IS_DEV=True`) and `FIREBASE_PROJECT_ID` is unset or empty, `audience=None` is passed to Google Auth's `verify_firebase_token`. Under Google Auth specifications, passing `audience=None` skips JWT audience (`aud`) claim verification entirely. A rogue actor could present a valid Firebase token minted for ANY arbitrary Google Firebase project (e.g. an attacker-owned free project) containing an email matching a target user, and the server would authenticate the request under that user's identity.
- **Suggested Fix:**  
  Enforce that `project_id` is always required for token verification regardless of environment:
  ```python
  if not project_id:
      raise RuntimeError("FIREBASE_PROJECT_ID environment variable is strictly required for token verification.")
  ```
  Never pass `audience=None` to `verify_firebase_token`.

---

### 2. [HIGH] Content Security Policy Allows Unpinned, Open CDNs in `script-src`
- **Status:** `VERIFIED`
- **Location:** [`main.py:129-135`](file:///home/bakar/Desktop/Hisaab_Ai/main.py#L129-L135)
- **Evidence:**
  ```python
  response.headers["Content-Security-Policy"] = (
      "default-src 'self'; "
      "script-src 'self' https://apis.google.com https://cdn.tailwindcss.com https://unpkg.com https://cdn.jsdelivr.net; "
      ...
  )
  ```
- **Vulnerability Mechanism:**  
  1. `unpkg.com` and `cdn.jsdelivr.net` are public, user-publishable CDNs where anyone with an npm account can publish packages. If a developer or third-party script makes an insecure dynamic import, or if an attacker finds an open redirect / XSS vector, scripts hosted on these domains will execute within the application origin without CSP restriction.
  2. `cdn.tailwindcss.com` is a client-side development JIT compiler explicitly documented by Tailwind Labs as insecure and unsuitable for production environments due to bundle size, performance degradation, and script execution risks.
- **Suggested Fix:**  
  1. Pre-vendor runtime dependencies (Lucide icons, DOMPurify, Marked.js, Chart.js) locally under `static/vendor/` with Subresource Integrity (SRI) hashes, matching the strategy used for Firebase SDK in `static/vendor/firebase/`.
  2. Precompile CSS using the standalone Tailwind CLI into `static/theme.css` at build time.
  3. Tighten `script-src` to: `'self' https://apis.google.com;`.

---

### 3. [MEDIUM] Absence of Per-User Rate Limiting on Cost-Intensive Endpoints
- **Status:** `VERIFIED`
- **Location:** [`main.py:410`](file:///home/bakar/Desktop/Hisaab_Ai/main.py#L410) (`/api/chat`), [`main.py:590`](file:///home/bakar/Desktop/Hisaab_Ai/main.py#L590) (`/api/transcribe`)
- **Evidence:**  
  While IP-based rate limiting exists for public endpoints (`/api/auth/register`), authenticated user routes (`/api/chat` and `/api/transcribe`) execute without per-user request throttling.
- **Vulnerability Mechanism:**  
  A single authenticated user (or an automated script with a valid token) can spam `/api/chat` or upload high-frequency audio files to `/api/transcribe`, causing rapid exhaustion of downstream LLM quotas (AWS Bedrock or Groq API), generating unexpected cloud compute charges, and causing denial-of-service for all other users.
- **Suggested Fix:**  
  Implement an in-memory sliding-window rate limiter keyed by `current_user.user_id`:
  - `/api/chat`: Maximum 30 requests per minute per user.
  - `/api/transcribe`: Maximum 10 requests per minute per user.

---

### 4. [MEDIUM] Unbounded In-Memory Session History Growth
- **Status:** `VERIFIED`
- **Location:** [`agent.py:41`](file:///home/bakar/Desktop/Hisaab_Ai/agent.py#L41), [`agent.py:1005-1011`](file:///home/bakar/Desktop/Hisaab_Ai/agent.py#L1005-L1011)
- **Evidence:**
  ```python
  _IN_MEMORY_SESSIONS = {}
  ...
  if clean_user_id not in _IN_MEMORY_SESSIONS:
      _IN_MEMORY_SESSIONS[clean_user_id] = []
  _IN_MEMORY_SESSIONS[clean_user_id].append(HumanMessage(content=user_text))
  ```
- **Vulnerability Mechanism:**  
  `_IN_MEMORY_SESSIONS` caps each user's history at 20 messages, but the dictionary itself never evicts users. In a multi-user environment or across days of service uptime, the dictionary retains entries for every user ID that has ever invoked the agent, leading to linear memory growth and potential worker OOM (Out Of Memory) crashes.
- **Suggested Fix:**  
  Wrap `_IN_MEMORY_SESSIONS` with a Least Recently Used (LRU) cache (e.g. `collections.OrderedDict` with `popitem(last=False)` at capacity 500) or an explicit TTL timestamp eviction.

---

### 5. [LOW] Insecure String Comparison for WhatsApp Webhook Signature
- **Status:** `VERIFIED`
- **Location:** [`main.py:1017`](file:///home/bakar/Desktop/Hisaab_Ai/main.py#L1017)
- **Evidence:**
  ```python
  if expected_signature != signature:
      raise HTTPException(status_code=403, detail="Invalid signature")
  ```
- **Vulnerability Mechanism:**  
  Using Python standard string inequality `!=` short-circuits on the first mismatched byte, creating theoretical timing discrepancies measurable over low-latency network paths.
- **Suggested Fix:**  
  Use `hmac.compare_digest(expected_signature, signature)`.

---

### 6. [LOW] Local Environment File (`.env`) File System Permissions
- **Status:** `VERIFIED`
- **Location:** Local workspace `.env`
- **Evidence:**  
  `ls -l .env` displays default umask permissions (`-rw-rw-r--` or `-rw-r--r--`), which allows local read access to other non-root system users on shared development workstations.
- **Suggested Fix:**  
  Enforce strict read/write permissions limited exclusively to the owning user:
  ```bash
  chmod 600 .env
  ```

---

### 7. [INFORMATIONAL] Secrets in Git History Audit
- **Status:** `VERIFIED`
- **Evidence:**  
  1. `git log --all --full-history -- ".env"` returns no commits (the real `.env` file was never committed to git).
  2. Commit `9585220` safely removed `.env.example`, which only contained template placeholders (`AIzaSy_your_firebase_web_api_key_here`, `your_groq_api_key_here`).
  3. `git log -p -S "AKIA" --all` verified that no AWS secret credentials have been committed.

---

## Summary Matrix

| ID | Issue | Severity | Status | Scope |
|---|---|---|---|---|
| SEC-01 | Audience=None dev-mode token bypass | HIGH | VERIFIED | `main.py:226` |
| SEC-02 | Permissive CDN sources in CSP `script-src` | HIGH | VERIFIED | `main.py:131` |
| SEC-03 | Missing per-user API rate limits | MEDIUM | VERIFIED | `main.py:410,590` |
| SEC-04 | Unbounded in-memory session growth | MEDIUM | VERIFIED | `agent.py:41` |
| SEC-05 | Non-constant time WhatsApp signature check | LOW | VERIFIED | `main.py:1017` |
| SEC-06 | `.env` file system permissions | LOW | VERIFIED | `.env` |
| SEC-07 | Secrets in Git History Audit | CLEAN | VERIFIED | Git history |

*Note: In accordance with Hard Rule 6, none of the above audit findings have been modified in application code without prior explicit user approval.*
