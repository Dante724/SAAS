/* Build 144cr — Settings → Finance → Currencies manager.
   Lists the base currency + additional currencies; add / edit rate + display /
   delete; shows rate history and can auto-fetch a live rate. Talks to
   /api/finance/currencies. Mount with CurrenciesUI.render(containerEl). */
const CurrenciesUI = {
  async render(el) {
    if (!el) return;
    let data;
    try { data = await get('/api/finance/currencies'); }
    catch (e) { el.innerHTML = '<div class="muted">Finance permission required.</div>'; return; }
    const base = data.base || {};
    const items = data.items || [];
    el.innerHTML = `
      <div class="cur-head">
        <div><div class="cur-title">Currencies</div>
          <div class="cur-base">Base — <b>${base.code || ''}</b> ${base.symbol || ''} (set by support)</div></div>
        <button class="btn btn-primary btn-sm" onclick="CurrenciesUI.add()">+ Currency</button>
      </div>
      <table class="cur-tbl"><thead><tr><th>Currency</th><th>Symbol</th><th>Rate (1 = ${base.code})</th><th>Updated</th><th></th></tr></thead>
      <tbody>${items.map(c => `
        <tr>
          <td><b>${c.code}</b></td><td>${c.symbol || ''}</td>
          <td class="mono">${c.rate}</td>
          <td class="muted">${c.updated_at ? new Date(c.updated_at).toLocaleString() : ''}</td>
          <td class="cur-actions">
            <button class="btn btn-glass btn-xs" onclick="CurrenciesUI.edit('${c.id}','${c.code}',${c.rate},'${c.symbol || ''}')">Edit</button>
            <button class="btn btn-glass btn-xs" onclick="CurrenciesUI.history('${c.id}','${c.code}')">History</button>
            <button class="btn btn-glass btn-xs" ${c.in_use ? 'disabled title="in use by an account"' : ''} onclick="CurrenciesUI.remove('${c.id}')">Delete</button>
          </td>
        </tr>`).join('') || `<tr><td colspan="5" class="muted">No additional currencies yet.</td></tr>`}
      </tbody></table>`;
    this._el = el;
  },

  async add() {
    const code = (await Modal.prompt('Currency code (e.g. EUR):') || '').trim().toUpperCase();
    if (!code) return;
    let rate = null;
    try { rate = (await get('/api/finance/currencies/lookup-rate?code=' + encodeURIComponent(code))).rate; } catch (e) {}
    const entered = await Modal.prompt(`Exchange rate — 1 ${code} = ? base` + (rate ? ` (live ≈ ${rate})` : ''), {default: rate || '1'});
    if (entered === null) return;
    const symbol = (await Modal.prompt('Display symbol:', {default: code})) || code;
    try {
      await post('/api/finance/currencies', { code, rate: parseFloat(entered), symbol });
      Toast.success('Currency added'); this.render(this._el);
    } catch (e) { Toast.error(e.message || 'Could not add currency'); }
  },

  async edit(id, code, rate, symbol) {
    const entered = await Modal.prompt(`New rate — 1 ${code} = ? base`, {default: rate});
    if (entered === null) return;
    try {
      await put_patch(`/api/finance/currencies/${id}`, { rate: parseFloat(entered) });
      Toast.success('Rate updated'); this.render(this._el);
    } catch (e) { Toast.error(e.message || 'Could not update'); }
  },

  async history(id, code) {
    const h = await get(`/api/finance/currencies/${id}/history`);
    const lines = (h.items || []).map(r => `1 ${code} = ${r.rate}  ·  ${r.changed_at ? new Date(r.changed_at).toLocaleString() : ''}`);
    Modal.open(`${code} rate history`, `<div class="cur-hist">${lines.map(l => `<div>${l}</div>`).join('') || '<div class="muted">No history.</div>'}</div>`);
  },

  async remove(id) {
    if (!await Modal.confirm('Hide this currency? Existing accounts/history stay intact.', {danger:true})) return;
    try { await del(`/api/finance/currencies/${id}`); Toast.success('Currency removed'); this.render(this._el); }
    catch (e) { Toast.error(e.message || 'Cannot delete — still in use'); }
  },
};
/* small helper if the app lacks a PATCH wrapper */
async function put_patch(url, body) {
  if (typeof patch === 'function') return patch(url, body);
  const r = await fetch(url, { method: 'PATCH', headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + Auth.token }, body: JSON.stringify(body) });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || 'Request failed');
  return r.json();
}
window.CurrenciesUI = CurrenciesUI;
