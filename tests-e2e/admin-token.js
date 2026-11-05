// One demo-admin token shared by the spec files of a run. The server allows 10 sign-ins per user
// per 5 minutes, and every spec signing in for each worker (pwa, tab-shift, visual) gets close to
// that on CI. The token is kept in .e2e-admin-token (git-ignored) and reused for 30 minutes.
const fs = require('fs');
const path = require('path');
const { expect } = require('@playwright/test');
const { demoAdminPassword } = require('./demo-credentials');

const FILE = path.join(__dirname, '.e2e-admin-token');
const EMAIL = process.env.E2E_ADMIN_EMAIL || 'admin@repairos.dev';
const PASS = process.env.E2E_ADMIN_PASS || demoAdminPassword();

async function adminToken(request, baseURL) {
  try {
    const { token, at, base } = JSON.parse(fs.readFileSync(FILE, 'utf8'));
    if (token && base === baseURL && Date.now() - at < 30 * 60 * 1000) {
      const me = await request.get('/api/auth/me', { headers: { Authorization: `Bearer ${token}` } });
      if (me.ok()) return token;                          // still valid (not a reseeded server)
    }
  } catch { /* no cached token yet */ }
  const r = await request.post('/api/auth/token', { form: { username: EMAIL, password: PASS } });
  expect(r.ok(), `sign-in failed: ${r.status()}`).toBeTruthy();
  const token = (await r.json()).access_token;
  try { fs.writeFileSync(FILE, JSON.stringify({ token, at: Date.now(), base: baseURL })); } catch { /* read-only: fine */ }
  return token;
}

module.exports = { adminToken };
