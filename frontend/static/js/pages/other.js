/* ════════════════════════════════════════════════════
   Finance page
════════════════════════════════════════════════════ */

const FinancePage = {
  _tab: 'invoices',
  async load() {
    // Build 144h: Finance now has Invoices and Payroll calculation (Orderry-style).
    setContent(`
      <div class="rep-tabs" style="margin-bottom:16px">
        <button class="rep-tab ${this._tab!=='payroll'&&this._tab!=='accruals'?'active':''}" onclick="FinancePage.setTab('invoices')">Invoices</button>
        <button class="rep-tab ${this._tab==='payroll'?'active':''}" onclick="FinancePage.setTab('payroll')">Payroll calculation</button>
        <button class="rep-tab ${this._tab==='accruals'?'active':''}" onclick="FinancePage.setTab('accruals')">Payroll accruals</button>
      </div>
      <div id="fin-body"></div>`);
    if (this._tab === 'payroll') {
      setTopbarActions('');
      await PayrollCalcPage.mountInto('fin-body');
      return;
    }
    if (this._tab === 'accruals') {
      setTopbarActions('');
      await PayrollAccrualsPage.mountInto('fin-body');
      return;
    }
    await this._showInvoices();
  },

  setTab(t) { this._tab = t; this.load(); },

  async _showInvoices() {
    setTopbarActions(`<button class="btn btn-primary" onclick="FinancePage.newInvoiceModal()">+ New invoice</button>`);
    const body = document.getElementById('fin-body');
    if (!body) return;
    body.innerHTML = `
      <div class="stats-grid" style="margin-bottom:20px" id="fin-stats">
        ${Array(4).fill(0).map(()=>`<div class="card stat-card"><div class="skeleton" style="height:80px"></div></div>`).join('')}
      </div>
      <div class="card" id="fin-inv-card">
        <div class="section-hd" style="padding:18px 20px 0">
          <div class="section-title">Invoices</div>
          <div class="chip-row" id="inv-filter">
            ${['','draft','partial','paid','overdue'].map((s,i)=>
              `<div class="chip${i===0?' active':''}" data-s="${s}" onclick="FinancePage.filterInv('${s}',this)">${s||'All'}</div>`
            ).join('')}
          </div>
        </div>
        <div class="tbl-wrap"><table class="tbl">
          <thead><tr><th>Invoice #</th><th>Order</th><th>Status</th><th>Total</th><th>Paid</th><th>Balance due</th><th>Issued</th><th></th></tr></thead>
          <tbody id="inv-tbody">${skeletonRows(8)}</tbody>
        </table></div>
      </div>`;
    this._invStatus = '';
    await this.loadSummary();
    await this.loadInvoices();
  },

  _invStatus: '',
  filterInv(s, el) {
    this._invStatus = s;
    document.querySelectorAll('#inv-filter .chip').forEach(c=>c.classList.remove('active'));
    el.classList.add('active');
    this.loadInvoices();
  },

  async loadSummary() {
    try {
      const s = await get('/api/finance/reports/summary');
      document.getElementById('fin-stats').innerHTML = [
        {icon:'wallet',label:'Total revenue',val:fmt.currency(s.total_revenue),accent:'#3dca7a'},
        {icon:'hourglass',label:'Outstanding',  val:fmt.currency(s.total_outstanding),accent:'#f5a623'},
        {icon:'trending',label:'Gross profit', val:fmt.currency(s.gross_profit),accent:'#4f8ef7'},
        {icon:'receipt',label:'Invoices',     val:s.invoice_count,accent:'#9b7bf4'},
      ].map(c=>`<div class="card stat-card" style="--accent-blob:${c.accent}1a">
        <div class="stat-icon" style="background:${c.accent}22;color:${c.accent}">${icon(c.icon,16)}</div>
        <div class="stat-val">${c.val}</div><div class="stat-label">${c.label}</div>
      </div>`).join('');
    } catch {}
  },

  async loadInvoices() {
    const tbody = document.getElementById('inv-tbody');
    if (!tbody) return;
    try {
      const data = await get(`/api/finance/invoices?limit=100${this._invStatus?'&status='+this._invStatus:''}${(typeof App !== 'undefined' && App.locParam) ? App.locParam('&') : ''}`);
      if (!data.items.length) { tbody.innerHTML = emptyRow(8,'wallet','No invoices yet','Invoices are auto-created when orders close'); return; }
      tbody.innerHTML = data.items.map(inv=>`<tr>
        <td><a class="tbl-num tbl-strong" style="cursor:pointer;color:var(--blue-dark)" onclick="FinancePage.invoiceDetail('${inv.id}')">${inv.invoice_number}</a></td>
        <td style="color:var(--tx-2);font-size:12px" class="tbl-num">${inv.order_number || (inv.order_id?'—':'—')}</td>
        <td>${badge(inv.status)}</td>
        <td><span class="tbl-strong">${fmt.currency(inv.total)}</span></td>
        <td style="color:var(--tx-2)">${fmt.currency(inv.amount_paid)}</td>
        <td><span style="font-weight:900;font-family:var(--mono);color:${inv.balance_due>0?'var(--red)':'var(--green)'}">${fmt.currency(inv.balance_due)}</span></td>
        <td style="color:var(--tx-3);font-size:12px">${fmt.date(inv.issued_at)}</td>
        <td>${inv.status!=='paid'
          ?`<button class="btn btn-success btn-sm" onclick="FinancePage.payModal('${inv.id}',${inv.balance_due})">Pay ₹${Math.round(inv.balance_due).toLocaleString('en-IN')}</button>`
          :'<span style="color:var(--green);font-size:12px;font-weight:800">Paid</span>'}</td>
      </tr>`).join('');
    } catch (e) { Toast.error(e.message); }
  },

  async invoiceDetail(id) {
    try {
      const inv = await get(`/api/finance/invoices/${id}`);
      this._renderInvoiceDetail(inv);
    } catch (e) { Toast.error(e.message); }
  },

  _invLineRows(inv) {
    return (inv.lines||[]).map(l=>`<tr>
        <td>${l.description||''}</td>
        <td style="text-align:right;color:var(--tx-2)">${l.qty}</td>
        <td style="text-align:right;color:var(--tx-2)">${fmt.currency(l.unit_price)}</td>
        <td style="text-align:right"><span class="tbl-strong">${fmt.currency(l.total)}</span></td>
      </tr>`).join('') || `<tr><td colspan="4" style="color:var(--tx-3);text-align:center;padding:16px">No lines</td></tr>`;
  },

  _invOrderChips(inv) {
    const orders = inv.orders||[];
    return orders.map(o=>`<span class="chip" style="display:inline-flex;align-items:center;gap:6px;margin:0 6px 6px 0;padding:5px 10px;background:var(--g-fill);border:1px solid var(--g-border);border-radius:var(--r-sm)">
        <span class="tbl-strong">${o.order_number}</span>
        ${orders.length>1?`<span style="cursor:pointer;color:var(--tx-3)" title="Remove from invoice" onclick="FinancePage.detachOrder('${inv.id}','${o.id}')">×</span>`:''}
      </span>`).join('');
  },

  _invPayer(inv) {
    const p = inv.payer || inv.customer;
    const isPayer = !!inv.payer;
    return `<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
        <span class="tbl-strong">${p ? p.name : '—'}</span>
        ${p && p.email ? `<span style="color:var(--tx-3);font-size:12px">${p.email}</span>` : ''}
        ${isPayer ? '<span class="badge" style="background:var(--g-fill);border:1px solid var(--g-border);padding:2px 8px;border-radius:8px;font-size:11px">payer ≠ customer</span>' : ''}
        <button class="btn btn-glass btn-sm" onclick="FinancePage.changePayer('${inv.id}')">Change payer</button>
        ${isPayer ? `<button class="btn btn-glass btn-sm" onclick="FinancePage.setPayer('${inv.id}', null)">Reset to customer</button>` : ''}
      </div>`;
  },

  changePayer(id) {
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Bill to a different payer</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <p style="color:var(--tx-3);font-size:13px;margin:0 0 12px">Choose the party that should receive and pay this invoice (e.g. an employer, insurer, or fleet account).</p>
        <div id="payer-pick"></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="FinancePage._confirmPayer('${id}')">Set payer</button>
      </div>`, 'modal-narrow', true);
    setTimeout(() => ClientPicker.mount('payer-pick'), 40);
  },

  async _confirmPayer(id) {
    const pid = ClientPicker.value;
    if (!pid) { Toast.error('Pick a client'); return; }
    await this.setPayer(id, pid, true);
  },

  async setPayer(id, payerId, closeModal) {
    try {
      const inv = await patch(`/api/finance/invoices/${id}`, { payer_client_id: payerId });
      if (closeModal) Modal.close();
      const el = document.getElementById('inv-payer'); if (el) el.innerHTML = this._invPayer(inv);
      Toast.success(payerId ? 'Payer updated' : 'Payer reset to customer'); this.loadInvoices();
    } catch (e) { Toast.error(e.message); }
  },

  sendInvoice(id, defaultEmail) {
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Send invoice by email</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">To</label><input class="field-input" id="si-to" value="${defaultEmail||''}" placeholder="client@email.com"></div>
        <div class="field"><label class="field-label">Subject</label><input class="field-input" id="si-subject" placeholder="(default: Invoice number)"></div>
        <div class="field"><label class="field-label">Message</label><textarea class="field-input" id="si-msg" rows="3" placeholder="(optional note)"></textarea></div>
        <p style="color:var(--tx-3);font-size:12px;margin:6px 0 0">The invoice PDF is attached automatically.</p>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="FinancePage._doSendInvoice('${id}')">Send</button>
      </div>`, 'modal-narrow', true);
  },

  async _doSendInvoice(id) {
    try {
      const r = await post(`/api/finance/invoices/${id}/send`, {
        to: (document.getElementById('si-to').value||'').trim() || null,
        subject: (document.getElementById('si-subject').value||'').trim() || null,
        message: (document.getElementById('si-msg').value||'').trim() || null,
      });
      Modal.close();
      if (r.status === 'sent') Toast.success('Invoice emailed to ' + r.recipient);
      else if (r.status === 'simulated') Toast.success('Queued (connect an email account in Settings to deliver live)');
      else Toast.error(r.error || 'Send failed');
    } catch (e) { Toast.error(e.message); }
  },

  _renderInvoiceDetail(inv) {
    const MODES = [['detailed','Detailed — every line'],
                   ['simplified','Simplified — one line per ticket'],
                   ['one_line','One line — single line for the invoice']];
    Modal.open(`
      <div class="modal-header"><span class="modal-title">${inv.invoice_number}</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div style="display:flex;gap:20px;flex-wrap:wrap;align-items:flex-end">
          <div class="field" style="max-width:340px;flex:1;min-width:240px">
            <label class="field-label">Order display</label>
            <select class="field-select" id="inv-mode" onchange="FinancePage.setInvoiceMode('${inv.id}', this.value)">
              ${MODES.map(([v,t])=>`<option value="${v}" ${inv.display_mode===v?'selected':''}>${t}</option>`).join('')}
            </select>
          </div>
        </div>
        <div style="margin-top:16px">
          <label class="field-label">Bill to (payer)</label>
          <div id="inv-payer" style="margin:8px 0">${this._invPayer(inv)}</div>
        </div>
        <div style="margin-top:16px">
          <label class="field-label">Work orders on this invoice</label>
          <div id="inv-orders" style="margin:8px 0">${this._invOrderChips(inv)}</div>
          <div style="display:flex;gap:8px;max-width:360px">
            <input class="field-input" id="inv-add-order" placeholder="Work order number to add…">
            <button class="btn btn-glass btn-sm" onclick="FinancePage.attachOrder('${inv.id}')">Add</button>
          </div>
        </div>
        <table class="tbl" style="margin-top:18px;width:100%">
          <thead><tr><th>Description</th><th style="text-align:right">Qty</th><th style="text-align:right">Unit</th><th style="text-align:right">Total</th></tr></thead>
          <tbody id="inv-lines">${this._invLineRows(inv)}</tbody>
        </table>
        <div style="text-align:right;margin-top:14px;font-weight:900;font-size:18px;color:var(--blue-dark)">${fmt.currency(inv.total)}</div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Close</button>
        <button class="btn btn-glass" onclick="openPrintPdf('/api/print/invoices/${inv.id}','invoice-${inv.invoice_number}.pdf')">Print / PDF</button>
        <button class="btn btn-glass" onclick="FinancePage.sendInvoice('${inv.id}','${(inv.payer&&inv.payer.email)||(inv.customer&&inv.customer.email)||''}')">Send by email</button>
        ${inv.status!=='paid'?`<button class="btn btn-success" onclick="Modal.close();FinancePage.payModal('${inv.id}',${inv.balance_due})">Record payment</button>`:''}
      </div>`, 'modal-wide');
  },

  _refreshInvLines(inv) {
    const tb = document.getElementById('inv-lines'); if (tb) tb.innerHTML = this._invLineRows(inv);
    const oc = document.getElementById('inv-orders'); if (oc) oc.innerHTML = this._invOrderChips(inv);
  },

  async setInvoiceMode(id, mode) {
    try { this._refreshInvLines(await patch(`/api/finance/invoices/${id}`, { display_mode: mode })); }
    catch (e) { Toast.error(e.message); }
  },

  async attachOrder(id) {
    const num = (document.getElementById('inv-add-order').value||'').trim();
    if (!num) return;
    try {
      // resolve the order number to an id
      const res = await get(`/api/orders?search=${encodeURIComponent(num)}&limit=5`);
      const list = res.items||res||[];
      const match = list.find(o=>(o.order_number||'')===num) || list[0];
      if (!match) { Toast.error('Work order not found'); return; }
      const inv = await post(`/api/finance/invoices/${id}/orders`, { order_id: match.id });
      document.getElementById('inv-add-order').value='';
      this._refreshInvLines(inv); Toast.success('Order added'); this.loadInvoices();
    } catch (e) { Toast.error(e.message); }
  },

  async detachOrder(id, orderId) {
    try {
      await del(`/api/finance/invoices/${id}/orders/${orderId}`);
      this._refreshInvLines(await get(`/api/finance/invoices/${id}`)); this.loadInvoices();
    } catch (e) { Toast.error(e.message); }
  },

  payModal(id, amount) {
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Record payment</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div style="font-size:24px;font-weight:900;text-align:center;color:var(--blue-dark);margin-bottom:20px">${fmt.currency(amount)} due</div>
        <div class="field-row">
          <div class="field"><label class="field-label">Amount ₹</label><input class="field-input" id="pay-amt" type="number" value="${amount}" min="0"></div>
          <div class="field"><label class="field-label">Method</label>
            <select class="field-select" id="pay-method">
              <option value="cash">Cash</option><option value="card">Card</option>
              <option value="transfer">Bank transfer</option><option value="upi">UPI</option><option value="balance">Client balance</option>
            </select>
          </div>
        </div>
        <div class="field"><label class="field-label">Reference / note</label><input class="field-input" id="pay-ref" placeholder="Transaction ID, receipt no…"></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-success" onclick="FinancePage.submitPayment('${id}')">Record payment</button>
      </div>`, 'modal-narrow');
  },

  async submitPayment(invoiceId) {
    try {
      await post(`/api/finance/invoices/${invoiceId}/pay`, {
        method: document.getElementById('pay-method').value,
        amount: parseFloat(document.getElementById('pay-amt').value)||0,
        reference: document.getElementById('pay-ref').value||null,
      });
      Toast.success('Payment recorded'); Modal.close(); this.loadInvoices(); this.loadSummary();
    } catch (e) { Toast.error(e.message); }
  },

  // Build 144ed — New invoice as a right-side drawer (Orderry parity + our WO
  // drawer pattern): searchable client, optional different payer, issue/due dates
  // with a term preset, Order-display mode, free-form catalog line items, and a
  // comment. Client uses the shared ClientPicker; a different payer uses a small
  // inline search (ClientPicker is a singleton, so it can't back both at once).
  _niPayerId: null,

  _isoToday(offsetDays = 0) {
    const d = new Date(); d.setDate(d.getDate() + offsetDays);
    return d.toISOString().slice(0, 10);
  },

  newInvoiceModal() {
    this._niPayerId = null;
    Modal.open(`
      <div class="modal-header"><span class="modal-title">New invoice</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Client</label><div id="ni-client"></div></div>

        <label class="cash-check" style="display:flex;align-items:center;gap:8px;font-size:13px;margin:4px 0 12px">
          <input type="checkbox" id="ni-payer-same" checked onchange="FinancePage._togglePayer(this.checked)"> Payer is the same as the client
        </label>
        <div class="field" id="ni-payer-wrap" style="display:none"><label class="field-label">Bill to (payer)</label><div id="ni-payer-chosen" style="margin-bottom:6px"></div>
          <div class="search-box"><span class="search-icon">${icon('search',14)}</span>
            <input class="field-input search-input" id="ni-payer-input" placeholder="Search a different payer…" autocomplete="off" oninput="FinancePage._payerType()"></div>
          <div class="cp-dropdown" id="ni-payer-drop"></div>
        </div>

        <div class="field-row">
          <div class="field"><label class="field-label">Issue date</label><input class="field-input" id="ni-issue" type="date" value="${this._isoToday(0)}"></div>
          <div class="field"><label class="field-label">Payment term</label>
            <select class="field-select" id="ni-term" onchange="FinancePage._applyTerm()">
              <option value="0">Due on receipt</option>
              <option value="7">Net 7</option>
              <option value="15">Net 15</option>
              <option value="30" selected>Net 30</option>
              <option value="">Custom</option>
            </select>
          </div>
          <div class="field"><label class="field-label">Due date</label><input class="field-input" id="ni-due" type="date" value="${this._isoToday(30)}"></div>
        </div>

        <div class="field"><label class="field-label">Order display</label>
          <div class="seg-pill" id="ni-mode">
            <button type="button" class="on" data-m="detailed" onclick="FinancePage._niMode('detailed')">Detailed</button>
            <button type="button" data-m="simplified" onclick="FinancePage._niMode('simplified')">Simplified</button>
            <button type="button" data-m="one_line" onclick="FinancePage._niMode('one_line')">One line</button>
          </div>
        </div>

        <div class="field"><label class="field-label">Work orders <span style="color:var(--tx-4);font-weight:500">(optional)</span></label>
          <select class="field-select" id="ni-order-add" onchange="FinancePage._attachOrderChip(this.value); this.value=''">
            <option value="">Bill a work order…</option>
          </select>
          <div id="ni-order-chips" style="display:flex;flex-wrap:wrap;gap:6px;margin-top:8px"></div>
        </div>

        <div class="field"><label class="field-label">Add labors, services, products</label><div id="ni-cat"></div></div>

        <div class="field-row">
          <div class="field"><label class="field-label">Discount %</label><input class="field-input" id="ni-disc" type="number" value="0" min="0" max="100"></div>
          <div class="field"><label class="field-label">Tax %</label><input class="field-input" id="ni-tax" type="number" value="0" min="0"></div>
        </div>

        <div class="field"><label class="field-label">Comment</label><textarea class="field-input" id="ni-comment" rows="2" placeholder="Internal note or terms shown on the invoice…"></textarea></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="FinancePage.submitNewInvoice()">Create invoice</button>
      </div>`, 'modal-side-wide');
    // mount pickers + load orders after the drawer paints
    setTimeout(() => {
      ClientPicker.mount('ni-client');
      CatalogPicker.repairPricing = false;
      CatalogPicker.mount('ni-cat');
      this._niOrderIds = [];
      this._loadNiOrders();
    }, 40);
  },

  _niMode(m) {
    document.querySelectorAll('#ni-mode button').forEach(b => b.classList.toggle('on', b.dataset.m === m));
    this._niMode_v = m;
  },

  _applyTerm() {
    const t = document.getElementById('ni-term').value;
    if (t === '') return;                       // Custom — leave the due date alone
    const issue = document.getElementById('ni-issue').value || this._isoToday(0);
    const d = new Date(issue); d.setDate(d.getDate() + parseInt(t || '0', 10));
    document.getElementById('ni-due').value = d.toISOString().slice(0, 10);
  },

  _togglePayer(same) {
    document.getElementById('ni-payer-wrap').style.display = same ? 'none' : 'block';
    if (same) { this._niPayerId = null; document.getElementById('ni-payer-chosen').innerHTML = ''; }
  },

  async _payerType() {
    const q = document.getElementById('ni-payer-input').value.trim();
    clearTimeout(this._payerDeb);
    this._payerDeb = setTimeout(async () => {
      const drop = document.getElementById('ni-payer-drop'); if (!drop) return;
      let items = [];
      try { items = (await get(`/api/clients?limit=8${q ? '&search=' + encodeURIComponent(q) : ''}`)).items; } catch {}
      drop.innerHTML = items.map(c => `<div class="cp-opt" onclick="FinancePage._pickPayer('${c.id}','${(c.name||'').replace(/'/g,"\\'")}')">
        <div class="cp-opt-name">${c.name}</div>${c.phone ? `<div class="cp-opt-meta">${c.phone}</div>` : ''}</div>`).join('') || '<div class="cp-opt" style="color:var(--tx-4)">No matches</div>';
      drop.classList.add('open');
    }, 200);
  },

  _pickPayer(id, name) {
    this._niPayerId = id;
    document.getElementById('ni-payer-chosen').innerHTML = `<span class="chip">${name} <button onclick="FinancePage._clearPayer()" style="border:none;background:none;cursor:pointer">×</button></span>`;
    document.getElementById('ni-payer-drop').classList.remove('open');
    document.getElementById('ni-payer-input').value = '';
  },

  _clearPayer() { this._niPayerId = null; document.getElementById('ni-payer-chosen').innerHTML = ''; },

  async _loadNiOrders() {
    let items = [];
    try { items = (await get('/api/orders?limit=50')).items || []; } catch {}
    this._niOrdersCache = items;
    const sel = document.getElementById('ni-order-add'); if (!sel) return;
    sel.insertAdjacentHTML('beforeend', items.map(o =>
      `<option value="${o.id}">${o.order_number}${o.client && o.client.name ? ' · ' + o.client.name : ''} · ${fmt.currency(o.total || 0)}</option>`).join(''));
  },

  _attachOrderChip(id) {
    if (!id || (this._niOrderIds || []).includes(id)) return;
    this._niOrderIds = this._niOrderIds || [];
    this._niOrderIds.push(id);
    const o = (this._niOrdersCache || []).find(x => x.id === id) || {};
    document.getElementById('ni-order-chips').insertAdjacentHTML('beforeend',
      `<span class="chip" data-oid="${id}">${o.order_number || 'Order'} <button onclick="FinancePage._detachOrderChip('${id}')" style="border:none;background:none;cursor:pointer">×</button></span>`);
  },

  _detachOrderChip(id) {
    this._niOrderIds = (this._niOrderIds || []).filter(x => x !== id);
    const chip = document.querySelector(`#ni-order-chips .chip[data-oid="${id}"]`); if (chip) chip.remove();
  },

  async submitNewInvoice() {
    const clientId = ClientPicker.value;
    const lines = (CatalogPicker.lines || []).map(l => ({
      kind: l.kind === 'service' ? 'service' : l.kind === 'product' ? 'product' : 'custom',
      source_id: l.ref_id || null,
      description: l.description || 'Line',
      qty: l.qty || 1,
      unit_price: l.unit_price || 0,
    }));
    const orderIds = this._niOrderIds || [];
    if (!clientId && !lines.length && !orderIds.length) {
      return Toast.error('Add a client, a work order, or at least one line');
    }
    try {
      await post('/api/finance/invoices', {
        client_id: clientId,
        payer_client_id: this._niPayerId || null,
        display_mode: this._niMode_v || 'detailed',
        issued_at: document.getElementById('ni-issue').value || null,
        due_at: document.getElementById('ni-due').value || null,
        comment: document.getElementById('ni-comment').value || '',
        order_ids: orderIds,
        lines,
        discount: parseFloat(document.getElementById('ni-disc').value) || 0,
        tax: parseFloat(document.getElementById('ni-tax').value) || 0,
      });
      Toast.success('Invoice created'); Modal.close(); this.loadInvoices(); this.loadSummary();
    } catch (e) { Toast.error(e.message); }
  },
};

Router.register('finance', () => FinancePage.load());

/* ════════════════════════════════════════════════════
   Tasks page
════════════════════════════════════════════════════ */

