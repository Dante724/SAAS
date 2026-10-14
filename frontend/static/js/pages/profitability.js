/* ════════════════════════════════════════════════════
   Job Profitability — build 144bf. Read-only report under Reports
   (no sidebar item, no WO-page changes). Margin per closed work order:
   revenue − parts cost (FIFO/avg) − labour cost (timer + manual hours),
   rolled up per location.
════════════════════════════════════════════════════ */
const ProfitPage = {
  _from: null, _to: null, _loc: '', _locs: [], _data: null,

  async load() {
    const today = new Date();
    const ago = new Date(today.getTime() - 30 * 864e5);
    this._to = today.toISOString().slice(0, 10);
    this._from = ago.toISOString().slice(0, 10);
    setTopbarActions('');
    setContent(`
      <div class="set-hero"><div><h1>Job profitability</h1>
        <p>True margin per closed work order — revenue minus parts and labour cost — rolled up by location.</p></div></div>
      <div class="rep-toolbar">
        <div class="rep-range">
          <label>From <input type="date" id="pf-from" class="field-input" value="${this._from}" onchange="ProfitPage.setRange()"></label>
          <label>To <input type="date" id="pf-to" class="field-input" value="${this._to}" onchange="ProfitPage.setRange()"></label>
          <label>Location <select id="pf-loc" class="field-input" onchange="ProfitPage.setLoc(this.value)"><option value="">All locations</option></select></label>
        </div>
        <button class="btn btn-glass btn-sm" style="margin-left:auto" onclick="Router.go('reports')">${icon('chart', 13)} Reports</button>
      </div>
      <div id="pf-body"><div class="pf-empty">Loading…</div></div>`);
    try {
      const locs = await get('/api/staff/locations');
      this._locs = Array.isArray(locs) ? locs : (locs.items || []);
      const sel = document.getElementById('pf-loc');
      if (sel) sel.innerHTML = '<option value="">All locations</option>' +
        this._locs.map(l => `<option value="${l.id}">${this._esc(l.name)}</option>`).join('');
    } catch (e) { /* locations optional */ }
    await this.reload();
  },

  setRange() {
    this._from = document.getElementById('pf-from').value;
    this._to = document.getElementById('pf-to').value;
    this.reload();
  },
  setLoc(v) { this._loc = v; this.reload(); },

  async reload() {
    const body = document.getElementById('pf-body');
    if (body) body.innerHTML = '<div class="pf-empty">Loading…</div>';
    const qs = `from_=${this._from}&to=${this._to}${this._loc ? '&location_id=' + encodeURIComponent(this._loc) : ''}`;
    try {
      this._data = await get(`/api/reports/job-profitability?${qs}`);
      this._render();
    } catch (e) {
      if (body) body.innerHTML = `<div class="pf-empty">${this._esc(e.message || 'Could not load report')}</div>`;
    }
  },

  _render() {
    const d = this._data, body = document.getElementById('pf-body');
    if (!body) return;
    if (!d.rows.length) { body.innerHTML = '<div class="pf-empty">No closed work orders in this range.</div>'; return; }
    const t = d.totals;
    const C = (n) => fmt.currency(n);
    const kpis = `
      <div class="pf-kpis">
        <div class="pf-kpi"><div class="pf-kpi-l">Revenue</div><div class="pf-kpi-v">${C(t.revenue)}</div></div>
        <div class="pf-kpi"><div class="pf-kpi-l">Margin</div><div class="pf-kpi-v ${t.margin < 0 ? 'neg' : 'pos'}">${C(t.margin)}</div></div>
        <div class="pf-kpi"><div class="pf-kpi-l">Margin %</div><div class="pf-kpi-v">${t.margin_pct}%</div></div>
        <div class="pf-kpi"><div class="pf-kpi-l">Closed jobs</div><div class="pf-kpi-v">${t.jobs}</div></div>
      </div>`;

    const locRows = d.by_location.map(l => `
      <tr><td>${this._esc(l.name)}</td><td class="r">${l.jobs}</td><td class="r">${C(l.revenue)}</td>
      <td class="r ${l.margin < 0 ? 'neg' : 'pos'}">${C(l.margin)}</td><td class="r">${l.margin_pct}%</td></tr>`).join('');
    const locTable = d.by_location.length > 1 ? `
      <div class="pf-sec">Margin by location</div>
      <div class="pf-tablewrap"><table class="pf-table">
        <thead><tr><th>Location</th><th class="r">Jobs</th><th class="r">Revenue</th><th class="r">Margin</th><th class="r">Margin %</th></tr></thead>
        <tbody>${locRows}</tbody></table></div>` : '';

    const woRows = d.rows.map(r => `
      <tr class="${r.margin < 0 ? 'pf-neg' : ''}" onclick="ProfitPage.open('${r.order_id}')">
        <td><span class="pf-num">${this._esc(r.order_number || '')}</span>${r.client_name ? ' · ' + this._esc(r.client_name) : ''}
          ${!r.has_labour ? '<span class="pf-flag">' + icon('clock', 11) + ' no clocked labour</span>' : ''}</td>
        <td class="r">${C(r.revenue)}</td><td class="r dim">${C(r.parts_cost)}</td>
        <td class="r ${r.has_labour ? 'dim' : 'muted'}">${C(r.labour_cost)}</td>
        <td class="r ${r.margin < 0 ? 'neg' : 'pos'}">${C(r.margin)}</td><td class="r ${r.margin < 0 ? 'neg' : ''}">${r.margin_pct}%</td></tr>`).join('');

    body.innerHTML = kpis + locTable + `
      <div class="pf-sec">By work order <span class="pf-hint">sorted by margin · click a row to open</span></div>
      <div class="pf-tablewrap"><table class="pf-table">
        <thead><tr><th>WO / client</th><th class="r">Revenue</th><th class="r">Parts</th><th class="r">Labour</th><th class="r">Margin</th><th class="r">%</th></tr></thead>
        <tbody>${woRows}</tbody></table></div>
      <div class="pf-note">Margin = revenue − parts cost (FIFO/avg) − labour cost (timer + manual hours). Closed jobs only.
        The flag marks work orders with no logged hours, so labour reads ${C(0)} and margin is parts-only.</div>`;
  },

  open(id) { if (window.OrdersPage && OrdersPage.viewOrder) OrdersPage.viewOrder(id); },
  _esc(s) { const d = document.createElement('div'); d.textContent = (s == null ? '' : String(s)); return d.innerHTML; },
};
window.ProfitPage = ProfitPage;
Router.register('profitability', () => ProfitPage.load());
