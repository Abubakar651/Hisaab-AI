/**
 * HisaabAI — Firebase Authentication Layer (Google Sign-In Only)
 * - Zero custom sessions / JWTs.
 * - ID token sent via Authorization: Bearer on every authFetch call.
 * - Handles auto-refresh on 401 retry, token cache, and login modal sync.
 */

(function () {
  'use strict';

  let firebaseAuth = null;
  let currentUser = null;
  let isConfigured = false;
  let authMode = 'signin'; // 'signin' or 'signup'
  let resendCooldownTimer = null;

  // Map Firebase error codes to user-friendly messages
  function mapAuthError(err) {
    const code = err && err.code ? err.code : '';
    switch (code) {
      case 'auth/invalid-credential':
      case 'auth/user-not-found':
      case 'auth/wrong-password':
        return 'Incorrect email or password.';
      case 'auth/email-already-in-use':
        return 'An account already exists with this email.';
      case 'auth/weak-password':
        return 'Password is too weak. Please use at least 8 characters.';
      case 'auth/invalid-email':
        return 'Please enter a valid email address.';
      case 'auth/too-many-requests':
        return 'Too many failed attempts. Please wait a moment and try again.';
      case 'auth/configuration-not-found':
        return 'Firebase Authentication is not initialized in the Firebase Console (auth/configuration-not-found).';
      case 'auth/popup-closed-by-user':
        return 'Sign-in cancelled';
      case 'auth/popup-blocked':
        return 'Sign-in popup was blocked by your browser. Please allow popups.';
      case 'auth/unauthorized-domain':
        return 'Domain not authorized in Firebase Console (Authentication > Settings > Authorized domains).';
      default:
        return err.message || 'Authentication failed. Please try again.';
    }
  }

  // Show verify email panel
  function showVerifyEmailPanel(email) {
    const mainPanel = document.getElementById('auth-main-panel');
    const verifyPanel = document.getElementById('auth-verify-panel');
    const modal = document.getElementById('auth-modal');
    const emailText = document.getElementById('verify-email-text');

    if (mainPanel) mainPanel.classList.add('hidden');
    if (verifyPanel) verifyPanel.classList.remove('hidden');
    if (modal) modal.classList.remove('hidden');
    if (emailText && email) {
      emailText.textContent = `A verification link has been sent to ${email}. Please click the link to activate your account.`;
    }
  }

  // Hide verify email panel and return to main form
  function hideVerifyEmailPanel() {
    const mainPanel = document.getElementById('auth-main-panel');
    const verifyPanel = document.getElementById('auth-verify-panel');
    if (verifyPanel) verifyPanel.classList.add('hidden');
    if (mainPanel) mainPanel.classList.remove('hidden');
  }

  // 30-second cooldown timer for verification resend
  function startResendCooldown() {
    const resendBtn = document.getElementById('btn-verify-resend');
    const countdownEl = document.getElementById('verify-resend-countdown');
    if (!resendBtn || !countdownEl) return;

    let seconds = 30;
    resendBtn.classList.add('hidden');
    countdownEl.classList.remove('hidden');
    countdownEl.textContent = `Resend in ${seconds}s`;

    if (resendCooldownTimer) clearInterval(resendCooldownTimer);
    resendCooldownTimer = setInterval(() => {
      seconds--;
      if (seconds <= 0) {
        clearInterval(resendCooldownTimer);
        resendCooldownTimer = null;
        countdownEl.classList.add('hidden');
        resendBtn.classList.remove('hidden');
      } else {
        countdownEl.textContent = `Resend in ${seconds}s`;
      }
    }, 1000);
  }

  // Switch between Sign In and Create Account tabs
  function setAuthMode(mode) {
    authMode = mode;
    const tabSignin = document.getElementById('auth-tab-signin');
    const tabSignup = document.getElementById('auth-tab-signup');
    const submitBtn = document.getElementById('auth-submit-btn');
    const forgotBtn = document.getElementById('auth-forgot-btn');

    if (mode === 'signup') {
      if (tabSignin) tabSignin.classList.remove('active');
      if (tabSignup) tabSignup.classList.add('active');
      if (submitBtn) submitBtn.innerHTML = '<span>Create Account</span>';
      if (forgotBtn) forgotBtn.classList.add('hidden');
    } else {
      if (tabSignup) tabSignup.classList.remove('active');
      if (tabSignin) tabSignin.classList.add('active');
      if (submitBtn) submitBtn.innerHTML = '<span>Sign In</span>';
      if (forgotBtn) forgotBtn.classList.remove('hidden');
    }
  }

  // Show unconfigured warning in modal
  function showUnconfiguredUI() {
    isConfigured = false;
    const noticeEl = document.getElementById('google-config-notice');
    if (noticeEl) {
      noticeEl.textContent = 'Sign-in is not configured';
      noticeEl.className = 'text-xs text-bad font-semibold text-center pt-2';
    }
    const googleBtn = document.getElementById('google-signin-btn');
    if (googleBtn) {
      googleBtn.disabled = true;
      googleBtn.setAttribute('title', 'Sign-in is not configured');
    }
    const submitBtn = document.getElementById('auth-submit-btn');
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.setAttribute('title', 'Sign-in is not configured');
    }
  }

  // Initialize Firebase from backend configuration
  async function initAuth() {
    try {
      const res = await fetch('/api/auth/config');
      if (!res.ok) throw new Error('Failed to load auth config');
      const cfg = await res.json();

      const fb = (cfg && cfg.firebase) ? cfg.firebase : cfg;
      const isComplete = Boolean(
        cfg &&
        cfg.configured &&
        fb &&
        fb.apiKey &&
        fb.authDomain &&
        fb.projectId &&
        fb.appId
      );

      if (!isComplete || !window.firebase) {
        showUnconfiguredUI();
        console.warn('Sign-in is not configured on backend.');
        return;
      }

      if (!firebase.apps.length) {
        firebase.initializeApp(fb);
      }
      firebaseAuth = firebase.auth();
      isConfigured = true;

      // Set persistence to LOCAL so user stays signed in across reloads
      await firebaseAuth.setPersistence(firebase.auth.Auth.Persistence.LOCAL);

      // Listen for auth state changes
      firebaseAuth.onAuthStateChanged(async (user) => {
        if (user) {
          currentUser = user;
          const isPasswordProvider = user.providerData && user.providerData[0] && user.providerData[0].providerId === 'password';
          if (isPasswordProvider && !user.emailVerified) {
            showVerifyEmailPanel(user.email);
            return; // CRITICAL: RETURN BEFORE calling /api/auth/me or any API
          }
          await handleUserSignedIn(user);
        } else {
          currentUser = null;
          handleUserSignedOut();
        }
      });
    } catch (err) {
      console.error('Error initializing Firebase Auth:', err);
      showUnconfiguredUI();
    }
  }

  // Handle successful sign-in
  async function handleUserSignedIn(user) {
    try {
      // Sync backend user profile
      const meRes = await authFetch('/api/auth/me', { method: 'POST' });
      let userData = {
        email: user.email,
        display_name: user.displayName || (user.email ? user.email.split('@')[0] : ''),
        photo_url: user.photoURL,
      };
      if (meRes.ok) {
        const body = await meRes.json();
        userData = body.user || {
          email: user.email,
          display_name: user.displayName || (user.email ? user.email.split('@')[0] : ''),
          photo_url: user.photoURL,
        };
      }

      updateUserUI(userData);

      // Close modal
      const modal = document.getElementById('auth-modal');
      if (modal) modal.classList.add('hidden');

      // Dispatch event or callback for app to refresh ledger
      if (window.HisaabApp && typeof window.HisaabApp.onUserChanged === 'function') {
        const targetEmail = userData.email || user.email;
        if (targetEmail) {
          window.HisaabApp.onUserChanged(targetEmail);
        }
      }
    } catch (err) {
      console.error('Failed to sync user on sign-in:', err);
    }
  }

  // Handle sign-out
  function handleUserSignedOut() {
    const nameEl = document.getElementById('user-display-name');
    const letterEl = document.getElementById('avatar-letter');
    const activeEl = document.getElementById('active-user-display');

    if (letterEl) letterEl.textContent = '?';
    if (nameEl) nameEl.textContent = 'Sign In';
    if (activeEl) activeEl.textContent = 'Guest';

    // Show modal if needed
    const modal = document.getElementById('auth-modal');
    if (modal) modal.classList.remove('hidden');
  }

  // Update header and dropdown UI
  function updateUserUI(user) {
    const name = user.display_name || user.email || 'User';
    const email = user.email || '';
    const initial = (name.charAt(0) || 'U').toUpperCase();

    const nameEl = document.getElementById('user-display-name');
    const letterEl = document.getElementById('avatar-letter');
    const avatarEl = document.getElementById('user-avatar');
    const dropdownName = document.getElementById('dropdown-name');
    const dropdownSub = document.getElementById('dropdown-sub');
    const activeUserDisplay = document.getElementById('active-user-display');

    if (nameEl) nameEl.textContent = name;
    if (dropdownName) dropdownName.textContent = name;
    if (dropdownSub) dropdownSub.textContent = email;
    if (activeUserDisplay) activeUserDisplay.textContent = email;

    if (avatarEl) {
      avatarEl.textContent = '';
      if (user.photo_url && /^https:\/\//i.test(user.photo_url)) {
        const img = document.createElement('img');
        img.src = user.photo_url;
        img.alt = name;
        img.referrerPolicy = 'no-referrer';
        img.className = 'w-full h-full object-cover rounded-full';
        avatarEl.appendChild(img);
      } else {
        const span = document.createElement('span');
        span.id = 'avatar-letter';
        span.textContent = initial;
        avatarEl.appendChild(span);
      }
    }
  }

  // Email & Password Sign-In / Sign-Up
  async function handleEmailAuthSubmit(e) {
    if (e && e.preventDefault) e.preventDefault();

    if (!isConfigured) {
      showUnconfiguredUI();
      if (window.showToast) window.showToast('Sign-in is not configured', 'error');
      return;
    }
    if (!firebaseAuth) {
      await initAuth();
    }
    if (!firebaseAuth || !isConfigured) {
      showUnconfiguredUI();
      if (window.showToast) window.showToast('Sign-in is not configured', 'error');
      return;
    }

    const emailInput = document.getElementById('auth-email');
    const passwordInput = document.getElementById('auth-password');
    const submitBtn = document.getElementById('auth-submit-btn');

    const email = (emailInput ? emailInput.value : '').trim();
    const password = passwordInput ? passwordInput.value : '';

    if (!email) {
      if (window.showToast) window.showToast('Please enter your email address.', 'error');
      return;
    }
    if (password.length < 8) {
      if (window.showToast) window.showToast('Password must be at least 8 characters long.', 'error');
      return;
    }

    if (submitBtn) submitBtn.disabled = true;

    try {
      if (authMode === 'signup') {
        const cred = await firebaseAuth.createUserWithEmailAndPassword(email, password);
        currentUser = cred.user;
        await cred.user.sendEmailVerification();
        showVerifyEmailPanel(email);
        startResendCooldown();
        if (window.showToast) window.showToast('Account created! Please check your email for the verification link.', 'success');
      } else {
        const cred = await firebaseAuth.signInWithEmailAndPassword(email, password);
        currentUser = cred.user;
        if (!cred.user.emailVerified) {
          showVerifyEmailPanel(email);
          return;
        }
        if (window.showToast) window.showToast('Successfully signed in!', 'success');
      }
    } catch (err) {
      console.error('Email authentication error:', err);
      if (window.showToast) window.showToast(mapAuthError(err), 'error');
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  }

  // Forgot Password
  async function handleForgotPassword() {
    if (!isConfigured || !firebaseAuth) {
      if (window.showToast) window.showToast('Sign-in is not configured', 'error');
      return;
    }
    const emailInput = document.getElementById('auth-email');
    const email = (emailInput ? emailInput.value : '').trim();

    if (!email) {
      if (window.showToast) window.showToast('Please enter your email address to reset password.', 'warning');
      if (emailInput) emailInput.focus();
      return;
    }

    try {
      await firebaseAuth.sendPasswordResetEmail(email);
    } catch (err) {
      console.warn('Password reset request error:', err);
    }
    // Always the same generic success message
    if (window.showToast) {
      window.showToast('If an account exists with this email, a password reset link has been sent.', 'info');
    }
  }

  // Google Sign-In with Popup
  async function signInWithGoogle() {
    if (!isConfigured) {
      showUnconfiguredUI();
      if (window.showToast) window.showToast('Sign-in is not configured', 'error');
      return;
    }
    if (!firebaseAuth) {
      await initAuth();
    }
    if (!firebaseAuth || !isConfigured) {
      showUnconfiguredUI();
      if (window.showToast) window.showToast('Sign-in is not configured', 'error');
      return;
    }
    const googleBtn = document.getElementById('google-signin-btn');
    if (googleBtn) googleBtn.disabled = true;

    try {
      const provider = new firebase.auth.GoogleAuthProvider();
      provider.setCustomParameters({ prompt: 'select_account' });
      await firebaseAuth.signInWithPopup(provider);
      if (window.showToast) window.showToast('Successfully signed in!', 'success');
    } catch (err) {
      console.error('Google Sign-In failed:', err);
      let msg = err.message || 'Google Sign-In failed';
      if (err.code === 'auth/configuration-not-found') {
        msg = 'Firebase Authentication is not initialized in the Firebase Console (auth/configuration-not-found).';
      } else if (err.code === 'auth/popup-closed-by-user') {
        msg = 'Sign-in cancelled';
      } else if (err.code === 'auth/popup-blocked') {
        msg = 'Sign-in popup was blocked by your browser. Please allow popups.';
      } else if (err.code === 'auth/unauthorized-domain') {
        msg = 'Domain not authorized in Firebase Console (Authentication > Settings > Authorized domains).';
      }
      if (window.showToast) window.showToast(msg, 'error');
    } finally {
      if (googleBtn && isConfigured) googleBtn.disabled = false;
    }
  }

  // Sign out
  async function signOut() {
    try {
      if (firebaseAuth) {
        await firebaseAuth.signOut();
      }
      currentUser = null;
      handleUserSignedOut();

      // Clear Service Worker caches on sign-out
      if (navigator.serviceWorker && navigator.serviceWorker.controller) {
        navigator.serviceWorker.controller.postMessage({ action: 'clear-cache' });
      }

      if (window.showToast) window.showToast('Signed out', 'info');
    } catch (err) {
      console.error('Sign out error:', err);
    }
  }

  // Authenticated fetch wrapper
  async function authFetch(url, options = {}) {
    if (!currentUser) {
      return new Response(JSON.stringify({ detail: 'Not authenticated' }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    const opts = { ...options };
    opts.headers = { ...(opts.headers || {}) };

    let token = '';
    try {
      token = await currentUser.getIdToken();
    } catch (e) {
      console.warn('Failed to retrieve ID token:', e);
      return new Response(JSON.stringify({ detail: 'Token retrieval failed' }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    opts.headers['Authorization'] = `Bearer ${token}`;
    let res = await fetch(url, opts);

    // 401 retry with forced token refresh
    if (res.status === 401 && currentUser) {
      let isEmailNotVerified = false;
      try {
        const cloned = res.clone();
        const errJson = await cloned.json();
        if (errJson && errJson.detail === 'email_not_verified') {
          isEmailNotVerified = true;
        }
      } catch (_) {}

      if (!isEmailNotVerified) {
        try {
          token = await currentUser.getIdToken(true); // force refresh
          opts.headers['Authorization'] = `Bearer ${token}`;
          res = await fetch(url, opts);
        } catch (refreshErr) {
          console.warn('Token refresh failed:', refreshErr);
        }

        if (res.status === 401) {
          // Still unauthorized - log out
          signOut();
          const modal = document.getElementById('auth-modal');
          if (modal) modal.classList.remove('hidden');
        }
      }
    }

    return res;
  }

  // Expose to window
  window.HisaabAuth = {
    init: initAuth,
    signInWithGoogle,
    signOut,
    authFetch,
    getUser: () => currentUser,
    isConfigured: () => isConfigured,
  };

  // Replace global authFetch
  window.authFetch = authFetch;

  // Auto-init on DOMContentLoaded
  document.addEventListener('DOMContentLoaded', () => {
    initAuth();

    // Bind Email & Password tabs
    const tabSignin = document.getElementById('auth-tab-signin');
    const tabSignup = document.getElementById('auth-tab-signup');
    if (tabSignin) tabSignin.addEventListener('click', () => setAuthMode('signin'));
    if (tabSignup) tabSignup.addEventListener('click', () => setAuthMode('signup'));

    // Bind Email form
    const emailForm = document.getElementById('auth-email-form');
    if (emailForm) {
      emailForm.addEventListener('submit', handleEmailAuthSubmit);
    }

    // Bind Forgot password
    const forgotBtn = document.getElementById('auth-forgot-btn');
    if (forgotBtn) {
      forgotBtn.addEventListener('click', handleForgotPassword);
    }

    // Bind Verify Email panel actions
    const verifyDoneBtn = document.getElementById('btn-verify-done');
    if (verifyDoneBtn) {
      verifyDoneBtn.addEventListener('click', async () => {
        if (currentUser) {
          verifyDoneBtn.disabled = true;
          try {
            await currentUser.reload();
            if (currentUser.emailVerified) {
              await currentUser.getIdToken(true);
              hideVerifyEmailPanel();
              await handleUserSignedIn(currentUser);
              if (window.showToast) window.showToast('Email verified successfully!', 'success');
            } else {
              if (window.showToast) window.showToast('Email not verified yet. Please check your inbox or spam folder.', 'warning');
            }
          } catch (e) {
            if (window.showToast) window.showToast(mapAuthError(e), 'error');
          } finally {
            verifyDoneBtn.disabled = false;
          }
        }
      });
    }

    const verifyResendBtn = document.getElementById('btn-verify-resend');
    if (verifyResendBtn) {
      verifyResendBtn.addEventListener('click', async () => {
        if (currentUser && !resendCooldownTimer) {
          try {
            await currentUser.sendEmailVerification();
            if (window.showToast) window.showToast('Verification email resent.', 'info');
            startResendCooldown();
          } catch (e) {
            if (window.showToast) window.showToast(mapAuthError(e), 'error');
          }
        }
      });
    }

    const verifyCancelBtn = document.getElementById('btn-verify-cancel');
    if (verifyCancelBtn) {
      verifyCancelBtn.addEventListener('click', async () => {
        await signOut();
        hideVerifyEmailPanel();
      });
    }

    // Bind Google button
    const googleBtn = document.getElementById('google-signin-btn');
    if (googleBtn) {
      googleBtn.addEventListener('click', signInWithGoogle);
    }

    // Bind sign out
    const signoutBtn = document.getElementById('signout-btn');
    if (signoutBtn) {
      signoutBtn.addEventListener('click', signOut);
    }

    // Bind modal open/close
    const openBtn = document.getElementById('open-auth-modal-btn');
    const closeBtn = document.getElementById('close-auth-modal-btn');
    const modal = document.getElementById('auth-modal');

    if (openBtn && modal) {
      openBtn.addEventListener('click', () => modal.classList.remove('hidden'));
    }
    if (closeBtn && modal) {
      closeBtn.addEventListener('click', () => modal.classList.add('hidden'));
    }
  });
})();
