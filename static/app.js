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
      elements.micModal = document.getElementById('mic-permission-modal');
      elements.closeMicModalBtn = document.getElementById('close-mic-modal-btn');
      elements.dismissMicModalBtn = document.getElementById('dismiss-mic-modal-btn');
      elements.retryMicBtn = document.getElementById('retry-mic-btn');
    }

    // Helper to detect Arabic / Urdu / Shahmukhi script
    function isUrduScript(str) {
      return /[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]/.test(str || '');
    }

    // Initialize UI
    function initApp() {
      initElements();
      elements.userIdInput.value = state.userId;
      elements.activeUserDisplay.textContent = state.userId;

      // Auto-resize textarea and auto-detect Nastaleeq script
      elements.messageInput.addEventListener('input', function () {
        this.style.height = 'auto';
        this.style.height = (this.scrollHeight) + 'px';

        if (isUrduScript(this.value)) {
          this.classList.add('font-nastaleeq', 'text-right', 'text-base', 'sm:text-lg', 'leading-loose');
          this.setAttribute('dir', 'rtl');
        } else {
          this.classList.remove('font-nastaleeq', 'text-right', 'leading-loose');
          this.setAttribute('dir', 'ltr');
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
        elements.tabChat.className = 'flex-1 py-2 text-xs font-semibold rounded-lg bg-emerald-600 text-white flex items-center justify-center gap-1.5 transition-colors';
        elements.tabDashboard.className = 'flex-1 py-2 text-xs font-semibold rounded-lg bg-surface-800 text-slate-400 hover:text-white flex items-center justify-center gap-1.5 transition-colors';
        elements.chatPane.classList.remove('hidden');
        elements.dashboardPane.classList.add('hidden');
        elements.dashboardPane.classList.remove('flex');
      } else {
        elements.tabDashboard.className = 'flex-1 py-2 text-xs font-semibold rounded-lg bg-emerald-600 text-white flex items-center justify-center gap-1.5 transition-colors';
        elements.tabChat.className = 'flex-1 py-2 text-xs font-semibold rounded-lg bg-surface-800 text-slate-400 hover:text-white flex items-center justify-center gap-1.5 transition-colors';
        elements.chatPane.classList.add('hidden');
        elements.dashboardPane.classList.remove('hidden');
        elements.dashboardPane.classList.add('flex');
      }
    }

    // =========================================================================
    // Chat Message Flow
    // =========================================================================
    async function handleTextMessageSubmit(e) {
      e.preventDefault();
      const message = elements.messageInput.value.trim();
      if (!message) return;

      // Add user message to UI
      appendUserMessage(message, null);
      elements.messageInput.value = '';
      elements.messageInput.style.height = 'auto';

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
          if (state.audioChunks.length > 0 && state.shouldSendAudio) {
            const audioBlob = new Blob(state.audioChunks, { type: state.mediaRecorder.mimeType || 'audio/webm' });
            appendUserMessage("🎤 [Voice Note Uploaded]", "Transcribing voice with Groq Whisper...");
            await sendChatRequest({ audioBlob });
          }
        };

        state.shouldSendAudio = true;
        state.mediaRecorder.start(250);
        state.isRecording = true;

        // UI Updates for Recording
        elements.recordingBanner.classList.remove('hidden');
        elements.micBtn.classList.add('bg-rose-600', 'text-white', 'border-rose-500', 'animate-pulse');
        elements.micBtn.classList.remove('bg-surface-800', 'text-slate-300');
        elements.recordingSeconds = 0;
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

      clearInterval(state.recordingTimerInterval);
      elements.recordingBanner.classList.add('hidden');
      elements.micBtn.classList.remove('bg-rose-600', 'text-white', 'border-rose-500', 'animate-pulse');
      elements.micBtn.classList.add('bg-surface-800', 'text-slate-300');
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
      wrapper.className = 'flex justify-end gap-2.5 max-w-2xl ml-auto';

      const isUrdu = isUrduScript(text);
      const nastaleeqClass = isUrdu ? 'font-nastaleeq text-lg sm:text-xl leading-loose text-right' : 'text-base sm:text-lg leading-relaxed';
      const dirAttr = isUrdu ? 'dir="rtl"' : 'dir="ltr"';

      let innerContent = `<p class="whitespace-pre-wrap ${nastaleeqClass}" ${dirAttr}>${escapeHtml(text)}</p>`;
      if (voiceSubtext) {
        innerContent = `
          <div class="flex items-center gap-1.5 text-xs font-semibold text-emerald-300 mb-1.5">
            <i data-lucide="mic" class="w-4 h-4"></i> ${escapeHtml(voiceSubtext)}
          </div>
          <p class="whitespace-pre-wrap ${nastaleeqClass}" ${dirAttr}>${escapeHtml(text)}</p>
        `;
      }

      wrapper.innerHTML = `
        <div class="bg-gradient-to-r from-emerald-700 to-teal-700 text-white rounded-2xl rounded-tr-sm p-3.5 sm:p-4 shadow-md">
          ${innerContent}
        </div>
      `;

      elements.messagesContainer.appendChild(wrapper);
      scrollToBottom();
      lucide.createIcons();
    }

    function appendAssistantMessage(markdownContent) {
      const wrapper = document.createElement('div');
      wrapper.className = 'flex gap-3 max-w-2xl';

      const isUrdu = isUrduScript(markdownContent);
      const nastaleeqClass = isUrdu ? 'font-nastaleeq text-lg sm:text-xl leading-loose' : 'text-base sm:text-lg leading-relaxed';
      const dirAttr = isUrdu ? 'dir="rtl"' : 'dir="ltr"';
      const parsedHtml = DOMPurify.sanitize(marked.parse(markdownContent || ''));

      wrapper.innerHTML = `
        <div class="w-9 h-9 rounded-xl bg-emerald-600/30 border border-emerald-500/40 flex items-center justify-center shrink-0 text-emerald-400 mt-0.5">
          <i data-lucide="bot" class="w-5 h-5"></i>
        </div>
        <div class="bg-surface-800 border border-slate-700/70 rounded-2xl rounded-tl-sm p-4 sm:p-5 text-slate-100 shadow-sm ${nastaleeqClass} prose prose-invert max-w-none" ${dirAttr}>
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
        <span class="inline-block px-3 py-1 rounded-full bg-surface-800 border border-slate-700 text-[11px] text-slate-400">
          ${parsedInline}
        </span>
      `;
      elements.messagesContainer.appendChild(wrapper);
      scrollToBottom();
    }

    function appendLoadingBubble() {
      const wrapper = document.createElement('div');
      wrapper.className = 'flex gap-3 max-w-xl loading-bubble';
      wrapper.innerHTML = `
        <div class="w-8 h-8 rounded-lg bg-emerald-600/30 border border-emerald-500/40 flex items-center justify-center shrink-0 text-emerald-400">
          <i data-lucide="bot" class="w-4 h-4 animate-spin"></i>
        </div>
        <div class="bg-surface-800 border border-slate-700/70 rounded-2xl rounded-tl-sm px-4 py-3 text-xs text-slate-400 flex items-center gap-2">
          <span>HisaabAI is thinking...</span>
          <div class="flex gap-1">
            <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-bounce"></span>
            <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-bounce [animation-delay:0.2s]"></span>
            <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-bounce [animation-delay:0.4s]"></span>
          </div>
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
      wrapper.className = 'flex gap-3 max-w-xl';
      wrapper.innerHTML = `
        <div class="w-8 h-8 rounded-lg bg-rose-600/20 border border-rose-500/40 flex items-center justify-center shrink-0 text-rose-400 mt-1">
          <i data-lucide="alert-circle" class="w-4 h-4"></i>
        </div>
        <div class="bg-rose-950/40 border border-rose-800/50 rounded-2xl rounded-tl-sm p-3.5 text-xs sm:text-sm text-rose-200 shadow-sm leading-relaxed">
          <p class="font-semibold text-rose-300 mb-0.5">Processing Issue</p>
          <p>${escapeHtml(errorText)}</p>
        </div>
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
          banner.className = 'my-3 p-4 rounded-xl bg-amber-950/60 border border-amber-600/50 shadow-lg text-slate-100 max-w-xl';
          banner.innerHTML = `
            <div class="flex items-start gap-3">
              <div class="w-9 h-9 rounded-xl bg-amber-500/20 border border-amber-500/40 flex items-center justify-center shrink-0 text-amber-400 mt-0.5">
                <i data-lucide="alert-triangle" class="w-5 h-5"></i>
              </div>
              <div class="flex-1 min-w-0">
                <h4 class="font-bold text-amber-300 text-sm flex items-center gap-2">
                  <span>بڑے خرچے کی تصدیق درکار ہے</span>
                  <span class="text-xs text-amber-400/80 font-normal">/ Large Expense Confirmation</span>
                </h4>
                <p class="text-xs text-slate-200 mt-1 leading-relaxed">
                  <strong>${escapeHtml(p.store_name)}</strong> · <span class="font-mono text-amber-300 font-bold">PKR ${formatMoney(p.total_amount)}</span> (${escapeHtml(p.payment_method)})
                </p>
                <p class="text-[11px] text-slate-400 mt-0.5">
                  یہ رقم سرور پر محفوظ ہے اور تصدیق کا انتظار ہے۔ کیا آپ اس خرچے کو درج کرنا چاہتے ہیں؟
                </p>
                <div class="flex items-center gap-2 mt-3">
                  <button id="btn-confirm-pending" class="px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-semibold text-xs flex items-center gap-1.5 transition-colors shadow">
                    <i data-lucide="check" class="w-4 h-4"></i>
                    <span>تصدیق کریں (Confirm)</span>
                  </button>
                  <button id="btn-cancel-pending" class="px-3 py-1.5 rounded-lg bg-surface-800 hover:bg-surface-700 border border-slate-700 text-rose-300 hover:text-rose-200 font-semibold text-xs flex items-center gap-1.5 transition-colors">
                    <i data-lucide="x" class="w-4 h-4"></i>
                    <span>کینسل کریں (Cancel)</span>
                  </button>
                </div>
              </div>
            </div>
          `;
          elements.messagesContainer.appendChild(banner);
          scrollToBottom();
          lucide.createIcons();

          document.getElementById('btn-confirm-pending')?.addEventListener('click', async () => {
            banner.remove();
            appendUserMessage("Haan, confirm kar do.");
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
            appendUserMessage("Nahi, cancel kar do.");
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
      // 1. KPI Cards
      elements.kpiMonthlyTotal.textContent = `PKR ${formatMoney(data.monthly_total || 0)}`;
      elements.kpiVisitCount.textContent = data.monthly_count || 0;
      elements.kpiTopStore.textContent = data.top_store || 'N/A';

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
          pill.className = 'px-2.5 py-1 rounded-lg bg-surface-800 border border-slate-700/80 flex items-center gap-1.5 text-xs';
          pill.innerHTML = `
            <span class="font-medium text-slate-200">${escapeHtml(item.store_name)}</span>
            <span class="text-[11px] text-emerald-400 font-bold">PKR ${formatMoney(item.total_pkr)}</span>
            <span class="text-[10px] text-slate-500">(${item.visit_count})</span>
          `;
          elements.storeBreakdownContainer.appendChild(pill);
        });
      } else {
        elements.storeBreakdownContainer.innerHTML = `<span class="text-xs text-slate-500 italic">No store data yet for this user.</span>`;
      }

      // 3. Transactions List
      const txs = data.recent_transactions || [];
      elements.txCountBadge.textContent = `${txs.length} records`;
      elements.transactionsList.innerHTML = '';

      if (txs.length === 0) {
        elements.transactionsList.innerHTML = `
          <div class="text-center py-10 text-slate-500 text-xs">
            <i data-lucide="inbox" class="w-8 h-8 mx-auto mb-2 opacity-50"></i>
            No transactions found for <strong>${escapeHtml(state.userId)}</strong>.<br>Record an expense to see it here!
          </div>
        `;
        lucide.createIcons();
        return;
      }

      txs.forEach(tx => {
        const card = document.createElement('div');
        card.className = 'bg-surface-850/90 border border-slate-800 hover:border-slate-700 rounded-xl p-3 transition-colors shadow-sm';

        let itemsHtml = '';
        if (tx.items && tx.items.length > 0) {
          const itemsBadges = tx.items.map(it => `
            <span class="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-surface-900 border border-slate-800 text-[11px] text-slate-300">
              <span class="text-slate-200 font-medium">${escapeHtml(it.item_name)}</span>
              ${it.quantity > 1 ? `<span class="text-slate-400">×${it.quantity}</span>` : ''}
              <span class="text-emerald-400 font-mono">Rs ${formatMoney(it.price)}</span>
            </span>
          `).join('');
          itemsHtml = `<div class="flex flex-wrap gap-1 mt-2">${itemsBadges}</div>`;
        }

        card.innerHTML = `
          <div class="flex items-start justify-between gap-2">
            <div class="flex items-center gap-2">
              <div class="w-7 h-7 rounded-lg bg-surface-800 border border-slate-700 flex items-center justify-center text-emerald-400 shrink-0">
                <i data-lucide="shopping-bag" class="w-3.5 h-3.5"></i>
              </div>
              <div>
                <h4 class="text-xs sm:text-sm font-bold text-white capitalize flex items-center gap-1.5">
                  ${escapeHtml(tx.store_name)}
                  <span class="text-[10px] text-slate-500 font-normal">#${tx.id}</span>
                </h4>
                <span class="text-[11px] text-slate-400">${escapeHtml(tx.transaction_date || '')}</span>
                <span class="ml-1.5 px-1.5 py-0.5 rounded bg-surface-900 border border-slate-700 text-[10px] uppercase tracking-wide ${tx.payment_method && tx.payment_method !== 'cash' ? 'text-sky-300' : 'text-slate-400'}">${escapeHtml((tx.payment_method || 'cash').replace('_', ' '))}</span>
              </div>
            </div>

            <div class="flex items-center gap-2">
              <span class="text-xs sm:text-sm font-bold text-emerald-400 font-mono">
                PKR ${formatMoney(tx.total_amount)}
              </span>
              <button 
                class="delete-tx-btn p-1.5 rounded-lg text-slate-400 hover:text-rose-400 hover:bg-rose-950/40 border border-transparent hover:border-rose-900/50 transition-colors"
                data-id="${tx.id}"
                data-store="${escapeHtml(tx.store_name)}"
                title="Delete Transaction"
              >
                <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>
              </button>
            </div>
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
          if (confirm(`Are you sure you want to delete transaction #${txId} (${store})?`)) {
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
    const CHART_COLORS = ['#10b981', '#38bdf8', '#f59e0b', '#a78bfa', '#f43f5e', '#14b8a6', '#eab308', '#94a3b8'];

    function renderBudgets(budgets) {
      const box = document.getElementById('budgets-container');
      if (!budgets.length) {
        box.innerHTML = `<span class="text-xs text-slate-500 italic">No budget set. Add one below or say "mera mahana budget 50000 hai".</span>`;
        return;
      }
      box.innerHTML = '';
      budgets.forEach(b => {
        const pct = Math.min(b.percent, 100);
        const barColor = b.level === 'over' ? 'bg-rose-500' : b.level === 'warning' ? 'bg-amber-400' : 'bg-emerald-500';
        const txtColor = b.level === 'over' ? 'text-rose-300' : b.level === 'warning' ? 'text-amber-300' : 'text-emerald-300';
        const label = b.category === 'total' ? 'Overall' : b.category;
        const row = document.createElement('div');
        row.innerHTML = `
          <div class="flex items-center justify-between text-xs mb-1">
            <span class="font-medium text-slate-200 capitalize">${escapeHtml(label)}</span>
            <span class="flex items-center gap-2">
              <span class="${txtColor} font-mono">PKR ${formatMoney(b.spent_pkr)} / ${formatMoney(b.monthly_limit)} (${b.percent}%)</span>
              <button class="delete-budget-btn text-slate-500 hover:text-rose-400" data-id="${b.id}" title="Remove budget">
                <i data-lucide="x" class="w-3 h-3"></i>
              </button>
            </span>
          </div>
          <div class="h-2 rounded-full bg-surface-900 border border-slate-800 overflow-hidden">
            <div class="h-full ${barColor} transition-all" style="width:${pct}%"></div>
          </div>
          ${b.level === 'over'
            ? `<p class="text-[11px] text-rose-300 mt-1">Over budget by PKR ${formatMoney(Math.abs(b.remaining_pkr))}</p>`
            : b.level === 'warning'
              ? `<p class="text-[11px] text-amber-300 mt-1">Only PKR ${formatMoney(b.remaining_pkr)} left this month</p>`
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
      if (typeof Chart === 'undefined') return;
      if (charts.category) { charts.category.destroy(); charts.category = null; }
      empty.classList.toggle('hidden', rows.length > 0);
      canvas.parentElement.classList.toggle('hidden', rows.length === 0);
      if (!rows.length) return;
      charts.category = new Chart(canvas, {
        type: 'doughnut',
        data: {
          labels: rows.map(r => `${r.category} (${r.percent}%)`),
          datasets: [{
            data: rows.map(r => r.total_pkr),
            backgroundColor: rows.map((_, i) => CHART_COLORS[i % CHART_COLORS.length]),
            borderColor: '#0f172a',
            borderWidth: 2,
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          cutout: '62%',
          plugins: {
            legend: { position: 'right', labels: { color: '#cbd5e1', boxWidth: 10, font: { size: 11 } } },
            tooltip: { callbacks: { label: (c) => ` PKR ${formatMoney(c.parsed)}` } },
          },
        },
      });
    }

    function renderTrendChart(days) {
      const canvas = document.getElementById('trend-chart');
      if (typeof Chart === 'undefined') return;
      if (charts.trend) { charts.trend.destroy(); charts.trend = null; }
      charts.trend = new Chart(canvas, {
        type: 'bar',
        data: {
          labels: days.map(d => d.date.slice(5)),
          datasets: [{
            data: days.map(d => d.total_pkr),
            backgroundColor: '#10b981',
            borderRadius: 3,
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { display: false },
            tooltip: { callbacks: { label: (c) => ` PKR ${formatMoney(c.parsed.y)}` } },
          },
          scales: {
            x: { ticks: { color: '#94a3b8', maxTicksLimit: 8, font: { size: 10 } }, grid: { display: false } },
            y: { beginAtZero: true, ticks: { color: '#94a3b8', font: { size: 10 } }, grid: { color: '#1e293b' } },
          },
        },
      });
    }

    function renderPaymentSplit(rows) {
      const box = document.getElementById('payment-split-container');
      if (!rows.length) {
        box.innerHTML = `<span class="text-xs text-slate-500 italic">No payment data yet.</span>`;
        return;
      }
      const total = rows.reduce((s, r) => s + r.total_pkr, 0) || 1;
      const cash = rows.filter(r => r.method === 'cash').reduce((s, r) => s + r.total_pkr, 0);
      const digital = total - cash;
      box.innerHTML = `
        <div class="flex gap-2 text-xs mb-1.5">
          <span class="px-2 py-1 rounded-lg bg-surface-900 border border-slate-700 text-slate-200">Cash <b class="text-emerald-300">PKR ${formatMoney(cash)}</b></span>
          <span class="px-2 py-1 rounded-lg bg-surface-900 border border-slate-700 text-slate-200">Digital <b class="text-sky-300">PKR ${formatMoney(digital)}</b></span>
        </div>
        ${rows.map(r => `
          <div class="flex items-center gap-2 text-xs">
            <span class="w-24 capitalize text-slate-300">${escapeHtml(r.method.replace('_', ' '))}</span>
            <div class="flex-1 h-2 rounded-full bg-surface-900 overflow-hidden border border-slate-800">
              <div class="h-full ${r.method === 'cash' ? 'bg-emerald-500' : 'bg-sky-400'}" style="width:${(r.total_pkr / total * 100).toFixed(1)}%"></div>
            </div>
            <span class="font-mono text-slate-200 w-24 text-right">PKR ${formatMoney(r.total_pkr)}</span>
          </div>`).join('')}
      `;
    }

    function renderRecurring(items) {
      const box = document.getElementById('recurring-container');
      if (!items.length) {
        box.innerHTML = `<span class="text-xs text-slate-500 italic">No recurring bills. Add rent, LESCO, school fee, BC below.</span>`;
        return;
      }
      box.innerHTML = '';
      items.forEach(r => {
        const badge = r.paid_this_month
          ? `<span class="px-1.5 py-0.5 rounded bg-emerald-900/50 border border-emerald-600/40 text-emerald-300 text-[10px] font-semibold">PAID</span>`
          : r.overdue
            ? `<span class="px-1.5 py-0.5 rounded bg-rose-900/50 border border-rose-600/40 text-rose-300 text-[10px] font-semibold">OVERDUE</span>`
            : `<span class="px-1.5 py-0.5 rounded bg-amber-900/40 border border-amber-600/40 text-amber-300 text-[10px] font-semibold">PENDING</span>`;
        const row = document.createElement('div');
        row.className = 'flex items-center justify-between gap-2 bg-surface-900/60 border border-slate-800 rounded-lg px-2.5 py-2';
        row.innerHTML = `
          <div class="min-w-0">
            <div class="text-xs font-semibold text-slate-100 capitalize truncate">${escapeHtml(r.title)}</div>
            <div class="text-[11px] text-slate-400">Due day ${r.due_day} · PKR ${formatMoney(r.amount)}</div>
          </div>
          <div class="flex items-center gap-1.5 shrink-0">
            ${badge}
            ${r.paid_this_month ? '' : `<button class="pay-rec-btn px-2 py-1 rounded-md bg-emerald-600 hover:bg-emerald-500 text-white text-[11px] font-semibold" data-id="${r.id}">Mark paid</button>`}
            <button class="del-rec-btn text-slate-500 hover:text-rose-400" data-id="${r.id}" title="Remove"><i data-lucide="trash-2" class="w-3.5 h-3.5"></i></button>
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
