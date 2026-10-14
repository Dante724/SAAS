/* ════════════════════════════════════════════════════════════════
   Build 144cb — logged-out account flows: forgot / reset password and
   email verification. These render inside #auth-view as sibling cards to
   the sign-in card, so they reuse the same glass/clay auth styling.

   Deep links (served by the SPA fallback):
     /reset-password?token=…   → reset card
     /verify-email?token=…     → verify card (auto-submits)
════════════════════════════════════════════════════════════════ */
window.AuthFlows = {
  _resetToken: null,

  _inviteToken: null,

  _cards: ['auth-card-main', 'auth-forgot', 'auth-reset', 'auth-verify', 'auth-invite', 'auth-signup'],
  _signupOpts: null,
  _show(id) {
    document.getElementById('auth-view').style.display = 'flex';
    document.getElementById('app-view').style.display = 'none';
    // the primary sign-in card has no id; tag it once so we can toggle it
    const main = document.querySelector('#auth-view .auth-card:first-child');
    if (main && !main.id) main.id = 'auth-card-main';
    this._cards.forEach(c => {
      const el = document.getElementById(c);
      if (el) el.style.display = (c === id) ? '' : 'none';
    });
  },

  showLogin() { history.replaceState({}, '', '/'); this._show('auth-card-main'); },
  showForgot() { this._show('auth-forgot'); },

  // ── Build 144cg: Orderry-style signup ──────────────────────────
  async showSignup() {
    this._show('auth-signup');
    if (!this._signupOpts) {
      try { this._signupOpts = await get('/api/auth/signup-options'); } catch (e) { this._signupOpts = { templates: [], regions: [], region_defaults: {}, employee_brackets: [] }; }
    }
    const o = this._signupOpts;
    const tSel = document.getElementById('su-template');
    tSel.innerHTML = o.templates.map(t => `<option value="${t.key}">${t.label}</option>`).join('');
    const rSel = document.getElementById('su-region');
    rSel.innerHTML = o.regions.map(r => `<option value="${r.key}">${r.label}</option>`).join('');
    const eSel = document.getElementById('su-employees');
    eSel.innerHTML = (o.employee_brackets || []).map(b => `<option value="${b}">${b}</option>`).join('');
    const cSel = document.getElementById('su-country');
    if (cSel && typeof Geo !== 'undefined') cSel.innerHTML = Geo.options('');
    this.regionChanged();
  },

  regionChanged() {
    const o = this._signupOpts; if (!o) return;
    const region = document.getElementById('su-region').value;
    const d = (o.region_defaults || {})[region] || {};
    if (d.currency_code) document.getElementById('su-currency').value = d.currency_code;
  },

  // Build 144da — country is the primary driver at signup: it fills the
  // currency and drives the region bucket (which seeds tax + template defaults);
  // the timezone is remembered for submit. Region stays visible/overridable.
  countryChanged() {
    const sel = document.getElementById('su-country');
    const meta = (typeof Geo !== 'undefined') ? Geo.meta(sel && sel.value) : null;
    if (!meta) return;
    const cur = document.getElementById('su-currency'); if (cur) cur.value = meta.currency_code;
    const reg = document.getElementById('su-region');
    if (reg && [...reg.options].some(o => o.value === meta.region)) reg.value = meta.region;
    this._tz = meta.timezone;
  },

  async submitSignup() {
    const g = id => (document.getElementById(id).value || '').trim();
    const org = g('su-org'), name = g('su-name'), email = g('su-email'), pass = document.getElementById('su-pass').value;
    if (!org || !name || !email) return Toast.error('Company, name and email are required');
    if (!pass || pass.length < 8) return Toast.error('Password must be at least 8 characters');
    const region = document.getElementById('su-region').value;
    const code = g('su-currency');
    const body = {
      org_name: org, name, email, password: pass,
      industry_template: document.getElementById('su-template').value || null,
      region: region || null,
      country: (document.getElementById('su-country') || {}).value || null,   // Build 144da
      timezone: this._tz || null,                                              // derived from country
      currency_code: code || null,
      currency: code || null,   // symbol falls back to code; editable later in Settings
      employee_count: document.getElementById('su-employees').value || null,
      company_phone: g('su-phone') || null,
    };
    const btn = document.getElementById('su-btn'); btn.disabled = true; btn.textContent = 'Creating…';
    try {
      const d = await post('/api/auth/register', body);
      Auth.token = d.access_token; App.me = d;
      // Guarded root cutover: land in the React redesign at "/".
      location.replace('/'); return;
    } catch (e) { Toast.error(e.message || 'Could not create the account'); }
    finally { btn.disabled = false; btn.textContent = 'Create shop & sign in →'; }
  },

  /* Called by App.boot(): claim the page if this is a reset/verify deep link. */
  intercept() {
    const path = location.pathname;
    const token = new URLSearchParams(location.search).get('token') || '';
    if (path === '/reset-password') { this._resetToken = token; this._show('auth-reset'); return true; }
    if (path === '/verify-email') { this._show('auth-verify'); this._runVerify(token); return true; }
    if (path === '/accept-invite') { this._inviteToken = token; this._show('auth-invite'); this._loadInvite(token); return true; }
    return false;
  },

  async _loadInvite(token) {
    try {
      const r = await post('/api/auth/invite/preview', { token });
      if (!r.valid) {
        document.getElementById('iv-title').textContent = 'Invitation invalid or expired';
        document.getElementById('iv-sub').textContent = 'Ask your administrator to send a new invite.';
        document.getElementById('iv-btn').style.display = 'none';
        return;
      }
      if (r.org_name) document.getElementById('iv-title').textContent = `Join ${r.org_name}`;
      if (r.name) document.getElementById('iv-name').value = r.name;
      document.getElementById('iv-sub').textContent = `Invited as ${r.email}. Set a password to continue.`;
    } catch (e) { /* leave defaults */ }
  },

  async submitInvite() {
    const name = (document.getElementById('iv-name').value || '').trim();
    const pass = document.getElementById('iv-pass').value;
    if (!name) return Toast.error('Enter your name');
    if (!pass || pass.length < 8) return Toast.error('Password must be at least 8 characters');
    const btn = document.getElementById('iv-btn'); btn.disabled = true; btn.textContent = 'Joining…';
    try {
      const d = await post('/api/auth/invite/accept', { token: this._inviteToken, name, password: pass });
      Auth.token = d.access_token; App.me = d;
      // Guarded root cutover: land in the React redesign at "/".
      location.replace('/'); return;
    } catch (e) { Toast.error(e.message || 'This invitation is invalid or has expired'); }
    finally { btn.disabled = false; btn.textContent = 'Accept & sign in →'; }
  },

  async sendForgot() {
    const email = (document.getElementById('fg-email').value || '').trim();
    if (!email) return Toast.error('Enter your email');
    const btn = document.getElementById('fg-btn'); btn.disabled = true; btn.textContent = 'Sending…';
    try {
      const r = await post('/api/auth/password/forgot', { email });
      Toast.success(r.message || 'If that email is registered, a link is on its way.');
      this.showLogin();
    } catch (e) { Toast.error(e.message); }
    finally { btn.disabled = false; btn.textContent = 'Send reset link'; }
  },

  async submitReset() {
    const p1 = document.getElementById('rs-pass').value;
    const p2 = document.getElementById('rs-pass2').value;
    if (!p1 || p1.length < 8) return Toast.error('Password must be at least 8 characters');
    if (p1 !== p2) return Toast.error('Passwords do not match');
    const btn = document.getElementById('rs-btn'); btn.disabled = true; btn.textContent = 'Saving…';
    try {
      await post('/api/auth/password/reset', { token: this._resetToken, new_password: p1 });
      Toast.success('Password updated — please sign in');
      this.showLogin();
    } catch (e) { Toast.error(e.message || 'This reset link is invalid or has expired'); }
    finally { btn.disabled = false; btn.textContent = 'Set new password'; }
  },

  async _runVerify(token) {
    const t = document.getElementById('vf-title'), s = document.getElementById('vf-sub'),
          b = document.getElementById('vf-btn');
    try {
      await post('/api/auth/email/verify', { token });
      t.textContent = 'Email verified';
      s.textContent = 'Your address is confirmed. You can sign in now.';
    } catch (e) {
      t.textContent = 'Link invalid or expired';
      s.textContent = 'Ask an administrator to resend your verification email.';
    }
    b.style.display = '';
  },
};

/* ════════════════════════════════════════════════════════════════
   Build 144cd — mandatory 2FA. When the org requires 2FA and the
   signed-in user hasn't enrolled, App.showApp() calls MfaGate.enforce()
   which opens a non-dismissable enrollment flow. Also reused by the
   Settings → Security "Set up 2FA" button for voluntary enrollment.
════════════════════════════════════════════════════════════════ */
window.MfaGate = {
  _forced: false,

  // Called after login when policy demands it and the user isn't enrolled.
  enforce() {
    if (App.me && App.me.mfa_required && !App.me.mfa_enabled) {
      this._forced = true;
      this.start(true);
    }
  },

  async start(forced = false) {
    this._forced = forced;
    try {
      const r = await post('/api/mfa/enroll', {});
      this._secret = r.secret;
      this._renderScan(r.secret, r.otpauth_uri);
    } catch (e) {
      // already enrolled or error
      Toast.error(e.message || 'Could not start 2FA setup');
    }
  },

  _renderScan(secret, uri) {
    const closeBtn = this._forced ? '' : '<button class="modal-close" onclick="Modal.close()">×</button>';
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Set up two-factor authentication</span>${closeBtn}</div>
      <div class="modal-body">
        <div style="font-size:13.5px;color:var(--tx-2);margin-bottom:12px">
          ${this._forced ? 'Your organisation requires 2FA. ' : ''}Open your authenticator app
          (Google Authenticator, Authy, 1Password…) and add a new account using this key:</div>
        <div style="font-family:monospace;font-size:15px;letter-spacing:2px;background:var(--panel-2,rgba(255,255,255,.06));
             border:1px solid var(--line);border-radius:10px;padding:12px;text-align:center;word-break:break-all">${secret}</div>
        <div style="font-size:11.5px;color:var(--tx-4);margin:8px 0 16px">Account: RepairOS · ${App.me?.email || ''}</div>
        <div class="field"><label class="field-label">Enter the 6-digit code from the app</label>
          <input class="field-input" id="mfa-code" inputmode="numeric" maxlength="6" placeholder="123456"></div>
      </div>
      <div class="modal-footer">
        ${this._forced ? '' : '<button class="btn btn-glass" onclick="Modal.close()">Cancel</button>'}
        <button class="btn btn-primary" onclick="MfaGate.activate()">Verify &amp; turn on</button>
      </div>`, 'modal-narrow');
  },

  async activate() {
    const code = (document.getElementById('mfa-code').value || '').trim();
    if (code.length < 6) return Toast.error('Enter the 6-digit code');
    try {
      const r = await post('/api/mfa/activate', { code });
      if (App.me) App.me.mfa_enabled = true;
      this._renderRecovery(r.recovery_codes || []);
    } catch (e) { Toast.error(e.message || 'Invalid code — try the current one'); }
  },

  _renderRecovery(codes) {
    Modal.open(`
      <div class="modal-header"><span class="modal-title">2FA is on — save your recovery codes</span></div>
      <div class="modal-body">
        <div style="font-size:13.5px;color:var(--tx-2);margin-bottom:12px">
          Keep these somewhere safe. Each works once if you lose your authenticator.</div>
        <div style="font-family:monospace;font-size:14px;line-height:2;background:var(--panel-2,rgba(255,255,255,.06));
             border:1px solid var(--line);border-radius:10px;padding:12px;columns:2">
          ${codes.map(c => `<div>${c}</div>`).join('')}</div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-primary" onclick="MfaGate.done()">I've saved them — continue</button>
      </div>`, 'modal-narrow');
  },

  done() {
    Modal.close();
    Toast.success('Two-factor authentication enabled');
    this._forced = false;
  },
};
