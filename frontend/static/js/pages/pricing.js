/* Settings > Prices & discounts — price types are named markups over cost. */
const PricingSettingsPage = {
  async _renderDefaults() {
    let types = [], defaults = {};
    try { [types, defaults] = await Promise.all([
      get('/api/pricing/types').then(r => r.items),
      get('/api/pricing/defaults')]); } catch (e) { return; }
    const opt = (sel) => `<option value="">— none —</option>` +
      types.map(t => `<option value="${t.id}" ${sel === t.id ? 'selected' : ''}>${t.name} (+${t.markup_pct}%)</option>`).join('');
    const host = document.getElementById('pricing-defaults');
    if (!host) return;
    host.innerHTML = `
      <h3 style="margin:20px 0 6px">Company default price lists</h3>
      <p class="muted" style="font-size:12px">Which price list each document type uses by default. A location can override this on its own settings.</p>
      <div style="display:grid;grid-template-columns:120px 260px;gap:10px;align-items:center;max-width:420px">
        <label>Tickets</label><select class="field-select" id="pd-tickets">${opt(defaults.price_type_tickets_id)}</select>
        <label>Sales</label><select class="field-select" id="pd-sales">${opt(defaults.price_type_sales_id)}</select>
        <label>Invoices</label><select class="field-select" id="pd-invoices">${opt(defaults.price_type_invoices_id)}</select>
        <label>Decimal places</label><select class="field-select" id="pd-dec">${[2,3,4,5].map(n => `<option ${defaults.price_decimals === n ? 'selected' : ''}>${n}</option>`).join('')}</select>
      </div>
      <button class="btn btn-primary btn-sm" style="margin-top:12px" id="pd-save">Save defaults</button>`;
    document.getElementById('pd-save').addEventListener('click', async () => {
      try {
        await put('/api/pricing/defaults', {
          price_type_tickets_id: document.getElementById('pd-tickets').value || null,
          price_type_sales_id: document.getElementById('pd-sales').value || null,
          price_type_invoices_id: document.getElementById('pd-invoices').value || null,
          price_decimals: parseInt(document.getElementById('pd-dec').value) });
        Toast.success('Default price lists saved');
      } catch (e) { Toast.error(e.message || 'Save failed'); }
    });
  },

  async load() {
    setTopbarActions(`<button class="btn btn-primary" onclick="PricingSettingsPage.editModal()">+ New price type</button>`);
    setContent(`
      <div style="font-size:13px;color:var(--tx-3);margin-bottom:14px;max-width:680px">
        A <b>price type</b> is a markup over cost price — products use their moving-average cost,
        services use their cost price. Assign a price type to a client (on their profile) as a
        personal price level, or give them a fixed percent discount instead.
      </div>
      <div id="pt-list"></div>`);
    await this.reload();
  },

  async reload() {
    const el = document.getElementById('pt-list');
    if (!el) return;
    try {
      const data = await get('/api/pricing/types');
      this._items = data.items;
      el.innerHTML = `
        <div class="card" style="padding:0">
          <div class="tbl-wrap"><table class="tbl">
            <thead><tr><th>Price type</th><th>Markup over cost</th><th>Default</th></tr></thead>
            <tbody>
              ${data.items.map(p => `
                <tr style="cursor:pointer" ondblclick="PricingSettingsPage.editModal('${p.id}')">
                  <td class="tbl-strong">${p.name}</td>
                  <td>+${p.markup_pct}%</td>
                  <td>${p.is_default ? `<span class="badge badge-est" style="color:var(--green);border-color:var(--green)">default</span>` : ''}</td>
                </tr>`).join('') || `<tr><td colspan="3" style="text-align:center;color:var(--tx-4);padding:24px">No price types yet — create your first markup</td></tr>`}
            </tbody>
          </table></div>
        </div>
        <div id="pricing-defaults"></div>`;
      this._renderDefaults();
    } catch (e) { Toast.error(e.message); }
  },

  editModal(id) {
    const p = id ? this._items.find(x => x.id === id) : null;
    Modal.open(`
      <div class="modal-header"><span class="modal-title">${p ? 'Edit price type' : 'New price type'}</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Name</label>
          <input class="field-input" id="pt-name" value="${p ? p.name : ''}" placeholder="Wholesale, VIP, Promotional..."></div>
        <div class="field"><label class="field-label">Markup over cost price (%)</label>
          <input class="field-input" id="pt-markup" type="number" value="${p ? p.markup_pct : 50}"></div>
        <label class="cash-check"><input type="checkbox" id="pt-default" ${p && p.is_default ? 'checked' : ''}> Use as the default price</label>
      </div>
      <div class="modal-footer" style="display:flex;justify-content:space-between;align-items:center">
        <div style="display:flex;gap:8px">
          <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
          <button class="btn btn-primary" onclick="PricingSettingsPage.submit(${p ? `'${p.id}'` : 'null'})">${p ? 'Save changes' : 'Create price type'}</button>
        </div>
        ${p ? `<button class="btn btn-danger btn-sm" onclick="PricingSettingsPage.remove('${p.id}')">Delete</button>` : ''}
      </div>`);
  },

  async submit(id) {
    const body = {
      name: document.getElementById('pt-name').value.trim(),
      markup_pct: parseFloat(document.getElementById('pt-markup').value) || 0,
      is_default: document.getElementById('pt-default').checked,
    };
    if (!body.name) return Toast.error('Name is required');
    try {
      if (id) await put(`/api/pricing/types/${id}`, body);
      else await post('/api/pricing/types', body);
      Modal.close();
      Toast.success(id ? 'Price type updated' : 'Price type created');
      await this.reload();
    } catch (e) { Toast.error(e.message); }
  },

  async remove(id) {
    if (!(await confirm2('Delete this price type? Clients using it as a personal price level fall back to standard prices.'))) return;
    try { await del(`/api/pricing/types/${id}`); await this.reload(); }
    catch (e) { Toast.error(e.message); }
  },
};
