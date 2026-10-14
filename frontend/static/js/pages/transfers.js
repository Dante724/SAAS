/* Inter-branch transfers with transit statuses: draft -> ready -> in transit -> received -> completed. */
const TransfersPage = {
  _locations: [], _parts: [], _lines: [],
  STEPS: ['draft', 'ready', 'in_transit', 'received', 'completed'],
  LABELS: { draft: 'Draft', ready: 'Ready to ship', in_transit: 'In transit', received: 'Received', completed: 'Completed', canceled: 'Canceled' },
  NEXT_VERB: { draft: 'Mark ready (reserve stock)', ready: 'Ship (in transit)', in_transit: 'Mark received', received: 'Complete (land stock)' },

  async load() {
    setTopbarActions(`<button class="btn btn-primary" onclick="TransfersPage.newModal()">+ New transfer</button>`);
    setContent(`
      <div style="font-size:13px;color:var(--tx-3);margin-bottom:14px;max-width:760px">
        Move stock between branches as a tracked process: <b>Ready</b> reserves it at the source,
        <b>In transit</b> puts it on the road, <b>Completed</b> lands it at the destination.
        Cancel after shipping asks which warehouse takes the goods back.
      </div>
      <div id="tf-list"></div>`);
    await this.reload();
  },

  async reload() {
    const el = document.getElementById('tf-list');
    if (!el) return;
    try {
      const data = await get('/api/transfers');
      el.innerHTML = `<div class="card" style="padding:0"><div class="tbl-wrap"><table class="tbl">
        <thead><tr><th>Transfer</th><th>Route</th><th>Items</th><th>Status</th><th style="width:230px"></th></tr></thead>
        <tbody>${data.items.map(t => {
          const colr = t.status === 'completed' ? 'var(--green)' : t.status === 'canceled' ? 'var(--tx-4)' : t.status === 'draft' ? 'var(--tx-3)' : 'var(--amber)';
          const next = this.NEXT_VERB[t.status];
          return `<tr>
            <td class="tbl-strong"><a href="#" onclick="TransfersPage.openDetail('${t.id}');return false" style="color:var(--blue);text-decoration:none">${t.transfer_number}</a></td>
            <td>${t.from_location} <span style="color:var(--tx-4)">to</span> ${t.to_location}</td>
            <td style="color:var(--tx-3)">${t.lines.map(l => `${l.qty}x ${l.part_name}`).join(', ')}</td>
            <td style="color:${colr};font-weight:600">${this.LABELS[t.status]}</td>
            <td style="text-align:right">
              ${(t.status === 'in_transit' || t.status === 'received')
                ? `<button class="btn btn-glass btn-sm" onclick="TransfersPage.verify('${t.id}')">Verify arrival</button>` : ''}
              <button class="btn btn-glass btn-sm" onclick="openPrintPdf('/api/print/transfers/${t.id}','transfer.pdf')">Print</button>
              ${next ? `<button class="btn btn-primary btn-sm" onclick="TransfersPage.advance('${t.id}','${this.STEPS[this.STEPS.indexOf(t.status) + 1]}')">${next}</button>` : ''}
              ${!['completed', 'canceled'].includes(t.status) ? `<button class="btn btn-glass btn-sm" onclick="TransfersPage.cancel('${t.id}','${t.status}','${t.from_location_id}','${t.to_location_id}')">Cancel</button>` : ''}
            </td></tr>`;
        }).join('') || `<tr><td colspan="5" style="text-align:center;color:var(--tx-4);padding:22px">No transfers yet</td></tr>`}
        </tbody></table></div></div>`;
    } catch (e) { Toast.error(e.message); }
  },

  async openDetail(id) {
    let t;
    try { t = await get(`/api/transfers/${id}`); }
    catch (e) { return Toast.error(e.message); }
    const colr = t.status === 'completed' ? 'var(--green)' : t.status === 'canceled' ? 'var(--tx-4)' : t.status === 'draft' ? 'var(--tx-3)' : 'var(--amber)';
    const stageBar = t.stage_index < 0 ? '' : `
      <div style="display:flex;align-items:center;margin:4px 0 18px">
        ${t.stages.map((s, i) => `
          <div style="flex:1;text-align:center;position:relative">
            <div style="width:22px;height:22px;border-radius:50%;margin:0 auto;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:800;
              background:${i <= t.stage_index ? 'var(--blue)' : 'var(--line,rgba(0,0,0,.08))'};color:${i <= t.stage_index ? '#fff' : 'var(--tx-4)'}">${i < t.stage_index ? '&#10003;' : i + 1}</div>
            <div style="font-size:9.5px;font-weight:700;margin-top:5px;color:${i <= t.stage_index ? 'var(--tx-1)' : 'var(--tx-4)'};text-transform:capitalize">${this.LABELS[s] || s}</div>
            ${i < t.stages.length - 1 ? `<div style="position:absolute;top:11px;left:50%;width:100%;height:2px;background:${i < t.stage_index ? 'var(--blue)' : 'var(--line,rgba(0,0,0,.08))'};z-index:-1"></div>` : ''}
          </div>`).join('')}
      </div>`;
    Modal.open(`
      <div class="modal-header">
        <span class="modal-title">${t.transfer_number}
          <span style="color:${colr};font-weight:700;font-size:13px;margin-left:8px">${t.status === 'canceled' ? 'Canceled' : (this.LABELS[t.status] || t.status)}</span></span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div style="font-size:13px;color:var(--tx-2);margin-bottom:14px">
          <b>${t.from_location}</b> <span style="color:var(--tx-4)">&rarr;</span> <b>${t.to_location}</b>
          <span style="float:right;color:var(--tx-4)">${fmt.datetime(t.created_at)}</span></div>
        ${stageBar}
        ${t.note ? `<div style="font-size:12.5px;color:var(--tx-3);background:var(--hover,rgba(0,0,0,.04));padding:9px 12px;border-radius:8px;margin-bottom:14px">${t.note}</div>` : ''}
        <div class="tbl-wrap"><table class="tbl">
          <thead><tr><th>Product</th><th>SKU</th><th style="text-align:right">Qty</th><th style="text-align:right">Unit cost</th><th style="text-align:right">Value</th></tr></thead>
          <tbody>${t.lines.map(l => `<tr>
            <td class="tbl-strong">${l.part_name}</td>
            <td style="color:var(--tx-4)">${l.sku || '—'}</td>
            <td style="text-align:right">${l.qty}${l.uom ? ' ' + l.uom : ''}</td>
            <td style="text-align:right">${fmt.currency(l.unit_cost)}</td>
            <td style="text-align:right">${fmt.currency(l.line_value)}</td></tr>`).join('')}
          </tbody>
          <tfoot><tr style="font-weight:800;border-top:2px solid var(--line,rgba(0,0,0,.08))">
            <td colspan="2">${t.line_count} line(s)</td>
            <td style="text-align:right">${t.total_qty}</td><td></td>
            <td style="text-align:right">${fmt.currency(t.total_value)}</td></tr></tfoot>
        </table></div>
        <div style="font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.06em;color:var(--tx-3);margin:18px 0 8px">Lifecycle</div>
        ${t.events.length ? t.events.map(e => `
          <div style="display:flex;gap:10px;padding:7px 0;border-bottom:1px solid var(--line,rgba(0,0,0,.05))">
            <div style="width:7px;height:7px;border-radius:50%;background:var(--blue);margin-top:6px;flex-shrink:0"></div>
            <div style="flex:1"><div style="font-size:13px;color:var(--tx-1)">${e.body}</div>
              <div style="font-size:11px;color:var(--tx-4);margin-top:1px">${fmt.datetime(e.at)}${e.author ? ' · ' + e.author : ''}</div></div>
          </div>`).join('') : '<div style="font-size:12.5px;color:var(--tx-4)">No events recorded.</div>'}
      </div>
      <div class="modal-footer"><button class="btn btn-glass" onclick="Modal.close()">Close</button></div>`);
  },

  async advance(id, next) {
    try {
      await patch(`/api/transfers/${id}/status`, { status: next });
      Toast.success(`Transfer ${this.LABELS[next].toLowerCase()}`);
      await this.reload();
    } catch (e) { Toast.error(e.message); }
  },

  async cancel(id, status, fromId, toId) {
    let body = { status: 'canceled' };
    if (status === 'in_transit' || status === 'received') {
      const back = window.prompt('Goods already shipped. Type FROM to return them to the source, or TO to keep them at the destination:', 'FROM');
      if (back === null) return;
      body.return_location_id = back.trim().toUpperCase() === 'TO' ? toId : fromId;
    } else if (!(await confirm2('Cancel this transfer?'))) return;
    try {
      await patch(`/api/transfers/${id}/status`, body);
      Toast.success('Transfer canceled');
      await this.reload();
    } catch (e) { Toast.error(e.message); }
  },

  async newModal() {
    try {
      [this._locations, this._parts] = await Promise.all([
        get('/api/staff/locations').then(r => r.items),
        get('/api/inventory/parts').then(r => r.items)]);
    } catch (e) { return Toast.error(e.message); }
    if (this._locations.length < 2) return Toast.error('You need a second branch to transfer to (add one in Staff > Company)');
    this._lines = [];
    Modal.open(`
      <div class="modal-header"><span class="modal-title">New transfer</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field-row">
          <div class="field"><label class="field-label">From warehouse</label>
            <select class="field-select" id="tf-from">${this._locations.map(l => `<option value="${l.id}">${l.name}</option>`).join('')}</select></div>
          <div class="field"><label class="field-label">To warehouse</label>
            <select class="field-select" id="tf-to">${this._locations.map((l, i) => `<option value="${l.id}" ${i === 1 ? 'selected' : ''}>${l.name}</option>`).join('')}</select></div>
        </div>
        <div class="field"><label class="field-label">Add product</label>
          <select class="field-select" id="tf-part" onchange="TransfersPage.addLine()">
            <option value="">— pick a product —</option>
            ${this._parts.map(p => `<option value="${p.id}">${p.name} (${p.qty_available ?? 0} on hand)</option>`).join('')}</select></div>
        <div id="tf-lines"></div>
        <div class="field" style="margin-top:10px"><label class="field-label">Note</label>
          <input class="field-input" id="tf-note" placeholder="Why are these moving?"></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="TransfersPage.submit()">Create draft</button>
      </div>`);
    this.renderLines();
  },

  addLine() {
    const sel = document.getElementById('tf-part');
    if (!sel.value) return;
    const p = this._parts.find(x => x.id === sel.value);
    this._lines.push({ part_id: p.id, name: p.name, qty: 1 });
    sel.value = '';
    this.renderLines();
  },

  renderLines() {
    const el = document.getElementById('tf-lines');
    if (!el) return;
    el.innerHTML = this._lines.length ? `<table class="tbl">
      <thead><tr><th>Product</th><th style="width:90px">Qty</th><th style="width:36px"></th></tr></thead>
      <tbody>${this._lines.map((l, i) => `<tr>
        <td>${l.name}</td>
        <td><input class="field-input" style="padding:4px 8px" type="number" value="${l.qty}" onchange="TransfersPage._lines[${i}].qty=parseFloat(this.value)||0"></td>
        <td><button class="btn btn-glass btn-sm" onclick="TransfersPage._lines.splice(${i},1);TransfersPage.renderLines()">×</button></td>
      </tr>`).join('')}</tbody></table>` :
      `<div style="text-align:center;color:var(--tx-4);font-size:12.5px;padding:14px 0">Pick the products to move</div>`;
  },

  async submit() {
    if (!this._lines.length) return Toast.error('Add at least one product');
    try {
      await post('/api/transfers', {
        from_location_id: document.getElementById('tf-from').value,
        to_location_id: document.getElementById('tf-to').value,
        note: document.getElementById('tf-note').value || null,
        lines: this._lines.map(l => ({ part_id: l.part_id, qty: l.qty })),
      });
      Modal.close(); Toast.success('Transfer drafted'); await this.reload();
    } catch (e) { Toast.error(e.message); }
  },
};

Router.register('transfers', () => TransfersPage.load())

/* ── Build 93: confirm what actually arrived ── */
Object.assign(TransfersPage, {
  // A box that leaves with ten and arrives with nine is the normal case this exists for.
  // Until it's verified, completing assumes everything arrived; once verified, the far
  // end is credited with the real count and the shortfall is booked as a loss.
  async verify(id) {
    let t;
    try { t = await get(`/api/transfers/${id}`); }
    catch (e) { return Toast.error(e.message); }
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Verify arrival — ${t.transfer_number || ''}</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <p style="color:var(--tx-3);font-size:13px;margin:0 0 12px">
          Enter what actually turned up. Anything short of what was shipped is recorded as a
          loss against the source rather than quietly reappearing as stock here.
        </p>
        <div class="tbl-wrap"><table class="tbl">
          <thead><tr><th>Product</th><th style="width:110px">Shipped</th><th style="width:140px">Received</th></tr></thead>
          <tbody>${(t.lines || []).map(l => `<tr>
            <td class="tbl-strong">${l.part_name || l.part_id}</td>
            <td>${l.qty}</td>
            <td><input class="field-input" style="padding:4px 8px" type="number" min="0" max="${l.qty}"
                       data-line="${l.id}" value="${l.qty_received ?? l.qty}"></td>
          </tr>`).join('')}</tbody>
        </table></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="TransfersPage.saveVerify('${id}')">Confirm arrival</button>
      </div>`, 'modal-wide');
  },

  async saveVerify(id) {
    const lines = [...document.querySelectorAll('[data-line]')].map(el => ({
      line_id: el.dataset.line,
      qty_received: parseFloat(el.value) || 0,
    }));
    try {
      const r = await post(`/api/transfers/${id}/receive`, { lines });
      Modal.close();
      Toast.success(r.discrepancies
        ? `Arrival confirmed — ${r.discrepancies} discrepancy(ies) recorded`
        : 'Arrival confirmed — everything matched');
      await this.reload();
    } catch (e) { Toast.error(e.message || 'Could not confirm arrival'); }
  },

  async scanLine(id) {
    const el = document.getElementById('tr-scan');
    const code = el.value.trim();
    if (!code) return;
    try {
      const r = await post(`/api/transfers/${id}/scan`, { code });
      el.value = ''; el.focus();
      Toast.success(`${r.part_name}: ${r.qty}`);
      await this.reload();
    } catch (e) { Toast.error(e.message || 'Nothing matched that code'); el.select(); }
  },
});

Router.register('transfers', () => TransfersPage.load());
