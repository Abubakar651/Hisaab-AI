# Comprehensive Implementation Plan: UI Language Toggle, Voice STT Language Handling, and Dynamic Auto-Growing Composer

> **Document Type:** Implementation Architecture & Phased Execution Plan  
> **Target File:** `PLAN_UI_LANG_COMPOSER.md` (Repository Root)  
> **Mode:** PLAN ONLY (No code changes committed or applied during this planning phase)  
> **Claim Labels:** Every claim in this document is labeled as **`[VERIFIED]`** (command/measurement executed, output quoted), **`[REPORTED]`** (read directly from repository code), or **`[UNVERIFIED]`**.

---

## Executive Summary & Baseline State

### 1. Verification Baseline
* **Working Directory:** Clean on `main` branch.  
  `[VERIFIED]` Output of `git status --short`:  
  ```
  (clean - 0 modified files)
  ```
* **Git Branches:**  
  `[VERIFIED]` Output of `git branch -a`:
  ```
    fix/auth-polish
    fix/chat-layout
  * main
    remotes/origin/fix/auth-polish
    remotes/origin/fix/chat-layout
    remotes/origin/main
  ```
  Branch `fix/stt-language` is **NOT MERGED** and does not exist locally or on remote `origin` `[VERIFIED]`.
* **Pytest Test Suite:**  
  `[VERIFIED]` Ran `.venv/bin/pytest -v`: **40 passed in 29.56s**.
* **Verification Suite:**  
  `[VERIFIED]` Ran `.venv/bin/python test_verification_suite.py`: **All 7 verification tests passed successfully (7/7)**.
* **Storage Audit:**  
  `[VERIFIED]` Grep for `localStorage` across `tests/`: 0 test assertions mention or forbid `localStorage`.  
  `[REPORTED]` `SECURITY_AUDIT.md:14`: *"Client-side storage of authentication tokens in localStorage has been eliminated in favor of memory-held Firebase credentials (`currentUser.getIdToken()`)."*  
  *Strategy:* Use a single, strictly isolated, non-sensitive key `hisaab_ui_lang` (`'ur'` | `'en'`) wrapped in `try { ... } catch (e) { ... }`. Zero auth tokens, user IDs, emails, or transaction records will ever be placed in storage.

---

## A. Detailed Findings & Current Code Paths

### 1. Current Header Code Paths
* `templates/index.html:237-292` `[REPORTED]`:
  * `<header class="app-header">` contains three flex sections:
    1. `.app-logo` (lines 239-244): Wallet icon + "HisaabAI" brand link.
    2. `#desktop-view-switcher.view-switcher-bar` (lines 247-260): Desktop pill buttons for Chat, Split, and Ledger (hidden below 980px viewport).
    3. `.app-header-right` (lines 263-291): Contains `#account-btn` (avatar circle, user display name, chevron icon) and `#account-dropdown` (Export CSV, Switch Account, Sign Out).
* `static/app.js:232-236` `[REPORTED]`: View switcher event listeners (`btnViewChat`, `btnViewSplit`, `btnViewLedger`).
* `static/theme.css:76-89` `[REPORTED]`:
  ```css
  /* Header retains clean Figtree English font without language change */
  .app-header .app-logo-text,
  .app-header .view-pill-btn,
  .app-header .user-pill-name,
  .app-header .dropdown-item-btn {
    font-family: var(--ui) !important;
  }
  ```
  *Current Limitation:* Header styling forces `font-family: var(--ui) !important;` on all header items, preventing Nastaliq typography when Urdu UI is active.

### 2. Current Composer Code Paths
* `templates/index.html:356-373` `[REPORTED]`:
  * `.chat-composer-box` contains `<form id="chat-form" class="composer-input-row">`.
  * `#message-input` (line 359): `<textarea id="message-input" rows="1" placeholder="اردو، پنجابی یا انگریزی میں بولیں یا لکھیں..." class="composer-input font-ur"></textarea>`.
  * `#mic-btn` (line 362): Circle microphone button with Lucide `mic` icon.
  * `#send-btn` (line 366): Circle send button with Lucide `send` icon.
* `static/app.js:124-137` `[REPORTED]`:
  ```javascript
  elements.messageInput.addEventListener('input', function () {
    this.style.height = 'auto';
    this.style.height = Math.min(this.scrollHeight, 140) + 'px';
    syncInputScriptDirection();
  });
  ```
  *Current Limitation:* Hardcoded `Math.min(this.scrollHeight, 140)` without animated transition, without collapse-on-empty behavior, and without viewport-relative cap (`min(40dvh, 240px)`).
* `static/app.js:518-535` `[REPORTED]`:
  ```javascript
  if (data.transcript && data.transcript.trim()) {
    elements.messageInput.value = data.transcript.trim();
    elements.messageInput.style.height = 'auto';
    elements.messageInput.style.height = Math.min(elements.messageInput.scrollHeight, 140) + 'px';
    syncInputScriptDirection();
    state.pendingVoiceNote = true;
    showToast("🎤 آواز چیٹ باکس میں تحریر ہو گئی! جائزہ لیں اور Send دبائیں۔", "success");
  }
  ...
  finally {
    elements.messageInput.disabled = false;
    elements.messageInput.placeholder = origPlaceholder;
    elements.messageInput.focus();
  }
  ```
  `[VERIFIED]` Current behavior on STT receipt: It places the transcript into `messageInput`, sets height, focuses the textarea, and does **NOT** auto-send. This conforms to user requirements and must be preserved.
* `static/theme.css:678-705` `[REPORTED]`:
  * `.composer-input` has `min-height: 46px; max-height: 120px; resize: none;`.

### 3. Current Voice STT Code Paths
* `main.py:554-604` `[REPORTED]`:
  * Endpoint `POST /api/transcribe` validates MIME/extension (`VALID_AUDIO_EXTENSIONS`), enforces `MAX_AUDIO_SIZE = 5 * 1024 * 1024` (5 MB), and dispatches to `agent.transcribe_audio`.
* `agent.py:175-208` `[REPORTED]`:
  * `transcribe_audio` uses `groq.audio.transcriptions.create` with `model="whisper-large-v3"` and `temperature=0.0`.
  * `[REPORTED]` Prompt contains instructional sentences:  
    `"ہمیشہ اردو رسم الخط (Nastaliq script) میں لکھیں، ہندی (Devanagari) یا گرمکھی (Gurmukhi) میں ہرگز نہیں لکھنا۔ "`  
    `"Voice note about daily expenses in Pakistan. Always transcribe Urdu and Punjabi in Urdu script, never in Devanagari or Gurmukhi."`  
    *Problem:* Whisper's official documentation notes that instruction sentences in the `prompt` field often degrade ASR accuracy or cause the model to hallucinate the instructions. The prompt should only contain vocabulary context (store names, local food items, currency units).
  * `[REPORTED]` `response_format` is default (`json`), which does not return the `language` detection metadata.
  * `[REPORTED]` If spoken Urdu/Punjabi is transcribed into Latin transliteration or Roman Urdu, Whisper currently has no retry with `language="ur"`.

### 4. Font, CSP, and Storage Facts
* **Font Configuration (`static/theme.css:6-48`) `[REPORTED]`:**
  * Self-hosted fallback font: `/static/fonts/jameel-noori-nastaleeq4.woff2` (served locally).
  * CDN font: `https://cdn.jsdelivr.net/npm/jameel-noori@1.1.2/jameel-noori.min.css`.
  * Google Fonts: Figtree (UI), Fraunces (numbers/currency), Noto Nastaliq Urdu (fallback).
  * Variables:
    * `--ui`: `"Figtree", system-ui, -apple-system, sans-serif`
    * `--num`: `"Fraunces", Georgia, serif`
    * `--ur`: `"Jameel Noori Nastaleeq", "Noori Nastaleeq", "JameelNooriNastaliq", "Noto Nastaliq Urdu", serif`
* **Content Security Policy (`main.py:129-136`) `[REPORTED]`:**
  ```python
  "default-src 'self'; "
  "script-src 'self' https://apis.google.com https://cdn.tailwindcss.com https://unpkg.com https://cdn.jsdelivr.net; "
  "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
  "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net https://unpkg.com data:; "
  "connect-src 'self' https://identitytoolkit.googleapis.com https://securetoken.googleapis.com https://www.googleapis.com https://unpkg.com https://cdn.jsdelivr.net; "
  ```
  `[VERIFIED]` Strict constraints: No inline scripts (`<script>` without `src=`), no inline event handlers (`on*`), and no new external domains. Any new script must be local (`/static/i18n.js`).
* **Service Worker Precaching (`static/sw.js:6-14`) `[REPORTED]`:**
  ```javascript
  const CACHE = 'hisaabai-shell-v11';
  const SHELL = [
    '/',
    '/manifest.webmanifest',
    '/static/theme.css',
    '/static/app.js',
    '/static/auth.js',
    '/static/icons/icon-192.png',
    '/static/icons/icon-512.png',
    '/static/icons/apple-touch-icon.png'
  ];
  ```
  `[VERIFIED]` Adding `/static/i18n.js` requires adding it to `SHELL` and bumping `CACHE` to `hisaabai-shell-v12`.

---

## B. String Inventory & i18n Architecture

### 1. Exact String Counts by File
* **`templates/index.html` `[VERIFIED]`: 84 user-visible static text nodes and placeholders.**
  * Modal: Mic Permission Guide (12 strings)
  * Modal: Account Auth & Email Verification (22 strings)
  * Header & Navigation: 9 strings
  * Chat Pane: 15 strings (header, welcome bubble, chips, recording banner, composer tip, input placeholder)
  * Ledger Pane: 26 strings (hero card, budget section, category analytics, trend chart, store breakdown, payment split, recurring bills, transactions list)
* **`static/app.js` `[VERIFIED]`: 62 user-visible dynamic strings.**
  * Toast notifications: 26 strings
  * Empty state descriptions: 7 strings
  * Action button labels & placeholders: 11 strings
  * Pending large-expense confirmation banner: 8 strings
  * Bill status pills (`Paid`, `Unpaid`, `Pending`, `Mark as Paid`): 4 strings
  * Budget warning alerts: 6 strings
* **`static/auth.js` `[VERIFIED]`: 24 user-visible strings.**
  * Firebase error code mappings: 11 strings
  * Status messages, button labels, and validation toasts: 13 strings
* **Server-Generated Strings (Out of Scope for Backend Modification) `[REPORTED]`:**
  * LLM Chat Replies: Generated dynamically based on user input language (product rule: AI replies follow prompt language).
  * API Error Details (`HTTPException.detail`): Kept in English for backend logging integrity; mapped client-side where relevant.

### 2. Proposed Client-Side i18n Design (`static/i18n.js`)
* **Format:** Zero-dependency, lightweight (~9 KB) static script loaded before `auth.js` and `app.js`.
* **State Management:**
  * Initial language resolution:
    1. Read `localStorage.getItem('hisaab_ui_lang')` inside `try ... catch`.
    2. If valid (`'ur'` or `'en'`), use it.
    3. Otherwise, default strictly to `'ur'`.
  * Persist language: `localStorage.setItem('hisaab_ui_lang', lang)` inside `try ... catch`.
* **DOM Translation Engine:**
  * Elements with `data-i18n="key"`: Updates `textContent` or `innerHTML` (sanitized).
  * Elements with `data-i18n-placeholder="key"`: Updates `placeholder`.
  * Elements with `data-i18n-title="key"`: Updates `title` attribute.
  * Updates `<html lang="..." dir="...">` dynamically (`lang="ur"` / `dir="rtl"` vs `lang="en"` / `dir="ltr"`).
* **JavaScript Translation Function (`t(key, params)`):**
  * Exported globally as `window.t(key, params)` and `window.HisaabI18n`.
  * Supports variable interpolation: `t('toast.tx_deleted', { id: 45 })` $\rightarrow$ `"اندراج #45 مٹا دیا گیا"` / `"Transaction #45 deleted"`.
* **Scope Boundary (What is deliberately NOT translated client-side in this round):**
  * Dynamic LLM assistant responses (they follow the conversation language).
  * User-entered expense titles and custom budget names.
  * Backend raw exception traces in 500 errors.

---

## C. RTL & Layout Risk Matrix

| Component | Selector / Location | RTL Risk | Mitigation & Verification Plan |
|---|---|---|---|
| **HTML Root** | `<html lang dir>` | Flash of LTR layout on load | Hardcode `lang="ur" dir="rtl"` in `templates/index.html` by default. Script immediately reads language before first render. |
| **Header Layout** | `.app-header` | Logo and account button flipping sides | Use CSS flexbox with `justify-content: space-between`. Use `margin-inline-start/end` instead of `margin-left/right`. Verify on 360px & 390px viewports. |
| **Language Toggle** | `#lang-toggle-btn` | Bad contrast or cramped touch target | Design as a compact pill (`padding: 4px 10px; font-size: 13pt; min-height: 38px`) placed immediately adjacent to the account pill. Keyboard accessible (`role="button"`, `aria-label`, `tabindex="0"`). |
| **Chat Bubbles** | `.bubble-user`, `.bubble-assistant` | Mixed Urdu/English prompts misaligning | Keep `dir="auto"` on message content wrappers so Urdu text renders RTL and English text renders LTR regardless of UI language. |
| **Composer** | `#message-input` | Cursor jumps or reversed typing | Retain `syncInputScriptDirection()` which applies `dir="rtl"` for Urdu/Arabic characters and `dir="ltr"` for Latin script. |
| **Send / Mic Buttons** | `.composer-btn-circle` | Send arrow icon points backwards in RTL | The Lucide `send` paper airplane icon is conventionally angled. In pure RTL it can point inward; test visually. If awkward, preserve natural orientation or use CSS `transform: scaleX(-1)` only for directional icons. |
| **Ledger Hero Card** | `.ledger-hero-card` | Currency symbol (`Rs`) and digits inverted | Wrap numeric amounts in `<bdi>` or apply `dir="ltr"` and `unicode-bidi: isolate; font-variant-numeric: tabular-nums`. |
| **Budget Track Bars** | `.budget-fill-bar` | Progress bar filling from wrong side | In RTL, filling from right to left is standard for Arabic/Urdu interfaces. CSS `left: 0` vs `right: 0` will be converted to logical `inset-inline-start: 0`. |
| **Chart.js Analytics** | `#category-chart`, `#trend-chart` | Axis labels flipped or overlapping | Chart.js 4 supports `options.plugins.tooltip.rtl = isRtl` and `options.scales.x.reverse = isRtl`. Verify trend chart timeline flows logically. |
| **Modals** | `#auth-modal`, `#mic-permission-modal` | Close button ('X') trapped or misplaced | Use `inset-inline-end: 16px` for `.modal-close-btn` so it appears on the top-left in RTL and top-right in LTR. |
| **Mobile Tab Bar** | `.mobile-tab-bar` | Tab order reversed | Tabs ordered logically: Chat on inline-start, Ledger on inline-end. |

---

## D. Voice STT Language Handling Plan (Whisper & Groq)

### 1. Architectural Goal
* **English Speech** $\rightarrow$ English Latin text.
* **Urdu / Punjabi Speech** $\rightarrow$ Authentic Pakistani Urdu Perso-Arabic Nastaliq script only.
* **Never** Devanagari (Hindi), Gurmukhi (Indian Punjabi), or Latin transliteration for spoken Urdu/Punjabi.

### 2. Proposed Changes in `agent.py`
1. **Clean Domain Prompt:**
   Remove meta-instructions from Whisper prompt. Keep domain vocabulary only:
   ```python
   DOMAIN_VOCAB_PROMPT = (
       "امتیاز، الفتح، جلال سنز، کیارفور، میٹرو، چیس اپ، شاہین کیمسٹ، پٹرول، ڈیزل، "
       "دودھ، دہی، انڈے، ڈبل روٹی، چینی، چاول، دال، آٹا، سبزی، گوشت، کرایہ، بل، فیس، "
       "PKR, Rs, groceries, fuel, milk, eggs, bread, dining, utilities"
   )
   ```
2. **Use `response_format="verbose_json"`:**
   Read `transcription.language` from Whisper's response.
3. **Smart Retry Gate (At Most One Retry):**
   * If detected language is in `{"ur", "pa", "hi", "sd", "ps", "fa", "ar"}`:
     * Check if the returned transcript contains Arabic script (`is_arabic_script(text)`).
     * If NOT Arabic script (e.g., returned Devanagari, Gurmukhi, or Latin transliteration), make **ONE** retry call with explicit `language="ur"`.
   * On HTTP 429 (Rate Limit), catch exception and immediately return the first transcript without failing.
   * Retain `ensure_urdu_script` as the final safety net for any residual Devanagari/Gurmukhi characters.
   * Ensure no private transcript text is logged in application logs.

---

## E. Dynamic Auto-Growing Composer Plan

### 1. Behavior Specification
* **Empty & Unfocused:** Collapsed to single compact row (`min-height: 48px`).
* **On Focus or When Typing:** Grows smoothly with content.
* **Height Cap:** Maximum height set to `min(40dvh, 240px)`.
* **Beyond Cap:** Stops growing, pins to 240px, and enables smooth vertical scrolling (`overflow-y: auto`).
* **After Send or Clear:** Resets smoothly back to `48px`.
* **On STT Transcript Insertion:** Populates text, triggers `autosize(el)`, focuses textarea, and does **NOT** auto-send.
* **Keystroke Handling:** Retains existing `Enter` to submit, `Shift+Enter` for newline.

### 2. Smooth Height Resize Algorithm Evaluation
Evaluating the proposed reflow technique:
```javascript
function autosizeComposer(el) {
  if (!el) return;
  const maxHeight = Math.min(window.innerHeight * 0.4, 240);
  const minHeight = 48;
  el.style.transition = 'none';
  el.style.height = 'auto';
  const targetHeight = Math.max(minHeight, Math.min(el.scrollHeight, maxHeight));
  const prevHeight = el.offsetHeight;
  el.style.height = prevHeight + 'px';
  el.offsetHeight; // Force synchronous reflow
  el.style.transition = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    ? 'none'
    : 'height 0.18s ease';
  el.style.height = targetHeight + 'px';
  el.style.overflowY = el.scrollHeight > maxHeight ? 'auto' : 'hidden';
}
```
*Evaluation:* **FITS PERFECTLY.**  
Direct CSS height animations fail when transitioning to `height: auto`. Forcing reflow between explicit previous pixel height and target pixel height guarantees a smooth transition while preventing layout jumps.

---

## F. Phased Implementation Plan & Verification Gates

### Phase 1: i18n Skeleton, Header Toggle & Default Urdu
* **Tasks:**
  * Create `/static/i18n.js` with complete Urdu and English dictionaries for common UI strings.
  * Add `#lang-toggle-btn` to `.app-header-right` in `templates/index.html`.
  * Set `<html lang="ur" dir="rtl">` as default in `templates/index.html`.
  * Add language persistence in `localStorage` under key `hisaab_ui_lang` wrapped in `try/catch`.
* **Gate & Tests:**
  * Unit test: `test_default_html_lang_and_dir_is_urdu_rtl`.
  * Unit test: `test_i18n_dictionary_key_parity`.
  * Unit test: `test_lang_toggle_element_exists_in_html`.

### Phase 2: RTL & CSS Logical Properties
* **Tasks:**
  * Refactor `static/theme.css` to use logical properties (`margin-inline`, `padding-inline`, `inset-inline-start/end`, `text-align: start/end`).
  * Ensure `.modal-close-btn`, dropdowns, and button icons flip cleanly in RTL/LTR.
  * Configure Chart.js options (`options.plugins.tooltip.rtl`).
* **Gate & Tests:**
  * Automated headless Chrome CDP visual measurement on 1280px and 390px viewports in both RTL and LTR.

### Phase 3: Comprehensive String Migration
* **Tasks:**
  * Tag all remaining static strings in `templates/index.html` with `data-i18n`, `data-i18n-placeholder`, `data-i18n-title`.
  * Migrate `static/app.js` and `static/auth.js` toasts, modals, and dynamic labels to use `window.t(key)`.
* **Gate & Tests:**
  * Unit test: `test_no_missing_i18n_keys_in_templates`.
  * Pytest 40/40 passed.

### Phase 4: Voice STT Language Handling & Retry Gate
* **Tasks:**
  * Update `agent.transcribe_audio` with clean vocabulary-only prompt, `response_format="verbose_json"`, and conditional retry.
* **Gate & Tests:**
  * Unit test with mock Groq responses: `test_transcribe_audio_retries_urdu_when_non_arabic_script_detected`.
  * Unit test: `test_transcribe_audio_handles_rate_limit_gracefully`.

### Phase 5: Composer Auto-Grow & Height Transition
* **Tasks:**
  * Implement `autosizeComposer` in `static/app.js`.
  * Update `static/theme.css` with `transition: height 0.18s ease; resize: none;`.
  * Connect STT transcript insertion to expand composer smoothly without auto-sending.
* **Gate & Tests:**
  * Headless Chrome CDP test: verify textarea collapses to 48px when empty and caps at 240px with 10 lines of text.

### Phase 6: Service Worker Precache & Cache Bump
* **Tasks:**
  * Add `/static/i18n.js` to `SHELL` array in `static/sw.js`.
  * Bump cache version to `hisaabai-shell-v12`.
* **Gate & Tests:**
  * Unit test: `test_sw_precache_includes_i18n_and_bumped_version`.
  * Verification suite `test_verification_suite.py` passes 7/7.

---

## G. Manual Test Checklist

1. **First-Time Visit:** Clear browser cookies/storage; open `http://localhost:8000`. Verify page loads in **Urdu (RTL)** with no layout flash.
2. **Language Toggle:** Click "اردو | EN". Verify static text (header, tabs, buttons, placeholders, ledger titles) instantly switches to English (LTR).
3. **Persistence:** Refresh the browser; verify English choice persists across reload.
4. **Auth Modal:** Open "Sign In" modal in both Urdu and English. Verify form labels, placeholder text, and error toasts render in the chosen language.
5. **Mobile Viewport (390 × 844):** Verify mobile tab bar, ledger hero card, budget bars, and composer on mobile screens in both RTL and LTR.
6. **Chart.js Direction:** Verify category doughnut legend and daily trend bar chart render without label overlap in both RTL and LTR.
7. **Voice Recording (Urdu):** Speak Urdu into mic. Verify transcript appears in Perso-Arabic Urdu script in the composer, box expands, and does **not** auto-send.
8. **Voice Recording (English):** Speak English into mic. Verify transcript appears in English Latin characters in the composer.
9. **Composer Expansion:** Paste a 300-word paragraph into composer. Verify it expands up to 240px and shows scrollbar. Press Send or clear text; verify box shrinks back to 48px.
10. **PWA Standalone Mode:** Verify offline precaching and service worker installation in application tab.

---

## H. Unverified Items & Questions for the User

### Could Not Verify
* `[UNVERIFIED]` Real microphone behavior in headless Linux container (hardware mic unavailable; verified via mock audio payloads).
* `[UNVERIFIED]` AWS App Runner subscription activation status (pending account identity/billing verification by user).

### Questions for User Alignment
1. **Digit Formatting in Urdu UI:**
   * Option A (Recommended): Use Western Arabic numerals (`1, 2, 3`) with PKR formatting (standard in Pakistani banking and digital wallets like Easypaisa/Nayapay).
   * Option B: Use Eastern Arabic numerals (`۱, ۲, ۳`) in Urdu mode.
2. **Server-Generated Validation Messages:**
   * Shall server error messages (e.g. from backend 400 Bad Request) be displayed as returned, or mapped to standardized client-side localized strings?
3. **Terminology Preference:**
   * Preferred translation for "Live Ledger":
     * Option A: `لائیو کھاتہ` (Traditional Pakistani ledger term)
     * Option B: `حساب کتاب کا ڈیش بورڈ` (Modern dashboard term)

