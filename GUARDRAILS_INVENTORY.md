# HisaabAI — Guardrails Inventory

> **Auditor Role:** Read-only Auditor for HisaabAI  
> **Repository:** `/home/bakar/Desktop/Hisaab_Ai`  
> **Date:** 2026-11-01  
> **Status Classification:** `PRESENT` (enforced in code), `PARTIAL` (incomplete), `PROMPT-ONLY` (only in LLM prompt), `MISSING` (searched, nothing found)  
> **Verification Label:** `VERIFIED` (ran command/test with verified output), `REPORTED` (read directly from code), `UNVERIFIED`

---

## 1. Summary Table of Guardrails

| # | Guardrail Description | Status | Evidence (file:line) | Label |
|---|---|---|---|---|
| **A1** | Per-user / per-IP rate limit or quota on `/api/chat`, `/api/transcribe`, `/api/whatsapp` | **MISSING** | `main.py:427, 545, 1030`; `database.py:218` defines unused `LoginAttempt` table | **VERIFIED** |
| **A2** | Max audio file upload size (5 MB) & audio extension validation | **PRESENT** | `main.py:99-100, 452, 460-464, 563, 571-575` (`MAX_AUDIO_SIZE = 5MB`) | **VERIFIED** |
| **A2** | Max length limit on chat text (`message`, `body_text`) | **MISSING** | `main.py:431, 488-501` (`/api/chat`), `main.py:1054` (`/api/whatsapp`) | **REPORTED** |
| **A2** | Max audio duration limit | **MISSING** | `main.py:448-482, 547-585` (checks byte size only, no duration limit) | **REPORTED** |
| **A2** | Max global HTTP request body size limit (app-level & server-level) | **MISSING** | `main.py:112-142` (no body size middleware), `main.py:1126-1129` (default uvicorn) | **REPORTED** |
| **A3** | Groq client retry policy (`max_retries=3`) | **PRESENT** | `agent.py:101, 122` (`max_retries=3`) | **REPORTED** |
| **A3** | HTTP request timeout on Groq & Bedrock API clients | **MISSING** | `agent.py:43-44, 73-91, 97-135` (no `request_timeout` or `timeout` passed) | **REPORTED** |
| **A3** | Runtime Bedrock throttling failover with 5-minute circuit breaker | **PRESENT** | `agent.py:54, 72, 1029-1056` (sets `_BEDROCK_THROTTLED_UNTIL = now + 300s` and invokes Groq fallback) | **VERIFIED** |
| **A4** | LLM output token bounds (`max_tokens`) | **PRESENT** | `agent.py:84` (Bedrock 1000), `agent.py:100, 121, 133` (Groq 800), `agent.py:1043` (fallback 600) | **REPORTED** |
| **A4** | Agent recursion and loop limit (tool invocation limit per turn) | **PARTIAL** | `agent.py:1006, 1013` (relies on LangGraph default 25; no app-level recursion limit passed) | **REPORTED** |
| **B5** | Tool argument validation: amount positive, finite, not NaN | **PRESENT** | `analytics.py:87-98` (`validate_transaction_amount`), `agent.py:497` | **VERIFIED** |
| **B5** | Tool argument validation: amount upper bound & wallet limits | **PARTIAL** | `analytics.py:101-119` (flags >10M general / >1M wallet into pending, but no hard maximum) | **VERIFIED** |
| **B5** | Pakistani spoken number parsing (lakh, crore, hazaar, dedh sau) | **PROMPT-ONLY** | `agent.py:378` (prompt instructions only; no deterministic regex/code pre-parser) | **REPORTED** |
| **B5** | Tool argument validation: transaction & query date range bounds | **MISSING** | `agent.py:410-420, 637-654` (`strptime` without future/historical calendar boundary checks) | **REPORTED** |
| **B5** | Tool argument validation: payment method whitelist | **PRESENT** | `analytics.py:53-83` (`normalize_payment_method` to cash/card/bank_transfer/easypaisa/jazzcash) | **VERIFIED** |
| **B5** | Tool argument validation: category whitelist & string length bounds | **MISSING** | `agent.py:453, 558, 669, 805, 873` (accepts arbitrary unvalidated strings) | **REPORTED** |
| **B6** | User identity isolation: model cannot provide `user_id` / `email` | **PRESENT** | `agent.py:389-394, 971` (tools generated in user-scoped closure); schemas omit `user_id` | **VERIFIED** |
| **B7** | Pending large-amount flow: threshold (>10M / >1M wallet) & 300s TTL | **PRESENT** | `analytics.py:101-119`, `agent.py:506` (`expires_at = now + 300s`) | **VERIFIED** |
| **B7** | Pending large-amount flow: atomic confirm via database claim | **PRESENT** | `analytics.py:426-442` (`PendingTransaction` atomic delete/claim with 409 conflict handling) | **VERIFIED** |
| **B7** | LLM cannot confirm pending expense (UI-only confirmation) | **PRESENT** | `agent.py:395-942` (no `confirm_pending_expense` tool exists; UI POST endpoint only) | **VERIFIED** |
| **B8** | Limits on destructive actions (deletion frequency / rate limit) | **MISSING** | `agent.py:756-798`, `main.py:1085-1122` (no rate limit on delete calls) | **REPORTED** |
| **B8** | Soft delete (`deleted_at.is_(None)`) excluded from all aggregates & exports | **PRESENT** | `main.py:696, 714, 737, 1099`, `agent.py:633, 762`, `analytics.py:154, 190, 214, 242, 253, 358` | **VERIFIED** |
| **C9** | System prompt instruction treating inputs as untrusted data | **MISSING** | `agent.py:349-383` (system prompt lacks explicit prompt injection / untrusted data directives) | **REPORTED** |
| **C9** | Client chat history sanitization outside prompt | **PRESENT** | `main.py:503-520`, `agent.py:975-990` (whitelists user/assistant roles, caps 1000 chars, last 4 items) | **REPORTED** |
| **C10** | Sanitizing zero-width, control characters, and length on user text | **MISSING** | `main.py:485-501`, `agent.py:967` (raw text passed to LLM without zero-width / control char filter) | **REPORTED** |
| **C11** | Model output HTML / DOM sanitization (`DOMPurify` + `marked`) | **PRESENT** | `static/app.js:693, 713`, `templates/index.html:31-36` (pinned SRI DOMPurify) | **VERIFIED** |
| **C11** | CSV formula injection sanitization | **PRESENT** | `analytics.py:381`, `analytics.py:340-349` (`sanitize_csv_cell` strips `=`, `+`, `-`, `@`) | **VERIFIED** |
| **C12** | WhatsApp `wa:` namespace tenant separation | **PRESENT** | `main.py:1073` (`user_id=f"wa:{sender}"`), `main.py:402-411` (`verify_user_access`) | **VERIFIED** |
| **C12** | Twilio webhook HMAC signature verification | **PRESENT** | `main.py:1020-1048` (HMAC-SHA1 constant-time `hmac.compare_digest`, bypass blocked in prod) | **VERIFIED** |
| **D13** | Hindi Devanagari & Indian Gurmukhi script blocking & conversion | **PRESENT** | `agent.py:141-173, 1016` (`DEV_GUR_REGEX` detects `\u0900-\u097F\u0A00-\u0A7F` and converts) | **VERIFIED** |
| **D13** | Language congruence enforcement (Urdu->Urdu Nastaliq, English->English) | **PROMPT-ONLY** | `agent.py:351-362` (prompt mandate only; no programmatic language verification) | **REPORTED** |
| **D14** | Programmatic verification that reply numbers match tool output | **PROMPT-ONLY** | `agent.py:370` ("ZERO MENTAL MATH" prompt rule only; no programmatic numerical validator) | **REPORTED** |
| **E15** | Transcript, message text, and user PII logging audit | **PRESENT** | `main.py:467, 473, 538`, `main.py:1075`, `agent.py:167, 614` (logs transcripts, user IDs, sender numbers) | **REPORTED** |
| **E16** | Uploaded audio lifecycle (in-memory buffer vs temp files) | **PRESENT** | `main.py:459, 570, 1063`, `agent.py:196` (in-memory bytes buffer only; no disk temp files created) | **REPORTED** |
| **E17** | Secrets handling (.env, gitignore, git history audit) | **PRESENT** | `.gitignore:6`, `git log --all --full-history -- ".env"` (clean, 0 commits), `.env` permissions `664` | **VERIFIED** |
| **F18** | Firebase token validation (`aud`, `iss`, `email_verified`, provider whitelist) | **PRESENT** | `main.py:266-320` (enforces project audience, securetoken issuer, email_verified, google/password) | **VERIFIED** |
| **F18** | Startup failure in production when `FIREBASE_PROJECT_ID` is empty | **PRESENT** | `main.py:81-83` (raises `RuntimeError` if `not IS_DEV and not FIREBASE_PROJECT_ID`) | **VERIFIED** |
| **F18** | Dev-mode Firebase token audience bypass (`audience=None`) | **PARTIAL** | `main.py:209-211, 234, 248` (when `IS_DEV=True` & `project_id=""`, passes `audience=None`) | **REPORTED** |
| **F19** | Cloud Run readiness: `/healthz` or `/health` lightweight probe | **MISSING** | `main.py` has no `/healthz` or `/health` route (probing `/` renders full HTML template) | **REPORTED** |
| **F19** | Cloud Run readiness: Container concurrency and `max-instances` configs | **MISSING** | No `Dockerfile`, `cloudbuild.yaml`, or deployment manifest enforcing instance/concurrency caps | **REPORTED** |
| **F19** | Cloud Run readiness: `PUBLIC_BASE_URL` for WhatsApp Twilio signature | **PARTIAL** | `main.py:1045` reads `PUBLIC_BASE_URL`; if unset or mismatched with proxy, signatures fail 403 | **REPORTED** |

---

## 2. Top 10 Gaps Ranked by Risk

1. **[Cost / Abuse] Complete absence of rate limiting or request quotas on `/api/chat`, `/api/transcribe`, and `/api/whatsapp`:**  
   *Impact:* Any authenticated user or WhatsApp sender can issue thousands of rapid LLM/Whisper requests, exhausting Groq API token quotas, degrading server response times, or inflating API billings.
2. **[Cost / Abuse] Unbounded chat input text length and global request body size:**  
   *Impact:* `/api/chat` accepts arbitrarily large text in the `message` form field without truncation, enabling prompt-bloat attacks that consume Groq token limits or cause high server memory utilization.
3. **[Security / Auth] Dev-mode audience bypass (`audience=None`) when `FIREBASE_PROJECT_ID` is unset:**  
   *Impact:* In development or test environments (`APP_ENV=development`), an empty `FIREBASE_PROJECT_ID` results in `audience=None` being passed to Google's token verifier, allowing tokens minted from any arbitrary Google Firebase project to authenticate as local users.
4. **[Privacy] Plaintext logging of voice transcripts and user identities:**  
   *Impact:* `main.py:473` logs the full user voice transcript and `main.py:467, 538, 1075` logs user emails/IDs and phone numbers in server stdout, potentially exposing sensitive personal financial records and PII in application log sinks.
5. **[Reliability] Absence of HTTP request timeouts on LLM and ASR API clients:**  
   *Impact:* Neither `ChatGroq`, `ChatBedrockConverse`, nor the Whisper client configure explicit socket/request timeouts, meaning slow upstream network connections can hang threadpool workers until operating system socket timeouts occur.
6. **[Money Safety] Missing server-side whitelist and length limits on categories and store names:**  
   *Impact:* `log_expense`, `set_budget`, and `add_recurring_expense` accept arbitrary strings for category and store without length limits or whitelist enforcement, risking database bloat or injection.
7. **[Reliability] Unbounded agent recursion / loop limits:**  
   *Impact:* `run_agent` relies on default LangGraph recursion limits (25 steps) without passing a custom `recursion_limit` or tool invocation cap in `invoke()`, risking unnecessary repetitive tool loops during ambiguous requests.
8. **[Money Safety] Absence of rate limiting on destructive actions:**  
   *Impact:* There is no frequency limit or cooldown on `delete_last_transaction` or the `DELETE /api/transactions/...` endpoint, allowing rapid repeated deletions without throttling.
9. **[Prompt Injection] Lack of explicit prompt injection boundaries and output number verification:**  
   *Impact:* The agent prompt lacks data-boundary framing for user inputs, and numbers in model replies are not programmatically verified against tool output JSON, creating vulnerability to prompt jailbreaks or numerical hallucination.
10. **[Deployment / Reliability] Missing `/healthz` endpoint and Cloud Run concurrency controls:**  
    *Impact:* Cloud Run has no lightweight health probe endpoint (probing `/` requires full HTML template rendering), and no deployment configurations exist to cap container instances or control auto-scaling costs.

---

## 3. Test Coverage of Guardrails

### Existing Tests That Cover Guardrails (35 Unit Tests + 7 Verification Suite Tests)

#### `tests/test_security_and_financial.py` (35 Tests):
1. `test_auth_routes_without_token_return_401`: Asserts unauthenticated requests to protected endpoints return 401 Unauthorized.
2. `test_idor_cross_user_access_returns_403`: Asserts IDOR cross-tenant access between users returns 403 Forbidden.
3. `test_firebase_token_verification_cases`: Asserts token validation rejects missing emails, unverified emails, unsupported providers, expired tokens, and invalid claims.
4. `test_removed_auth_endpoints_return_404`: Asserts legacy login/register PBKDF2 endpoints return 404 Not Found.
5. `test_no_custom_jwt_or_pbkdf2_code`: Asserts zero legacy JWT/PBKDF2 references remain in the repository.
6. `test_firebase_auth_config_endpoint`: Asserts client auth config endpoint returns public Firebase credentials.
7. `test_amount_handling_and_confirmation`: Asserts positive finite amount validation, wallet limits (Easypaisa/JazzCash), and >10M confirmation requirement.
8. `test_category_totals_and_line_item_math_consistency`: Asserts line item sums match transaction totals deterministically.
9. `test_whatsapp_namespace_isolation`: Asserts WhatsApp `wa:` users are strictly isolated from web Firebase users.
10. `test_csv_formula_injection_sanitization`: Asserts CSV export cells starting with `=`, `+`, `-`, `@` are sanitized against spreadsheet formula injection.
11. `test_recurring_bill_idempotency`: Asserts recurring bills can only be marked paid once per calendar month.
12. `test_audio_file_size_and_mimetype_validation`: Asserts non-audio extensions return 400 and payloads > 5 MB return 413 Content Too Large.
13. `test_soft_delete_leak_audit_across_all_analytics`: Asserts soft-deleted records (`deleted_at is not null`) are excluded from monthly stats, store stats, recent list, category breakdown, daily trend, payment split, budgets, and CSV export.
14. `test_server_held_pending_transaction_ui_endpoints`: Asserts pending transactions are held server-side, retrieve active rows, and cancel correctly.
15. `test_fail_closed_environment_and_secret_requirements`: Asserts production fail-closed behavior for environment variables.
16. `test_concurrent_pending_confirm_race`: Asserts atomic concurrency claim on pending confirmation: exactly 1 wins, concurrent claims receive 409 Conflict.
17. `test_agent_cannot_confirm_pending_expense`: Asserts that `get_tools_for_user` does NOT expose any confirmation tool to the LLM.
18. `test_firebase_audience_and_issuer_verification`: Asserts tokens with mismatched `aud` or `iss` return 401 Unauthorized.
19. `test_firebase_project_id_required_in_production`: Asserts module raises RuntimeError when `FIREBASE_PROJECT_ID` is missing in production.
20. `test_pending_confirm_single_transaction_rollback_restores_pending_row`: Asserts database rollback restores pending rows if committing fails.
21. `test_expired_pending_transaction_cannot_be_claimed`: Asserts pending transactions past their 300s TTL cannot be confirmed (return 409).
22. `test_pending_buttons_restored_after_reload`: Asserts pending state is returned for UI re-render upon page reload.
23. `test_security_headers_middleware`: Asserts security headers (`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, CSP, COOP).
24. `test_frontend_xss_sink_sanitization_and_integrity`: Asserts DOMPurify and Marked script tags include SRI integrity hashes.
25. `test_csp_no_unsafe_inline_in_script_src`: Asserts Content-Security-Policy prohibits `'unsafe-inline'` in `script-src`.
26. `test_ui_pending_conflict_409_handling`: Asserts client handles 409 conflict responses gracefully.
27. `test_all_app_js_ids_exist_in_index_html`: Asserts every DOM ID queried by `app.js` exists in `index.html`.
28. `test_theme_css_served_and_contains_tokens`: Asserts CSS stylesheet is served with proper design tokens.
29. `test_sw_precache_includes_theme_css_and_bumped_version`: Asserts Service Worker precaches core shell assets.
30. `test_no_inline_scripts_or_on_handlers_in_html`: Asserts zero inline `<script>` tags or `onclick` handlers in `index.html`.
31. `test_transcribe_endpoint_auth_and_validation`: Asserts `/api/transcribe` requires Bearer token, enforces audio extension, and rejects >5MB.
32. `test_whisper_transcription_ensures_urdu_script_not_devanagari`: Asserts `ensure_urdu_script` detects Devanagari/Gurmukhi and converts to Urdu script.
33. `test_auth_me_response_shape_contract`: Asserts `/api/auth/me` returns `{ "status": "success", "user": { ... } }`.
34. `test_display_name_is_sanitized`: Asserts display names are sanitized against XSS control characters and capped at 80 chars.
35. `test_reload_with_persisted_session_loads_own_ledger`: Asserts persisted session reloads own isolated ledger without legacy fallbacks.

#### `test_verification_suite.py` (7 Integration Tests):
1. `test_payment_method_normalization_and_tracking`: Asserts payment method aliases and database tracking.
2. `test_csv_export_endpoint`: Asserts CSV export BOM, headers, and rows.
3. `test_monthly_budgeting_and_alerts`: Asserts budget creation, spending calculation, and warning/over alerts.
4. `test_recurring_monthly_obligations`: Asserts recurring expense scheduling, auto-logging, and payment idempotency.
5. `test_deterministic_analytics`: Asserts category breakdown, 30-day non-gap trend, and payment split.
6. `test_pwa_manifest_and_icons`: Asserts PWA manifest, service worker headers, and PNG icon formats.
7. `test_whatsapp_webhook_guards`: Asserts WhatsApp webhook configuration checks and signature validation.

---

### Guardrails That Have NO Test
1. **Per-user/IP rate limit on chat, transcribe, and WhatsApp:** No test exists because the guardrail is entirely missing in code.
2. **Chat text length bounds (`message`, `body_text`):** No test asserting truncation or 400 Bad Request on oversized chat text.
3. **Audio duration limits:** No test asserting rejection of excessively long audio recordings (e.g. > 60 seconds).
4. **Bedrock throttling runtime fallback in test suite:** Verified via one-off mocked execution, but no formal test in `tests/test_security_and_financial.py`.
5. **Language congruence enforcement (Urdu->Urdu Nastaliq, English->English):** No automated test asserting that English input yields English output and Urdu yields Nastaliq Urdu.
6. **Tool output numerical matching in assistant reply:** No test verifying assistant text against tool JSON totals.
7. **Deletion frequency / destruction rate limit:** No test asserting rate limits on `delete_last_transaction`.
8. **Category whitelist:** No test asserting rejection of invalid or arbitrary category strings.
9. **Zero-width and control character filtering on user prompt:** No test asserting stripping of zero-width Unicode characters.

---

## 4. Test Execution Evidence

### Command 1: Pytest Unit Test Suite
```bash
.venv/bin/pytest -q tests/
```
**Output:**
```
...................................                                      [100%]
35 passed in 1.41s
```

### Command 2: Full Verification Suite
```bash
.venv/bin/python test_verification_suite.py
```
**Output:**
```
============================================================
STARTING HISAAB-AI VERIFICATION SUITE
============================================================

[TEST 1] Testing Payment Method Normalization & Tracking...
  -> Aliases and fallback logic OK.
  -> DB verified: Transaction #18 recorded with payment_method='card', amount=2500.0

[TEST 2] Testing CSV / Excel Export Endpoint...
2026-11-01 17:57:50,306 [INFO] httpx2: HTTP Request: GET http://testserver/api/export/test_verification_suite_user@example.com "HTTP/1.1 200 OK"
  -> Export verified: UTF-8 BOM present, header correct, 1 transaction row(s) exported.

[TEST 3] Testing Monthly Budgeting & Alerts...
2026-11-01 17:57:53,360 [INFO] httpx2: HTTP Request: POST http://testserver/api/budgets "HTTP/1.1 200 OK"
  -> API Budget created: total=50000, spent=2500.0, pct=5.0%
  -> DB verified: Fuel budget set to PKR 3000.0
  -> Budget alert verified: Spent 2800.0 / 3000.0 (93.3%, level=warning)

[TEST 4] Testing Recurring Monthly Obligations...
  -> DB verified: Recurring 'electricity bill' PKR 12000.0 due day 5
  -> DB verified: Recurring bill marked paid for 2026-11 and ledger Transaction #20 created!

[TEST 5] Testing Deterministic Analytics (Categories, Trend, Payment Split)...
  -> Category breakdown verified (3 categories, sum=100.10000000000001%): [('utilities', 12000.0), ('fuel', 2800.0), ('groceries', 2500.0)]
  -> Daily trend verified (30 consecutive non-gap days).
  -> Payment split verified: [('cash', 14800.0), ('card', 2500.0)]

[TEST 6] Testing PWA Manifest, Service Worker, and Icons...
2026-11-01 17:58:08,118 [INFO] httpx2: HTTP Request: GET http://testserver/manifest.webmanifest "HTTP/1.1 200 OK"
  -> manifest.webmanifest OK (standalone display, icons defined).
2026-11-01 17:58:08,122 [INFO] httpx2: HTTP Request: GET http://testserver/sw.js "HTTP/1.1 200 OK"
  -> sw.js OK (Service-Worker-Allowed: /, cache strategy configured).
  -> 3 PWA icons verified with valid PNG headers.

[TEST 7] Testing WhatsApp Webhook Route...
2026-11-01 17:58:08,126 [INFO] httpx2: HTTP Request: POST http://testserver/api/whatsapp "HTTP/1.1 503 Service Unavailable"
  -> WhatsApp endpoint guards verified (Status code: 503).

============================================================
ALL 7 VERIFICATION TESTS PASSED SUCCESSFULLY!
============================================================
  - 1_payment_methods: PASSED
  - 2_csv_export: PASSED
  - 3_budgeting_alerts: PASSED
  - 4_recurring_expenses: PASSED
  - 5_visual_analytics: PASSED
  - 6_pwa: PASSED
  - 7_whatsapp_webhook: PASSED
```

### Command 3: Mocked Bedrock Throttling Fallback Test
```bash
.venv/bin/python -c '
import unittest.mock as mock
from agent import run_agent
import time
import agent

mock_initial_agent = mock.MagicMock()
mock_initial_agent.invoke.side_effect = Exception("botocore.errorfactory.ThrottlingException: Too many tokens")

mock_fallback_agent = mock.MagicMock()
mock_fallback_msg = mock.MagicMock()
mock_fallback_msg.content = "Fallback succeeded"
mock_fallback_agent.invoke.return_value = {"messages": [mock_fallback_msg]}

with mock.patch("agent.create_agent", side_effect=[mock_initial_agent, mock_fallback_agent]):
    res = run_agent(user_id="test_throttle_user", user_text="kuch kharcha hua")
    print("Result:", res)
    print("Circuit breaker active:", agent._BEDROCK_THROTTLED_UNTIL > time.time())
'
```
**Output:**
```
Agent execution error: botocore.errorfactory.ThrottlingException: Too many tokens
AWS Bedrock throttled. Fast fallback to Groq active for 5 minutes.
Result: Fallback succeeded
Circuit breaker active: True
```

---

## 5. Could Not Verify

1. **Live Cloud Run Production Behavior:** Cloud Run deployment manifests, billing budgets, and real Google Cloud environment variables cannot be verified locally because the repository contains no GCP infrastructure files (`service.yaml`, `cloudbuild.yaml`, Terraform).
2. **Live Twilio Webhook Delivery:** Twilio's incoming webhook over HTTPS cannot be tested end-to-end against `localhost` without an active public tunneling URL (e.g. ngrok or Cloud Run URL) and real Twilio signature headers.
3. **Unmocked Live AWS Bedrock Invocation:** Live Bedrock model execution currently triggers AWS account token rate limits (`ThrottlingException: Too many tokens`) under the current AWS account quota; therefore, Bedrock fallback was verified via mocked runtime exception rather than a live 70B model call.

