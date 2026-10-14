/* Build 144cs — GDPR / privacy helpers (Settings → Privacy, and per-client).
   Talks to /api/compliance/gdpr/*. Owner / settings-manager only. */
const GdprUI = {
  async exportOrg() {
    try {
      const data = await get('/api/compliance/gdpr/export-org');
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `repairos-export-${new Date().toISOString().slice(0, 10)}.json`;
      a.click(); URL.revokeObjectURL(a.href);
      Toast.success('Data export downloaded');
    } catch (e) { Toast.error(e.message || 'Export failed'); }
  },

  async exportClient(clientId) {
    const data = await get(`/api/compliance/gdpr/client/${clientId}/export`);
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = `client-${clientId}.json`; a.click();
    URL.revokeObjectURL(a.href);
  },

  async setConsent(clientId, channel, granted, source) {
    await post(`/api/compliance/gdpr/client/${clientId}/consent`,
               { channel, granted, source: source || 'staff' });
    Toast.success(`${channel} consent ${granted ? 'granted' : 'withdrawn'}`);
  },

  async consentHistory(clientId) {
    const h = await get(`/api/compliance/gdpr/client/${clientId}/consent`);
    const lines = (h.items || []).map(r =>
      `${r.channel} — ${r.granted ? 'granted' : 'withdrawn'} · ${r.source || ''} · ${r.at ? new Date(r.at).toLocaleString() : ''}`);
    Modal.open('Consent history', `<div class="gdpr-hist">${lines.map(l => `<div>${l}</div>`).join('') || '<div class="muted">No records.</div>'}</div>`);
  },

  async erase(clientId) {
    if (!await Modal.confirm('Erase this client’s personal data? Financial records are kept for accounting. This cannot be undone.', {danger:true})) return;
    try {
      const r = await post(`/api/compliance/gdpr/client/${clientId}/erase`, {});
      Toast.success('Personal data erased'); return r;
    } catch (e) { Toast.error(e.message || 'Erase failed'); }
  },
};
window.GdprUI = GdprUI;
