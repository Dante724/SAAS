/* Bins & serialized stock — storage locations, transfers, cost layers, serials. */
const BinsPage = {
  _bins: [], _parts: [], _locations: [],

  async load() {
    setTopbarActions(`
      <button class="btn btn-glass" onclick="BinsPage.transferModal()">Transfer between bins</button>
      <button class="btn btn-primary" onclick="BinsPage.binModal()">+ New bin</button>`);
    setContent(`
      <div style="font-size:13px;color:var(--tx-3);margin-bottom:14px;max-width:720px">
        Storage bins give every product an address inside the warehouse. Pick any product below to
        inspect its <b>cost layers</b> (FIFO batches) and <b>serialized units</b>.
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;align-items:start">
        <div><div class="page-sec-title" style="margin-bottom:8px">Bins</div><div id="bins-list"></div></div>
        <div><div class="page-sec-title" style="margin-bottom:8px">Serialized &amp; cost layers</div>
          <div class="card">
            <div class="field" style="margin-bottom:10px"><label class="field-label">Inspect product</label>
              <select class="field-select" id="bp-part" onchange="BinsPage.inspect(this.value)">
                <option value="">— pick a product —</option></select></div>
            <div id="bp-detail" style="font-size:12.5px;color:var(--tx-4)">Cost layers and serial numbers appear here.</div>
          </div>
        </div>
      </div>`);
    await this.reload();
  },

  async reload() {
    try {
      const [bins, parts] = await Promise.all([
        get('/api/bins').then(r => r.items),
        get('/api/inventory/parts').then(r => r.items)]);
      this._bins = bins; this._parts = parts;
      const el = document.getElementById('bins-list');
      if (el) el.innerHTML = `<div class="card" style="padding:0"><div class="tbl-wrap"><table class="tbl">
        <thead><tr><th>Bin</th><th>Zone / shelf</th><th>On hand</th><th style="width:80px"></th></tr></thead>
        <tbody>${bins.map(b => `<tr>
          <td class="tbl-strong">${b.name}</td>
          <td style="color:var(--tx-3)">${[b.zone, b.shelf, b.bin_number].filter(Boolean).join(' / ') || ''}</td>
          <td>${b.qty_on_hand}</td>
          <td><button class="btn btn-glass btn-sm" onclick="BinsPage.binModal('${b.id}')">Edit</button></td>
        </tr>`).join('') || `<tr><td colspan="4" style="text-align:center;color:var(--tx-4);padding:22px">No bins yet</td></tr>`}
        </tbody></table></div></div>`;
      const sel = document.getElementById('bp-part');
      if (sel) sel.innerHTML = `<option value="">— pick a product —</option>` +
        parts.map(p => `<option value="${p.id}">${p.name} (${p.qty_available ?? 0} on hand)</option>`).join('');
    } catch (e) { Toast.error(e.message); }
  },

  async inspect(partId) {
    const el = document.getElementById('bp-detail');
    if (!partId) { el.innerHTML = 'Cost layers and serial numbers appear here.'; return; }
    try {
      const d = await get(`/api/bins/part/${partId}/detail`);
      const layers = d.batches.length ? `<table class="tbl" style="margin-bottom:12px">
        <thead><tr><th>Layer (FIFO order)</th><th>Unit cost</th><th>Remaining</th><th>Bin</th></tr></thead>
        <tbody>${d.batches.map(b => `<tr>
          <td style="color:var(--tx-3)">${b.received_at ? new Date(b.received_at).toLocaleDateString() : ''}</td>
          <td class="tbl-strong">${fmt.currency(b.unit_cost)}</td>
          <td>${b.qty_remaining} / ${b.qty_received}</td>
          <td style="color:var(--tx-3)">${b.bin || ''}</td></tr>`).join('')}</tbody></table>`
        : `<div style="color:var(--tx-4);margin-bottom:12px">No cost layers — receive this product via a purchase order to create one.</div>`;
      const serials = d.serials.length ? `<div style="display:flex;flex-wrap:wrap;gap:6px">
        ${d.serials.map(s => `<span class="badge badge-solid" style="color:${s.status === 'in_stock' ? 'var(--green)' : s.status === 'allocated_to_order' ? 'var(--amber)' : 'var(--tx-4)'}">${s.serial_number} · ${s.status.replace(/_/g, ' ')}</span>`).join('')}</div>`
        : `<div style="color:var(--tx-4)">No serialized units for this product.</div>`;
      el.innerHTML = layers + serials;
    } catch (e) { Toast.error(e.message); }
  },

  binModal(id) {
    const b = id ? this._bins.find(x => x.id === id) : null;
    Modal.open(`
      <div class="modal-header"><span class="modal-title">${b ? 'Edit bin' : 'New bin'}</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Bin name / code</label>
          <input class="field-input" id="bn-name" value="${b ? b.name : ''}" placeholder="A1, RACK-3-TOP..."></div>
        <div class="field-row">
          <div class="field"><label class="field-label">Zone</label>
            <input class="field-input" id="bn-zone" value="${b && b.zone ? b.zone : ''}" placeholder="A"></div>
          <div class="field"><label class="field-label">Shelf</label>
            <input class="field-input" id="bn-shelf" value="${b && b.shelf ? b.shelf : ''}" placeholder="3"></div>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="BinsPage.submitBin(${b ? `'${b.id}'` : 'null'})">${b ? 'Save' : 'Create bin'}</button>
      </div>`);
  },

  async submitBin(id) {
    const name = document.getElementById('bn-name').value.trim();
    if (!name) return Toast.error('Bin name is required');
    let me = {};
    try { me = await get('/api/auth/me'); } catch {}
    const body = { location_id: me.location_id, name,
      zone: document.getElementById('bn-zone').value || null,
      shelf: document.getElementById('bn-shelf').value || null };
    try {
      if (id) await put(`/api/bins/${id}`, body); else await post('/api/bins', body);
      Modal.close(); Toast.success(id ? 'Bin updated' : 'Bin created');
      await this.reload();
    } catch (e) { Toast.error(e.message); }
  },

  transferModal() {
    if (this._bins.length < 2) return Toast.error('You need at least two bins to transfer');
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Transfer between bins</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Product</label>
          <select class="field-select" id="tr-part">${this._parts.map(p => `<option value="${p.id}">${p.name}</option>`).join('')}</select></div>
        <div class="field-row">
          <div class="field"><label class="field-label">From bin</label>
            <select class="field-select" id="tr-src">${this._bins.map(b => `<option value="${b.id}">${b.name}</option>`).join('')}</select></div>
          <div class="field"><label class="field-label">To bin</label>
            <select class="field-select" id="tr-dst">${this._bins.map((b, i) => `<option value="${b.id}" ${i === 1 ? 'selected' : ''}>${b.name}</option>`).join('')}</select></div>
        </div>
        <div class="field"><label class="field-label">Quantity</label>
          <input class="field-input" id="tr-qty" type="number" value="1"></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="BinsPage.submitTransfer()">Transfer</button>
      </div>`);
  },

  async submitTransfer() {
    try {
      await post('/api/bins/transfer', {
        part_id: document.getElementById('tr-part').value,
        source_bin_id: document.getElementById('tr-src').value,
        destination_bin_id: document.getElementById('tr-dst').value,
        qty: parseFloat(document.getElementById('tr-qty').value) || 0,
      });
      Modal.close(); Toast.success('Stock moved'); await this.reload();
    } catch (e) { Toast.error(e.message); }
  },
};

Router.register('bins', () => BinsPage.load());
