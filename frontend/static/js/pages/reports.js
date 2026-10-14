/* ════════════════════════════════════════════════════
   Reports — 7 tabs, date range, run + Excel export (Phase C)
════════════════════════════════════════════════════ */

const ReportsPage = {
  _tabs: [],
  _activeTab: null,
  _activeReport: null,
  _from: null,
  _to: null,
  _view: 'table',   // build 144ad: table ↔ chart
  _last: null,

  async load() {
    // default range: last 30 days
    const today = new Date();
    const ago = new Date(today.getTime() - 30 * 864e5);
    this._to = today.toISOString().slice(0, 10);
    this._from = ago.toISOString().slice(0, 10);
    setTopbarActions('');
    setContent(`
      <div class="set-hero"><div><h1>Reports</h1><p>Financial, ticket, inventory, and marketing reports. Pick a date range and export to Excel.</p></div></div>
      <div class="rep-toolbar">
        <div class="rep-range">
          <label>From <input type="date" id="rep-from" class="field-input" value="${this._from}" onchange="ReportsPage.setRange()"></label>
          <label>To <input type="date" id="rep-to" class="field-input" value="${this._to}" onchange="ReportsPage.setRange()"></label>
        </div>
        <button class="btn btn-glass btn-sm" style="margin-left:auto" onclick="Router.go('profitability')">${typeof icon==='function'?icon('wallet',13):''} Job profitability →</button>
        <button class="btn btn-glass btn-sm" onclick="Router.go('insights')">${typeof icon==='function'?icon('trending',13):''} Company Insights →</button>
      </div>
      <div class="rep-tabs" id="rep-tabs"></div>
      <div class="rep-reports" id="rep-reports"></div>
      <div id="rep-output"></div>`);
    try {
      const cat = await get('/api/reports/catalog');
      this._tabs = cat.tabs;
      this._activeTab = cat.tabs[0]?.name;
      this._renderTabs();
      this._renderReportList();
      // auto-run first report
      const first = cat.tabs[0]?.reports[0];
      if (first) this.run(first.key, first.title);
    } catch (e) { Toast.error(e.message); }
  },

  setRange() {
    this._from = document.getElementById('rep-from').value;
    this._to = document.getElementById('rep-to').value;
    if (this._activeReport) this.run(this._activeReport.key, this._activeReport.title);
  },

  _renderTabs() {
    document.getElementById('rep-tabs').innerHTML = this._tabs.map(t =>
      `<button class="rep-tab ${t.name === this._activeTab ? 'active' : ''}" onclick="ReportsPage.pickTab('${t.name}')">${t.name}</button>`
    ).join('');
  },

  pickTab(name) {
    this._activeTab = name;
    this._renderTabs();
    this._renderReportList();
  },

  _renderReportList() {
    const tab = this._tabs.find(t => t.name === this._activeTab);
    if (!tab) return;
    document.getElementById('rep-reports').innerHTML = tab.reports.map(r =>
      `<button class="rep-report-btn ${this._activeReport?.key === r.key ? 'active' : ''}" onclick="ReportsPage.run('${r.key}','${r.title.replace(/'/g, "\\'")}')">${r.title}</button>`
    ).join('');
  },

  async run(key, title) {
    this._activeReport = { key, title };
    this._renderReportList();
    const out = document.getElementById('rep-output');
    out.innerHTML = `<div class="card" style="padding:40px;text-align:center;color:var(--tx-4)">Running…</div>`;
    try {
      const _lp = (typeof App !== 'undefined' && App.locParam) ? App.locParam('&') : '';
      const r = await get(`/api/reports/run/${key}?date_from=${this._from}&date_to=${this._to}${_lp}`);
      const numericCols = r.columns.map((c, i) => r.rows.length ? r.rows.every(row => typeof row[i] === 'number') : false);
      const totals = r.columns.map((c, i) => numericCols[i] ? r.rows.reduce((s, row) => s + (row[i] || 0), 0) : null);
      // Chartable when there's a label column + a value column and a sane row count.
      const labelIdx = numericCols.findIndex(n => !n);
      const valueIdx = numericCols.findIndex(n => n);
      const chartable = labelIdx >= 0 && valueIdx >= 0 && r.rows.length >= 1 && r.rows.length <= 60;
      this._last = { r, numericCols, totals, labelIdx, valueIdx, chartable };
      this._draw();
    } catch (e) { out.innerHTML = `<div class="card" style="padding:30px;color:var(--red)">${e.message}</div>`; }
  },

  setView(v) { this._view = v; this._draw(); },

  _draw() {
    const out = document.getElementById('rep-output');
    if (!out || !this._last) return;
    const { r, numericCols, totals, labelIdx, valueIdx, chartable } = this._last;
    const view = (chartable && this._view === 'chart') ? 'chart' : 'table';

    const toggle = chartable ? `
      <div class="rep-viewtoggle">
        <button class="rvt ${view === 'table' ? 'on' : ''}" onclick="ReportsPage.setView('table')">${icon('list', 13)} Table</button>
        <button class="rvt ${view === 'chart' ? 'on' : ''}" onclick="ReportsPage.setView('chart')">${icon('chart', 13)} Chart</button>
      </div>` : '';

    let bodyHtml;
    if (view === 'chart') {
      const pairs = r.rows.map(row => [row[labelIdx], row[valueIdx]]);
      const looksDate = r.rows.length > 2 && /^\d{4}-\d{2}/.test(String(r.rows[0][labelIdx] || ''));
      const svg = looksDate ? Charts.line(pairs) : Charts.bars(pairs, { unit: r.columns[valueIdx] });
      bodyHtml = `<div class="rep-chart">${svg}</div>
        <div class="rep-chart-cap">${r.columns[valueIdx]} by ${r.columns[labelIdx]}</div>`;
    } else {
      bodyHtml = `<div class="tbl-wrap"><table class="tbl rep-tbl">
        <thead><tr>${r.columns.map((c, i) => `<th${numericCols[i] ? ' style="text-align:right"' : ''}>${c}</th>`).join('')}</tr></thead>
        <tbody>
          ${r.rows.length ? r.rows.map(row => `<tr>${row.map((v, i) => `<td${numericCols[i] ? ' style="text-align:right;font-variant-numeric:tabular-nums"' : ''}>${numericCols[i] && v != null ? fmt.currency(v) : (v ?? '—')}</td>`).join('')}</tr>`).join('')
            : `<tr><td colspan="${r.columns.length}" style="text-align:center;color:var(--tx-4);padding:30px">No data in this period</td></tr>`}
        </tbody>
        ${r.rows.length && totals.some(t => t !== null) ? `<tfoot><tr>${totals.map((t, i) => `<td${numericCols[i] ? ' style="text-align:right;font-weight:800"' : ''}>${i === 0 ? 'Total' : (t !== null ? fmt.currency(t) : '')}</td>`).join('')}</tr></tfoot>` : ''}
      </table></div>`;
    }

    out.innerHTML = `
      <div class="card" style="padding:0">
        <div class="rep-head">
          <div>
            <div class="rep-title">${r.title}</div>
            <div class="rep-sub">${this._from} → ${this._to} · ${r.row_count} rows</div>
          </div>
          <div style="display:flex;gap:8px;align-items:center">
            ${toggle}
            <button class="btn btn-glass btn-sm" onclick="ReportsPage.exportCsv()">${icon('file', 14)} Export CSV</button>
            <button class="btn btn-primary btn-sm" onclick="ReportsPage.exportXlsx()">${icon('file', 14)} Export Excel</button>
          </div>
        </div>
        ${bodyHtml}
      </div>`;
  },

  exportCsv() {
    if (!this._activeReport) return;
    const url = `/api/reports/export-csv/${this._activeReport.key}?date_from=${this._from}&date_to=${this._to}${(typeof App !== 'undefined' && App.locParam) ? App.locParam('&') : ''}`;
    fetch(url, { headers: { Authorization: 'Bearer ' + Auth.token } })
      .then(r => r.blob())
      .then(blob => {
        const u = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = u; a.download = `${this._activeReport.key}.csv`;
        document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(u);
      })
      .catch(e => Toast.error(e.message));
  },

  exportXlsx() {
    if (!this._activeReport) return;
    const url = `/api/reports/export/${this._activeReport.key}?date_from=${this._from}&date_to=${this._to}${(typeof App !== 'undefined' && App.locParam) ? App.locParam('&') : ''}`;
    // fetch with auth then trigger download
    fetch(url, { headers: { Authorization: 'Bearer ' + Auth.token } })
      .then(r => r.blob())
      .then(blob => {
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = `${this._activeReport.key}.xlsx`;
        a.click();
        URL.revokeObjectURL(a.href);
        Toast.success('Excel downloaded');
      })
      .catch(() => Toast.error('Export failed'));
  },
};

Router.register('reports', () => ReportsPage.load());
