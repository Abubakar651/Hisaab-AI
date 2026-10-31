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

  // Initialize Firebase from backend configuration
  async function initAuth() {
    try {
      const res = await fetch('/api/auth/config');
      if (!res.ok) throw new Error('Failed to load auth config');
      const cfg = await res.json();

      if (cfg.configured && cfg.firebase && window.firebase) {
        if (!firebase.apps.length) {
          firebase.initializeApp(cfg.firebase);
        }
        firebaseAuth = firebase.auth();
        isConfigured = true;

        // Set persistence to LOCAL so user stays signed in across reloads
        await firebaseAuth.setPersistence(firebase.auth.Auth.Persistence.LOCAL);

        // Listen for auth state changes
        firebaseAuth.onAuthStateChanged(async (user) => {
          if (user) {
            currentUser = user;
            await handleUserSignedIn(user);
          } else {
            currentUser = null;
            handleUserSignedOut();
          }
        });
      } else {
        console.warn('Firebase Auth is not configured on backend.');
      }
    } catch (err) {
      console.error('Error initializing Firebase Auth:', err);
    }
  }

  // Handle successful sign-in
  async function handleUserSignedIn(user) {
    try {
      const idToken = await user.getIdToken();
      localStorage.setItem('hisaab_session_token', idToken);
      localStorage.setItem('hisaab_user_id', (user.email || 'bakar').toLowerCase());

      // Sync backend user profile
      const meRes = await authFetch('/api/auth/me', { method: 'POST' });
      let userData = {
        email: user.email,
        display_name: user.displayName || user.email.split('@')[0],
        photo_url: user.photoURL,
      };
      if (meRes.ok) {
        userData = await meRes.json();
      }

      updateUserUI(userData);

      // Close modal
      const modal = document.getElementById('auth-modal');
      if (modal) modal.classList.add('hidden');

      // Dispatch event or callback for app to refresh ledger
      if (window.HisaabApp && typeof window.HisaabApp.onUserChanged === 'function') {
        window.HisaabApp.onUserChanged(userData.email);
      }
    } catch (err) {
      console.error('Failed to sync user on sign-in:', err);
    }
  }

  // Handle sign-out
  function handleUserSignedOut() {
    localStorage.removeItem('hisaab_session_token');
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
    const userIdInput = document.getElementById('user-id-input');

    if (nameEl) nameEl.textContent = name;
    if (dropdownName) dropdownName.textContent = name;
    if (dropdownSub) dropdownSub.textContent = email;
    if (activeUserDisplay) activeUserDisplay.textContent = email;
    if (userIdInput) userIdInput.value = email;

    if (avatarEl) {
      if (user.photo_url) {
        avatarEl.innerHTML = `<img src="${user.photo_url}" alt="${name}" referrerpolicy="no-referrer" class="w-full h-full object-cover rounded-full" />`;
      } else {
        avatarEl.innerHTML = `<span id="avatar-letter">${initial}</span>`;
      }
    }
  }

  // Google Sign-In with Popup
  async function signInWithGoogle() {
    if (!firebaseAuth) {
      if (window.showToast) window.showToast('Firebase Auth is not ready yet.', 'error');
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
      if (err.code === 'auth/popup-closed-by-user') {
        msg = 'Sign-in cancelled';
      } else if (err.code === 'auth/popup-blocked') {
        msg = 'Sign-in popup was blocked by your browser. Please allow popups.';
      }
      if (window.showToast) window.showToast(msg, 'error');
    } finally {
      if (googleBtn) googleBtn.disabled = false;
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
      if (window.showToast) window.showToast('Signed out', 'info');
    } catch (err) {
      console.error('Sign out error:', err);
    }
  }

  // Authenticated fetch wrapper
  async function authFetch(url, options = {}) {
    const opts = { ...options };
    opts.headers = { ...(opts.headers || {}) };

    let token = '';
    if (currentUser) {
      try {
        token = await currentUser.getIdToken();
      } catch (e) {
        token = localStorage.getItem('hisaab_session_token') || '';
      }
    } else {
      token = localStorage.getItem('hisaab_session_token') || '';
    }

    if (token) {
      opts.headers['Authorization'] = `Bearer ${token}`;
    }

    let res = await fetch(url, opts);

    // 401 retry with forced token refresh
    if (res.status === 401 && currentUser) {
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
