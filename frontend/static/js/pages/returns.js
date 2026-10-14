/* Returns (goods back) & Refunds (money back) — separate documents. */
const ReturnsPage = {
  _parts: [], _accounts: [], _clients: [], _orders: [], _retLines: [],

  async load() {
    setTopbarActions(`
      <button class="btn btn-glass" onclick="ReturnsPage.refundModal()">New refund</button>
      <button class="btn btn-primary" onclick="ReturnsPage.returnModal()">+ New return</button>`);
    setContent(`
      <div style="font-size:13px;color:var(--tx-3);margin-bottom:14px;max-width:720px">
        <b>Returns</b> bring goods back to the warehouse; <b>Refunds</b> send money back from a cashbox.
        They are separate documents — a refund tied to an order also claws back its commissions.
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;align-items:start" id="ret-grid">
        <div><div class="page-sec-title" style="margin-bottom:8px">Returns</div><div id="ret-list"></div></div>
        <div><div class="page-sec-title" style="margin-bottom:8px">Refunds</div><div id="rf-list"></div></div>
      </div>`);
    await this.reload();
  },

  async reload() {
    try {
      const [rets, rfs] = await Promise.all([get('/api/returns'), get('/api/returns/refunds').catch(() => ({items:[]}))]);
      const rl = document.getElementById('ret-list');
      if (rl) rl.innerHTML = `<div class="card" style="padding:0"><div class="tbl-wrap"><table class="tbl">
        <thead><tr><th>When</th><th>Order</th><th>Items</th><th>Reason</th></tr></thead>
        <tbody>${rets.items.map(r => `<tr>
          <td style="color:var(--tx-3)">${new Date(r.created_at).toLocaleDateString()}</td>
          <td class="tbl-strong">${r.order_number || (r.client_name || 'no order')}</td>
          <td>${r.lines.map(l => `${l.qty}x ${l.part_name}${l.restock ? '' : ' (no restock)'}`).join(', ')}</td>
          <td style="color:var(--tx-3)">${r.reason || ''}</td></tr>`).join('') ||
          `<tr><td colspan="4" style="text-align:center;color:var(--tx-4);padding:22px">No returns yet</td></tr>`}
        </tbody></table></div></div>`;
      const fl = document.getElementById('rf-list');
      if (fl) fl.innerHTML = `<div class="card" style="padding:0"><div class="tbl-wrap"><table class="tbl">
        <thead><tr><th>When</th><th>Order</th><th>Amount</th><th>From</th></tr></thead>
        <tbody>${rfs.items.map(r => `<tr>
          <td style="color:var(--tx-3)">${new Date(r.created_at).toLocaleDateString()}</td>
          <td class="tbl-strong">${r.order_number || 'standalone'}</td>
          <td style="color:var(--red);font-weight:700">-${fmt.currency(r.amount)}</td>
          <td style="color:var(--tx-3)">${r.account_name || ''}</td></tr>`).join('') ||
          `<tr><td colspan="4" style="text-align:center;color:var(--tx-4);padding:22px">No refunds yet</td></tr>`}
        </tbody></table></div></div>`;
    } catch (e) { Toast.error(e.message); }
  },

  async returnModal() {
    try {
      [this._parts, this._orders, this._clients] = await Promise.all([
        get('/api/inventory/parts').then(r => r.items),
        get('/api/orders?limit=50').then(r => r.items),
        get('/api/clients?limit=50').then(r => r.items)]);
    } catch (e) { return Toast.error(e.message); }
    this._retLines = [];
    Modal.open(`
      <div class="modal-header"><span class="modal-title">New client return</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field-row">
          <div class="field"><label class="field-label">Linked order (optional)</label>
            <select class="field-select" id="rt-order"><option value="">No order</option>
              ${this._orders.map(o => `<option value="${o.id}">${o.order_number} — ${o.client_name || ''}</option>`).join('')}</select></div>
          <div class="field"><label class="field-label">Client (optional)</label>
            <select class="field-select" id="rt-client"><option value="">—</option>
              ${this._clients.map(c => `<option value="${c.id}">${c.name}</option>`).join('')}</select></div>
        </div>
        <div class="field"><label class="field-label">Add product</label>
          <select class="field-select" id="rt-part" onchange="ReturnsPage.addRetLine()">
            <option value="">— pick the returned product —</option>
            ${this._parts.map(p => `<option value="${p.id}">${p.name}</option>`).join('')}</select></div>
        <div id="rt-lines"></div>
        <div class="field" style="margin-top:10px"><label class="field-label">Reason</label>
          <input class="field-input" id="rt-reason" placeholder="Defective, wrong part, changed mind..."></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="ReturnsPage.submitReturn()">Create return</button>
      </div>`);
    this.renderRetLines();
  },

  addRetLine() {
    const sel = document.getElementById('rt-part');
    if (!sel.value) return;
    const p = this._parts.find(x => x.id === sel.value);
    this._retLines.push({ part_id: p.id, name: p.name, qty: 1, restock: true });
    sel.value = '';
    this.renderRetLines();
  },

  renderRetLines() {
    const el = document.getElementById('rt-lines');
    if (!el) return;
    el.innerHTML = this._retLines.length ? `<table class="tbl">
      <thead><tr><th>Product</th><th style="width:80px">Qty</th><th style="width:110px">Restock</th><th style="width:36px"></th></tr></thead>
      <tbody>${this._retLines.map((l, i) => `<tr>
        <td>${l.name}</td>
        <td><input class="field-input" style="padding:4px 8px" type="number" value="${l.qty}" onchange="ReturnsPage._retLines[${i}].qty=parseFloat(this.value)||0"></td>
        <td><label class="cash-check" style="margin:0"><input type="checkbox" ${l.restock ? 'checked' : ''} onchange="ReturnsPage._retLines[${i}].restock=this.checked"> to stock</label></td>
        <td><button class="btn btn-glass btn-sm" onclick="ReturnsPage._retLines.splice(${i},1);ReturnsPage.renderRetLines()">×</button></td>
      </tr>`).join('')}</tbody></table>` :
      `<div style="text-align:center;color:var(--tx-4);font-size:12.5px;padding:14px 0">Pick the products coming back</div>`;
  },

  async submitReturn() {
    if (!this._retLines.length) return Toast.error('Add at least one product');
    try {
      await post('/api/returns', {
        order_id: document.getElementById('rt-order').value || null,
        client_id: document.getElementById('rt-client').value || null,
        reason: document.getElementById('rt-reason').value || null,
        lines: this._retLines.map(l => ({ part_id: l.part_id, qty: l.qty, restock: l.restock })),
      });
      Modal.close(); Toast.success('Return created — restocked items are back in the warehouse');
      await this.reload();
    } catch (e) { Toast.error(e.message); }
  },

  async refundModal() {
    try {
      [this._accounts, this._orders] = await Promise.all([
        get('/api/cash/accounts').then(r => r.items),
        get('/api/orders?limit=50').then(r => r.items)]);
    } catch (e) { return Toast.error(e.message); }
    Modal.open(`
      <div class="modal-header"><span class="modal-title">New refund</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field-row">
          <div class="field"><label class="field-label">Refund from cashbox</label>
            <select class="field-select" id="rf-acct">${this._accounts.map(a => `<option value="${a.id}">${a.name} (${fmt.currency(a.balance)})</option>`).join('')}</select></div>
          <div class="field"><label class="field-label">Amount</label>
            <input class="field-input" id="rf-amt" type="number" placeholder="0.00"></div>
        </div>
        <div class="field"><label class="field-label">Linked order (claws back its commissions)</label>
          <select class="field-select" id="rf-order"><option value="">No order</option>
            ${this._orders.map(o => `<option value="${o.id}">${o.order_number} — ${o.client_name || ''}</option>`).join('')}</select></div>
        <div class="field"><label class="field-label">Reason</label>
          <input class="field-input" id="rf-reason" placeholder="Why is money going back?"></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="ReturnsPage.submitRefund()">Post refund</button>
      </div>`);
  },

  async submitRefund() {
    const amt = parseFloat(document.getElementById('rf-amt').value);
    if (!amt || amt <= 0) return Toast.error('Enter a positive amount');
    try {
      await post('/api/returns/refunds', {
        account_id: document.getElementById('rf-acct').value,
        amount: amt,
        order_id: document.getElementById('rf-order').value || null,
        reason: document.getElementById('rf-reason').value || null,
      });
      Modal.close(); Toast.success('Refund posted to the cash ledger');
      await this.reload();
    } catch (e) { Toast.error(e.message); }
  },
};

Router.register('returns', () => ReturnsPage.load());
