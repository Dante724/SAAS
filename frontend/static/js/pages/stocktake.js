/* Stocktake — guided count: snapshot -> enter counted -> post variances. */
const StocktakePage = {
  _locations: [], _bins: [], _current: null,

  async load() {
    setTopbarActions(`<button class="btn btn-primary" onclick="StocktakePage.startModal()">+ Start stocktake</button>`);
    setContent(`
      <div style="font-size:13px;color:var(--tx-3);margin-bottom:14px;max-width:720px">
        Start a count for a warehouse (or one bin): the system <b>snapshots</b> every quantity,
        you enter what you physically counted, and posting writes <b>one signed adjustment per variance</b>.
      </div>
      <div id="st-list"></div>
      <div id="st-detail" style="margin-top:18px"></div>`);
    await this.reload();
  },

  async reload() {
    const el = document.getElementById('st-list');
    if (!el) return;
    try {
      const data = await get('/api/stocktake');
      el.innerHTML = `<div class="card" style="padding:0"><div class="tbl-wrap"><table class="tbl">
        <thead><tr><th>Started</th><th>Warehouse</th><th>Scope</th><th>Progress</th><th>Status</th><th style="width:110px"></th></tr></thead>
        <tbody>${data.items.map(s => `<tr>
          <td style="color:var(--tx-3)">${new Date(s.created_at).toLocaleString()}</td>
          <td class="tbl-strong">${s.location_name || ''}</td>
          <td>${s.bin_code ? 'Bin ' + s.bin_code : 'Whole warehouse'}</td>
          <td>${s.counted}/${s.lines} counted</td>
          <td>${s.status === 'posted'
            ? `<span style="color:var(--green);font-weight:600">posted</span>`
            : `<span style="color:var(--amber);font-weight:600">in progress</span>`}</td>
          <td><button class="btn btn-glass btn-sm" onclick="StocktakePage.open('${s.id}')">${s.status === 'posted' ? 'View' : 'Count'}</button></td>
        </tr>`).join('') || `<tr><td colspan="6" style="text-align:center;color:var(--tx-4);padding:22px">No stocktakes yet — start your first count</td></tr>`}
        </tbody></table></div></div>`;
    } catch (e) { Toast.error(e.message); }
  },

  async open(id) {
    try { this._current = await get(`/api/stocktake/${id}/lines?diff=${this._diff || ''}`); }
    catch (e) { return Toast.error(e.message); }
    const st = this._current;
    const open_ = st.status !== 'posted';
    const sm = st.summary || {};
    const chip = (k, label) => `<div class="chip${(this._diff || '') === k ? ' active' : ''}"
      onclick="StocktakePage.setDiff('${k}')">${label}</div>`;

    const el = document.getElementById('st-detail');
    el.innerHTML = `
      <div class="card" style="padding:0">
        <div style="display:flex;align-items:center;gap:10px;padding:12px 14px;border-bottom:1px solid var(--g-border);flex-wrap:wrap">
          <span style="font-weight:700">Count sheet</span>
          <span class="badge">${st.kind || 'full'}</span>
          <span style="color:var(--tx-3);font-size:12.5px">
            ${sm.counted ?? 0}/${sm.total ?? 0} counted
            ${sm.unknown ? ` · ${sm.unknown} new` : ''}
            ${sm.serial_mismatch ? ` · ${sm.serial_mismatch} serial issue(s)` : ''}
          </span>
          <div style="margin-left:auto;display:flex;gap:6px">
            <button class="btn btn-glass btn-sm" onclick="StocktakePage.exportSheet('${st.id}')">Export sheet</button>
            ${open_ ? `<button class="btn btn-glass btn-sm" onclick="StocktakePage.importModal('${st.id}')">Import counts</button>` : ''}
            <button class="btn btn-glass btn-sm" onclick="StocktakePage.settlement('${st.id}')">Settlement act</button>
            ${open_ ? `<button class="btn btn-primary btn-sm" onclick="StocktakePage.post('${st.id}')">Post adjustments</button>` : ''}
          </div>
        </div>

        ${open_ ? `
        <div style="display:flex;gap:8px;align-items:center;padding:10px 14px;border-bottom:1px solid var(--g-border);flex-wrap:wrap">
          <input class="field-input" id="stk-scan" placeholder="Scan or type a barcode / SKU / serial…"
                 style="flex:1;min-width:220px"
                 onkeydown="if(event.key==='Enter'){event.preventDefault();StocktakePage.scan('${st.id}')}">
          <input class="field-input" id="stk-scan-qty" type="number" value="1" style="width:76px" title="Qty per scan">
          <label class="cash-check" style="margin:0;white-space:nowrap">
            <input type="checkbox" id="stk-scan-unknown"> register unknown
          </label>
          <button class="btn btn-glass btn-sm" onclick="StocktakePage.scan('${st.id}')">Count</button>
        </div>` : ''}

        <div class="chip-row" style="padding:10px 14px">
          ${chip('', 'All')}${chip('surplus', `Surplus ${sm.surplus ?? 0}`)}
          ${chip('shortage', `Shortage ${sm.shortage ?? 0}`)}
          ${chip('match', 'Match')}${chip('uncounted', `Uncounted ${sm.uncounted ?? 0}`)}
        </div>

        <div class="tbl-wrap"><table class="tbl">
          <thead><tr><th>Product</th><th style="width:110px">System</th><th style="width:130px">Counted</th><th style="width:110px">Variance</th></tr></thead>
          <tbody>${st.items.map(l => `<tr>
            <td class="tbl-strong">${l.part_name}
              ${l.is_unknown ? '<span class="badge" title="Found on the shelf, not in the system">new</span>' : ''}
              ${l.serial_mismatch ? `<span class="badge" title="${l.note || ''}">serial?</span>` : ''}</td>
            <td>${l.system_qty}</td>
            <td>${open_
              ? `<input class="field-input" style="padding:4px 8px" type="number" value="${l.counted_qty ?? ''}" placeholder="count..." onchange="StocktakePage.count('${st.id}','${l.id}',this.value)">`
              : (l.counted_qty ?? '<span style="color:var(--tx-4)">not counted</span>')}</td>
            <td id="var-${l.id}" style="font-weight:700;color:${l.variance > 0 ? 'var(--green)' : l.variance < 0 ? 'var(--red)' : 'var(--tx-3)'}">${l.counted_qty != null ? (l.variance > 0 ? '+' : '') + l.variance : ''}</td>
          </tr>`).join('') || `<tr><td colspan="4" style="text-align:center;color:var(--tx-4);padding:22px">${this._diff ? 'Nothing matches this filter' : 'Scan an item to start counting'}</td></tr>`}
          </tbody></table></div>
      </div>`;
    if (open_) document.getElementById('stk-scan')?.focus();
    el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  },

  async count(stId, lineId, val) {
    if (val === '') return;
    try {
      const r = await patch(`/api/stocktake/${stId}/lines/${lineId}`, { counted_qty: parseFloat(val) || 0 });
      const cell = document.getElementById(`var-${lineId}`);
      if (cell) {
        cell.textContent = (r.variance > 0 ? '+' : '') + r.variance;
        cell.style.color = r.variance > 0 ? 'var(--green)' : r.variance < 0 ? 'var(--red)' : 'var(--tx-3)';
      }
    } catch (e) { Toast.error(e.message); }
  },

  async post(id) {
    if (!(await confirm2('Post this stocktake? Every counted variance becomes a signed stock adjustment.'))) return;
    try {
      const r = await post(`/api/stocktake/${id}/post`, {});
      Toast.success(`Posted ${r.adjustments} adjustment(s)`);
      await this.reload();
      await this.open(id);
    } catch (e) { Toast.error(e.message); }
  },

  async startModal() {
    try {
      [this._locations, this._bins, this._warehouses] = await Promise.all([
        get('/api/staff/locations').then(r => r.items).catch(() => []),
        get('/api/bins').then(r => r.items),
        get('/api/settings/warehouses?kind=product').then(r => r.items).catch(() => [])]);
    } catch (e) { return Toast.error(e.message); }
    let me = {};
    try { me = await get('/api/auth/me'); } catch {}
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Start stocktake</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Warehouse</label>
          <select class="field-select" id="stk-wh">
            ${this._warehouses.map(w => `<option value="${w.id}" ${w.is_default ? 'selected' : ''}>${w.name}</option>`).join('')}</select></div>
        <div class="field"><label class="field-label">Count type</label>
          <select class="field-select" id="stk-kind">
            <option value="full">Full — snapshot everything the warehouse holds</option>
            <option value="partial">Partial — only what you scan gets counted</option>
          </select>
          <div style="font-size:11.5px;color:var(--tx-4);margin-top:4px">
            In a partial count an item you never scan is left alone, rather than being treated as zero.
          </div></div>
        <div class="field"><label class="field-label">Scope</label>
          <select class="field-select" id="stk-bin"><option value="">Whole warehouse</option>
            ${this._bins.map(b => `<option value="${b.id}">Bin ${b.name}</option>`).join('')}</select></div>
        <div class="field"><label class="field-label">Note</label>
          <input class="field-input" id="stk-note" placeholder="Monthly count, spot check..."></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="StocktakePage.start()">Snapshot &amp; start</button>
      </div>`);
  },

  async start() {
    try {
      const kind = document.getElementById('stk-kind').value;
      const r = await post('/api/stocktake', {
        warehouse_id: document.getElementById('stk-wh').value,
        kind,
        bin_id: document.getElementById('stk-bin').value || null,
        note: document.getElementById('stk-note').value || null,
      });
      Modal.close();
      Toast.success(kind === 'full'
        ? `Snapshot taken — ${r.lines} line(s) to count`
        : 'Partial count started — scan items to add them');
      await this.reload(); await this.open(r.id);
    } catch (e) { Toast.error(e.message); }
  },
};

/* ── Build 91: scan counting, difference filters, and the settlement act ── */
Object.assign(StocktakePage, {
  _diff: '',

  // A scan *adds one* rather than setting a total — that's what pointing a scanner at a
  // shelf actually means. Typing a number in the sheet still sets the total outright.
  async scan(stId) {
    const el = document.getElementById('stk-scan');
    const code = el.value.trim();
    if (!code) return;
    const qty = parseFloat(document.getElementById('stk-scan-qty').value) || 1;
    const registerUnknown = document.getElementById('stk-scan-unknown').checked;
    try {
      const r = await post(`/api/stocktake/${stId}/scan`, {
        code, qty, register_unknown: registerUnknown });
      el.value = '';
      el.focus();
      let msg = `${r.part_name}: counted ${r.counted_qty}`;
      if (r.is_unknown) msg += ' · new product';
      if (r.serial_mismatch) msg += ' · serial is filed elsewhere';
      Toast.success(msg);
      await this.open(stId);
      document.getElementById('stk-scan')?.focus();
    } catch (e) {
      Toast.error(e.message || 'Nothing matched that code');
      el.select();
    }
  },

  async setDiff(d) { this._diff = d; await this.open(this._current.id); },

  async settlement(stId) {
    let act;
    try { act = await get(`/api/stocktake/${stId}/settlement`); }
    catch (e) { return Toast.error(e.message); }
    const money = (n) => (n > 0 ? '+' : '') + n.toFixed(2);
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Settlement act</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <p style="color:var(--tx-3);font-size:13px;margin:0 0 12px">
          Every discrepancy, valued at cost — what the count actually cost the business.
        </p>
        <div style="display:flex;gap:18px;margin-bottom:12px;font-size:13px">
          <div>Surplus <b style="color:var(--green)">${money(act.surplus_value)}</b></div>
          <div>Shortage <b style="color:var(--red,#e5484d)">${money(act.shortage_value)}</b></div>
          <div>Net <b>${money(act.net_value)}</b></div>
        </div>
        <div class="tbl-wrap" style="max-height:340px;overflow:auto"><table class="tbl">
          <thead><tr><th>Product</th><th>System</th><th>Counted</th><th>Variance</th><th>Value</th></tr></thead>
          <tbody>${act.lines.map(l => `<tr>
            <td class="tbl-strong">${l.part_name}
              ${l.is_unknown ? '<span class="badge">new</span>' : ''}
              ${l.serial_mismatch ? '<span class="badge">serial?</span>' : ''}</td>
            <td>${l.system_qty}</td><td>${l.counted_qty}</td>
            <td style="font-weight:700;color:${l.variance > 0 ? 'var(--green)' : 'var(--red,#e5484d)'}">${l.variance > 0 ? '+' : ''}${l.variance}</td>
            <td>${money(l.value)}</td>
          </tr>`).join('') || '<tr><td colspan="5" style="text-align:center;color:var(--tx-4);padding:20px">No discrepancies</td></tr>'}
          </tbody></table></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Close</button>
        <button class="btn btn-primary" onclick="openPrintPdf('/api/print/stocktakes/${stId}','stocktake.pdf')">Print act</button>
      </div>`, 'modal-wide');
  },

  exportSheet(stId) { openPrintPdf(`/api/stocktake/${stId}/export/csv`,'stocktake.csv'); },

  importModal(stId) {
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Import counts</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <p style="color:var(--tx-3);font-size:13px;margin:0 0 10px">
          Paste the returned count sheet (CSV with <code>code,counted_qty</code>). Unlike scanning,
          an imported number is the <b>total</b> counted — it's what the counter wrote down.
        </p>
        <textarea class="field-input" id="stk-imp" rows="8"
          placeholder="code,counted_qty&#10;WS-1,8"></textarea>
        <label class="cash-check" style="margin-top:8px">
          <input type="checkbox" id="stk-imp-unknown"> Register products the sheet has and we don't
        </label>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="StocktakePage.runImport('${stId}')">Import</button>
      </div>`);
  },

  async runImport(stId) {
    const text = document.getElementById('stk-imp').value.trim();
    if (!text) return Toast.error('Paste the count sheet first');
    const lines = text.split(/\r?\n/).filter(Boolean);
    const header = lines[0].split(',').map(x => x.trim());
    const ci = header.indexOf('code');
    const qi = header.indexOf('counted_qty');
    if (ci < 0 || qi < 0) return Toast.error('Need "code" and "counted_qty" columns');
    const rows = lines.slice(1).map(l => {
      const c = l.split(',');
      return { code: (c[ci] || '').trim(), counted_qty: (c[qi] || '').trim() };
    }).filter(r => r.code && r.counted_qty !== '');
    try {
      const r = await post(`/api/stocktake/${stId}/import`, {
        rows, code_field: 'code', qty_field: 'counted_qty',
        register_unknown: document.getElementById('stk-imp-unknown').checked });
      Modal.close();
      Toast.success(`Applied ${r.applied} count(s)`
        + (r.errors.length ? ` · ${r.errors.length} row(s) skipped` : ''));
      await this.open(stId);
    } catch (e) { Toast.error(e.message || 'Import failed'); }
  },
});

Router.register('stocktake', () => StocktakePage.load());
