tailwind.config = {
  darkMode: 'class',
  theme: {
    extend: {
      fontFamily: {
        nastaleeq: ["'Jameel Noori Nastaleeq'", "'Noori Nastaleeq'", "'Noto Nastaliq Urdu'", "'Urdu Typesetting'", "serif"],
        ui: ["var(--ui)"],
        num: ["var(--num)"],
        ur: ["var(--ur)"],
      },
      colors: {
        ink: 'var(--ink)',
        s1: 'var(--s1)',
        s2: 'var(--s2)',
        rule: 'var(--rule)',
        tx: 'var(--tx)',
        mut: 'var(--mut)',
        brass: 'var(--brass)',
        ok: 'var(--ok)',
        warn: 'var(--warn)',
        bad: 'var(--bad)',
        brand: {
          50: '#ecfdf5',
          100: '#d1fae5',
          500: '#10b981',
          600: '#059669',
          700: '#047857',
          800: '#065f46',
          900: '#064e3b',
        },
        surface: {
          800: '#1e293b',
          850: '#172033',
          900: '#0f172a',
          950: '#090d16',
        }
      }
    }
  }
}

// Automatically redirect 0.0.0.0 to localhost because Chrome disables microphone on 0.0.0.0
if (window.location.hostname === '0.0.0.0') {
  window.location.hostname = 'localhost';
}

function cssVar(name, fallback = '') {
  const val = getComputedStyle(document.documentElement).getPropertyValue(name);
  return val ? val.trim() : fallback;
}

// State management
const state = {
  userId: '',
  token: '',
  books: [],
  activeBookId: null,
  activeBookType: 'business',
  udhaarFilter: 'all',
  partyFilter: 'all',
  partySearch: '',
  partyLedgerFilter: 'all',
  activePartyId: null,
  activePartyDetail: null,
  isRecording: false,
  mediaRecorder: null,
  audioChunks: [],
  recordingTimerInterval: null,
  recordingSeconds: 0,
  activeTab: 'chat', // 'chat' or 'dashboard' for mobile
  chatHistory: [],   // Multi-turn conversation memory
};

// Authentication & Fetch Interceptor
async function authFetch(url, options = {}) {
  if (window.HisaabAuth && typeof window.HisaabAuth.authFetch === 'function') {
    return window.HisaabAuth.authFetch(url, options);
  }
  return new Response(JSON.stringify({ detail: 'Not authenticated' }), {
    status: 401,
    headers: { 'Content-Type': 'application/json' },
  });
}

function getCurrentLang() {
  if (window.HisaabI18n) {
    if (typeof window.HisaabI18n.getLanguage === 'function') return window.HisaabI18n.getLanguage();
    if (typeof window.HisaabI18n.getLang === 'function') return window.HisaabI18n.getLang();
  }
  return document.documentElement.lang || 'ur';
}

// DOM Elements
const elements = {};

function initElements() {
  elements.activeUserDisplay = document.getElementById('active-user-display');
  elements.chatForm = document.getElementById('chat-form');
  elements.messageInput = document.getElementById('message-input');
  elements.sendBtn = document.getElementById('send-btn');
  elements.micBtn = document.getElementById('mic-btn');
  elements.micIcon = document.getElementById('mic-icon');
  elements.recordingBanner = document.getElementById('recording-banner');
  elements.recordingTimer = document.getElementById('recording-timer');
  elements.cancelRecordingBtn = document.getElementById('cancel-recording-btn');
  elements.messagesContainer = document.getElementById('messages-container');
  elements.clearChatBtn = document.getElementById('clear-chat-btn');
  elements.refreshDashboardBtn = document.getElementById('refresh-dashboard-btn');
  elements.kpiMonthlyTotal = document.getElementById('kpi-monthly-total');
  elements.kpiVisitCount = document.getElementById('kpi-visit-count');
  elements.kpiTopStore = document.getElementById('kpi-top-store');
  elements.storeBreakdownContainer = document.getElementById('store-breakdown-container');
  elements.transactionsList = document.getElementById('transactions-list');
  elements.txCountBadge = document.getElementById('tx-count-badge');
  elements.tabChat = document.getElementById('tab-chat');
  elements.tabDashboard = document.getElementById('tab-dashboard');
  elements.chatPane = document.getElementById('chat-pane');
  elements.dashboardPane = document.getElementById('dashboard-pane');
  elements.toastContainer = document.getElementById('toast-container');
  elements.voiceWaveCanvas = document.getElementById('voice-wave-canvas');
  elements.desktopViewSwitcher = document.getElementById('desktop-view-switcher');
  elements.btnViewChat = document.getElementById('btn-view-chat');
  elements.btnViewSplit = document.getElementById('btn-view-split');
  elements.btnViewLedger = document.getElementById('btn-view-ledger');
  elements.micModal = document.getElementById('mic-permission-modal');
  elements.closeMicModalBtn = document.getElementById('close-mic-modal-btn');
  elements.dismissMicModalBtn = document.getElementById('dismiss-mic-modal-btn');
  elements.retryMicBtn = document.getElementById('retry-mic-btn');
}

// Helper to detect Arabic / Urdu / Shahmukhi script
function isUrduScript(str) {
  return /[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]/.test(str || '');
}

function syncInputScriptDirection() {
  if (!elements.messageInput) return;
  if (isUrduScript(elements.messageInput.value)) {
    elements.messageInput.classList.add('font-ur', 'font-nastaleeq');
    elements.messageInput.setAttribute('dir', 'rtl');
  } else {
    elements.messageInput.classList.remove('font-ur', 'font-nastaleeq');
    elements.messageInput.setAttribute('dir', 'ltr');
  }
}

function autosizeComposer(el) {
  if (!el) return;
  const max = Math.min(window.innerHeight * 0.4, 240);
  const prev = el.offsetHeight || 48;
  el.style.transition = 'none';
  el.style.height = 'auto';
  const scrollH = el.scrollHeight;
  const target = Math.max(48, Math.min(scrollH, max));
  el.style.height = prev + 'px';
  el.offsetHeight; // force reflow
  el.style.transition = '';
  el.style.height = target + 'px';
  el.style.overflowY = scrollH > max ? 'auto' : 'hidden';
}

// Initialize UI
function initApp() {
  initElements();
  if (elements.activeUserDisplay) {
    elements.activeUserDisplay.textContent = state.userId || 'Guest';
  }

  // Auto-resize textarea and auto-detect Nastaleeq script
  elements.messageInput.addEventListener('input', function () {
    autosizeComposer(this);
    syncInputScriptDirection();
  });

  elements.messageInput.addEventListener('focus', function () {
    autosizeComposer(this);
  });

  elements.messageInput.addEventListener('blur', function () {
    if (!this.value.trim()) {
      this.style.height = '48px';
    }
  });

  // Enter key sends message (Shift+Enter for new line)
  elements.messageInput.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      elements.chatForm.dispatchEvent(new Event('submit'));
    }
  });

  bindEvents();
  bindBookEvents();
  bindAnalyticsForms();
  bindPartyEvents();
  bindAuthEvents();
  initTheme();
  initViewportResizing();
  setMobileTab('chat');
  setDesktopView('chat');
  try {
    const params = new URLSearchParams(window.location.search);
    if (params.get('tab') === 'ledger') {
      setMobileTab('dashboard');
      setDesktopView('ledger');
    }
    if (params.get('view') === 'split') {
      setDesktopView('split');
    } else if (params.get('view') === 'ledger') {
      setDesktopView('ledger');
    } else if (params.get('view') === 'chat') {
      setDesktopView('chat');
    }
    if (params.get('modal') === 'auth' || params.get('modal') === 'signup') {
      const authModal = document.getElementById('auth-modal');
      if (authModal) authModal.classList.remove('hidden');
      if (params.get('modal') === 'signup') {
        const signupTab = document.getElementById('auth-tab-signup');
        if (signupTab) setTimeout(() => signupTab.click(), 50);
      }
    }
  } catch (_) { }
  if (elements.messagesContainer) {
    elements.messagesContainer.scrollTop = 0;
  }
  renderCategoryChart([]);
  if (state.userId) {
    fetchBooks().then(() => fetchDashboard());
  }

  if (window.HisaabI18n) {
    HisaabI18n.onLanguageChange(() => {
      renderBookSwitcher();
      if (state.lastDashboardData) {
        renderDashboard(state.lastDashboardData);
      } else {
        renderGalla({});
        renderUdhaar({ items: [], total_receivable_pkr: 0, total_payable_pkr: 0 });
      }
      if (Array.isArray(state.parties)) {
        renderPartiesList(state.parties);
      }
      const detView = document.getElementById('party-detail-view');
      if (state.activePartyDetail && detView && !detView.classList.contains('hidden')) {
        applyPartyDetailLocale(state.activePartyDetail);
      }
      if (window.Chart) {
        Chart.defaults.plugins.tooltip.rtl = (HisaabI18n.getLanguage() === 'ur');
      }
    });
  }

  lucide.createIcons();
}

// Bind Event Listeners
function bindEvents() {
  // Prompt Suggestion Chips
  document.querySelectorAll('.prompt-chip').forEach(chip => {
    chip.addEventListener('click', () => {
      if (state.isSending) return;
      const prompt = chip.getAttribute('data-prompt');
      if (!prompt) return;
      state.nextIntent = chip.getAttribute('data-intent-id') || null;
      state.awaitingBudget = false;
      elements.messageInput.value = prompt;
      elements.chatForm.dispatchEvent(new Event('submit'));
    });
  });

  // Chat Submit
  elements.chatForm.addEventListener('submit', handleTextMessageSubmit);

  // Microphone Recording Toggle
  elements.micBtn.addEventListener('click', toggleRecording);
  elements.cancelRecordingBtn.addEventListener('click', cancelRecording);

  // Clear Chat (preserve welcome card so chat feed is never a blank void)
  elements.clearChatBtn.addEventListener('click', async () => {
    clearChatMessagesPreservingWelcome();
    state.chatHistory = [];
    state.nextIntent = null;
    state.awaitingBudget = false;
    const fd = new FormData();
    fd.append('user_id', state.userId);
    authFetch('/api/chat/clear', { method: 'POST', body: fd }).catch(() => { });
    showToast(t('toast.chat_cleared'), 'info');
  });

  // Mic Modal buttons
  if (elements.closeMicModalBtn) {
    elements.closeMicModalBtn.addEventListener('click', () => {
      elements.micModal.classList.add('hidden');
    });
  }
  if (elements.dismissMicModalBtn) {
    elements.dismissMicModalBtn.addEventListener('click', () => {
      elements.micModal.classList.add('hidden');
    });
  }
  if (elements.retryMicBtn) {
    elements.retryMicBtn.addEventListener('click', () => {
      elements.micModal.classList.add('hidden');
      startRecording();
    });
  }

  // Refresh Dashboard
  elements.refreshDashboardBtn.addEventListener('click', () => {
    fetchDashboard();
    showToast(t('toast.ledger_refreshed'), 'info');
  });

  // Export CSV with Authorization
  const exportBtn = document.getElementById('export-csv-btn');
  if (exportBtn) {
    exportBtn.addEventListener('click', async (e) => {
      e.preventDefault();
      try {
        const res = await authFetch(withBookQuery(`/api/export/${encodeURIComponent(state.userId)}`));
        if (!res.ok) throw new Error('Export failed');
        const blob = await res.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.style.display = 'none';
        a.href = url;
        a.download = `hisaabai_${state.userId}_${new Date().toISOString().slice(0, 10)}.csv`;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        showToast(t('toast.csv_downloaded'), 'success');
      } catch (err) {
        showToast(err.message, 'error');
      }
    });
  }

  // Mobile Tabs
  elements.tabChat.addEventListener('click', () => setMobileTab('chat'));
  elements.tabDashboard.addEventListener('click', () => setMobileTab('dashboard'));

  // Desktop View Switcher (Chat / Split / Ledger)
  if (elements.btnViewChat) elements.btnViewChat.addEventListener('click', () => setDesktopView('chat'));
  if (elements.btnViewSplit) elements.btnViewSplit.addEventListener('click', () => setDesktopView('split'));
  if (elements.btnViewLedger) elements.btnViewLedger.addEventListener('click', () => setDesktopView('ledger'));
}

// Update Account UI in Header
function updateAccountUI(account) {
  if (!account) return;
  state.account = account;
  state.userId = account.user_id;

  const nameEl = document.getElementById('user-display-name');
  const badgeEl = document.getElementById('user-provider-badge');
  const dropNameEl = document.getElementById('dropdown-name');
  const dropSubEl = document.getElementById('dropdown-sub');
  const avatarBox = document.getElementById('user-avatar');

  const isGoogle = account.auth_provider === 'google' || account.auth_provider === 'google.com';
  const isPassword = account.auth_provider === 'password';
  const label = isGoogle ? 'Google Account' : (isPassword ? 'Email / Password' : 'User Account');

  if (nameEl) nameEl.textContent = account.display_name || account.user_id;
  if (badgeEl) badgeEl.textContent = label;
  if (dropNameEl) dropNameEl.textContent = account.display_name || account.user_id;
  if (dropSubEl) dropSubEl.textContent = `${label} • ${account.email || account.user_id}`;

  if (avatarBox) {
    avatarBox.textContent = '';
    if (account.photo_url && /^https:\/\//i.test(account.photo_url)) {
      const img = document.createElement('img');
      img.src = account.photo_url;
      img.alt = 'avatar';
      img.referrerPolicy = 'no-referrer';
      img.className = 'w-full h-full object-cover rounded-full';
      avatarBox.appendChild(img);
    } else {
      const letter = (account.display_name || account.user_id || 'U').charAt(0).toUpperCase();
      const span = document.createElement('span');
      span.id = 'avatar-letter';
      span.textContent = letter;
      avatarBox.appendChild(span);
    }
  }

  if (elements.activeUserDisplay) elements.activeUserDisplay.textContent = account.user_id;
}

function clearChatMessagesPreservingWelcome() {
  if (!elements.messagesContainer) return;
  Array.from(elements.messagesContainer.children).forEach((child) => {
    if (child.id !== 'welcome-card') {
      child.remove();
    }
  });
}

// Expose app interface for auth state updates
window.HisaabApp = {
  onUserChanged: (newUserId) => {
    if (!newUserId) return;
    state.userId = String(newUserId).trim().toLowerCase();
    state.books = [];
    state.activeBookId = null;
    state.activeBookType = 'business';
    state.chatHistory = [];
    clearChatMessagesPreservingWelcome();
    fetchBooks().then(() => fetchDashboard());
  }
};

// Bind Auth Dropdown Events
function bindAuthEvents() {
  const accountBtn = document.getElementById("account-btn");
  const accountDropdown = document.getElementById("account-dropdown");
  const signinHeaderBtn = document.getElementById("signin-header-btn");

  if (signinHeaderBtn) {
    signinHeaderBtn.addEventListener("click", () => {
      const authModal = document.getElementById("auth-modal");
      if (authModal) authModal.classList.remove("hidden");
    });
  }

  if (accountBtn && accountDropdown) {
    accountBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      const authUser = window.HisaabAuth && typeof window.HisaabAuth.getUser === "function" ? window.HisaabAuth.getUser() : null;
      if (!authUser) {
        const authModal = document.getElementById("auth-modal");
        if (authModal) authModal.classList.remove("hidden");
        accountDropdown.classList.add("hidden");
        return;
      }
      accountDropdown.classList.toggle("hidden");
    });
    document.addEventListener("click", (e) => {
      if (!accountDropdown.contains(e.target) && !accountBtn.contains(e.target)) {
        accountDropdown.classList.add("hidden");
      }
    });
  }
}

// Color Theme (Light / Dark mode toggle)
function initTheme() {
  const themeBtn = document.getElementById('theme-toggle-btn');
  let savedTheme = 'dark';
  try {
    const urlTheme = new URLSearchParams(window.location.search).get('theme');
    if (urlTheme === 'light' || urlTheme === 'dark') {
      savedTheme = urlTheme;
    } else {
      savedTheme = localStorage.getItem('hisaab_theme') || 'dark';
    }
  } catch (_) { }
  applyTheme(savedTheme);

  if (themeBtn) {
    themeBtn.addEventListener('click', () => {
      const isDark = document.documentElement.classList.contains('dark');
      const newTheme = isDark ? 'light' : 'dark';
      applyTheme(newTheme);
      try {
        localStorage.setItem('hisaab_theme', newTheme);
      } catch (_) { }
    });
  }

  function applyTheme(theme) {
    const sunIcon = document.querySelector('.theme-icon-sun');
    const moonIcon = document.querySelector('.theme-icon-moon');
    const metaTheme = document.querySelector('meta[name="theme-color"]');
    if (theme === 'light') {
      document.documentElement.classList.remove('dark');
      document.documentElement.classList.add('light');
      if (sunIcon) sunIcon.classList.add('hidden');
      if (moonIcon) moonIcon.classList.remove('hidden');
      if (themeBtn) themeBtn.setAttribute('title', 'Switch to Dark Mode');
      if (metaTheme) metaTheme.setAttribute('content', '#edf4f3');
    } else {
      document.documentElement.classList.remove('light');
      document.documentElement.classList.add('dark');
      if (sunIcon) sunIcon.classList.remove('hidden');
      if (moonIcon) moonIcon.classList.add('hidden');
      if (themeBtn) themeBtn.setAttribute('title', 'Switch to Light Mode');
      if (metaTheme) metaTheme.setAttribute('content', '#0b1316');
    }
    if (state.lastDashboardData) {
      renderCategoryChart(state.lastDashboardData.category_breakdown || []);
      renderTrendChart(state.lastDashboardData.daily_trend || []);
    }
    if (window.lucide && typeof window.lucide.createIcons === 'function') {
      window.lucide.createIcons();
    }
  }
}

// Dynamic Viewport Height & Keyboard Resizing (iOS visualViewport support)
function initViewportResizing() {
  if (window.visualViewport) {
    const handleViewportChange = () => {
      const vh = window.visualViewport.height;
      document.documentElement.style.setProperty('--app-viewport-height', `${vh}px`);
    };
    window.visualViewport.addEventListener('resize', handleViewportChange);
    window.visualViewport.addEventListener('scroll', handleViewportChange);
    handleViewportChange();
  }
}

// Desktop View Switcher (Chat / Split / Ledger)
function setDesktopView(mode) {
  state.desktopView = mode;
  const workspace = document.querySelector('.app-workspace');
  if (!workspace) return;
  workspace.classList.remove('view-chat-only', 'view-ledger-only');
  if (elements.btnViewChat) elements.btnViewChat.classList.remove('active');
  if (elements.btnViewSplit) elements.btnViewSplit.classList.remove('active');
  if (elements.btnViewLedger) elements.btnViewLedger.classList.remove('active');

  if (mode === 'chat') {
    workspace.classList.add('view-chat-only');
    if (elements.btnViewChat) elements.btnViewChat.classList.add('active');
  } else if (mode === 'ledger') {
    workspace.classList.add('view-ledger-only');
    if (elements.btnViewLedger) elements.btnViewLedger.classList.add('active');
  } else {
    if (elements.btnViewSplit) elements.btnViewSplit.classList.add('active');
  }
  if (charts.trend) charts.trend.resize();
  if (charts.category) charts.category.resize();
}

// Mobile Tab Switching
function setMobileTab(tab) {
  state.activeTab = tab;
  if (tab === 'chat') {
    if (elements.tabChat) elements.tabChat.classList.add('active');
    if (elements.tabDashboard) elements.tabDashboard.classList.remove('active');
    elements.chatPane.classList.remove('hidden');
    elements.dashboardPane.classList.add('hidden');
  } else {
    if (elements.tabDashboard) elements.tabDashboard.classList.add('active');
    if (elements.tabChat) elements.tabChat.classList.remove('active');
    elements.chatPane.classList.add('hidden');
    elements.dashboardPane.classList.remove('hidden');
  }
}

// =========================================================================
// Chat Message Flow
// =========================================================================
async function handleTextMessageSubmit(e) {
  e.preventDefault();
  const message = elements.messageInput.value.trim();
  if (!message) return;

  const wasVoice = !!state.pendingVoiceNote;
  state.pendingVoiceNote = false;

  // Add user message to UI (tag with 'Voice note, transcribed' if originated from mic)
  appendUserMessage(message, wasVoice ? 'Voice note, transcribed' : null);
  elements.messageInput.value = '';
  autosizeComposer(elements.messageInput);
  syncInputScriptDirection();

  // Default-prompt chips (and the budget amount follow-up) get fixed-schema JSON replies
  let intentId = state.nextIntent || null;
  let awaitingAmount = false;
  state.nextIntent = null;
  if (!intentId && state.awaitingBudget) {
    intentId = 'budget_set';
    awaitingAmount = true;
  }

  // Send to API
  await sendChatRequest({ message, intentId, awaitingAmount });
}

// Renders a structured chip response ({intent,status,data}) into localized markdown via i18n templates.
function renderStructuredReply(env) {
  const tt = (k, p) => (typeof window.t === 'function' ? window.t(k, p) : k);
  const m = formatMoney;
  const d = (env && env.data) || {};
  const list = (titleKey, rows) => rows && rows.length
    ? `\n\n${tt(titleKey)}\n` + rows.map(r => `- ${r[0]}: PKR ${m(r[1])}`).join('\n')
    : '';
  switch (env && env.intent) {
    case 'spend_summary': {
      const k = d.period === 'last_3_days' ? '3d' : 'month';
      if (env.status === 'empty') return tt(`tpl.spend.${k}.empty`);
      let out = tt(`tpl.spend.${k}.ok`, { total: m(d.total), count: d.txn_count });
      out += list('tpl.spend.top', (d.top_categories || []).map(c => [formatCategoryName(c.name), c.amount]));
      if (d.budget) out += `\n\n${tt('tpl.spend.budget', { limit: m(d.budget.limit), remaining: m(d.budget.remaining) })}`;
      return out;
    }
    case 'galla_summary':
      if (env.status === 'empty') return tt('tpl.galla.empty');
      return tt('tpl.galla.ok', { date: d.date, sales: m(d.sales_total), expenses: m(d.expenses_total), cash: m(d.cash_in_drawer) });
    case 'udhaar_summary':
      if (env.status === 'empty') return tt('tpl.udhaar.empty');
      return tt('tpl.udhaar.ok', { receivable: m(d.receivable_total), payable: m(d.payable_total) })
        + list('tpl.udhaar.receivables', (d.receivables || []).map(r => [r.party, r.amount]))
        + list('tpl.udhaar.payables', (d.payables || []).map(r => [r.party, r.amount]));
    case 'budget_set':
      if (env.status === 'need_input') return tt('tpl.budget.need_input');
      return d.previous_amount != null
        ? tt('tpl.budget.ok_prev', { month: d.month, previous: m(d.previous_amount), amount: m(d.amount) })
        : tt('tpl.budget.ok', { month: d.month, amount: m(d.amount) });
    case 'undo_last':
      if (env.status === 'empty') return tt('tpl.undo.empty');
      return tt('tpl.undo.ok', { description: d.deleted.description || d.deleted.type, amount: m(d.deleted.amount), date: d.deleted.date });
    default:
      return tt('tpl.error');
  }
}

async function sendChatRequest({ message = null, audioBlob = null, intentId = null, awaitingAmount = false }) {
  state.isSending = true;
  const loadingBubble = appendLoadingBubble();
  elements.sendBtn.disabled = true;
  document.querySelectorAll('.prompt-chip').forEach(c => {
    c.disabled = true;
    c.classList.add('opacity-50', 'pointer-events-none');
  });

  const formData = new FormData();
  formData.append('user_id', state.userId);
  if (state.activeBookId) formData.append('book_id', String(state.activeBookId));
  if (message) formData.append('message', message);
  if (intentId && !audioBlob) {
    formData.append('intent_id', intentId);
    if (awaitingAmount) formData.append('awaiting_amount', 'true');
  }
  if (audioBlob) formData.append('audio', audioBlob, 'voice_recording.webm');

  const currentLang = getCurrentLang();
  formData.append('lang', currentLang);

  // Pass recent conversation history for multi-turn context memory (last 4 messages to optimize token bandwidth)
  if (state.chatHistory && state.chatHistory.length > 0) {
    formData.append('history', JSON.stringify(state.chatHistory.slice(-4)));
  }

  try {
    const response = await authFetch('/api/chat', {
      method: 'POST',
      body: formData,
    });

    if (!response.ok) {
      const errData = await response.json().catch(() => ({ detail: 'Server error' }));
      throw new Error(errData.detail || 'Failed to communicate with HisaabAI');
    }

    const data = await response.json();
    removeLoadingBubble(loadingBubble);

    // Record into client conversation memory
    const userContent = message || (data.transcript ? `[Voice Note]: ${data.transcript}` : 'Voice note recorded');
    state.chatHistory.push({ role: 'user', content: userContent });
    // Structured chip replies are rendered client-side from localized templates
    let replyText = data.reply;
    if (data.structured) {
      replyText = renderStructuredReply(data.structured);
      state.awaitingBudget = data.structured.intent === 'budget_set' && data.structured.status === 'need_input';
    } else {
      state.awaitingBudget = false;
    }
    state.chatHistory.push({ role: 'assistant', content: replyText });
    if (state.chatHistory.length > 20) {
      state.chatHistory = state.chatHistory.slice(-20);
    }

    // Render AI Reply
    appendAssistantMessage(replyText);

    if (data.cross_book_suggestion && data.cross_book_suggestion.transaction_id && data.cross_book_suggestion.target_book_id) {
      appendCrossBookSuggestion(data.cross_book_suggestion);
    }

    // Auto-refresh ledger dashboard after any action
    fetchDashboard();
    await checkAndRenderPendingConfirmation();
  } catch (err) {
    removeLoadingBubble(loadingBubble);
    const isUrdu = (getCurrentLang() === 'ur');
    const defaultNetErr = isUrdu
      ? (typeof window.t === 'function' ? window.t('chat.error_network') : 'سرور سے رابطہ نہیں ہو سکا۔ انٹرنیٹ کنکشن چیک کریں اور دوبارہ کوشش کریں۔')
      : (typeof window.t === 'function' ? window.t('chat.error_network') : 'Unable to connect to server. Please check your internet connection and try again.');
    const rawMsg = err.message || '';
    const userMsg = (rawMsg && rawMsg !== 'Failed to fetch' && !rawMsg.includes('Failed to communicate'))
      ? rawMsg
      : defaultNetErr;
    appendErrorMessage(userMsg);
    showToast(userMsg, 'error');
  } finally {
    state.isSending = false;
    elements.sendBtn.disabled = false;
    document.querySelectorAll('.prompt-chip').forEach(c => {
      c.disabled = false;
      c.classList.remove('opacity-50', 'pointer-events-none');
    });
  }
}

// =========================================================================
// Web Audio MediaRecorder API (Voice Notes)
// =========================================================================
async function toggleRecording() {
  if (state.isRecording) {
    stopRecording(true); // Stop and submit
  } else {
    startRecording();
  }
}

async function startRecording() {
  // Check if browser context is insecure (e.g. 0.0.0.0 or non-localhost HTTP)
  if (window.isSecureContext === false || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    if (window.location.hostname !== 'localhost' && window.location.hostname !== '127.0.0.1') {
      showToast('Chrome blocks microphone on ' + window.location.hostname + '. Switching to localhost...', 'error');
      window.location.hostname = 'localhost';
      return;
    }
    if (elements.micModal) {
      elements.micModal.classList.remove('hidden');
      lucide.createIcons();
    }
    showToast('Microphone access blocked or not supported on this origin.', 'error');
    return;
  }

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });

    let mimeType = 'audio/webm;codecs=opus';
    if (!MediaRecorder.isTypeSupported(mimeType)) {
      mimeType = MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : '';
    }

    state.mediaRecorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
    state.audioChunks = [];

    state.mediaRecorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) {
        state.audioChunks.push(event.data);
      }
    };

    state.mediaRecorder.onstop = async () => {
      stream.getTracks().forEach(track => track.stop());

      // Clean up Web Audio Visualizer
      if (state.animFrameId) {
        cancelAnimationFrame(state.animFrameId);
        state.animFrameId = null;
      }
      if (state.audioCtx && state.audioCtx.state !== 'closed') {
        try { state.audioCtx.close(); } catch (e) { }
        state.audioCtx = null;
      }
      if (elements.voiceWaveCanvas) {
        const ctx = elements.voiceWaveCanvas.getContext('2d');
        if (ctx) ctx.clearRect(0, 0, elements.voiceWaveCanvas.width, elements.voiceWaveCanvas.height);
      }

      if (state.audioChunks.length > 0 && state.shouldSendAudio) {
        const audioBlob = new Blob(state.audioChunks, { type: state.mediaRecorder.mimeType || 'audio/webm' });

        // Speech-to-Text Paste-to-Composer Review Flow
        const origPlaceholder = (typeof t === 'function' ? t('chat.placeholder') : null) || "اردو یا انگریزی میں بولیں یا لکھیں...";
        elements.messageInput.placeholder = (typeof t === 'function' ? t('chat.transcribing_placeholder') : null) || "آواز تحریر میں تبدیل ہو رہی ہے...";
        elements.messageInput.disabled = true;
        showToast(t('toast.mic_transcribing'), "info");

        try {
          const formData = new FormData();
          formData.append('audio', audioBlob, 'voice_recording.webm');
          const res = await authFetch('/api/transcribe', {
            method: 'POST',
            body: formData,
          });

          if (!res.ok) {
            const errData = await res.json().catch(() => ({ detail: 'Transcription failed' }));
            throw new Error(errData.detail || 'Failed to transcribe audio');
          }

          const data = await res.json();
          if (data.transcript && data.transcript.trim()) {
            elements.messageInput.value = data.transcript.trim();
            autosizeComposer(elements.messageInput);
            syncInputScriptDirection();
            state.pendingVoiceNote = true;
            showToast(t("toast.speech_transcribed"), "success");
          } else {
            showToast(t("toast.no_speech"), "warning");
          }
        } catch (err) {
          console.error("Transcription error:", err);
          showToast(err.message || t("toast.stt_failed"), "error");
        } finally {
          elements.messageInput.disabled = false;
          elements.messageInput.placeholder = t("chat.placeholder");
          elements.messageInput.focus();
        }
      }
    };

    // Live Audio Waveform & Equalizer Visualizer via Web Audio API
    try {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (AudioContextClass) {
        const audioCtx = new AudioContextClass();
        const source = audioCtx.createMediaStreamSource(stream);
        const analyser = audioCtx.createAnalyser();
        analyser.fftSize = 256;
        source.connect(analyser);

        state.audioCtx = audioCtx;
        state.analyser = analyser;

        const canvas = elements.voiceWaveCanvas;
        if (canvas) {
          const ctx = canvas.getContext('2d');
          const bufferLength = analyser.frequencyBinCount;
          const dataArray = new Uint8Array(bufferLength);

          function renderWave() {
            if (!state.isRecording) return;
            state.animFrameId = requestAnimationFrame(renderWave);
            analyser.getByteTimeDomainData(dataArray);

            ctx.clearRect(0, 0, canvas.width, canvas.height);

            // 1. Draw smooth oscillating sound curve
            ctx.lineWidth = 2.2;
            ctx.strokeStyle = '#d4a95a'; // Brass gold curve
            ctx.beginPath();

            const sliceWidth = canvas.width / bufferLength;
            let x = 0;
            let maxDeviation = 0;
            for (let i = 0; i < bufferLength; i++) {
              const v = dataArray[i] / 128.0;
              const dev = Math.abs(dataArray[i] - 128);
              if (dev > maxDeviation) maxDeviation = dev;
              const y = (v * canvas.height) / 2;
              if (i === 0) {
                ctx.moveTo(x, y);
              } else {
                ctx.lineTo(x, y);
              }
              x += sliceWidth;
            }
            ctx.lineTo(canvas.width, canvas.height / 2);
            ctx.stroke();

            // 2. Draw 7 dancing frequency bars in center reacting live to voice input
            const isSpeaking = maxDeviation > 3;
            const barCount = 7;
            const barWidth = 3;
            const barGap = 4;
            const startX = (canvas.width / 2) - ((barCount * (barWidth + barGap)) / 2);
            const centerY = canvas.height / 2;

            for (let b = 0; b < barCount; b++) {
              const sampleIdx = Math.floor((b / barCount) * bufferLength);
              const amp = isSpeaking ? (Math.abs(dataArray[sampleIdx] - 128) / 128.0) : 0.08;
              const barH = Math.max(3, amp * (canvas.height - 6));
              ctx.fillStyle = b % 2 === 0 ? '#d4a95a' : '#6fbf94';
              ctx.fillRect(startX + b * (barWidth + barGap), centerY - (barH / 2), barWidth, barH);
            }
          }
          renderWave();
        }
      }
    } catch (webaudioErr) {
      console.warn('Web Audio visualizer could not be initialized:', webaudioErr);
    }

    state.shouldSendAudio = true;
    state.mediaRecorder.start(250);
    state.isRecording = true;

    // UI Updates for Recording
    elements.recordingBanner.classList.remove('hidden');
    elements.micBtn.classList.add('rec');
    elements.messageInput.classList.add('recording-active-glow');
    elements.messageInput.placeholder = "🎙️ Listening... Speak your expense in Urdu or English";
    state.recordingSeconds = 0;
    updateRecordingTimer();
    state.recordingTimerInterval = setInterval(updateRecordingTimer, 1000);

  } catch (err) {
    console.error('Microphone access denied:', err);
    if (elements.micModal) {
      elements.micModal.classList.remove('hidden');
      lucide.createIcons();
    }
    showToast('Microphone access blocked. Click modal for help.', 'error');
  }
}

function stopRecording(send = true) {
  if (!state.isRecording) return;
  state.shouldSendAudio = send;
  state.isRecording = false;

  if (state.mediaRecorder && state.mediaRecorder.state !== 'inactive') {
    state.mediaRecorder.stop();
  }

  if (state.animFrameId) {
    cancelAnimationFrame(state.animFrameId);
    state.animFrameId = null;
  }
  if (state.audioCtx && state.audioCtx.state !== 'closed') {
    try { state.audioCtx.close(); } catch (e) { }
    state.audioCtx = null;
  }
  if (elements.voiceWaveCanvas) {
    const ctx = elements.voiceWaveCanvas.getContext('2d');
    if (ctx) ctx.clearRect(0, 0, elements.voiceWaveCanvas.width, elements.voiceWaveCanvas.height);
  }

  clearInterval(state.recordingTimerInterval);
  elements.recordingBanner.classList.add('hidden');
  elements.micBtn.classList.remove('rec');
  elements.messageInput.classList.remove('recording-active-glow');
}

function cancelRecording() {
  stopRecording(false);
  state.audioChunks = [];
  showToast(t('toast.recording_cancelled'), 'info');
}

function updateRecordingTimer() {
  state.recordingSeconds++;
  const mins = String(Math.floor(state.recordingSeconds / 60)).padStart(2, '0');
  const secs = String(state.recordingSeconds % 60).padStart(2, '0');
  elements.recordingTimer.textContent = `${mins}:${secs}`;
}

// =========================================================================
// UI Message Rendering
// =========================================================================
function appendUserMessage(text, voiceSubtext) {
  const wrapper = document.createElement('div');
  wrapper.className = 'bubble-user';

  const isUrdu = isUrduScript(text);
  if (isUrdu) wrapper.classList.add('has-urdu');
  const nastaleeqClass = isUrdu ? 'font-ur font-nastaleeq' : 'text-sm sm:text-base leading-relaxed';
  const dirAttr = isUrdu ? 'dir="rtl" lang="ur"' : 'dir="ltr"';

  let innerContent = `<p class="whitespace-pre-wrap ${nastaleeqClass}" ${dirAttr}>${escapeHtml(text)}</p>`;
  if (voiceSubtext) {
    innerContent = `
          <div class="voice-note-tag">
            <i data-lucide="mic" class="w-3.5 h-3.5"></i> ${escapeHtml(voiceSubtext)}
          </div>
          <p class="whitespace-pre-wrap ${nastaleeqClass}" ${dirAttr}>${escapeHtml(text)}</p>
        `;
  }

  wrapper.innerHTML = innerContent;
  elements.messagesContainer.appendChild(wrapper);
  scrollToBottom();
  lucide.createIcons();
}

function appendAssistantMessage(markdownContent) {
  const wrapper = document.createElement('div');
  wrapper.className = 'bubble-assistant';

  const isUrdu = isUrduScript(markdownContent);
  if (isUrdu) wrapper.classList.add('has-urdu');
  const nastaleeqClass = isUrdu ? 'font-ur font-nastaleeq' : 'text-sm sm:text-base leading-relaxed';
  const dirAttr = isUrdu ? 'dir="rtl" lang="ur"' : 'dir="ltr"';
  const parsedHtml = DOMPurify.sanitize(marked.parse(markdownContent || ''));

  wrapper.innerHTML = `
        <div class="flex items-center gap-1.5 mb-1.5 text-brass">
          <i data-lucide="bot" class="w-3.5 h-3.5"></i>
          <span class="text-xs font-semibold">HisaabAI</span>
        </div>
        <div class="${nastaleeqClass} text-tx prose max-w-none" ${dirAttr}>
          ${parsedHtml}
        </div>
      `;

  elements.messagesContainer.appendChild(wrapper);
  scrollToBottom();
  lucide.createIcons();
}

function appendSystemMessage(markdownContent) {
  const wrapper = document.createElement('div');
  wrapper.className = 'text-center my-2';
  const parsedInline = DOMPurify.sanitize(marked.parseInline(markdownContent || ''));
  wrapper.innerHTML = `
        <span class="inline-block px-3 py-1 rounded-full bg-s2 border border-rule text-[11px] text-mut">
          ${parsedInline}
        </span>
      `;
  elements.messagesContainer.appendChild(wrapper);
  scrollToBottom();
}

function appendLoadingBubble() {
  const wrapper = document.createElement('div');
  wrapper.className = 'bubble-assistant loading-bubble';
  const currentLang = getCurrentLang();
  const isUrdu = currentLang === 'ur';
  const loadingText = (typeof window.t === 'function')
    ? window.t('chat.loading')
    : (isUrdu ? 'حساب کتاب دیکھا جا رہا ہے...' : 'Calculating ledger...');
  const fontClass = isUrdu ? 'font-ur' : '';

  wrapper.innerHTML = `
        <div class="flex items-center gap-2 text-xs text-mut ${fontClass}">
          <i data-lucide="loader-2" class="w-4 h-4 animate-spin text-brass"></i>
          <span>${escapeHtml(loadingText)}</span>
        </div>
      `;
  elements.messagesContainer.appendChild(wrapper);
  scrollToBottom();
  lucide.createIcons();
  return wrapper;
}

function removeLoadingBubble(bubble) {
  if (bubble && bubble.parentNode) {
    bubble.parentNode.removeChild(bubble);
  }
}

function appendErrorMessage(errorText) {
  const wrapper = document.createElement('div');
  wrapper.className = 'bubble-assistant border-bad/40 bg-bad/10';
  const currentLang = getCurrentLang();
  const isUrdu = currentLang === 'ur';
  const titleText = isUrdu ? 'مسئلہ پیش آیا' : 'Processing Issue';
  const fontClass = isUrdu ? 'font-ur' : '';

  wrapper.innerHTML = `
        <div class="flex items-center gap-2 text-xs font-semibold text-bad mb-1 ${fontClass}">
          <i data-lucide="alert-circle" class="w-4 h-4"></i> ${escapeHtml(titleText)}
        </div>
        <p class="text-xs sm:text-sm text-tx ${fontClass}">${escapeHtml(errorText)}</p>
      `;
  elements.messagesContainer.appendChild(wrapper);
  scrollToBottom();
  lucide.createIcons();
}

function scrollToBottom() {
  requestAnimationFrame(() => {
    if (elements.messagesContainer) {
      elements.messagesContainer.scrollTop = elements.messagesContainer.scrollHeight;
    }
  });
}

async function checkAndRenderPendingConfirmation() {
  if (!state.userId) return;
  try {
    const res = await authFetch(withBookQuery('/api/transactions/pending'));
    if (!res.ok) return;
    const data = await res.json();
    const existingBanner = document.getElementById('pending-expense-banner');
    if (existingBanner) existingBanner.remove();

    if (data.pending) {
      const p = data.pending;
      const banner = document.createElement('div');
      banner.id = 'pending-expense-banner';
      banner.className = 'm hold';
      banner.innerHTML = `
            <strong class="font-ur text-base" style="line-height:2.2">PKR ${formatMoney(p.total_amount)} ایک بڑی رقم ہے۔</strong>
            <p class="font-ur text-sm" style="color:var(--mut);margin-top:4px;line-height:2">ابھی تک کچھ محفوظ نہیں ہوا۔ کیا آپ یہ خرچہ درج کرنا چاہتے ہیں؟${p.store_name ? ` (${escapeHtml(cleanDisplayTitle(p.store_name))} · ${escapeHtml(formatPaymentMethodName(p.payment_method))})` : ''}</p>
            <div class="acts mt-2">
              <button id="btn-confirm-pending" class="btn go font-ur">خرچہ درج کریں (Confirm)</button>
              <button id="btn-cancel-pending" class="btn no font-ur">منسوخ کریں (Cancel)</button>
            </div>
          `;
      elements.messagesContainer.appendChild(banner);
      scrollToBottom();
      lucide.createIcons();

      document.getElementById('btn-confirm-pending')?.addEventListener('click', async () => {
        banner.remove();
        appendUserMessage("ہاں، تصدیق کر دیں (Confirm)");
        const resp = await authFetch('/api/transactions/pending/confirm', { method: 'POST' });
        const resData = await resp.json();
        if (resp.ok) {
          appendAssistantMessage(`✅ آپ کا خرچہ درج کر لیا گیا ہے: **PKR ${formatMoney(p.total_amount)}** (${escapeHtml(p.store_name)})۔`);
          showToast(resData.message || t('toast.expense_confirmed'), 'success');
          fetchDashboard();
        } else {
          if (resp.status === 409) {
            appendAssistantMessage(`⚠️ یہ خرچہ پہلے ہی تصدیق، کینسل یا ختم ہو چکا ہے: **PKR ${formatMoney(p.total_amount)}** (${escapeHtml(p.store_name)})۔`);
            showToast(resData.detail || t('toast.conflict_409') || 'Already confirmed, cancelled, or expired.', 'warning');
            fetchDashboard();
          } else {
            showToast(resData.detail || 'Failed to confirm', 'error');
          }
        }
      });

      document.getElementById('btn-cancel-pending')?.addEventListener('click', async () => {
        banner.remove();
        appendUserMessage("نہیں، منسوخ کر دیں (Cancel)");
        const resp = await authFetch('/api/transactions/pending/cancel', { method: 'POST' });
        const resData = await resp.json();
        if (resp.ok) {
          appendAssistantMessage(`❌ خرچہ کینسل کر دیا گیا ہے: **PKR ${formatMoney(p.total_amount)}** (${escapeHtml(p.store_name)})۔`);
          showToast(t('toast.expense_cancelled'), 'info');
        } else {
          if (resp.status === 409) {
            showToast(resData.detail || t('toast.conflict_409') || 'Already confirmed, cancelled, or expired.', 'warning');
            fetchDashboard();
          } else {
            showToast(resData.detail || 'Failed to cancel', 'error');
          }
        }
      });
    }
  } catch (e) {
    console.error('Error checking pending transaction:', e);
  }
}

// =========================================================================
// Dashboard & Live Ledger Flow
// =========================================================================
async function fetchDashboard() {
  if (!state.userId) return;
  try {
    const response = await authFetch(withBookQuery(`/api/transactions/${encodeURIComponent(state.userId)}`));
    if (!response.ok) throw new Error('Failed to load user ledger');

    const data = await response.json();
    renderDashboard(data);
    await checkAndRenderPendingConfirmation();
  } catch (err) {
    console.error('Error fetching dashboard:', err);
  }
}

function renderDashboard(data) {
  state.lastDashboardData = data;
  if (Array.isArray(data.books)) {
    syncActiveBookState(data.books, (data.active_book && data.active_book.id) || state.activeBookId);
  }
  // 1. KPI Cards / Hero
  elements.kpiMonthlyTotal.textContent = `${formatMoney(data.monthly_total || 0)}`;
  elements.kpiVisitCount.textContent = data.monthly_count || 0;
  let formattedTopStore = t('ledger.not_recorded');
  if (data.top_store) {
    const rawTop = String(data.top_store);
    const pkrIdx = rawTop.indexOf(' (PKR ');
    if (pkrIdx > 0) {
      formattedTopStore = cleanDisplayTitle(rawTop.slice(0, pkrIdx)) + rawTop.slice(pkrIdx);
    } else {
      formattedTopStore = cleanDisplayTitle(rawTop);
    }
  }
  elements.kpiTopStore.textContent = formattedTopStore;

  const heroBar = document.querySelector('.ledger-hero-bar');
  if (heroBar) {
    const totalBudget = (data.budgets || []).find(b => b.category === 'total');
    if (totalBudget && totalBudget.monthly_limit > 0) {
      const pct = Math.min(Math.round((data.monthly_total / totalBudget.monthly_limit) * 100), 100);
      heroBar.style.width = `${pct}%`;
      heroBar.style.backgroundColor = pct >= 100 ? 'var(--bad)' : pct >= 80 ? 'var(--warn)' : 'var(--brass)';
    } else {
      heroBar.style.width = '100%';
      heroBar.style.backgroundColor = 'var(--brass)';
    }
  }

  // Analytics sections (galla, udhaar, budgets, charts, payment split, recurring bills)
  renderGalla(data.galla || {});
  renderUdhaar(data.udhaar || {});
  fetchParties();
  renderBudgets(data.budgets || []);
  renderCategoryChart(data.category_breakdown || []);
  renderTrendChart(data.daily_trend || []);
  renderPaymentSplit(data.payment_split || []);
  renderRecurring(data.recurring || []);
  document.getElementById('export-csv-btn').href = withBookQuery(`/api/export/${encodeURIComponent(state.userId)}`);

  // 2. Store Breakdown
  elements.storeBreakdownContainer.innerHTML = '';
  if (data.store_breakdown && data.store_breakdown.length > 0) {
    data.store_breakdown.forEach(item => {
      const storeLabel = cleanDisplayTitle(item.store_name);
      const pill = document.createElement('div');
      pill.className = 'prompt-chip text-xs';
      pill.setAttribute('data-store', storeLabel);
      pill.innerHTML = `
            <span class="font-medium text-tx">${escapeHtml(storeLabel)}</span>
            <span class="text-[11px] text-brass font-bold font-num tabular ml-1.5">PKR ${formatMoney(item.total_pkr)}</span>
            <span class="text-[10px] text-mut ml-1">(${item.visit_count})</span>
          `;
      elements.storeBreakdownContainer.appendChild(pill);
    });
  } else {
    elements.storeBreakdownContainer.innerHTML = `<span class="text-xs text-mut italic font-ur">${t('ledger.stores_empty')}</span>`;
  }

  // 3. Transactions List
  const txs = data.recent_transactions || [];
  const emptyDiv = document.getElementById('tx-empty-divider');
  if (elements.txCountBadge) {
    if (txs.length === 0) {
      elements.txCountBadge.classList.remove('hidden');
      if (emptyDiv) emptyDiv.classList.remove('hidden');
      elements.txCountBadge.textContent = t('ledger.no_tx_this_month');
    } else {
      elements.txCountBadge.classList.add('hidden');
      if (emptyDiv) emptyDiv.classList.add('hidden');
    }
  }
  elements.transactionsList.innerHTML = '';

  if (txs.length === 0) {
    elements.transactionsList.innerHTML = `
          <div class="text-center py-10 text-mut text-xs font-ur">
            <i data-lucide="inbox" class="w-8 h-8 mx-auto mb-2 opacity-50"></i>
            ${t('ledger.recent_tx_empty')}
          </div>
        `;
    lucide.createIcons();
    return;
  }

  txs.forEach(tx => {
    const card = document.createElement('div');
    card.className = 'tx-ledger-row';

    let itemsHtml = '';
    if (tx.items && tx.items.length > 0) {
      const itemsBadges = tx.items.map(it => `
            <span class="inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-s2 border border-rule text-[13px] text-mut font-ur">
              <span class="text-tx font-medium">${escapeHtml(it.item_name)}</span>
              ${it.quantity > 1 ? `<span class="text-mut" dir="ltr">×${it.quantity}</span>` : ''}
              <span class="text-brass font-num tabular font-semibold" dir="ltr">Rs ${formatMoney(it.price)}</span>
            </span>
          `).join('');
      itemsHtml = `<div class="flex flex-wrap gap-1.5 mt-2">${itemsBadges}</div>`;
    }

    const isSale = tx.tx_type === 'sale';
    const amountHtml = isSale
      ? `<span class="text-ok font-bold font-num text-[17px]" dir="ltr">+PKR ${formatMoney(tx.total_amount)}</span>`
      : `<span class="text-brass font-bold font-num text-[17px]" dir="ltr">PKR ${formatMoney(tx.total_amount)}</span>`;
    const typeBadgeHtml = isSale
      ? `<span class="udhaar-pill udhaar-pill-in font-ur">${t('tx.sale_badge')}</span>`
      : '';

    const rawDisplayTitle = tx.display_title || tx.title || tx.store_name || 'Expense';
    const displayTitle = cleanDisplayTitle(rawDisplayTitle);
    const rawStore = cleanDisplayTitle((tx.store_name || '').trim());
    const showVenueSub = rawStore && !isGenericVenueName(rawStore) && rawStore.toLowerCase() !== String(displayTitle).toLowerCase();
    const venueSubHtml = showVenueSub
      ? `<span class="tx-venue-sub font-ur"><i data-lucide="store" class="w-3 h-3"></i>${escapeHtml(rawStore)}</span>`
      : '';

    card.innerHTML = `
          <div class="tx-row-top">
            <div class="tx-store-name capitalize flex items-center gap-2 font-ur font-bold text-[18px]">${escapeHtml(displayTitle)} ${typeBadgeHtml}</div>
            <div class="tx-row-amount flex items-center gap-2">
              ${amountHtml}
              <button 
                class="delete-tx-btn btn-icon-del"
                data-id="${tx.id}"
                data-store="${escapeHtml(displayTitle)}"
                title="Delete Transaction"
              >
                <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>
              </button>
            </div>
          </div>
          <div class="tx-row-sub flex items-center gap-2 mt-1">
            <span class="font-num text-[13.5px] text-mut" dir="ltr">${escapeHtml(tx.transaction_date || '')}</span>
            <span class="badge-pill-method font-ur">${escapeHtml(formatPaymentMethodName(tx.payment_method || 'cash'))}</span>
            ${venueSubHtml}
            <span class="text-mut font-num tabular text-[13px]" dir="ltr">#${tx.id}</span>
          </div>
          ${itemsHtml}
        `;

    elements.transactionsList.appendChild(card);
  });

  // Bind delete buttons
  document.querySelectorAll('.delete-tx-btn').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const txId = btn.getAttribute('data-id');
      const store = btn.getAttribute('data-store');
      if (confirm(`کیا آپ واقعی اس خرچے کو مٹانا چاہتے ہیں؟ (#${txId} - ${store})`)) {
        await deleteTransaction(txId);
      }
    });
  });

  lucide.createIcons();
}

async function deleteTransaction(transactionId) {
  try {
    const response = await authFetch(`/api/transactions/${encodeURIComponent(state.userId)}/${transactionId}`, {
      method: 'DELETE',
    });

    if (!response.ok) {
      const errData = await response.json().catch(() => ({ detail: 'Failed to delete' }));
      throw new Error(errData.detail || 'Delete failed');
    }

    showToast(t('toast.tx_deleted', { id: transactionId }), 'success');
    fetchDashboard();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

// =========================================================================
// Analytics: budgets, charts, payment split, recurring bills
// (all numbers are computed by SQL on the server; the browser only draws them)
// =========================================================================
const charts = { category: null, trend: null };

function renderGalla(galla) {
  const salesEl = document.getElementById('galla-today-sales');
  const expEl = document.getElementById('galla-today-expenses');
  const netEl = document.getElementById('galla-today-net');
  const splitEl = document.getElementById('galla-cash-split');
  if (!salesEl || !expEl || !netEl) return;

  const todaySales = Number(galla.today_sales_pkr || 0);
  const todayExp = Number(galla.today_expenses_pkr || 0);
  const todayNet = Number(galla.today_net_pkr || 0);
  const cashSales = Number(galla.today_cash_sales_pkr || 0);
  const digSales = Number(galla.today_digital_sales_pkr || 0);

  salesEl.textContent = `+Rs ${formatMoney(todaySales)}`;
  expEl.textContent = `-Rs ${formatMoney(todayExp)}`;
  netEl.textContent = `${todayNet >= 0 ? '' : '-'}Rs ${formatMoney(Math.abs(todayNet))}`;
  netEl.style.color = todayNet > 0 ? 'var(--ok)' : todayNet < 0 ? 'var(--bad)' : 'var(--tx)';

  if (splitEl) {
    splitEl.innerHTML = `
          <span class="inline-flex items-center gap-1"><span class="font-ur">${t('galla.cash_short')}:</span> <span class="font-num font-semibold" dir="ltr">Rs ${formatMoney(cashSales)}</span></span>
          <span class="text-mut px-1">•</span>
          <span class="inline-flex items-center gap-1"><span class="font-ur">${t('galla.digital_short')}:</span> <span class="font-num font-semibold" dir="ltr">Rs ${formatMoney(digSales)}</span></span>
        `;
  }
}

function renderUdhaar(udhaar) {
  const recEl = document.getElementById('udhaar-total-receivable');
  const payEl = document.getElementById('udhaar-total-payable');
  const box = document.getElementById('udhaar-list-container');
  if (!box) return;

  if (recEl) recEl.textContent = `Rs ${formatMoney(udhaar.total_receivable_pkr || 0)}`;
  if (payEl) payEl.textContent = `Rs ${formatMoney(udhaar.total_payable_pkr || 0)}`;

  const items = udhaar.open_entries || [];
  if (!items.length) {
    box.innerHTML = `<span class="budget-empty-hint font-ur">${t('udhaar.empty')}</span>`;
    return;
  }

  box.innerHTML = '';
  items.forEach(item => {
    const isReceivable = item.direction === 'receivable';
    const badgeClass = isReceivable ? 'udhaar-pill-in' : 'udhaar-pill-out';
    const badgeLabel = isReceivable ? t('udhaar.badge_lene') : t('udhaar.badge_dene');
    const amountColor = isReceivable ? 'var(--ok)' : 'var(--bad)';
    const remaining = Number(item.remaining_pkr || item.amount || 0);

    const waMessage = isReceivable
      ? `السلام علیکم ${item.person_name} صاحب، آپ کے ذمے PKR ${formatMoney(remaining)} بقایا ہیں (${item.notes || 'ادھار کھاتہ'})۔ براہ کرم جلد ادائیگی فرما دیں۔ شکریہ!`
      : `السلام علیکم ${item.person_name} صاحب، آپ کے PKR ${formatMoney(remaining)} ہمارے کھاتے میں درج ہیں۔`;
    const cleanPhone = (item.phone_number || '').replace(/[^0-9]/g, '');
    let waPhone = cleanPhone;
    if (waPhone.startsWith('0092')) {
      waPhone = waPhone.slice(2);
    } else if (waPhone.startsWith('03')) {
      waPhone = '92' + waPhone.slice(1);
    }
    const hasValidPhone = /^923\d{9}$/.test(waPhone);
    const waHref = hasValidPhone ? `https://wa.me/${waPhone}?text=${encodeURIComponent(waMessage)}` : `https://wa.me/?text=${encodeURIComponent(waMessage)}`;

    const row = document.createElement('div');
    row.className = 'bill-row';
    row.dataset.direction = isReceivable ? 'receivable' : 'payable';
    row.innerHTML = `
          <div class="bill-main">
            <div class="bill-title capitalize flex flex-wrap items-center gap-2 font-ur">
              <span class="font-bold text-[18px]">${escapeHtml(item.person_name)}</span>
              <span class="udhaar-pill ${badgeClass}">${badgeLabel}</span>
            </div>
            <div class="bill-sub font-ur mt-1 flex flex-wrap items-center gap-2">
              <strong style="color:${amountColor}" class="text-[17px] font-num">PKR ${formatMoney(remaining)}</strong>
              ${item.paid_amount > 0 ? `<span class="text-mut text-[14px]">(${t('bill.paid', 'ادا شدہ')}: PKR ${formatMoney(item.paid_amount)})</span>` : ''}
              ${item.notes ? `<span class="text-mut text-[15px]">· ${escapeHtml(item.notes)}</span>` : ''}
            </div>
          </div>
          <div class="bill-actions flex items-center gap-2 shrink-0">
            ${isReceivable ? `<a href="${waHref}" target="_blank" rel="noopener noreferrer" class="btn-whatsapp-remind" title="${t('udhaar.btn_whatsapp')}"><i data-lucide="message-circle" class="w-4 h-4"></i></a>` : ''}
            <button class="settle-udhaar-btn btn-mark-paid font-ur" data-id="${item.id}">
              <i data-lucide="check" class="w-4 h-4"></i> <span>${t('udhaar.btn_settle')}</span>
            </button>
            <button class="del-udhaar-btn btn-icon-del" data-id="${item.id}" title="${t('btn.remove')}">
              <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>
            </button>
          </div>
        `;
    box.appendChild(row);
  });

  box.querySelectorAll('.settle-udhaar-btn').forEach(btn => btn.addEventListener('click', async () => {
    const fd = new FormData();
    if (state.activeBookId) fd.append('book_id', String(state.activeBookId));
    const res = await authFetch(`/api/udhaar/${encodeURIComponent(state.userId)}/${btn.dataset.id}/settle`, { method: 'POST', body: fd });
    if (res.ok) {
      showToast(t('toast.udhaar_settled'), 'success');
      fetchDashboard();
    }
  }));

  box.querySelectorAll('.del-udhaar-btn').forEach(btn => btn.addEventListener('click', async () => {
    const res = await authFetch(withBookQuery(`/api/udhaar/${encodeURIComponent(state.userId)}/${btn.dataset.id}`), { method: 'DELETE' });
    if (res.ok) {
      showToast(t('toast.udhaar_deleted'), 'info');
      fetchDashboard();
    }
  }));

  applyUdhaarFilter();
  lucide.createIcons();
}

function renderBudgets(budgets) {
  const box = document.getElementById('budgets-container');
  if (!budgets.length) {
    box.innerHTML = `<span class="budget-empty-hint font-ur">${t('ledger.budgets_empty')}</span>`;
    return;
  }
  box.innerHTML = '';
  const categoryLabels = {
    total: t('budget.overall'),
    groceries: t('budget.groceries'),
    dairy: t('budget.dairy'),
    fuel: t('budget.fuel'),
    dining: t('budget.dining'),
    utilities: t('budget.utilities'),
    healthcare: t('budget.healthcare'),
    clothing: t('budget.clothing'),
    rent: t('budget.rent'),
    education: t('budget.education'),
    other: t('budget.other')
  };
  budgets.forEach(b => {
    const pct = Math.min(b.percent, 100);
    const barColor = b.level === 'over' ? 'var(--bad)' : b.level === 'warning' ? 'var(--warn)' : 'var(--ok)';
    const txtColor = b.level === 'over' ? 'var(--bad)' : b.level === 'warning' ? 'var(--warn)' : 'var(--ok)';
    const catKey = (b.category || '').toLowerCase();
    const isPreset = categoryLabels.hasOwnProperty(catKey);
    const label = categoryLabels[catKey] || b.category;
    const customBadge = (!isPreset && catKey !== 'total')
      ? `<span class="budget-custom-badge">Custom</span>`
      : '';

    const row = document.createElement('div');
    row.className = 'budget-item-box';
    row.innerHTML = `
          <div class="budget-item-header">
            <span class="budget-title font-ur">
              <span class="capitalize">${escapeHtml(label)}</span>
              ${customBadge}
            </span>
            <span class="flex items-center gap-2">
              <span class="budget-numbers tabular" style="color:${txtColor}">PKR ${formatMoney(b.spent_pkr)} / ${formatMoney(b.monthly_limit)} (${b.percent}%)</span>
              <button class="delete-budget-btn btn-icon-del" data-id="${b.id}" title="${t('btn.remove', 'Remove')}">
                <i data-lucide="x" class="w-4 h-4"></i>
              </button>
            </span>
          </div>
          <div class="budget-track-bg">
            <div class="budget-fill-bar" style="width:${pct}%; background-color:${barColor};"></div>
          </div>
          ${b.level === 'over'
        ? `<p class="budget-alert-text font-ur tabular" style="color:var(--bad)">${t('budget.over_by', { amount: formatMoney(Math.abs(b.remaining_pkr)) })}</p>`
        : b.level === 'warning'
          ? `<p class="budget-alert-text font-ur tabular" style="color:var(--warn)">${t('budget.remaining', { amount: formatMoney(b.remaining_pkr) })}</p>`
          : ''}
        `;
    box.appendChild(row);
  });
  box.querySelectorAll('.delete-budget-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      await authFetch(`/api/budgets/${encodeURIComponent(state.userId)}/${btn.dataset.id}`, { method: 'DELETE' });
      fetchDashboard();
    });
  });
  lucide.createIcons();
}

function formatCategoryName(c) {
  if (!c) return '';
  const cat = String(c).toLowerCase().trim();
  const key = 'budget.' + cat;
  const trans = t(key);
  if (trans && trans !== key) return trans;
  const urMap = {
    groceries: 'راشن و گروسری',
    dairy: 'ڈیری و بیکری',
    fuel: 'فیول و پیٹرول',
    dining: 'کھانا پینا',
    utilities: 'یوٹیلیٹی بلز',
    healthcare: 'ادویات و صحت',
    clothing: 'کپڑے و شاپنگ',
    rent: 'مکان کا کرایہ',
    education: 'تعلیم و فیس',
    entertainment: 'تفریح',
    other: 'دیگر اخراجات'
  };
  if (document.documentElement.getAttribute('lang') === 'ur' || (window.HisaabI18n && HisaabI18n.getLanguage() === 'ur')) {
    return urMap[cat] || cat;
  }
  return cat.charAt(0).toUpperCase() + cat.slice(1).replace('_', ' ');
}

function formatPaymentMethodName(m) {
  if (!m) return '';
  let method = String(m).toLowerCase().trim().replace(/_+/g, ' ');
  method = method.replace(/\b([a-z]+)\s+\1\b/gi, '$1').trim().replace(/\s+/g, '_');
  const urMap = {
    cash: 'نقد (کیش)',
    bank_transfer: 'بینک ٹرانسفر',
    easypaisa: 'ایزی پیسہ',
    jazzcash: 'جاز کیش',
    card: 'کارڈ',
    credit_card: 'کریڈٹ کارڈ',
    debit_card: 'ڈیبٹ کارڈ',
    digital: 'ڈیجیٹل',
    udhaar: 'ادھار کھاتہ'
  };
  const enMap = {
    cash: 'Cash',
    bank_transfer: 'Bank Transfer',
    easypaisa: 'Easypaisa',
    jazzcash: 'JazzCash',
    card: 'Card',
    credit_card: 'Credit Card',
    debit_card: 'Debit Card',
    digital: 'Digital',
    udhaar: 'Udhaar Khata'
  };
  if (document.documentElement.getAttribute('lang') === 'ur' || (window.HisaabI18n && HisaabI18n.getLanguage() === 'ur')) {
    return urMap[method] || method.replace(/_/g, ' ');
  }
  return enMap[method] || method.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function renderCategoryChart(rows) {
  const canvas = document.getElementById('category-chart');
  const empty = document.getElementById('category-empty');
  const legendBox = document.getElementById('category-legend');
  if (typeof Chart === 'undefined') return;
  if (charts.category) { charts.category.destroy(); charts.category = null; }
  empty.classList.toggle('hidden', rows.length > 0);
  if (canvas && canvas.parentElement && canvas.parentElement.parentElement) {
    canvas.parentElement.parentElement.classList.toggle('hidden', rows.length === 0);
  }
  if (legendBox) legendBox.innerHTML = '';
  if (!rows.length) return;

  const cInk = cssVar('--ink', '#0d1517');
  const cBrass = cssVar('--brass', '#d4a95a');
  const cOk = cssVar('--ok', '#6fbf94');
  const palette = [cBrass, cOk, '#5fa3b3', '#a88fd0', '#5d7074', '#e8b04a', '#e5707e'];

  charts.category = new Chart(canvas, {
    type: 'doughnut',
    data: {
      labels: rows.map(r => `${r.category} (${r.percent}%)`),
      datasets: [{
        data: rows.map(r => r.total_pkr),
        backgroundColor: rows.map((_, i) => palette[i % palette.length]),
        borderColor: cInk,
        borderWidth: 3,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: '72%',
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: cssVar('--s2', '#18262a'),
          borderColor: cssVar('--rule', '#26383d'),
          borderWidth: 1,
          titleColor: cssVar('--tx', '#ece7da'),
          bodyColor: cBrass,
          callbacks: { label: (c) => ` PKR ${formatMoney(c.parsed)}` }
        },
      },
    },
  });

  if (legendBox) {
    legendBox.innerHTML = rows.map((r, i) => {
      const color = palette[i % palette.length];
      const catLabel = formatCategoryName(r.category);
      return `
            <div class="legend-row">
              <div class="legend-label-group">
                <span class="legend-color-box" style="background-color: ${color}"></span>
                <span class="legend-cat-name font-ur text-[16px] text-tx">${escapeHtml(catLabel)}</span>
              </div>
              <span class="legend-amount tabular font-num text-mut text-[15px]" dir="ltr">PKR ${formatMoney(r.total_pkr)}</span>
            </div>
          `;
    }).join('');
  }
}

function renderTrendChart(days) {
  const canvas = document.getElementById('trend-chart');
  if (typeof Chart === 'undefined') return;
  if (charts.trend) { charts.trend.destroy(); charts.trend = null; }
  const cBrass = cssVar('--brass', '#d4a95a');
  const cRule = cssVar('--rule', '#26383d');
  const cMut = cssVar('--mut', '#8fa1a4');
  const cS2 = cssVar('--s2', '#18262a');
  const cTx = cssVar('--tx', '#ece7da');
  const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  charts.trend = new Chart(canvas, {
    type: 'bar',
    data: {
      labels: days.map(d => d.date.slice(5)),
      datasets: [{
        data: days.map(d => d.total_pkr),
        backgroundColor: days.map((_, i) => i === days.length - 1 ? cBrass : '#3a5157'),
        borderRadius: 3,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: prefersReducedMotion ? false : { duration: 400 },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: cS2,
          borderColor: cRule,
          borderWidth: 1,
          titleColor: cTx,
          bodyColor: cBrass,
          callbacks: { label: (c) => ` PKR ${formatMoney(c.parsed.y)}` }
        },
      },
      scales: {
        x: { ticks: { color: cMut, maxTicksLimit: 8, font: { size: 10 } }, grid: { display: false } },
        y: { beginAtZero: true, ticks: { color: cMut, font: { size: 10 } }, grid: { color: cRule } },
      },
    },
  });
}

function renderPaymentSplit(rows) {
  const box = document.getElementById('payment-split-container');
  if (!rows.length) {
    box.innerHTML = `<span class="text-xs text-mut italic font-ur">${t('ledger.payment_split_empty')}</span>`;
    return;
  }
  const total = rows.reduce((s, r) => s + r.total_pkr, 0) || 1;
  const cash = rows.filter(r => r.method === 'cash').reduce((s, r) => s + r.total_pkr, 0);
  const digital = total - cash;
  const cashPct = ((cash / total) * 100).toFixed(1);
  const digitalPct = ((digital / total) * 100).toFixed(1);
  const isUr = (document.documentElement.getAttribute('lang') === 'ur');

  box.innerHTML = `
        <div class="split-bar-wrap mb-3">
          <div class="split-track">
            <div class="split-fill-cash" style="width: ${cashPct}%;"></div>
            <div class="split-fill-digital" style="width: ${digitalPct}%;"></div>
          </div>
          <div class="flex items-center justify-between mt-2 font-ur ${isUr ? 'text-[15.5px]' : 'text-xs'}">
            <span class="text-brass inline-flex items-center gap-1.5">
              <span>${t('ledger.cash')}:</span>
              <span class="font-num font-semibold" dir="ltr">${cashPct}%</span>
              <span class="text-mut text-[13.5px]" dir="ltr">(PKR ${formatMoney(cash)})</span>
            </span>
            <span class="text-ok inline-flex items-center gap-1.5">
              <span>${t('ledger.digital')}:</span>
              <span class="font-num font-semibold" dir="ltr">${digitalPct}%</span>
              <span class="text-mut text-[13.5px]" dir="ltr">(PKR ${formatMoney(digital)})</span>
            </span>
          </div>
        </div>
        ${rows.map(r => `
          <div class="flex items-center justify-between py-1.5 border-b border-dashed border-rule">
            <span class="text-tx font-ur ${isUr ? 'text-[16px]' : 'text-sm'}">${escapeHtml(formatPaymentMethodName(r.method))}</span>
            <span class="font-num tabular text-mut text-[15px]" dir="ltr">PKR ${formatMoney(r.total_pkr)} (${((r.total_pkr / total) * 100).toFixed(1)}%)</span>
          </div>`).join('')}
      `;
}

function renderRecurring(items) {
  const box = document.getElementById('recurring-container');
  if (!items.length) {
    box.innerHTML = `<span class="bill-empty-hint font-ur">${t('ledger.recurring_empty')}</span>`;
    return;
  }
  box.innerHTML = '';
  items.forEach(r => {
    const badge = r.paid_this_month
      ? `<span class="bill-status-pill pill-paid">${t('bill.paid', 'Paid')}</span>`
      : r.overdue
        ? `<span class="bill-status-pill pill-unpaid">${t('bill.unpaid', 'Unpaid')}</span>`
        : `<span class="bill-status-pill pill-pending">${t('bill.pending', 'Pending')}</span>`;
    const row = document.createElement('div');
    row.className = 'bill-row';
    row.innerHTML = `
          <div class="bill-main">
            <div class="bill-title capitalize truncate font-ur">${escapeHtml(r.title)}</div>
            <div class="bill-sub font-ur flex items-center gap-2 text-[15.5px]">
              <span>${t('bill.due_day', 'Due Day')}: <span class="font-num font-semibold" dir="ltr">${r.due_day}</span></span>
              <span class="text-mut">·</span>
              <span class="font-num font-semibold" dir="ltr">PKR ${formatMoney(r.amount)}</span>
            </div>
          </div>
          <div class="bill-actions flex items-center gap-2 shrink-0">
            ${badge}
            ${r.paid_this_month ? '' : `<button class="pay-rec-btn btn-mark-paid font-ur" data-id="${r.id}"><i data-lucide="check" class="w-4 h-4"></i> <span>${t('btn.mark_paid', 'Mark as Paid')}</span></button>`}
            <button class="del-rec-btn btn-icon-del" data-id="${r.id}" title="${t('btn.remove', 'Remove')}"><i data-lucide="trash-2" class="w-4 h-4"></i></button>
          </div>
        `;
    box.appendChild(row);
  });
  box.querySelectorAll('.pay-rec-btn').forEach(btn => btn.addEventListener('click', async () => {
    const res = await authFetch(`/api/recurring/${encodeURIComponent(state.userId)}/${btn.dataset.id}/pay`, { method: 'POST' });
    const out = await res.json().catch(() => ({}));
    showToast(out.message || t('toast.recurring_saved'), res.ok ? 'success' : 'error');
    fetchDashboard();
  }));
  box.querySelectorAll('.del-rec-btn').forEach(btn => btn.addEventListener('click', async () => {
    await authFetch(`/api/recurring/${encodeURIComponent(state.userId)}/${btn.dataset.id}`, { method: 'DELETE' });
    fetchDashboard();
  }));
  lucide.createIcons();
}

function bindAnalyticsForms() {
  const budgetCategorySelect = document.getElementById('budget-category');
  const budgetCustomInput = document.getElementById('budget-custom-name');
  if (budgetCategorySelect && budgetCustomInput) {
    budgetCategorySelect.addEventListener('change', () => {
      if (budgetCategorySelect.value === 'custom') {
        budgetCustomInput.classList.remove('hidden');
        budgetCustomInput.focus();
        budgetCustomInput.required = true;
      } else {
        budgetCustomInput.classList.add('hidden');
        budgetCustomInput.value = '';
        budgetCustomInput.required = false;
      }
    });
  }

  document.getElementById('budget-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const selVal = document.getElementById('budget-category').value;
    const customInput = document.getElementById('budget-custom-name');
    const customVal = customInput ? customInput.value.trim() : '';
    let cat = selVal;
    if (selVal === 'custom') {
      if (!customVal) {
        showToast(t('toast.budget_name_required'), 'error');
        if (customInput) customInput.focus();
        return;
      }
      cat = customVal;
    }
    const fd = new FormData();
    fd.append('user_id', state.userId);
    if (state.activeBookId) fd.append('book_id', String(state.activeBookId));
    fd.append('category', cat);
    fd.append('monthly_limit', document.getElementById('budget-limit').value);
    const res = await authFetch('/api/budgets', { method: 'POST', body: fd });
    if (res.ok) {
      document.getElementById('budget-limit').value = '';
      if (customInput) {
        customInput.value = '';
        customInput.classList.add('hidden');
        customInput.required = false;
      }
      if (budgetCategorySelect) budgetCategorySelect.value = 'total';
      showToast(t('toast.budget_saved'), 'success');
    } else {
      showToast((await res.json().catch(() => ({}))).detail || 'Could not save budget', 'error');
    }
    fetchDashboard();
  });
  document.getElementById('recurring-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const fd = new FormData();
    fd.append('user_id', state.userId);
    if (state.activeBookId) fd.append('book_id', String(state.activeBookId));
    fd.append('title', document.getElementById('rec-title').value);
    fd.append('amount', document.getElementById('rec-amount').value);
    fd.append('due_day', document.getElementById('rec-day').value || '1');
    const res = await authFetch('/api/recurring', { method: 'POST', body: fd });
    if (res.ok) {
      document.getElementById('rec-title').value = '';
      document.getElementById('rec-amount').value = '';
      showToast(t('toast.recurring_saved'), 'success');
    } else showToast((await res.json().catch(() => ({}))).detail || 'Could not save', 'error');
    fetchDashboard();
  });

  const udhaarForm = document.getElementById('udhaar-form');
  if (udhaarForm) {
    udhaarForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const personEl = document.getElementById('udhaar-person');
      const amountEl = document.getElementById('udhaar-amount');
      const dirEl = document.getElementById('udhaar-direction');
      const notesEl = document.getElementById('udhaar-notes');

      const rawNotes = notesEl ? notesEl.value.trim() : '';
      let phone = '';
      let notes = rawNotes;
      const phoneMatch = rawNotes.match(/(03\d{9}|\+?923\d{9})/);
      if (phoneMatch) {
        phone = phoneMatch[1];
      }

      const fd = new FormData();
      fd.append('user_id', state.userId);
      if (state.activeBookId) fd.append('book_id', String(state.activeBookId));
      fd.append('person_name', personEl.value);
      fd.append('amount', amountEl.value);
      fd.append('direction', dirEl ? dirEl.value : 'receivable');
      if (notes) fd.append('notes', notes);
      if (phone) fd.append('phone_number', phone);

      const res = await authFetch('/api/udhaar', { method: 'POST', body: fd });
      if (res.ok) {
        personEl.value = '';
        amountEl.value = '';
        if (notesEl) notesEl.value = '';
        showToast(t('toast.udhaar_saved'), 'success');
      } else {
        showToast((await res.json().catch(() => ({}))).detail || 'Could not save udhaar', 'error');
      }
      fetchDashboard();
    });
  }
}

// Register the service worker (PWA install + offline shell)
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => navigator.serviceWorker.register('/sw.js').catch(() => { }));
}

// =========================================================================
// Helpers
// =========================================================================
function formatMoney(amount) {
  return Number(amount).toLocaleString('en-US', {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  });
}

function escapeHtml(string) {
  if (!string) return '';
  return String(string)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function showToast(message, type = 'info') {
  const container = elements.toastContainer || document.getElementById('toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  const bgColors = {
    success: 'bg-s1 border-rule text-ok',
    error: 'bg-s1 border-bad/40 text-bad',
    warning: 'bg-s1 border-warn/30 text-warn',
    info: 'bg-s1 border-rule text-tx',
  };
  const icons = {
    success: 'check-circle',
    error: 'alert-triangle',
    warning: 'alert-circle',
    info: 'info',
  };

  toast.className = `pointer-events-auto flex items-center gap-2.5 px-3.5 py-2.5 rounded-xl border backdrop-blur-md shadow-lg text-xs font-medium transition-all transform duration-200 translate-y-2 opacity-0 ${bgColors[type] || bgColors.info}`;
  toast.innerHTML = `
        <i data-lucide="${icons[type] || 'info'}" class="w-4 h-4 shrink-0"></i>
        <span>${escapeHtml(message)}</span>
      `;

  container.appendChild(toast);
  if (window.lucide) lucide.createIcons();

  // Animate in
  requestAnimationFrame(() => {
    toast.classList.remove('translate-y-2', 'opacity-0');
  });

  // Remove after 3.5s
  setTimeout(() => {
    toast.classList.add('opacity-0', 'translate-y-2');
    setTimeout(() => {
      if (toast.parentNode) toast.parentNode.removeChild(toast);
    }, 200);
  }, 3500);
}

window.showToast = showToast;

// =========================================================================
// Multi-Book Switcher, Highlight Titling Helpers & Cross-Book Suggestion
// =========================================================================
const GENERIC_VENUE_SET = new Set([
  'general store', 'local store', 'store', 'shop', 'market', 'dukaan',
  'cash sale', 'unknown', 'default', 'misc', 'other',
  'bank_transfer transfer', 'bank_transfer', 'bank transfer', 'bank transfer transfer',
  'transfer', 'transfer transfer', 'easypaisa transfer', 'jazzcash transfer', 'raast transfer',
  'دکان', 'مارکیٹ', 'جنرل سٹور', 'جنرل اسٹور', 'مقامی دکان', 'بینک ٹرانسفر'
]);

function cleanDisplayTitle(title) {
  if (!title) return 'Expense';
  let cleaned = String(title).replace(/_+/g, ' ').trim();
  cleaned = cleaned.replace(/\b(\w+)\s+\1\b/gi, '$1').trim();
  if (cleaned.toLowerCase() === 'bank transfer') {
    return (typeof window !== 'undefined' && window.HisaabI18n && window.HisaabI18n.getLanguage() === 'ur')
      ? 'بینک ٹرانسفر' : 'Bank Transfer';
  }
  return cleaned;
}

function isGenericVenueName(name) {
  if (!name) return true;
  return GENERIC_VENUE_SET.has(String(name).trim().toLowerCase());
}

function withBookQuery(url) {
  if (!state.activeBookId) return url;
  const sep = url.includes('?') ? '&' : '?';
  return `${url}${sep}book_id=${encodeURIComponent(String(state.activeBookId))}`;
}

function getSavedBookId() {
  try {
    const key = state.userId ? `hisaab_active_book_${state.userId}` : 'hisaab_active_book_id';
    const raw = localStorage.getItem(key) || localStorage.getItem('hisaab_active_book_id');
    const parsed = raw ? parseInt(raw, 10) : NaN;
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
  } catch (_) {
    return null;
  }
}

function saveActiveBookId(bookId) {
  try {
    if (!bookId) return;
    const val = String(bookId);
    localStorage.setItem('hisaab_active_book_id', val);
    if (state.userId) localStorage.setItem(`hisaab_active_book_${state.userId}`, val);
  } catch (_) { }
}

function syncActiveBookState(books, preferredBookId = null) {
  state.books = Array.isArray(books) ? books : [];
  if (!state.books.length) {
    renderBookSwitcher();
    return;
  }
  const savedId = getSavedBookId();
  let chosen = null;
  if (preferredBookId) {
    chosen = state.books.find(b => Number(b.id) === Number(preferredBookId)) || null;
  }
  if (!chosen && state.activeBookId) {
    chosen = state.books.find(b => Number(b.id) === Number(state.activeBookId)) || null;
  }
  if (!chosen && savedId) {
    chosen = state.books.find(b => Number(b.id) === Number(savedId)) || null;
  }
  if (!chosen) {
    chosen = state.books.find(b => b.is_default) || state.books[0];
  }
  if (chosen) {
    state.activeBookId = Number(chosen.id);
    state.activeBookType = chosen.book_type === 'personal' ? 'personal' : 'business';
    saveActiveBookId(state.activeBookId);
  }
  renderBookSwitcher();
}

function getHeaderBookLabel(book) {
  if (!book) {
    return state.activeBookType === 'personal' ? t('book.personal_badge') : t('book.business_badge');
  }
  const raw = String(book.name || '').trim();
  const isPersonal = book.book_type === 'personal';
  const isEn = getCurrentLang() === 'en';
  if (!raw) return isPersonal ? t('book.personal_badge') : t('book.business_badge');
  if (raw === 'کاروبار / My Business' || (book.is_default && raw.includes('کاروبار') && raw.includes('My Business'))) {
    return isEn ? 'My Business' : 'کاروبار';
  }
  if (raw === 'ذاتی حساب / Personal' || (raw.includes('ذاتی حساب') && raw.includes('Personal'))) {
    return isEn ? 'Personal' : 'ذاتی حساب';
  }
  return raw;
}

function renderBookSwitcher() {
  const activeBook = (state.books || []).find(b => Number(b.id) === Number(state.activeBookId))
    || (state.books && state.books[0])
    || { id: null, name: state.activeBookType === 'personal' ? t('book.personal_badge') : t('book.business_badge'), book_type: state.activeBookType || 'business' };

  const isPersonal = activeBook.book_type === 'personal';
  const typeIconEl = document.getElementById('book-type-icon');
  const labelEl = document.getElementById('active-book-label');
  if (typeIconEl) typeIconEl.textContent = isPersonal ? '🏠' : '🏢';
  if (labelEl) labelEl.textContent = getHeaderBookLabel(activeBook);

  const pane = elements.dashboardPane || document.getElementById('dashboard-pane');
  if (pane) {
    pane.dataset.bookMode = isPersonal ? 'personal' : 'business';
    pane.classList.toggle('book-mode-personal', isPersonal);
    pane.classList.toggle('book-mode-business', !isPersonal);
  }

  const stripBadge = document.getElementById('ledger-book-badge');
  const stripName = document.getElementById('ledger-book-name');
  if (stripBadge) {
    stripBadge.textContent = isPersonal ? t('book.personal_badge') : t('book.business_badge');
    stripBadge.classList.toggle('book-badge-personal', isPersonal);
    stripBadge.classList.toggle('book-badge-business', !isPersonal);
  }
  if (stripName) {
    stripName.textContent = activeBook.name || '';
  }

  const quickBiz = document.getElementById('quick-tab-business');
  const quickPers = document.getElementById('quick-tab-personal');
  if (quickBiz) quickBiz.classList.toggle('active', !isPersonal);
  if (quickPers) quickPers.classList.toggle('active', isPersonal);

  const listEl = document.getElementById('book-options-list');
  if (listEl) {
    listEl.innerHTML = '';
    (state.books || []).forEach(book => {
      const isCurr = Number(book.id) === Number(state.activeBookId);
      const isBookPersonal = book.book_type === 'personal';
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = `book-option-btn${isCurr ? ' active' : ''}`;
      btn.setAttribute('role', 'menuitem');
      btn.innerHTML = `
            <span class="flex items-center gap-2 min-w-0">
              <span class="shrink-0">${isBookPersonal ? '🏠' : '🏢'}</span>
              <span class="truncate font-medium">${escapeHtml(book.name)}</span>
            </span>
            <span class="flex items-center gap-1.5 shrink-0">
              <span class="book-type-pill ${isBookPersonal ? 'book-badge-personal' : 'book-badge-business'}">${escapeHtml(isBookPersonal ? t('book.personal_badge') : t('book.business_badge'))}</span>
              ${isCurr ? '<i data-lucide="check" class="w-3.5 h-3.5 text-brass"></i>' : ''}
            </span>
          `;
      btn.addEventListener('click', () => switchBook(book.id));
      listEl.appendChild(btn);
    });
    if (window.lucide) lucide.createIcons();
  }
}

async function fetchBooks() {
  if (!state.userId) return;
  try {
    const res = await authFetch(`/api/books/${encodeURIComponent(state.userId)}`);
    if (!res.ok) return;
    const data = await res.json();
    syncActiveBookState(data.books || [], state.activeBookId || data.default_book_id);
  } catch (err) {
    console.error('Error fetching books:', err);
  }
}

function switchBook(bookId, silent = false) {
  const target = (state.books || []).find(b => Number(b.id) === Number(bookId));
  if (!target) return;
  state.activeBookId = Number(target.id);
  state.activeBookType = target.book_type === 'personal' ? 'personal' : 'business';
  saveActiveBookId(state.activeBookId);
  renderBookSwitcher();

  const dropdown = document.getElementById('book-switcher-dropdown');
  const switcherBtn = document.getElementById('book-switcher-btn');
  if (dropdown) dropdown.classList.add('hidden');
  if (switcherBtn) switcherBtn.setAttribute('aria-expanded', 'false');

  fetchDashboard();
  if (!silent) {
    showToast(`${t('toast.book_switched')}: ${target.name}`, 'info');
  }
}

function applyUdhaarFilter() {
  const box = document.getElementById('udhaar-list-container');
  if (!box) return;
  const filter = state.udhaarFilter || 'all';
  box.querySelectorAll('.bill-row[data-direction]').forEach(row => {
    const show = filter === 'all' || row.dataset.direction === filter;
    row.classList.toggle('hidden', !show);
  });
}

function appendCrossBookSuggestion(sug) {
  if (!elements.messagesContainer || !sug) return;
  elements.messagesContainer.querySelectorAll('.cross-book-banner').forEach(el => el.remove());

  const isUrdu = getCurrentLang() === 'ur';
  const promptText = isUrdu ? (sug.message_ur || sug.message_en) : (sug.message_en || sug.message_ur);
  const banner = document.createElement('div');
  banner.className = 'cross-book-banner';
  banner.innerHTML = `
        <div class="${isUrdu ? 'font-ur text-[15.5px]' : 'text-xs sm:text-sm'} text-tx leading-relaxed" dir="${isUrdu ? 'rtl' : 'ltr'}">
          ${escapeHtml(promptText)}
        </div>
        <div class="cross-book-actions">
          <button type="button" class="cross-book-move-btn btn-primary-brass text-xs py-1.5 px-3 w-auto min-h-0 ${isUrdu ? 'font-ur' : ''}">
            ${escapeHtml(t('book.move_yes'))} ${escapeHtml(sug.target_book_name || '')}
          </button>
          <button type="button" class="cross-book-keep-btn btn-outlined-s2 text-xs py-1.5 px-3 w-auto min-h-0 ${isUrdu ? 'font-ur' : ''}">
            ${escapeHtml(t('book.move_keep'))}
          </button>
        </div>
      `;

  const moveBtn = banner.querySelector('.cross-book-move-btn');
  const keepBtn = banner.querySelector('.cross-book-keep-btn');

  if (moveBtn) {
    moveBtn.addEventListener('click', async () => {
      moveBtn.disabled = true;
      if (keepBtn) keepBtn.disabled = true;
      try {
        const fd = new FormData();
        fd.append('target_book_id', String(sug.target_book_id));
        const res = await authFetch(
          `/api/transactions/${encodeURIComponent(state.userId)}/${sug.transaction_id}/move-book`,
          { method: 'POST', body: fd }
        );
        if (!res.ok) throw new Error('Move failed');
        banner.remove();
        showToast(`${t('toast.tx_moved')} (${sug.target_book_name || ''})`, 'success');
        fetchDashboard();
      } catch (err) {
        moveBtn.disabled = false;
        if (keepBtn) keepBtn.disabled = false;
        showToast(err.message || 'Move failed', 'error');
      }
    });
  }

  if (keepBtn) {
    keepBtn.addEventListener('click', () => banner.remove());
  }

  elements.messagesContainer.appendChild(banner);
  scrollToBottom();
}

function openCreateBookModal(defaultType = 'business') {
  const modal = document.getElementById('create-book-modal');
  const dropdown = document.getElementById('book-switcher-dropdown');
  const switcherBtn = document.getElementById('book-switcher-btn');
  const nameInput = document.getElementById('new-book-name');
  const typeSelect = document.getElementById('new-book-type');
  if (dropdown) dropdown.classList.add('hidden');
  if (switcherBtn) switcherBtn.setAttribute('aria-expanded', 'false');
  if (typeSelect && (defaultType === 'business' || defaultType === 'personal')) {
    typeSelect.value = defaultType;
  }
  if (modal) {
    modal.classList.remove('hidden');
    if (nameInput) {
      nameInput.value = '';
      setTimeout(() => nameInput.focus(), 40);
    }
  }
}

function closeCreateBookModal() {
  const modal = document.getElementById('create-book-modal');
  if (modal) modal.classList.add('hidden');
}

function bindBookEvents() {
  const wrap = document.getElementById('book-switcher-wrap');
  const switcherBtn = document.getElementById('book-switcher-btn');
  const dropdown = document.getElementById('book-switcher-dropdown');
  const createBookBtn = document.getElementById('create-book-btn');
  const modal = document.getElementById('create-book-modal');
  const closeModalBtn = document.getElementById('close-book-modal-btn');
  const cancelModalBtn = document.getElementById('cancel-book-modal-btn');
  const createForm = document.getElementById('create-book-form');
  const quickBiz = document.getElementById('quick-tab-business');
  const quickPers = document.getElementById('quick-tab-personal');
  const udhaarTabsBox = document.getElementById('udhaar-filter-tabs');

  if (switcherBtn && dropdown) {
    switcherBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const accDrop = document.getElementById('account-dropdown');
      if (accDrop) accDrop.classList.add('hidden');
      const willOpen = dropdown.classList.contains('hidden');
      dropdown.classList.toggle('hidden');
      switcherBtn.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
      if (willOpen && (!state.books || !state.books.length) && state.userId) {
        await fetchBooks();
      }
    });

    document.addEventListener('click', (e) => {
      if (wrap && !wrap.contains(e.target)) {
        dropdown.classList.add('hidden');
        switcherBtn.setAttribute('aria-expanded', 'false');
      }
    });
  }

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' || e.key === 'Esc' || e.keyCode === 27) {
      if (dropdown) dropdown.classList.add('hidden');
      if (switcherBtn) switcherBtn.setAttribute('aria-expanded', 'false');
      closeCreateBookModal();
    }
  });

  const nameInput = document.getElementById('new-book-name');
  if (nameInput) {
    nameInput.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' || e.key === 'Esc' || e.keyCode === 27) {
        e.preventDefault();
        e.stopPropagation();
        closeCreateBookModal();
      }
    });
  }

  if (modal) {
    modal.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' || e.key === 'Esc' || e.keyCode === 27) {
        e.preventDefault();
        closeCreateBookModal();
      }
    });
  }

  if (createBookBtn) {
    createBookBtn.addEventListener('click', () => openCreateBookModal(state.activeBookType || 'business'));
  }
  if (closeModalBtn) closeModalBtn.addEventListener('click', closeCreateBookModal);
  if (cancelModalBtn) cancelModalBtn.addEventListener('click', closeCreateBookModal);
  if (modal) {
    modal.addEventListener('click', (e) => {
      if (e.target === modal) closeCreateBookModal();
    });
  }

  if (createForm) {
    createForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const nameInput = document.getElementById('new-book-name');
      const typeSelect = document.getElementById('new-book-type');
      const nameVal = nameInput ? nameInput.value.trim() : '';
      const typeVal = typeSelect ? typeSelect.value : 'business';
      if (!nameVal) return;
      if (!state.userId) {
        showToast('Please sign in first', 'warning');
        return;
      }
      try {
        const fd = new FormData();
        fd.append('user_id', state.userId);
        fd.append('name', nameVal);
        fd.append('book_type', typeVal);
        const res = await authFetch('/api/books', { method: 'POST', body: fd });
        const out = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(out.detail || 'Failed to create book');
        closeCreateBookModal();
        syncActiveBookState(out.books || [], out.book ? out.book.id : null);
        fetchDashboard();
        showToast(t('toast.book_created'), 'success');
      } catch (err) {
        showToast(err.message || 'Could not create book', 'error');
      }
    });
  }

  const handleQuickTypeSwitch = (targetType) => {
    const match = (state.books || []).find(b => b.book_type === targetType && b.is_default)
      || (state.books || []).find(b => b.book_type === targetType);
    if (match) {
      switchBook(match.id);
    } else {
      openCreateBookModal(targetType);
    }
  };

  if (quickBiz) quickBiz.addEventListener('click', () => handleQuickTypeSwitch('business'));
  if (quickPers) quickPers.addEventListener('click', () => handleQuickTypeSwitch('personal'));

  if (udhaarTabsBox) {
    udhaarTabsBox.querySelectorAll('.udhaar-filter-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        const nextFilter = tab.dataset.udhaarFilter || 'all';
        state.udhaarFilter = nextFilter;
        udhaarTabsBox.querySelectorAll('.udhaar-filter-tab').forEach(btn => {
          btn.classList.toggle('active', btn === tab);
        });
        applyUdhaarFilter();
      });
    });
  }
}

// =========================================================================
// Customer & Supplier Party Profiles, Ledgers & Business Settings
// =========================================================================
async function fetchParties() {
  if (!state.userId) return;
  try {
    const pType = state.partyFilter || 'all';
    const sTerm = state.partySearch || '';
    let url = `/api/parties/${encodeURIComponent(state.userId)}?party_type=${encodeURIComponent(pType)}&search=${encodeURIComponent(sTerm)}`;
    url = withBookQuery(url);
    const res = await authFetch(url);
    if (!res.ok) return;
    const data = await res.json();
    state.parties = data.parties || [];
    renderPartiesSummary(data.summary || {});
    renderPartiesList(state.parties);
  } catch (err) {
    console.error('Failed to fetch parties:', err);
  }
}

function renderPartiesSummary(summary) {
  const recEl = document.getElementById('parties-total-receivable');
  const payEl = document.getElementById('parties-total-payable');
  const netEl = document.getElementById('parties-net-balance');
  const countEl = document.getElementById('party-count-badge');

  if (recEl) recEl.textContent = `Rs ${formatMoney(summary.total_receivable_pkr || 0)}`;
  if (payEl) payEl.textContent = `Rs ${formatMoney(summary.total_payable_pkr || 0)}`;
  if (netEl) netEl.textContent = `Rs ${formatMoney(summary.net_balance_pkr || 0)}`;
  if (countEl) countEl.textContent = String(summary.total_parties || 0);
}

function getLocalizedBalanceText(obj) {
  if (!obj) return '';
  if (getCurrentLang() === 'en') {
    return obj.balance_text_en || obj.balance_text_ur || 'Settled';
  }
  return obj.balance_text_ur || obj.balance_text_en || 'حساب برابر';
}

function applyPartyDetailLocale(data) {
  if (!data) return;
  const party = data.party || {};
  const metrics = data.metrics || {};
  const txs = data.transactions || [];
  const isCust = (party.party_type || 'customer') === 'customer';

  const phoneEl = document.getElementById('detail-party-phone');
  const badgeEl = document.getElementById('detail-party-badge');
  const statusLabelEl = document.getElementById('detail-balance-status-label');
  const amtEl = document.getElementById('detail-balance-amount');
  const dueEl = document.getElementById('detail-due-date-badge');
  const btnDiyeSpan = document.querySelector('#btn-action-diye span');
  const btnLiyeSpan = document.querySelector('#btn-action-liye span');

  if (phoneEl) phoneEl.textContent = party.phone || t('parties.no_phone');
  if (badgeEl) {
    badgeEl.className = `party-badge ${isCust ? 'badge-customer' : 'badge-supplier'} font-ur`;
    badgeEl.textContent = isCust ? t('parties.customer_opt') : t('parties.supplier_opt');
  }
  if (statusLabelEl) statusLabelEl.textContent = getLocalizedBalanceText(metrics);
  if (amtEl) {
    amtEl.textContent = `Rs ${formatMoney(metrics.net_balance_pkr || 0)}`;
    if (metrics.balance_status === 'receivable') {
      amtEl.className = 'text-2xl font-bold font-num tabular text-ok';
    } else if (metrics.balance_status === 'payable') {
      amtEl.className = 'text-2xl font-bold font-num tabular text-bad';
    } else {
      amtEl.className = 'text-2xl font-bold font-num tabular text-mut';
    }
  }
  if (dueEl) {
    if (metrics.is_overdue) {
      dueEl.textContent = t('parties.overdue');
      dueEl.className = 'text-xs mt-1 party-badge badge-overdue font-ur animate-pulse';
      dueEl.classList.remove('hidden');
    } else if (metrics.nearest_due_date) {
      dueEl.textContent = `${t('parties.due_prefix')} ${metrics.nearest_due_date}`;
      dueEl.className = 'text-xs mt-1 party-badge badge-customer font-num';
      dueEl.classList.remove('hidden');
    } else {
      dueEl.classList.add('hidden');
    }
  }
  if (btnDiyeSpan) {
    btnDiyeSpan.textContent = isCust ? t('parties.gave_credit') : t('parties.supplier_paid_btn');
  }
  if (btnLiyeSpan) {
    btnLiyeSpan.textContent = isCust ? t('parties.received_payment') : t('parties.supplier_took_btn');
  }
  renderPartyLedgerTable(txs);
}

function renderPartiesList(parties) {
  const container = document.getElementById('parties-cards-container');
  if (!container) return;

  if (!parties || parties.length === 0) {
    container.innerHTML = `<div class="text-center py-6 text-mut text-sm font-ur col-span-full">${t('parties.empty_state')}</div>`;
    return;
  }

  container.innerHTML = parties.map(p => {
    const isCustomer = (p.party_type || 'customer') === 'customer';
    const typeBadge = isCustomer
      ? `<span class="party-badge badge-customer font-ur">${t('parties.customer_opt')}</span>`
      : `<span class="party-badge badge-supplier font-ur">${t('parties.supplier_opt')}</span>`;

    let statusClass = 'badge-settled';
    const statusLabel = getLocalizedBalanceText(p);
    if (p.balance_status === 'receivable') {
      statusClass = 'badge-receivable';
    } else if (p.balance_status === 'payable') {
      statusClass = 'badge-payable';
    }

    const overdueBadge = p.is_overdue
      ? `<span class="party-badge badge-overdue font-ur animate-pulse">${escapeHtml(t('parties.overdue'))}</span>`
      : (p.nearest_due_date ? `<span class="text-xs text-mut font-num">${escapeHtml(t('parties.due_prefix'))} ${escapeHtml(p.nearest_due_date)}</span>` : '');

    const initial = (p.name || 'P').trim()[0].toUpperCase();

    return `
      <div class="party-card font-ur" data-party-id="${p.id}">
        <div class="party-card-header">
          <div class="flex items-center gap-2.5">
            <div class="party-avatar">${escapeHtml(initial)}</div>
            <div>
              <div class="flex items-center gap-2">
                <strong class="text-tx font-bold text-sm">${escapeHtml(p.name)}</strong>
                ${typeBadge}
              </div>
              <div class="text-xs text-mut font-num">${escapeHtml(p.phone || t('parties.no_phone'))}</div>
            </div>
          </div>
          <div class="flex flex-col items-end gap-1">
            <span class="party-badge ${statusClass}">${escapeHtml(statusLabel)}</span>
            <span class="text-sm font-bold font-num tabular text-tx">Rs ${formatMoney(p.net_balance_pkr || 0)}</span>
          </div>
        </div>
        ${overdueBadge ? `<div class="flex justify-between items-center pt-2 border-t border-rule text-xs">${overdueBadge}</div>` : ''}
      </div>
    `;
  }).join('');

  container.querySelectorAll('.party-card').forEach(card => {
    card.addEventListener('click', () => {
      const pId = card.getAttribute('data-party-id');
      if (pId) openPartyDetail(parseInt(pId, 10));
    });
  });
}

async function openPartyDetail(partyId) {
  if (!state.userId) return;
  try {
    const res = await authFetch(`/api/parties/${encodeURIComponent(state.userId)}/${partyId}`);
    if (!res.ok) {
      showToast('Could not load party details', 'error');
      return;
    }
    const data = await res.json();
    state.activePartyId = partyId;
    state.activePartyDetail = data;
    state.partyLedgerFilter = 'all';
    document.querySelectorAll('.party-ledger-filter-tab').forEach(tab => {
      tab.classList.toggle('active', tab.getAttribute('data-ledger-filter') === 'all');
    });

    const dirView = document.getElementById('parties-directory-view');
    const detView = document.getElementById('party-detail-view');
    if (dirView) dirView.classList.add('hidden');
    if (detView) detView.classList.remove('hidden');

    const party = data.party || {};
    const nameEl = document.getElementById('detail-party-name');
    const avatarEl = document.getElementById('detail-party-avatar');
    const addressEl = document.getElementById('detail-party-address');

    if (nameEl) nameEl.textContent = party.name || '';
    if (avatarEl) avatarEl.textContent = (party.name || 'P').trim()[0].toUpperCase();
    if (addressEl) {
      if (party.address) {
        addressEl.textContent = `• ${party.address}`;
        addressEl.classList.remove('hidden');
      } else {
        addressEl.classList.add('hidden');
      }
    }

    applyPartyDetailLocale(data);
  } catch (err) {
    console.error('Error opening party detail:', err);
  }
}

function renderPartyLedgerTable(txs) {
  const tbody = document.getElementById('party-ledger-tbody');
  if (!tbody) return;

  const filter = state.partyLedgerFilter || 'all';
  const filteredTxs = (txs || []).filter(tx => filter === 'all' || tx.tx_type === filter);

  if (!filteredTxs.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="text-center py-6 text-mut font-ur">${t('parties.no_txs')}</td></tr>`;
    return;
  }

  tbody.innerHTML = filteredTxs.map(tx => {
    const isCredit = tx.tx_type === 'credit';
    const gaveHtml = isCredit
      ? `<span class="tx-amount-diye font-num tabular">Rs ${formatMoney(tx.amount)}</span>`
      : '<span class="text-mut">—</span>';
    const receivedHtml = !isCredit
      ? `<span class="tx-amount-liye font-num tabular">Rs ${formatMoney(tx.amount)}</span>`
      : '<span class="text-mut">—</span>';
    const runningHtml = `<span class="font-num tabular font-semibold">Rs ${formatMoney(tx.running_balance_pkr || 0)}</span>`;

    const defaultDesc = isCredit ? 'Credit / ادھار' : 'Payment / وصولی';
    const desc = escapeHtml(tx.description || defaultDesc);

    return `
      <tr class="hover:bg-s2/40 transition-colors">
        <td class="font-num text-xs text-mut whitespace-nowrap" dir="ltr">${escapeHtml(tx.tx_date || '')}</td>
        <td class="font-ur font-medium">${desc}</td>
        <td>${gaveHtml}</td>
        <td>${receivedHtml}</td>
        <td>${runningHtml}</td>
        <td>
          <button type="button" class="btn-icon-del delete-party-tx-btn" data-tx-id="${tx.id}" title="${t('btn.remove')}">
            <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>
          </button>
        </td>
      </tr>
    `;
  }).join('');

  if (window.lucide && typeof window.lucide.createIcons === 'function') {
    window.lucide.createIcons({ root: tbody });
  }

  tbody.querySelectorAll('.delete-party-tx-btn').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const txId = btn.getAttribute('data-tx-id');
      if (!txId || !state.activePartyId) return;
      try {
        const res = await authFetch(`/api/parties/${encodeURIComponent(state.userId)}/${state.activePartyId}/transactions/${txId}`, {
          method: 'DELETE'
        });
        if (res.ok) {
          showToast(t('toast.party_tx_deleted'), 'info');
          await openPartyDetail(state.activePartyId);
          fetchParties();
        }
      } catch (err) {
        console.error('Delete tx error:', err);
      }
    });
  });
}

function bindPartyEvents() {
  const backBtn = document.getElementById('party-back-btn');
  if (backBtn) {
    backBtn.addEventListener('click', () => {
      const dirView = document.getElementById('parties-directory-view');
      const detView = document.getElementById('party-detail-view');
      if (detView) detView.classList.add('hidden');
      if (dirView) dirView.classList.remove('hidden');
      state.activePartyId = null;
      fetchParties();
    });
  }

  // Filter Tabs
  document.querySelectorAll('.party-filter-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.party-filter-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      state.partyFilter = tab.getAttribute('data-party-type') || 'all';
      fetchParties();
    });
  });

  // Search
  const searchInput = document.getElementById('party-search-input');
  if (searchInput) {
    let debounceTimer;
    searchInput.addEventListener('input', (e) => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        state.partySearch = e.target.value.trim();
        fetchParties();
      }, 250);
    });
  }

  // Add Party Modal
  const openAddPartyBtn = document.getElementById('open-add-party-modal-btn');
  const addPartyModal = document.getElementById('add-party-modal');
  const closeAddPartyBtn = document.getElementById('close-add-party-modal-btn');
  const cancelAddPartyBtn = document.getElementById('cancel-add-party-btn');
  const addPartyForm = document.getElementById('add-party-form');

  if (openAddPartyBtn && addPartyModal) {
    openAddPartyBtn.addEventListener('click', () => {
      addPartyModal.classList.remove('hidden');
      const nameInp = document.getElementById('new-party-name');
      if (nameInp) {
        nameInp.value = '';
        setTimeout(() => nameInp.focus(), 40);
      }
    });
  }
  const closeAddParty = () => { if (addPartyModal) addPartyModal.classList.add('hidden'); };
  if (closeAddPartyBtn) closeAddPartyBtn.addEventListener('click', closeAddParty);
  if (cancelAddPartyBtn) cancelAddPartyBtn.addEventListener('click', closeAddParty);

  if (addPartyForm) {
    addPartyForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const name = document.getElementById('new-party-name')?.value.trim();
      const phone = document.getElementById('new-party-phone')?.value.trim();
      const partyType = document.getElementById('new-party-type')?.value;
      const openBal = parseFloat(document.getElementById('new-party-opening-balance')?.value || '0');
      const openType = document.getElementById('new-party-opening-type')?.value;
      const addr = document.getElementById('new-party-address')?.value.trim();

      if (!name) return;
      try {
        const payload = {
          name,
          phone,
          party_type: partyType,
          opening_balance: openBal,
          opening_balance_type: openType,
          address: addr,
          book_id: state.activeBookId,
        };
        const res = await authFetch('/api/parties', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        if (!res.ok) {
          const errData = await res.json().catch(() => ({}));
          throw new Error(errData.detail || 'Failed to create party');
        }
        closeAddParty();
        showToast(t('toast.party_created'), 'success');
        fetchParties();
      } catch (err) {
        showToast(err.message || 'Could not save party', 'error');
      }
    });
  }

  // Quick Action Buttons: Maine Diye / Maine Liye
  const btnDiye = document.getElementById('btn-action-diye');
  const btnLiye = document.getElementById('btn-action-liye');
  const txModal = document.getElementById('add-party-tx-modal');
  const closeTxModalBtn = document.getElementById('close-party-tx-modal-btn');
  const cancelTxModalBtn = document.getElementById('cancel-party-tx-btn');
  const txForm = document.getElementById('party-tx-form');

  const openTxModal = (type) => {
    if (!txModal) return;
    const titleEl = document.getElementById('add-party-tx-modal-title');
    const subEl = document.getElementById('party-tx-modal-sub');
    const typeInp = document.getElementById('party-tx-type');
    const iconWrap = document.getElementById('party-tx-icon-wrap');
    const dateInp = document.getElementById('party-tx-date');
    const amtInp = document.getElementById('party-tx-amount');
    const descInp = document.getElementById('party-tx-description');
    const dueInp = document.getElementById('party-tx-due-date');
    const activePartyType = (state.activePartyDetail && state.activePartyDetail.party && state.activePartyDetail.party.party_type) || 'customer';
    const isSupplier = activePartyType === 'supplier';

    if (typeInp) typeInp.value = type;
    if (amtInp) { amtInp.value = ''; setTimeout(() => amtInp.focus(), 40); }
    if (descInp) descInp.value = '';
    if (dueInp) dueInp.value = '';
    if (dateInp) dateInp.value = new Date().toISOString().substring(0, 10);

    if (type === 'credit') {
      if (titleEl) titleEl.textContent = isSupplier ? t('parties.tx_supplier_pay_title') : t('parties.tx_gave_credit_title');
      if (subEl) subEl.textContent = isSupplier ? t('parties.tx_supplier_pay_sub') : t('parties.tx_gave_credit_sub');
      if (iconWrap) {
        iconWrap.className = 'w-10 h-10 rounded-xl bg-bad/15 border border-bad/35 flex items-center justify-center text-bad shrink-0';
      }
    } else {
      if (titleEl) titleEl.textContent = isSupplier ? t('parties.tx_supplier_take_title') : t('parties.tx_received_title');
      if (subEl) subEl.textContent = isSupplier ? t('parties.tx_supplier_take_sub') : t('parties.tx_received_sub');
      if (iconWrap) {
        iconWrap.className = 'w-10 h-10 rounded-xl bg-ok/15 border border-ok/35 flex items-center justify-center text-ok shrink-0';
      }
    }
    txModal.classList.remove('hidden');
  };

  if (btnDiye) btnDiye.addEventListener('click', () => openTxModal('credit'));
  if (btnLiye) btnLiye.addEventListener('click', () => openTxModal('debit'));

  document.querySelectorAll('.party-ledger-filter-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.party-ledger-filter-tab').forEach(btn => btn.classList.remove('active'));
      tab.classList.add('active');
      state.partyLedgerFilter = tab.getAttribute('data-ledger-filter') || 'all';
      renderPartyLedgerTable((state.activePartyDetail && state.activePartyDetail.transactions) || []);
    });
  });

  const closeTxModal = () => { if (txModal) txModal.classList.add('hidden'); };
  if (closeTxModalBtn) closeTxModalBtn.addEventListener('click', closeTxModal);
  if (cancelTxModalBtn) cancelTxModalBtn.addEventListener('click', closeTxModal);

  if (txForm) {
    txForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (!state.activePartyId || !state.userId) return;
      const amt = parseFloat(document.getElementById('party-tx-amount')?.value || '0');
      const type = document.getElementById('party-tx-type')?.value || 'credit';
      const txDate = document.getElementById('party-tx-date')?.value;
      const dueDate = document.getElementById('party-tx-due-date')?.value;
      const desc = document.getElementById('party-tx-description')?.value.trim();
      const activePartyType = (state.activePartyDetail && state.activePartyDetail.party && state.activePartyDetail.party.party_type) || 'customer';
      const isSupplier = activePartyType === 'supplier';

      if (!amt || amt <= 0) return;
      try {
        const payload = {
          amount: amt,
          tx_type: type,
          tx_date: txDate,
          due_date: dueDate || null,
          description: desc,
          is_settlement: isSupplier ? (type === 'credit') : (type === 'debit'),
        };
        const res = await authFetch(`/api/parties/${encodeURIComponent(state.userId)}/${state.activePartyId}/transactions`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        if (!res.ok) {
          const errData = await res.json().catch(() => ({}));
          throw new Error(errData.detail || 'Failed to record transaction');
        }
        closeTxModal();
        showToast(t('toast.party_tx_added'), 'success');
        await openPartyDetail(state.activePartyId);
        fetchParties();
      } catch (err) {
        showToast(err.message || 'Could not record entry', 'error');
      }
    });
  }

  // WhatsApp Reminder Button
  const waBtn = document.getElementById('party-whatsapp-reminder-btn');
  if (waBtn) {
    waBtn.addEventListener('click', async () => {
      if (!state.activePartyId || !state.userId) return;
      try {
        const res = await authFetch(`/api/parties/${encodeURIComponent(state.userId)}/${state.activePartyId}/reminder?lang=${encodeURIComponent(getCurrentLang())}`);
        if (!res.ok) throw new Error('Failed to generate reminder');
        const data = await res.json();
        if (data.whatsapp_url) {
          window.open(data.whatsapp_url, '_blank');
        } else {
          showToast('No phone number registered for this party', 'warning');
        }
      } catch (err) {
        showToast(err.message || 'Could not generate reminder', 'error');
      }
    });
  }

  // Statement View Button
  const stmtBtn = document.getElementById('party-statement-btn');
  const stmtModal = document.getElementById('party-statement-modal');
  const closeStmtBtn = document.getElementById('close-statement-modal-btn');
  const stmtContent = document.getElementById('statement-content');
  const stmtWaBtn = document.getElementById('statement-whatsapp-share-btn');

  if (stmtBtn && stmtModal) {
    stmtBtn.addEventListener('click', async () => {
      if (!state.activePartyId || !state.userId) return;
      try {
        const res = await authFetch(`/api/parties/${encodeURIComponent(state.userId)}/${state.activePartyId}/statement`);
        if (!res.ok) throw new Error('Failed to load statement');
        const data = await res.json();
        const biz = data.business || {};
        const party = data.party || {};
        const txs = data.filtered_transactions || [];

        let txRows = txs.map(tx => `
          <tr class="border-b border-rule">
            <td class="py-1.5 px-2 font-num text-xs" dir="ltr">${escapeHtml(tx.tx_date || '')}</td>
            <td class="py-1.5 px-2 font-ur">${escapeHtml(tx.description || '')}</td>
            <td class="py-1.5 px-2 text-bad font-num tabular">${tx.tx_type === 'credit' ? 'Rs ' + formatMoney(tx.amount) : '—'}</td>
            <td class="py-1.5 px-2 text-ok font-num tabular">${tx.tx_type === 'debit' ? 'Rs ' + formatMoney(tx.amount) : '—'}</td>
            <td class="py-1.5 px-2 font-num tabular font-bold">Rs ${formatMoney(tx.running_balance_pkr || 0)}</td>
          </tr>
        `).join('');

        if (stmtContent) {
          stmtContent.innerHTML = `
            <div class="text-center pb-3 border-b border-rule">
              <h2 class="text-xl font-bold font-ur text-brass">${escapeHtml(biz.business_name || 'My Business')}</h2>
              <div class="text-xs text-mut font-ur">${escapeHtml(biz.address || '')} ${biz.phone_number ? '• ' + escapeHtml(biz.phone_number) : ''}</div>
              <div class="text-xs text-mut font-ur mt-1">${escapeHtml(data.statement_header || '')}</div>
            </div>
            <div class="flex justify-between items-center text-sm font-ur py-2 border-b border-rule">
              <div>
                <strong>${escapeHtml(t('parties.statement_party_label'))}</strong> ${escapeHtml(party.name || '')}
                <div class="text-xs text-mut font-num">${escapeHtml(party.phone || '')}</div>
              </div>
              <div class="text-right">
                <span class="text-xs text-mut">${escapeHtml(t('parties.statement_balance_label'))}</span>
                <div class="text-lg font-bold font-num tabular text-brass">Rs ${formatMoney(party.net_balance_pkr || 0)}</div>
              </div>
            </div>
            <table class="w-full text-right text-xs font-ur mt-2">
              <thead>
                <tr class="bg-s2 text-mut">
                  <th class="py-1.5 px-2">${escapeHtml(t('parties.statement_date'))}</th>
                  <th class="py-1.5 px-2">${escapeHtml(t('parties.statement_desc'))}</th>
                  <th class="py-1.5 px-2">${escapeHtml(t('parties.gave_col'))}</th>
                  <th class="py-1.5 px-2">${escapeHtml(t('parties.received_col'))}</th>
                  <th class="py-1.5 px-2">${escapeHtml(t('parties.statement_balance'))}</th>
                </tr>
              </thead>
              <tbody>${txRows || `<tr><td colspan="5" class="text-center py-4 text-mut">${escapeHtml(t('parties.statement_no_txs'))}</td></tr>`}</tbody>
            </table>
            <div class="text-center text-xs text-mut font-ur pt-3 border-t border-rule">
              ${escapeHtml(biz.statement_footer || data.statement_footer || t('parties.statement_default_footer'))}
            </div>
          `;
        }

        stmtModal.classList.remove('hidden');

        if (stmtWaBtn) {
          stmtWaBtn.onclick = async () => {
            const remRes = await authFetch(`/api/parties/${encodeURIComponent(state.userId)}/${state.activePartyId}/reminder?lang=${encodeURIComponent(getCurrentLang())}`);
            if (remRes.ok) {
              const remData = await remRes.json();
              if (remData.whatsapp_url) window.open(remData.whatsapp_url, '_blank');
            }
          };
        }
      } catch (err) {
        showToast(err.message || 'Could not load statement', 'error');
      }
    });
  }

  const closeStmtModal = () => { if (stmtModal) stmtModal.classList.add('hidden'); };
  if (closeStmtBtn && stmtModal) {
    closeStmtBtn.addEventListener('click', closeStmtModal);
  }

  const stmtPrintBtn = document.getElementById('statement-print-btn');
  if (stmtPrintBtn) {
    stmtPrintBtn.addEventListener('click', () => {
      window.print();
    });
  }

  // Business Profile Settings Modal
  const openBizBtn = document.getElementById('open-business-profile-btn');
  const bizModal = document.getElementById('business-profile-modal');
  const closeBizBtn = document.getElementById('close-biz-modal-btn');
  const cancelBizBtn = document.getElementById('cancel-biz-btn');
  const bizForm = document.getElementById('business-profile-form');

  if (openBizBtn && bizModal) {
    openBizBtn.addEventListener('click', async () => {
      if (!state.userId) return;
      try {
        const res = await authFetch(`/api/business-profile/${encodeURIComponent(state.userId)}`);
        if (res.ok) {
          const data = await res.json();
          const p = data.profile || {};
          const bName = document.getElementById('biz-business-name');
          const oName = document.getElementById('biz-owner-name');
          const pPhone = document.getElementById('biz-phone-number');
          const pRaast = document.getElementById('biz-raast-id');
          const pAddr = document.getElementById('biz-address');
          const pFoot = document.getElementById('biz-statement-footer');
          if (bName) bName.value = p.business_name || '';
          if (oName) oName.value = p.owner_name || '';
          if (pPhone) pPhone.value = p.phone_number || '';
          if (pRaast) pRaast.value = p.raast_id || p.easypaisa_number || '';
          if (pAddr) pAddr.value = p.address || '';
          if (pFoot) pFoot.value = p.statement_footer || '';
        }
      } catch (err) {
        console.error('Error fetching biz profile:', err);
      }
      bizModal.classList.remove('hidden');
    });
  }

  const closeBizModal = () => { if (bizModal) bizModal.classList.add('hidden'); };
  if (closeBizBtn) closeBizBtn.addEventListener('click', closeBizModal);
  if (cancelBizBtn) cancelBizBtn.addEventListener('click', closeBizModal);

  [
    [addPartyModal, closeAddParty],
    [txModal, closeTxModal],
    [stmtModal, closeStmtModal],
    [bizModal, closeBizModal],
  ].forEach(([mEl, closeFn]) => {
    if (mEl) {
      mEl.addEventListener('click', (e) => {
        if (e.target === mEl) closeFn();
      });
    }
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' || e.key === 'Esc' || e.keyCode === 27) {
      closeAddParty();
      closeTxModal();
      closeStmtModal();
      closeBizModal();
    }
  });

  if (bizForm) {
    bizForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (!state.userId) return;
      try {
        const payload = {
          business_name: document.getElementById('biz-business-name')?.value.trim(),
          owner_name: document.getElementById('biz-owner-name')?.value.trim(),
          phone_number: document.getElementById('biz-phone-number')?.value.trim(),
          raast_id: document.getElementById('biz-raast-id')?.value.trim(),
          address: document.getElementById('biz-address')?.value.trim(),
          statement_footer: document.getElementById('biz-statement-footer')?.value.trim(),
        };
        const res = await authFetch(`/api/business-profile/${encodeURIComponent(state.userId)}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        if (!res.ok) throw new Error('Failed to update business profile');
        closeBizModal();
        showToast(t('toast.biz_updated'), 'success');
      } catch (err) {
        showToast(err.message || 'Could not save business profile', 'error');
      }
    });
  }
}

// Initialize application when DOM is ready
document.addEventListener('DOMContentLoaded', initApp);
