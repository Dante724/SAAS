/* Build 94 — Suppliers.

   Three things live here: who you buy from, their price list (imported rather than
   retyped), and what's on the way — including whether it's already promised to a
   customer, and whether its expected date has quietly gone by. */
const SuppliersPage = {
  _tab: 'suppliers',
  _items: [],
  _rows: [], _cols: [],
  // Build 144c: when hosted under the Contacts page, render into this element
  // instead of taking over the whole page, so suppliers live under Contacts
  // without a second copy of the list.
  _embedBody: null,
  _setBody(html) {
    if (this._embedBody) { const el = document.getElementById(this._embedBody); if (el) { el.innerHTML = html; return; } }
    setContent(html);
  },
  _setActions(html) { setTopbarActions(html); },
  async mountInto(bodyId) {
    this._embedBody = bodyId;
    await this.load();
  },

  async load() {
    this._setActions(`<button class="btn btn-primary" onclick="SuppliersPage.edit()">+ New supplier</button>`);
    this._setBody(`
      <div class="tabs" style="margin-bottom:14px">
        <button class="tab ${this._tab === 'suppliers' ? 'active' : ''}" onclick="SuppliersPage.go('suppliers')">Suppliers</button>
        <button class="tab ${this._tab === 'incoming' ? 'active' : ''}" onclick="SuppliersPage.go('incoming')">Incoming stock</button>
      </div>
      <div id="sup-body"><div class="card" style="padding:18px;color:var(--tx-4)">Loading…</div></div>`);
    await this.reload();
  },

  async go(tab) { this._tab = tab; await this.load(); },

  async reload() {
    const body = document.getElementById('sup-body');
    if (!body) return;
    if (this._tab === 'incoming') return this._incoming(body);

    try { this._items = (await get('/api/suppliers')).items || []; }
    catch (e) { return Toast.error(e.message); }

    if (!this._sel) this._sel = new Set();
    body.innerHTML = `<div class="card" style="padding:0"><div class="tbl-wrap"><table class="tbl">
      <thead><tr><th style="width:34px"><input type="checkbox" id="sup-all" onclick="SuppliersPage.toggleAll(this.checked)"></th>
        <th>Supplier</th><th>Contact</th><th>Lead time</th>
        <th style="text-align:right">Balance</th><th></th></tr></thead>
      <tbody>${this._items.map(s => `<tr class="${this._sel.has(s.id) ? 'row-sel' : ''}">
        <td onclick="event.stopPropagation()"><input type="checkbox" ${this._sel.has(s.id) ? 'checked' : ''} onclick="SuppliersPage.toggleRow('${s.id}',this.checked)"></td>
        <td class="tbl-strong">${s.name}</td>
        <td style="font-size:12px;color:var(--tx-3)">${[s.contact_name, s.email, s.phone].filter(Boolean).join(' · ') || '—'}</td>
        <td>${s.lead_days ?? '—'} day(s)</td>
        <td style="text-align:right;font-weight:700;color:${s.balance > 0 ? 'var(--red,#e5484d)' : s.balance < 0 ? 'var(--green)' : 'var(--tx-3)'}">
          ${s.balance > 0 ? 'we owe ' : s.balance < 0 ? 'owed to us ' : ''}${Math.abs(s.balance).toFixed(2)}</td>
        <td style="text-align:right;white-space:nowrap">
          <button class="btn btn-glass btn-sm" onclick="SuppliersPage.balanceModal('${s.id}')">Balance</button>
          <button class="btn btn-glass btn-sm" onclick="SuppliersPage.priceList('${s.id}')">Price list</button>
          <button class="btn btn-glass btn-sm" onclick="SuppliersPage.edit('${s.id}')">Edit</button>
          <button class="btn btn-glass btn-sm" onclick="SuppliersPage.remove('${s.id}')">Delete</button>
        </td>
      </tr>`).join('') || `<tr><td colspan="6" style="text-align:center;color:var(--tx-4);padding:24px">
        No suppliers yet</td></tr>`}
      </tbody></table></div></div>`;
    this._syncBulk();
  },

  /* Build 144cw — bulk select + safe Export (no destructive bulk action) */
  toggleRow(ev_id, ck) {
    this._sel = this._sel || new Set();
    ck ? this._sel.add(ev_id) : this._sel.delete(ev_id);
    const all = document.getElementById('sup-all');
    if (all) all.checked = this._items.length > 0 && this._items.every(s => this._sel.has(s.id));
    this._syncBulk();
  },
  toggleAll(ck) { this._sel = new Set(ck ? this._items.map(s => s.id) : []); this.reload(); },
  _syncBulk() {
    const n = (this._sel && this._sel.size) || 0;
    if (typeof BulkBar === 'undefined') return;
    BulkBar.show(n, `<button class="btn btn-glass btn-sm" onclick="SuppliersPage.exportSelected()">Export CSV</button>`,
                 () => { this._sel = new Set(); this.reload(); });
  },
  exportSelected() {
    const rows = (this._items || []).filter(s => this._sel.has(s.id));
    if (!rows.length) return;
    const esc = v => `"${String(v ?? '').replace(/"/g, '""')}"`;
    const head = ['Name', 'Contact', 'Email', 'Phone', 'Lead days', 'Balance'];
    const body = rows.map(s => [s.name, s.contact_name, s.email, s.phone, s.lead_days, s.balance].map(esc).join(','));
    const csv = [head.join(','), ...body].join('\n');
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
    a.download = `suppliers-${new Date().toISOString().slice(0, 10)}.csv`; a.click(); URL.revokeObjectURL(a.href);
    if (typeof Toast !== 'undefined') Toast.success(`Exported ${rows.length} supplier(s)`);
  },

  async _incoming(body) {
    let data;
    try { data = await get('/api/suppliers/purchase-orders/incoming'); }
    catch (e) { return Toast.error(e.message); }
    body.innerHTML = `
      <div style="font-size:13px;color:var(--tx-3);margin-bottom:12px;max-width:740px">
        Goods on their way. A line reserved for a work order is already spoken for the moment it
        lands — it won't be sold over the counter to whoever walks in first.
      </div>
      <div class="card" style="padding:0"><div class="tbl-wrap"><table class="tbl">
        <thead><tr><th>PO</th><th>Qty</th><th>Expected</th><th>Reserved for</th></tr></thead>
        <tbody>${data.items.map(i => `<tr style="cursor:pointer" onclick="InventoryPage.openPo('${i.po_id}')" title="Open purchase order">
          <td class="tbl-strong">${i.po_number || i.po_id.slice(0, 8)}</td>
          <td>${i.qty_incoming}</td>
          <td style="color:${i.delayed ? 'var(--red,#e5484d)' : 'var(--tx-3)'};font-weight:${i.delayed ? '800' : '500'}">
            ${i.expected_at || '—'}${i.delayed ? ' · overdue' : ''}</td>
          <td>${i.reserved_for
            ? `<span class="badge b-done">${i.reserved_for}</span>`
            : '<span style="color:var(--tx-4)">unallocated</span>'}</td>
        </tr>`).join('') || `<tr><td colspan="4" style="text-align:center;color:var(--tx-4);padding:24px">
          Nothing on order</td></tr>`}
        </tbody></table></div></div>`;
  },

  edit(id = null) {
    const s = id ? this._items.find(x => x.id === id) : null;
    Modal.open(`
      <div class="modal-header"><span class="modal-title">${s ? 'Edit' : 'New'} supplier</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Name</label>
          <input class="field-input" id="sp-name" value="${s?.name || ''}"></div>
        <div class="field-row">
          <div class="field"><label class="field-label">Contact</label>
            <input class="field-input" id="sp-contact" value="${s?.contact_name || ''}"></div>
          <div class="field"><label class="field-label">Lead time (days)</label>
            <input class="field-input" type="number" id="sp-lead" value="${s?.lead_days ?? ''}"></div>
        </div>
        <div class="field-row">
          <div class="field"><label class="field-label">Email</label>
            <input class="field-input" id="sp-email" value="${s?.email || ''}"></div>
          <div class="field"><label class="field-label">Phone</label>
            <input class="field-input" id="sp-phone" value="${s?.phone || ''}"></div>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="SuppliersPage.save(${id ? `'${id}'` : 'null'})">Save</button>
      </div>`);
  },

  async save(id) {
    const name = document.getElementById('sp-name').value.trim();
    if (!name) return Toast.error('Name is required');
    const lead = document.getElementById('sp-lead').value;
    const body = {
      name,
      contact_name: document.getElementById('sp-contact').value || null,
      email: document.getElementById('sp-email').value || null,
      phone: document.getElementById('sp-phone').value || null,
      lead_days: lead === '' ? null : (parseInt(lead, 10) || 0),
    };
    try {
      if (id) await put(`/api/suppliers/${id}`, body);
      else await post('/api/suppliers', body);
      Modal.close();
      Toast.success(id ? 'Supplier updated' : 'Supplier created');
      await this.reload();
    } catch (e) { Toast.error(e.message || 'Save failed'); }
  },

  async remove(id) {
    if (!await confirm2('Delete this supplier?')) return;
    try {
      await del(`/api/suppliers/${id}`);
      Toast.success('Supplier deleted');
      await this.reload();
    } catch (e) {
      // refused while a balance is outstanding — the debt is real
      Toast.error(e.message || 'Delete failed');
    }
  },

  /* ── supplier balance ledger (build 124) ── */
  async balanceModal(id) {
    const s = this._items.find(x => x.id === id);
    let d;
    try { d = await get(`/api/suppliers/${id}/ledger`); }
    catch (e) { return Toast.error(e.message || 'Could not load balance'); }
    const label = { posting:'Posting', purchase_return:'Return', charge:'Charge', income:'Income',
      prepayment:'Prepayment', refund:'Refund', advance:'Advance', payout:'Payout', adjustment:'Adjustment' };
    const bal = d.supplier_balance || 0;
    Modal.open(`
      <div class="modal-header"><span class="modal-title">${s ? s.name : 'Supplier'} · Balance</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="detail-blob" style="margin-bottom:14px"><div class="d-label">Balance</div>
          <div class="d-value" style="font-size:22px;font-weight:900;color:${bal>0?'var(--red)':bal<0?'var(--green)':'var(--tx-1)'}">${fmt.currency(Math.abs(bal))}</div>
          <div style="font-size:11.5px;color:var(--tx-4);margin-top:2px">${bal>0?'We owe supplier':bal<0?'Supplier owes us':'Settled'}</div></div>
        ${d.entries && d.entries.length ? `<div class="tbl-wrap"><table class="tbl">
          <thead><tr><th>Type</th><th>Note</th><th style="text-align:right">Balance change</th><th>Date</th></tr></thead>
          <tbody>${d.entries.map(e=>`<tr>
            <td>${label[e.entry_type]||e.entry_type}</td>
            <td style="color:var(--tx-3)">${e.note||'—'}</td>
            <td style="text-align:right;font-weight:700;font-variant-numeric:tabular-nums;color:${e.amount>=0?'var(--green)':'var(--red)'}">${e.amount>=0?'+':''}${fmt.currency(e.amount)}</td>
            <td style="color:var(--tx-3);font-size:12px">${fmt.datetime(e.created_at)}</td></tr>`).join('')}</tbody>
        </table></div>` : '<div style="color:var(--tx-4);font-size:13px;padding:14px;text-align:center">No balance movements yet.</div>'}
      </div>
      <div class="modal-footer"><button class="btn btn-primary" onclick="Modal.close()">Close</button></div>`, 'modal-wide');
  },

  /* ── price-list import ── */
  priceList(id) {
    this._rows = []; this._cols = [];
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Import price list</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <p style="color:var(--tx-3);font-size:13px;margin:0 0 10px">
          Paste the supplier's CSV. Products are matched on <b>SKU or barcode</b> — never on
          name, which isn't stable enough to key on. Preview first: a bad mapping here would
          rewrite the cost basis of your whole catalogue.
        </p>
        <textarea class="field-input" id="pl-csv" rows="6"
          placeholder="Code,Cost&#10;WS-1,3.50"></textarea>
        <button class="btn btn-glass btn-sm" style="margin-top:8px"
          onclick="SuppliersPage.parsePL()">Read columns</button>
        <div id="pl-map" style="margin-top:12px"></div>
        <div id="pl-result" style="margin-top:10px"></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-glass" id="pl-dry" disabled onclick="SuppliersPage.runPL('${id}', true)">Preview</button>
        <button class="btn btn-primary" id="pl-go" disabled onclick="SuppliersPage.runPL('${id}', false)">Import</button>
      </div>`);
  },

  FIELDS: ['sku', 'barcode', 'name', 'cost_price', 'sell_price'],

  parsePL() {
    const text = document.getElementById('pl-csv').value.trim();
    if (!text) return Toast.error('Paste the price list first');
    const lines = text.split(/\r?\n/).filter(Boolean);
    if (lines.length < 2) return Toast.error('Need a header row and at least one row');
    const split = (l) => l.split(',').map(x => x.trim());
    this._cols = split(lines[0]);
    this._rows = lines.slice(1).map(l => {
      const cells = split(l);
      const row = {};
      this._cols.forEach((c, i) => { row[c] = cells[i] ?? ''; });
      return row;
    });
    const guess = (c) => {
      const lc = c.toLowerCase();
      if (lc.includes('sku') || lc === 'code') return 'sku';
      if (lc.includes('barcode') || lc.includes('ean')) return 'barcode';
      if (lc.includes('cost') || lc.includes('buy')) return 'cost_price';
      if (lc.includes('sell') || lc.includes('rrp')) return 'sell_price';
      if (lc.includes('name') || lc.includes('desc')) return 'name';
      return '';
    };
    document.getElementById('pl-map').innerHTML = `
      <div style="font-weight:800;font-size:12px;margin-bottom:6px">Map columns</div>
      ${this._cols.map(c => `
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
          <div style="flex:1;font-size:13px">${c}</div>
          <select class="field-select" data-plc="${c}" style="flex:1">
            <option value="">— skip —</option>
            ${this.FIELDS.map(f => `<option value="${f}" ${guess(c) === f ? 'selected' : ''}>${f}</option>`).join('')}
          </select>
        </div>`).join('')}
      <label class="cash-check" style="margin-top:6px">
        <input type="checkbox" id="pl-create"> Add products the price list has and we don't
      </label>`;
    document.getElementById('pl-dry').disabled = false;
    document.getElementById('pl-go').disabled = false;
    Toast.success(`${this._rows.length} row(s) read`);
  },

  async runPL(id, dryRun) {
    const mapping = {};
    document.querySelectorAll('[data-plc]').forEach(sel => {
      if (sel.value) mapping[sel.dataset.plc] = sel.value;
    });
    const vals = Object.values(mapping);
    const matchOn = vals.includes('sku') ? 'sku' : vals.includes('barcode') ? 'barcode' : null;
    if (!matchOn) return Toast.error('Map a column to "sku" or "barcode" so rows can be matched');

    try {
      const r = await post(`/api/suppliers/${id}/import-price-list`, {
        rows: this._rows, mapping, match_on: matchOn,
        create_missing: document.getElementById('pl-create').checked,
        dry_run: dryRun,
      });
      document.getElementById('pl-result').innerHTML = `
        <div class="card" style="padding:12px">
          <b>${dryRun ? 'Preview' : 'Imported'}:</b>
          ${r.updated} to update · ${r.created} to create · ${r.skipped} unchanged
          ${r.errors.length ? `<div style="color:var(--red,#e5484d);font-size:12px;margin-top:6px">
            ${r.errors.slice(0, 5).map(e => `Row ${e.row}: ${e.error}`).join('<br>')}</div>` : ''}
        </div>`;
      if (!dryRun) { Toast.success(`Updated ${r.updated} product(s)`); }
    } catch (e) { Toast.error(e.message || 'Import failed'); }
  },
};

// Build 144c: Suppliers now live under Contacts. Any jump to "suppliers"
// (command palette, deep link) lands on the Contacts page with the Suppliers
// tab open — one home, no duplicate list.
Router.register('suppliers', () => {
  if (typeof ClientsPage !== 'undefined') { ClientsPage._role = 'supplier'; Router.go('clients'); }
  else { SuppliersPage._embedBody = null; SuppliersPage.load(); }
});
