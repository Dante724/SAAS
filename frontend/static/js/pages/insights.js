/* ════════════════════════════════════════════════════
   Company Insights — build 144ay. Orderry-style build-your-own
   Diagrams (subject / metric / group) and Trends (over time).
   Lives under Reports (no new sidebar item).
════════════════════════════════════════════════════ */
const InsightsPage = {
  _meta: null, _cards: [], _period: '30d', _view: 'diagrams', _loc: '',

  async load() {
    setTopbarActions('');
    setContent(`
      <div class="set-hero"><div><h1>Company Insights</h1>
        <p>Build your own diagrams and trends in two clicks. The period applies to every card.</p></div></div>
      <div class="ci-bar">
        <span class="ci-seg"><b class="on" onclick="InsightsPage.setView('diagrams')">Diagrams</b><b onclick="InsightsPage.setView('trends')">Trends</b></span>
        <select class="field-select ci-period" id="ci-period" onchange="InsightsPage.setPeriod(this.value)"></select>
        <button class="btn btn-glass btn-sm" onclick="Router.go('reports')">${icon('chart', 13)} Reports</button>
        <button class="btn btn-primary" id="ci-add" onclick="InsightsPage.addModal()">${icon('plus', 14)} Add</button>
      </div>
      <div class="ci-grid" id="ci-grid"></div>`);
    try { this._meta = await get('/api/reports/insights/meta'); }
    catch (e) { return Toast.error(e.message); }
    this._fillPeriod();
    await this.reload();
  },

  setView(v) {
    this._view = v;
    document.querySelectorAll('.ci-seg b').forEach((b, i) => b.classList.toggle('on', (i === 0) === (v === 'diagrams')));
    this._fillPeriod();
    this.reload();
  },
  setPeriod(p) { this._period = p; this.reload(); },
  _fillPeriod() {
    const list = this._view === 'trends' ? this._meta.periods_trend : this._meta.periods_diagram;
    const sel = document.getElementById('ci-period'); if (!sel) return;
    if (!list.some(x => x.key === this._period)) this._period = list[0].key;
    sel.innerHTML = list.map(x => `<option value="${x.key}" ${x.key === this._period ? 'selected' : ''}>${x.label}</option>`).join('');
  },

  async reload() {
    const grid = document.getElementById('ci-grid'); if (!grid) return;
    try { this._cards = (await get('/api/reports/insights/cards')).items; } catch (e) { this._cards = []; }
    const want = this._view === 'trends' ? 'trend' : 'diagram';
    const cards = this._cards.filter(c => c.kind === want);
    if (!cards.length) {
      grid.innerHTML = `<div class="ci-empty">No ${this._view} yet. Click <b>+ Add</b> to build your first one.</div>`;
      return;
    }
    grid.innerHTML = cards.map(c => `
      <div class="ci-card" id="ci-card-${c.id}">
        <div class="ci-c-top"><div><div class="ci-c-t">${this._subjLabel(c.subject)}</div>
          <div class="ci-c-s">${this._sub(c)}</div></div>
          <button class="ci-c-x" title="Remove" onclick="InsightsPage.remove('${c.id}')">${icon('x', 14)}</button></div>
        <div class="ci-c-body" id="ci-body-${c.id}"><div class="skeleton" style="height:160px"></div></div>
      </div>`).join('');
    cards.forEach(c => this._renderCard(c));
  },

  async _renderCard(c) {
    const body = document.getElementById('ci-body-' + c.id); if (!body) return;
    const qs = new URLSearchParams({ kind: c.kind, subject: c.subject, metric: c.metric, period: this._period });
    if (c.group_by) qs.set('group', c.group_by);
    if (this._loc) qs.set('location_id', this._loc);
    try {
      const d = await get('/api/reports/insights/data?' + qs.toString());
      const pairs = d.labels.map((l, i) => [l, d.values[i]]);
      const hasData = pairs.some(p => p[1]);
      if (!pairs.length || !hasData) { body.innerHTML = `<div class="ci-none">No data in this period.</div>`; return; }
      if (c.kind === 'trend') body.innerHTML = (c.view === 'columns' ? Charts.bars(pairs, { height: 170 }) : Charts.line(pairs, { height: 170 }));
      else if (c.view === 'table') body.innerHTML = this._table(d);
      else body.innerHTML = Charts.bars(pairs, { height: 170 });
    } catch (e) { body.innerHTML = `<div class="ci-none">${e.message}</div>`; }
  },

  _table(d) {
    return `<table class="tbl ci-tbl"><thead><tr><th>${d.group_label}</th><th style="text-align:right">${d.metric_label}</th></tr></thead>
      <tbody>${d.table.map(r => `<tr><td>${r.label}</td><td style="text-align:right;font-weight:800">${this._fmt(r.value, d.metric)}</td></tr>`).join('')}</tbody></table>`;
  },
  _fmt(v, metric) { return metric === 'count' ? (+v).toLocaleString() : fmt.currency(v); },
  _subjLabel(s) { const x = this._meta.subjects.find(y => y.key === s); return x ? x.label : s; },
  _sub(c) {
    const ml = this._meta.metric_labels[c.metric] || c.metric;
    if (c.kind === 'trend') return `${ml} · trend`;
    const gl = this._meta.group_labels[c.group_by] || '';
    return `${ml}, by ${gl.toLowerCase()}${c.view === 'table' ? ' · table' : ''}`;
  },

  addModal() {
    const kind = this._view === 'trends' ? 'trend' : 'diagram';
    const subj = this._meta.subjects;
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Add ${kind}</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Subject</label>
          <select class="field-select" id="ci-subject" onchange="InsightsPage._syncBuilder()">${subj.map(s => `<option value="${s.key}">${s.label}</option>`).join('')}</select></div>
        <div class="field"><label class="field-label">Metric</label><select class="field-select" id="ci-metric"></select></div>
        <div class="field" id="ci-group-wrap"><label class="field-label">Group by</label><select class="field-select" id="ci-group"></select></div>
        <div class="field"><label class="field-label">Show as</label>
          <select class="field-select" id="ci-viewsel">${kind === 'diagram'
            ? '<option value="chart">Chart</option><option value="table">Table</option>'
            : '<option value="chart">Line</option><option value="columns">Columns</option>'}</select></div>
      </div>
      <div class="modal-footer"><button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="InsightsPage.saveCard('${kind}')">Save</button></div>`, 'modal-narrow');
    this._syncBuilder();
  },
  _syncBuilder() {
    const s = document.getElementById('ci-subject').value;
    const x = this._meta.subjects.find(y => y.key === s);
    document.getElementById('ci-metric').innerHTML = x.metrics.map(m => `<option value="${m.key}">${m.label}</option>`).join('');
    const gw = document.getElementById('ci-group-wrap');
    const g = document.getElementById('ci-group');
    if (this._view === 'trends') { if (gw) gw.style.display = 'none'; }
    else { if (gw) gw.style.display = ''; if (g) g.innerHTML = x.groups.map(gr => `<option value="${gr.key}">${gr.label}</option>`).join(''); }
  },
  async saveCard(kind) {
    const subject = document.getElementById('ci-subject').value;
    const metric = document.getElementById('ci-metric').value;
    const group_by = kind === 'diagram' ? document.getElementById('ci-group').value : null;
    const view = document.getElementById('ci-viewsel').value;
    try {
      await post('/api/reports/insights/cards', { kind, subject, metric, group_by, period: this._period, view });
      Modal.close(); Toast.success('Card added'); this.reload();
    } catch (e) { Toast.error(e.message); }
  },
  async remove(id) {
    if (!await Modal.confirm('Remove this card?', {danger:true})) return;
    try { await del('/api/reports/insights/cards/' + id); this.reload(); }
    catch (e) { Toast.error(e.message); }
  },
};
Router.register('insights', () => InsightsPage.load());
