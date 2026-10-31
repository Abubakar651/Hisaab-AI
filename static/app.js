tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          fontFamily: {
            nastaleeq: ["'Jameel Noori Nastaleeq'", "'Noori Nastaleeq'", "'Noto Nastaliq Urdu'", "'Urdu Typesetting'", "serif"],
          },
          colors: {
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
      userId: localStorage.getItem('hisaab_user_id') || 'guest',
      token: localStorage.getItem('hisaab_session_token') || '',
      isRecording: false,
      mediaRecorder: null,
      audioChunks: [],
      recordingTimerInterval: null,
      recordingSeconds: 0,
      activeTab: 'chat', // 'chat' or 'dashboard' for mobile
      chatHistory: [],   // Multi-turn conversation memory
    };

    // Authentication & Fetch Interceptor
    function getAuthToken() {
      return localStorage.getItem('hisaab_session_token') || '';
    }

    function setAuthToken(token) {
      if (token) {
        localStorage.setItem('hisaab_session_token', token);
        state.token = token;
      } else {
        localStorage.removeItem('hisaab_session_token');
        state.token = '';
      }
    }

    async function authFetch(url, options = {}) {
      const token = getAuthToken();
      const opts = { ...options };
      opts.headers = { ...(opts.headers || {}) };
      if (token) {
        opts.headers['Authorization'] = `Bearer ${token}`;
      }
      const res = await fetch(url, opts);
      if (res.status === 401 && !url.includes('/api/auth/login') && !url.includes('/api/auth/password-login') && !url.includes('/api/auth/register')) {
        console.warn('Authentication token expired or missing, prompting login modal...');
        setAuthToken('');
        const authModal = document.getElementById('auth-modal');
        if (authModal) authModal.classList.remove('hidden');
        showToast('Session expired or required. Please sign in.', 'warning');
      }
      return res;
    }

    // DOM Elements
    const elements = {};

    function initElements() {
      elements.userIdInput = document.getElementById('user-id-input');
      elements.switchUserBtn = document.getElementById('switch-user-btn');
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

    // Initialize UI
    function initApp() {
      initElements();
      elements.userIdInput.value = state.userId;
      elements.activeUserDisplay.textContent = state.userId;

      // Auto-resize textarea and auto-detect Nastaleeq script
      elements.messageInput.addEventListener('input', function () {
        this.style.height = 'auto';
        this.style.height = Math.min(this.scrollHeight, 140) + 'px';
        syncInputScriptDirection();
      });

      // Enter key sends message (Shift+Enter for new line)
      elements.messageInput.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          elements.chatForm.dispatchEvent(new Event('submit'));
        }
      });

      bindEvents();
      bindAnalyticsForms();
      bindAuthEvents();
      initAuth();
      fetchDashboard();
      lucide.createIcons();
    }

    // Bind Event Listeners
    function bindEvents() {
      // User switcher (legacy support)
      if (elements.switchUserBtn && elements.userIdInput) {
        elements.switchUserBtn.addEventListener('click', () => switchUser(elements.userIdInput.value));
        elements.userIdInput.addEventListener('keydown', (e) => {
          if (e.key === 'Enter') switchUser(elements.userIdInput.value);
        });
      }

      // Quick User Chips
      document.querySelectorAll('.user-chip').forEach(chip => {
        chip.addEventListener('click', () => {
          const u = chip.getAttribute('data-user');
          if (elements.userIdInput) elements.userIdInput.value = u;
          switchUser(u);
        });
      });

      // Prompt Suggestion Chips
      document.querySelectorAll('.prompt-chip').forEach(chip => {
        chip.addEventListener('click', () => {
          const prompt = chip.getAttribute('data-prompt');
          elements.messageInput.value = prompt;
          elements.chatForm.dispatchEvent(new Event('submit'));
        });
      });

      // Chat Submit
      elements.chatForm.addEventListener('submit', handleTextMessageSubmit);

      // Microphone Recording Toggle
      elements.micBtn.addEventListener('click', toggleRecording);
      elements.cancelRecordingBtn.addEventListener('click', cancelRecording);

      // Clear Chat
      elements.clearChatBtn.addEventListener('click', async () => {
        elements.messagesContainer.innerHTML = '';
        state.chatHistory = [];
        const fd = new FormData();
        fd.append('user_id', state.userId);
        authFetch('/api/chat/clear', { method: 'POST', body: fd }).catch(() => { });
        showToast('Chat history cleared from view', 'info');
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
        showToast('Ledger refreshed', 'info');
      });

      // Export CSV with Authorization
      const exportBtn = document.getElementById('export-csv-btn');
      if (exportBtn) {
        exportBtn.addEventListener('click', async (e) => {
          e.preventDefault();
          try {
            const res = await authFetch(`/api/export/${encodeURIComponent(state.userId)}`);
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
            showToast('CSV export downloaded', 'success');
          } catch (err) {
            showToast(err.message, 'error');
          }
        });
      }

      // Mobile Tabs
      elements.tabChat.addEventListener('click', () => setMobileTab('chat'));
      elements.tabDashboard.addEventListener('click', () => setMobileTab('dashboard'));

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

      if (elements.btnViewChat) elements.btnViewChat.addEventListener('click', () => setDesktopView('chat'));
      if (elements.btnViewSplit) elements.btnViewSplit.addEventListener('click', () => setDesktopView('split'));
      if (elements.btnViewLedger) elements.btnViewLedger.addEventListener('click', () => setDesktopView('ledger'));
    }

    // Update Account UI in Header
    function updateAccountUI(account) {
      if (!account) return;
      state.account = account;
      state.userId = account.user_id;
      localStorage.setItem('hisaab_user_id', account.user_id);
      try { localStorage.setItem('hisaab_user_account', JSON.stringify(account)); } catch (e) { }

      const nameEl = document.getElementById('user-display-name');
      const badgeEl = document.getElementById('user-provider-badge');
      const dropNameEl = document.getElementById('dropdown-name');
      const dropSubEl = document.getElementById('dropdown-sub');
      const avatarBox = document.getElementById('user-avatar');

      const isGoogle = account.auth_provider === 'google';
      const isPhone = account.auth_provider === 'phone';
      const label = isGoogle ? 'Google Account' : (isPhone ? 'Phone Number' : 'Guest Profile');

      if (nameEl) nameEl.textContent = account.display_name || account.user_id;
      if (badgeEl) badgeEl.textContent = label;
      if (dropNameEl) dropNameEl.textContent = account.display_name || account.user_id;
      if (dropSubEl) dropSubEl.textContent = `${label} • ${account.email || account.phone_number || account.user_id}`;

      if (avatarBox) {
        if (account.photo_url && /^https:\/\//i.test(account.photo_url)) {
          avatarBox.innerHTML = `<img src="${escapeHtml(account.photo_url)}" alt="avatar" class="w-full h-full object-cover rounded-full" />`;
        } else {
          const letter = (account.display_name || account.user_id || 'U').charAt(0).toUpperCase();
          avatarBox.innerHTML = `<span id="avatar-letter">${escapeHtml(letter)}</span>`;
        }
      }

      if (elements.userIdInput) elements.userIdInput.value = account.user_id;
      if (elements.activeUserDisplay) elements.activeUserDisplay.textContent = account.user_id;
    }

    // Switch User Logic
    function switchUser(newUserId) {
      const clean = (newUserId || '').trim() || 'default';
      state.userId = clean;
      state.chatHistory = [];
      localStorage.setItem('hisaab_user_id', clean);
      if (elements.userIdInput) elements.userIdInput.value = clean;
      if (elements.activeUserDisplay) elements.activeUserDisplay.textContent = clean;
      updateAccountUI({ user_id: clean, auth_provider: 'guest', display_name: clean });

      showToast(`Switched account to: ${clean}`, 'success');
      appendSystemMessage(`Active user switched to **${clean}**. All transactions and queries are isolated to this account.`);
      fetchDashboard();
    }

    // Initialize Authentication State & Firebase
    async function initAuth() {
      let token = getAuthToken();
      if (!token) {
        // Auto-provision secure guest session on first visit
        try {
          const res = await fetch('/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ provider: 'guest' }),
          });
          if (res.ok) {
            const data = await res.json();
            setAuthToken(data.token);
            updateAccountUI(data.user);
          }
        } catch (e) { }
      } else {
        const uid = state.userId || 'guest';
        try {
          const res = await authFetch(`/api/auth/profile/${encodeURIComponent(uid)}`);
          if (res.ok) {
            const data = await res.json();
            updateAccountUI(data.user);
          } else {
            const gRes = await fetch('/api/auth/login', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ provider: 'guest' }),
            });
            if (gRes.ok) {
              const gdata = await gRes.json();
              setAuthToken(gdata.token);
              updateAccountUI(gdata.user);
            }
          }
        } catch (e) { }
      }

      // Check Firebase SDK configuration
      try {
        const cfgRes = await fetch('/api/auth/config');
        const cfgData = await cfgRes.json();
        if (cfgData.configured && window.firebase && !firebase.apps.length) {
          firebase.initializeApp(cfgData.firebase);
          window.firebaseAuth = firebase.auth();
          const notice = document.getElementById('google-config-notice');
          if (notice) notice.textContent = 'Firebase connected. Click to sign in.';
        }
      } catch (err) {
        console.log('Firebase config note:', err);
      }
    }

    // Bind Auth Modal and Events
    function bindAuthEvents() {
      const accountBtn = document.getElementById('account-btn');
      const accountDropdown = document.getElementById('account-dropdown');
      const authModal = document.getElementById('auth-modal');
      const openAuthModalBtn = document.getElementById('open-auth-modal-btn');
      const closeAuthModalBtn = document.getElementById('close-auth-modal-btn');
      const signoutBtn = document.getElementById('signout-btn');

      if (accountBtn && accountDropdown) {
        accountBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          accountDropdown.classList.toggle('hidden');
        });
        document.addEventListener('click', (e) => {
          if (!accountDropdown.contains(e.target) && !accountBtn.contains(e.target)) {
            accountDropdown.classList.add('hidden');
          }
        });
      }

      if (openAuthModalBtn) {
        openAuthModalBtn.addEventListener('click', () => {
          if (accountDropdown) accountDropdown.classList.add('hidden');
          if (authModal) authModal.classList.remove('hidden');
        });
      }
      if (closeAuthModalBtn) {
        closeAuthModalBtn.addEventListener('click', () => {
          if (authModal) authModal.classList.add('hidden');
        });
      }

      // Tabs
      const tabPhone = document.getElementById('auth-tab-phone');
      const tabGoogle = document.getElementById('auth-tab-google');
      const tabEmail = document.getElementById('auth-tab-email');
      const tabGuest = document.getElementById('auth-tab-guest');
      const panelPhone = document.getElementById('auth-panel-phone');
      const panelGoogle = document.getElementById('auth-panel-google');
      const panelEmail = document.getElementById('auth-panel-email');
      const panelGuest = document.getElementById('auth-panel-guest');

      function setAuthTab(tab) {
        [tabPhone, tabGoogle, tabEmail, tabGuest].forEach(t => {
          if (t) t.className = 'pb-2 text-xs font-semibold text-slate-400 hover:text-slate-200 flex items-center gap-1.5 transition-colors whitespace-nowrap';
        });
        [panelPhone, panelGoogle, panelEmail, panelGuest].forEach(p => p && p.classList.add('hidden'));

        if (tab === 'phone') {
          if (tabPhone) tabPhone.className = 'pb-2 text-xs font-semibold text-emerald-400 border-b-2 border-emerald-500 flex items-center gap-1.5 transition-colors whitespace-nowrap';
          if (panelPhone) panelPhone.classList.remove('hidden');
        } else if (tab === 'google') {
          if (tabGoogle) tabGoogle.className = 'pb-2 text-xs font-semibold text-emerald-400 border-b-2 border-emerald-500 flex items-center gap-1.5 transition-colors whitespace-nowrap';
          if (panelGoogle) panelGoogle.classList.remove('hidden');
        } else if (tab === 'email') {
          if (tabEmail) tabEmail.className = 'pb-2 text-xs font-semibold text-emerald-400 border-b-2 border-emerald-500 flex items-center gap-1.5 transition-colors whitespace-nowrap';
          if (panelEmail) panelEmail.classList.remove('hidden');
        } else if (tab === 'guest') {
          if (tabGuest) tabGuest.className = 'pb-2 text-xs font-semibold text-emerald-400 border-b-2 border-emerald-500 flex items-center gap-1.5 transition-colors whitespace-nowrap';
          if (panelGuest) panelGuest.classList.remove('hidden');
        }
      }

      if (tabPhone) tabPhone.addEventListener('click', () => setAuthTab('phone'));
      if (tabGoogle) tabGoogle.addEventListener('click', () => setAuthTab('google'));
      if (tabEmail) tabEmail.addEventListener('click', () => setAuthTab('email'));
      if (tabGuest) tabGuest.addEventListener('click', () => setAuthTab('guest'));

      // Email / Password: Mode Toggle (Sign In vs Create Account)
      let emailMode = 'login'; // 'login' or 'register'
      const modeLoginBtn = document.getElementById('email-mode-login');
      const modeRegisterBtn = document.getElementById('email-mode-register');
      const registerFields = document.getElementById('email-register-fields');
      const emailSubmitBtn = document.getElementById('email-submit-btn');
      const emailSubmitText = document.getElementById('email-submit-text');
      const nameInput = document.getElementById('email-name-input');
      const identInput = document.getElementById('email-ident-input');
      const pwdInput = document.getElementById('email-password-input');

      function setEmailMode(mode) {
        emailMode = mode;
        if (mode === 'login') {
          if (modeLoginBtn) modeLoginBtn.className = 'flex-1 py-1.5 text-xs font-semibold rounded-lg bg-emerald-600 text-white transition-colors';
          if (modeRegisterBtn) modeRegisterBtn.className = 'flex-1 py-1.5 text-xs font-semibold rounded-lg text-slate-400 hover:text-white transition-colors';
          if (registerFields) registerFields.classList.add('hidden');
          if (emailSubmitText) emailSubmitText.textContent = 'Sign In';
        } else {
          if (modeRegisterBtn) modeRegisterBtn.className = 'flex-1 py-1.5 text-xs font-semibold rounded-lg bg-emerald-600 text-white transition-colors';
          if (modeLoginBtn) modeLoginBtn.className = 'flex-1 py-1.5 text-xs font-semibold rounded-lg text-slate-400 hover:text-white transition-colors';
          if (registerFields) registerFields.classList.remove('hidden');
          if (emailSubmitText) emailSubmitText.textContent = 'Create Account';
        }
      }

      if (modeLoginBtn) modeLoginBtn.addEventListener('click', () => setEmailMode('login'));
      if (modeRegisterBtn) modeRegisterBtn.addEventListener('click', () => setEmailMode('register'));

      if (emailSubmitBtn) {
        emailSubmitBtn.addEventListener('click', async () => {
          const ident = (identInput.value || '').trim();
          const pwd = (pwdInput.value || '').trim();
          const name = (nameInput.value || '').trim();

          if (!ident) {
            showToast('Please enter your email or phone number', 'error');
            return;
          }
          if (pwd.length < 6) {
            showToast('Password must be at least 6 characters', 'error');
            return;
          }

          if (emailMode === 'register') {
            if (!name) {
              showToast('Please enter your full name', 'error');
              return;
            }
            try {
              const res = await fetch('/api/auth/register', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: name, email_or_phone: ident, password: pwd }),
              });
              const data = await res.json();
              if (!res.ok) throw new Error(data.detail || 'Registration failed');
              if (data.token) setAuthToken(data.token);
              updateAccountUI(data.user);
              if (authModal) authModal.classList.add('hidden');
              showToast(`Account created! Welcome, ${data.user.display_name}`, 'success');
              fetchDashboard();
            } catch (err) {
              showToast(err.message, 'error');
            }
          } else {
            try {
              const res = await fetch('/api/auth/password-login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email_or_phone: ident, password: pwd }),
              });
              const data = await res.json();
              if (!res.ok) throw new Error(data.detail || 'Login failed');
              if (data.token) setAuthToken(data.token);
              updateAccountUI(data.user);
              if (authModal) authModal.classList.add('hidden');
              showToast(`Welcome back, ${data.user.display_name}`, 'success');
              fetchDashboard();
            } catch (err) {
              showToast(err.message, 'error');
            }
          }
        });
      }

      // Google Sign-In
      const googleBtn = document.getElementById('google-signin-btn');
      if (googleBtn) {
        googleBtn.addEventListener('click', async () => {
          if (window.firebaseAuth) {
            try {
              const provider = new firebase.auth.GoogleAuthProvider();
              const result = await firebase.auth().signInWithPopup(provider);
              const u = result.user;
              const idToken = await u.getIdToken();
              const res = await fetch('/api/auth/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                  provider: 'google',
                  email: u.email,
                  display_name: u.displayName || u.email.split('@')[0],
                  photo_url: u.photoURL,
                  id_token: idToken,
                }),
              });
              const data = await res.json();
              if (!res.ok) throw new Error(data.detail || 'Google login failed');
              if (data.token) setAuthToken(data.token);
              updateAccountUI(data.user);
              if (authModal) authModal.classList.add('hidden');
              showToast(`Signed in as ${data.user.display_name}`, 'success');
              fetchDashboard();
            } catch (err) {
              showToast(`Google Sign-In: ${err.message}`, 'error');
            }
          } else {
            showToast('Firebase is not configured for Google Sign-In. Use Email/Password.', 'warning');
          }
        });
      }

      // Phone Sign-In
      const sendPhoneBtn = document.getElementById('send-phone-otp-btn');
      const verifyPhoneBtn = document.getElementById('verify-phone-otp-btn');
      const phoneInput = document.getElementById('phone-number-input');
      const otpInput = document.getElementById('phone-otp-input');
      const phoneStep = document.getElementById('phone-input-step');
      const otpStep = document.getElementById('phone-otp-step');

      if (sendPhoneBtn) {
        sendPhoneBtn.addEventListener('click', async () => {
          let raw = (phoneInput.value || '').trim().replace(/\D/g, '');
          if (raw.startsWith('92')) raw = raw.slice(2);
          if (raw.startsWith('0')) raw = raw.slice(1);
          if (!raw || raw.length < 10) {
            showToast('Enter valid Pakistani phone (e.g. 300 1234567)', 'error');
            return;
          }
          const formatted = '+92' + raw;

          if (window.firebaseAuth) {
            try {
              if (!window.recaptchaVerifier) {
                window.recaptchaVerifier = new firebase.auth.RecaptchaVerifier('recaptcha-container', { size: 'invisible' });
              }
              const conf = await firebase.auth().signInWithPhoneNumber(formatted, window.recaptchaVerifier);
              window.phoneConfirmation = conf;
              if (phoneStep) phoneStep.classList.add('hidden');
              if (otpStep) otpStep.classList.remove('hidden');
              showToast(`Verification code sent to ${formatted}`, 'success');
            } catch (err) {
              showToast(`SMS gateway error: ${err.message}`, 'error');
            }
          } else {
            completePhoneLogin(formatted);
          }
        });
      }

      if (verifyPhoneBtn) {
        verifyPhoneBtn.addEventListener('click', async () => {
          const code = (otpInput.value || '').trim();
          if (!code || code.length < 6) {
            showToast('Please enter the 6-digit code', 'error');
            return;
          }
          if (window.phoneConfirmation) {
            try {
              const res = await window.phoneConfirmation.confirm(code);
              const idToken = await res.user.getIdToken();
              completePhoneLogin(res.user.phoneNumber, idToken);
            } catch (err) {
              showToast('Invalid verification code: ' + err.message, 'error');
            }
          } else {
            completePhoneLogin('+92' + phoneInput.value.replace(/\D/g, ''));
          }
        });
      }

      async function completePhoneLogin(phoneNum, idToken = null) {
        try {
          const res = await fetch('/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              provider: 'phone',
              phone_number: phoneNum,
              display_name: phoneNum,
              id_token: idToken,
            }),
          });
          const data = await res.json();
          if (!res.ok) throw new Error(data.detail || 'Phone login failed');
          if (data.token) setAuthToken(data.token);
          updateAccountUI(data.user);
          if (authModal) authModal.classList.add('hidden');
          if (phoneStep) phoneStep.classList.remove('hidden');
          if (otpStep) otpStep.classList.add('hidden');
          showToast(`Signed in with phone ${data.user.phone_number}!`, 'success');
          fetchDashboard();
        } catch (err) {
          showToast(err.message, 'error');
        }
      }

      // Guest Session Provisioning
      const confirmGuestBtn = document.getElementById('confirm-guest-btn');
      if (confirmGuestBtn) {
        confirmGuestBtn.addEventListener('click', async () => {
          try {
            const res = await fetch('/api/auth/login', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ provider: 'guest' }),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || 'Guest session error');
            if (data.token) setAuthToken(data.token);
            updateAccountUI(data.user);
            if (authModal) authModal.classList.add('hidden');
            showToast(`Started private guest session (${data.user.user_id})`, 'success');
            fetchDashboard();
          } catch (err) {
            showToast(err.message, 'error');
          }
        });
      }

      // Sign out / switch to guest
      if (signoutBtn) {
        signoutBtn.addEventListener('click', async () => {
          if (accountDropdown) accountDropdown.classList.add('hidden');
          if (window.firebaseAuth) {
            try { await firebase.auth().signOut(); } catch (e) { }
          }
          setAuthToken('');
          try {
            const res = await fetch('/api/auth/login', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ provider: 'guest' }),
            });
            if (res.ok) {
              const data = await res.json();
              if (data.token) setAuthToken(data.token);
              updateAccountUI(data.user);
            }
          } catch (e) { }
          showToast('Signed out. Active in private guest mode.', 'info');
          fetchDashboard();
        });
      }
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
      elements.messageInput.style.height = 'auto';
      syncInputScriptDirection();

      // Send to API
      await sendChatRequest({ message });
    }

    async function sendChatRequest({ message = null, audioBlob = null }) {
      const loadingBubble = appendLoadingBubble();
      elements.sendBtn.disabled = true;

      const formData = new FormData();
      formData.append('user_id', state.userId);
      if (message) formData.append('message', message);
      if (audioBlob) formData.append('audio', audioBlob, 'voice_recording.webm');
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
        state.chatHistory.push({ role: 'assistant', content: data.reply });
        if (state.chatHistory.length > 20) {
          state.chatHistory = state.chatHistory.slice(-20);
        }

        // Render AI Reply
        appendAssistantMessage(data.reply);

        // Auto-refresh ledger dashboard after any action
        fetchDashboard();
        await checkAndRenderPendingConfirmation();
      } catch (err) {
        removeLoadingBubble(loadingBubble);
        appendErrorMessage(err.message || 'Error occurred while contacting server.');
        showToast(err.message, 'error');
      } finally {
        elements.sendBtn.disabled = false;
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
            const origPlaceholder = "اردو، پنجابی یا انگریزی میں بولیں یا لکھیں...";
            elements.messageInput.placeholder = "آواز تحریر میں تبدیل ہو رہی ہے...";
            elements.messageInput.disabled = true;
            showToast("آواز تحریر میں تبدیل ہو رہی ہے...", "info");

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
                elements.messageInput.style.height = 'auto';
                elements.messageInput.style.height = Math.min(elements.messageInput.scrollHeight, 140) + 'px';
                syncInputScriptDirection();
                state.pendingVoiceNote = true;
                showToast("🎤 آواز چیٹ باکس میں تحریر ہو گئی! جائزہ لیں اور Send دبائیں۔", "success");
              } else {
                showToast("کوئی آواز ریکارڈ نہیں ہوئی۔ مائیک کے قریب بولیں۔", "warning");
              }
            } catch (err) {
              console.error("Transcription error:", err);
              showToast(err.message || "آواز تحریر کرنے میں مسئلہ پیش آیا۔", "error");
            } finally {
              elements.messageInput.disabled = false;
              elements.messageInput.placeholder = origPlaceholder;
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
        elements.messageInput.placeholder = "🎙️ Listening... Speak your expense in Urdu, Punjabi or English";
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
      showToast('Recording cancelled', 'info');
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
      const dirAttr = isUrdu ? 'dir="rtl"' : 'dir="ltr"';

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
      const dirAttr = isUrdu ? 'dir="rtl"' : 'dir="ltr"';
      const parsedHtml = DOMPurify.sanitize(marked.parse(markdownContent || ''));

      wrapper.innerHTML = `
        <div class="flex items-center gap-1.5 mb-1.5 text-brass">
          <i data-lucide="bot" class="w-3.5 h-3.5"></i>
          <span class="text-xs font-semibold">HisaabAI</span>
        </div>
        <div class="${nastaleeqClass} text-tx prose prose-invert max-w-none" ${dirAttr}>
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
      wrapper.innerHTML = `
        <div class="flex items-center gap-2 text-xs text-mut font-ur">
          <i data-lucide="loader-2" class="w-4 h-4 animate-spin text-brass"></i>
          <span>حساب کتاب دیکھا جا رہا ہے...</span>
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
      wrapper.innerHTML = `
        <div class="flex items-center gap-2 text-xs font-semibold text-bad mb-1">
          <i data-lucide="alert-circle" class="w-4 h-4"></i> Processing Issue
        </div>
        <p class="text-xs sm:text-sm text-tx">${escapeHtml(errorText)}</p>
      `;
      elements.messagesContainer.appendChild(wrapper);
      scrollToBottom();
      lucide.createIcons();
    }

    function scrollToBottom() {
      elements.messagesContainer.scrollTop = elements.messagesContainer.scrollHeight;
    }

    async function checkAndRenderPendingConfirmation() {
      try {
        const res = await authFetch('/api/transactions/pending');
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
            <p class="font-ur text-sm" style="color:var(--mut);margin-top:4px;line-height:2">ابھی تک کچھ محفوظ نہیں ہوا۔ کیا آپ یہ خرچہ درج کرنا چاہتے ہیں؟${p.store_name ? ` (${escapeHtml(p.store_name)} · ${escapeHtml(p.payment_method)})` : ''}</p>
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
              showToast(resData.message || 'Expense confirmed', 'success');
              fetchDashboard();
            } else {
              if (resp.status === 409) {
                appendAssistantMessage(`⚠️ یہ خرچہ پہلے ہی تصدیق، کینسل یا ختم ہو چکا ہے: **PKR ${formatMoney(p.total_amount)}** (${escapeHtml(p.store_name)})۔`);
                showToast(resData.detail || 'Already confirmed, cancelled, or expired.', 'warning');
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
              showToast('Expense cancelled', 'info');
            } else {
              if (resp.status === 409) {
                showToast(resData.detail || 'Already confirmed, cancelled, or expired.', 'warning');
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
      try {
        const response = await authFetch(`/api/transactions/${encodeURIComponent(state.userId)}`);
        if (!response.ok) throw new Error('Failed to load user ledger');

        const data = await response.json();
        renderDashboard(data);
        await checkAndRenderPendingConfirmation();
      } catch (err) {
        console.error('Error fetching dashboard:', err);
      }
    }

    function renderDashboard(data) {
      // 1. KPI Cards / Hero
      elements.kpiMonthlyTotal.textContent = `${formatMoney(data.monthly_total || 0)}`;
      elements.kpiVisitCount.textContent = data.monthly_count || 0;
      elements.kpiTopStore.textContent = data.top_store || 'درج نہیں';

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

      // Analytics sections (budgets, charts, payment split, recurring bills)
      renderBudgets(data.budgets || []);
      renderCategoryChart(data.category_breakdown || []);
      renderTrendChart(data.daily_trend || []);
      renderPaymentSplit(data.payment_split || []);
      renderRecurring(data.recurring || []);
      document.getElementById('export-csv-btn').href = `/api/export/${encodeURIComponent(state.userId)}`;

      // 2. Store Breakdown
      elements.storeBreakdownContainer.innerHTML = '';
      if (data.store_breakdown && data.store_breakdown.length > 0) {
        data.store_breakdown.forEach(item => {
          const pill = document.createElement('div');
          pill.className = 'prompt-chip text-xs';
          pill.setAttribute('data-store', item.store_name);
          pill.innerHTML = `
            <span class="font-medium text-tx">${escapeHtml(item.store_name)}</span>
            <span class="text-[11px] text-brass font-bold font-num tabular ml-1.5">PKR ${formatMoney(item.total_pkr)}</span>
            <span class="text-[10px] text-mut ml-1">(${item.visit_count})</span>
          `;
          elements.storeBreakdownContainer.appendChild(pill);
        });
      } else {
        elements.storeBreakdownContainer.innerHTML = `<span class="text-xs text-mut italic font-ur">ابھی تک کوئی دکان ریکارڈ میں نہیں۔</span>`;
      }

      // 3. Transactions List
      const txs = data.recent_transactions || [];
      if (txs.length === 0) {
        elements.txCountBadge.textContent = 'اس مہینے کا کوئی خرچہ درج نہیں ہے';
      } else {
        elements.txCountBadge.textContent = `${txs.length} ریکارڈز`;
      }
      elements.transactionsList.innerHTML = '';

      if (txs.length === 0) {
        elements.transactionsList.innerHTML = `
          <div class="text-center py-10 text-mut text-xs font-ur">
            <i data-lucide="inbox" class="w-8 h-8 mx-auto mb-2 opacity-50"></i>
            ابھی تک کوئی خرچہ ریکارڈ نہیں ہوا۔<br>چیٹ میں اپنا پہلا خرچہ بولیں یا لکھیں!
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
            <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-s2 border border-rule text-[11px] text-mut">
              <span class="text-tx font-medium">${escapeHtml(it.item_name)}</span>
              ${it.quantity > 1 ? `<span class="text-mut">×${it.quantity}</span>` : ''}
              <span class="text-brass font-num tabular">Rs ${formatMoney(it.price)}</span>
            </span>
          `).join('');
          itemsHtml = `<div class="flex flex-wrap gap-1 mt-1.5">${itemsBadges}</div>`;
        }

        card.innerHTML = `
          <div class="tx-row-top">
            <div class="tx-store-name capitalize">${escapeHtml(tx.store_name)}</div>
            <div class="tx-row-amount">
              <span class="text-brass font-bold">PKR ${formatMoney(tx.total_amount)}</span>
              <button 
                class="delete-tx-btn btn-icon-del"
                data-id="${tx.id}"
                data-store="${escapeHtml(tx.store_name)}"
                title="Delete Transaction"
              >
                <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>
              </button>
            </div>
          </div>
          <div class="tx-row-sub">
            <span>${escapeHtml(tx.transaction_date || '')}</span>
            <span class="badge-pill-method">${escapeHtml((tx.payment_method || 'cash').replace('_', ' '))}</span>
            <span class="text-mut font-num tabular">#${tx.id}</span>
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

        showToast(`Transaction #${transactionId} deleted`, 'success');
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

    function renderBudgets(budgets) {
      const box = document.getElementById('budgets-container');
      if (!budgets.length) {
        box.innerHTML = `<span class="text-xs text-mut italic font-ur">کوئی بجٹ مقرر نہیں ہے۔ نیچے شامل کریں یا چیٹ میں لکھیں "میرا ماہانہ بجٹ 50000 ہے"۔</span>`;
        return;
      }
      box.innerHTML = '';
      budgets.forEach(b => {
        const pct = Math.min(b.percent, 100);
        const barColor = b.level === 'over' ? 'var(--bad)' : b.level === 'warning' ? 'var(--warn)' : 'var(--ok)';
        const txtColor = b.level === 'over' ? 'var(--bad)' : b.level === 'warning' ? 'var(--warn)' : 'var(--ok)';
        const label = b.category === 'total' ? 'کل بجٹ (Overall)' : b.category;
        const row = document.createElement('div');
        row.className = 'budget-item-box';
        row.innerHTML = `
          <div class="budget-item-header">
            <span class="font-medium text-tx capitalize font-ur">${escapeHtml(label)}</span>
            <span class="flex items-center gap-2">
              <span class="font-num tabular" style="color:${txtColor}">PKR ${formatMoney(b.spent_pkr)} / ${formatMoney(b.monthly_limit)} (${b.percent}%)</span>
              <button class="delete-budget-btn btn-icon-del" data-id="${b.id}" title="Remove budget">
                <i data-lucide="x" class="w-3.5 h-3.5"></i>
              </button>
            </span>
          </div>
          <div class="budget-track-bg">
            <div class="budget-fill-bar" style="width:${pct}%; background-color:${barColor};"></div>
          </div>
          ${b.level === 'over'
            ? `<p class="text-xs mt-1 font-ur tabular" style="color:var(--bad)">بجٹ سے PKR ${formatMoney(Math.abs(b.remaining_pkr))} زیادہ خرچ ہو گئے</p>`
            : b.level === 'warning'
              ? `<p class="text-xs mt-1 font-ur tabular" style="color:var(--warn)">اس مہینے صرف PKR ${formatMoney(b.remaining_pkr)} باقی ہیں</p>`
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
          return `
            <div class="legend-row">
              <div class="legend-label-group">
                <span class="legend-color-box" style="background-color: ${color}"></span>
                <span class="legend-cat-name capitalize text-tx">${escapeHtml(r.category)}</span>
              </div>
              <span class="legend-amount tabular font-num text-mut">PKR ${formatMoney(r.total_pkr)}</span>
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
        box.innerHTML = `<span class="text-xs text-mut italic font-ur">ادائیگی کا کوئی ڈیٹا درج نہیں ہے۔</span>`;
        return;
      }
      const total = rows.reduce((s, r) => s + r.total_pkr, 0) || 1;
      const cash = rows.filter(r => r.method === 'cash').reduce((s, r) => s + r.total_pkr, 0);
      const digital = total - cash;
      const cashPct = ((cash / total) * 100).toFixed(1);
      const digitalPct = ((digital / total) * 100).toFixed(1);

      box.innerHTML = `
        <div class="split-bar-wrap mb-3">
          <div class="split-track">
            <div class="split-fill-cash" style="width: ${cashPct}%;"></div>
            <div class="split-fill-digital" style="width: ${digitalPct}%;"></div>
          </div>
          <div class="flex items-center justify-between text-xs mt-1.5 font-ur tabular">
            <span class="text-brass">نقد (Cash): ${cashPct}% (PKR ${formatMoney(cash)})</span>
            <span class="text-ok">ڈیجیٹل (Digital): ${digitalPct}% (PKR ${formatMoney(digital)})</span>
          </div>
        </div>
        ${rows.map(r => `
          <div class="flex items-center justify-between text-xs py-1 border-b border-dashed border-rule">
            <span class="capitalize text-tx">${escapeHtml(r.method.replace('_', ' '))}</span>
            <span class="font-num tabular text-mut">PKR ${formatMoney(r.total_pkr)} (${((r.total_pkr / total) * 100).toFixed(1)}%)</span>
          </div>`).join('')}
      `;
    }

    function renderRecurring(items) {
      const box = document.getElementById('recurring-container');
      if (!items.length) {
        box.innerHTML = `<span class="text-xs text-mut italic font-ur">کوئی ماہانہ بل شامل نہیں ہے۔ نیچے کرایہ، بجلی، اسکول فیس وغیرہ درج کریں۔</span>`;
        return;
      }
      box.innerHTML = '';
      items.forEach(r => {
        const badge = r.paid_this_month
          ? `<span class="px-2 py-0.5 rounded-full bg-ok/15 text-ok border border-ok/30 text-[10px] font-semibold font-ur">ادا شدہ</span>`
          : r.overdue
            ? `<span class="px-2 py-0.5 rounded-full bg-bad/15 text-bad border border-bad/30 text-[10px] font-semibold font-ur">واجب الادا</span>`
            : `<span class="px-2 py-0.5 rounded-full bg-warn/15 text-warn border border-warn/30 text-[10px] font-semibold font-ur">زیر التواء</span>`;
        const row = document.createElement('div');
        row.className = 'bill-row';
        row.innerHTML = `
          <div class="min-w-0">
            <div class="bill-title capitalize truncate font-ur">${escapeHtml(r.title)}</div>
            <div class="bill-sub font-ur tabular">تاریخ ادائیگی: ${r.due_day} · PKR ${formatMoney(r.amount)}</div>
          </div>
          <div class="flex items-center gap-2 shrink-0">
            ${badge}
            ${r.paid_this_month ? '' : `<button class="pay-rec-btn btn-mark-paid font-ur" data-id="${r.id}"><i data-lucide="check" class="w-3 h-3"></i> ادا شدہ نشان لگائیں</button>`}
            <button class="del-rec-btn btn-icon-del" data-id="${r.id}" title="Remove"><i data-lucide="trash-2" class="w-3.5 h-3.5"></i></button>
          </div>
        `;
        box.appendChild(row);
      });
      box.querySelectorAll('.pay-rec-btn').forEach(btn => btn.addEventListener('click', async () => {
        const res = await authFetch(`/api/recurring/${encodeURIComponent(state.userId)}/${btn.dataset.id}/pay`, { method: 'POST' });
        const out = await res.json().catch(() => ({}));
        showToast(out.message || 'Done', res.ok ? 'success' : 'error');
        fetchDashboard();
      }));
      box.querySelectorAll('.del-rec-btn').forEach(btn => btn.addEventListener('click', async () => {
        await authFetch(`/api/recurring/${encodeURIComponent(state.userId)}/${btn.dataset.id}`, { method: 'DELETE' });
        fetchDashboard();
      }));
      lucide.createIcons();
    }

    function bindAnalyticsForms() {
      document.getElementById('budget-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const fd = new FormData();
        fd.append('user_id', state.userId);
        fd.append('category', document.getElementById('budget-category').value);
        fd.append('monthly_limit', document.getElementById('budget-limit').value);
        const res = await authFetch('/api/budgets', { method: 'POST', body: fd });
        if (res.ok) { document.getElementById('budget-limit').value = ''; showToast('Budget saved', 'success'); }
        else showToast((await res.json().catch(() => ({}))).detail || 'Could not save budget', 'error');
        fetchDashboard();
      });
      document.getElementById('recurring-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const fd = new FormData();
        fd.append('user_id', state.userId);
        fd.append('title', document.getElementById('rec-title').value);
        fd.append('amount', document.getElementById('rec-amount').value);
        fd.append('due_day', document.getElementById('rec-day').value || '1');
        const res = await authFetch('/api/recurring', { method: 'POST', body: fd });
        if (res.ok) {
          document.getElementById('rec-title').value = '';
          document.getElementById('rec-amount').value = '';
          showToast('Recurring bill saved', 'success');
        } else showToast((await res.json().catch(() => ({}))).detail || 'Could not save', 'error');
        fetchDashboard();
      });
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
      const toast = document.createElement('div');
      const bgColors = {
        success: 'bg-emerald-900/90 border-emerald-500/50 text-emerald-200',
        error: 'bg-rose-900/90 border-rose-500/50 text-rose-200',
        info: 'bg-surface-800/90 border-slate-700 text-slate-200',
      };
      const icons = {
        success: 'check-circle',
        error: 'alert-triangle',
        info: 'info',
      };

      toast.className = `pointer-events-auto flex items-center gap-2.5 px-3.5 py-2.5 rounded-xl border backdrop-blur-md shadow-lg text-xs font-medium transition-all transform duration-200 translate-y-2 opacity-0 ${bgColors[type] || bgColors.info}`;
      toast.innerHTML = `
        <i data-lucide="${icons[type] || 'info'}" class="w-4 h-4 shrink-0"></i>
        <span>${escapeHtml(message)}</span>
      `;

      elements.toastContainer.appendChild(toast);
      lucide.createIcons();

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

    // Initialize application when DOM is ready
    document.addEventListener('DOMContentLoaded', initApp);
