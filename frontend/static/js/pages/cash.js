/* ════════════════════════════════════════════════════
   Cash — cashboxes, transaction ledger, client balances
════════════════════════════════════════════════════ */

const CashPage = {
  _accounts: [],
  _flowItems: [],
  _activeAccount: '',

  async load() {
    // Build 144bw: money actions grouped into one tidy segmented control;
    // "+ Account" is the primary and auto-moves next to the page title.
    setTopbarActions(`
      <div class="money-seg">
        <button class="money-btn income" onclick="CashPage.txnModal('income')">+ Income</button>
        <button class="money-btn expense" onclick="CashPage.txnModal('expense')">− Expense</button>
        <button class="money-btn" onclick="CashPage.transferModal()">${icon('refresh',14)} Transfer</button>
      </div>
      <button class="btn btn-primary" onclick="CashPage.accountModal()">+ Account</button>`);
    setContent(`
      <div id="cash-summary" class="cash-summary"></div>
      <div class="cash-cols">
        <div>
          <div class="sec-label">Accounts</div>
          <div id="cash-accounts"></div>
          <div class="sec-label" style="margin-top:22px">Client balances</div>
          <div id="cash-balances" class="card" style="padding:4px 0"></div>
        </div>
        <div>
          <div class="sec-label">Transactions ${this._activeAccount?'· filtered':''}</div>
          <div class="card" style="padding:0">
            <div class="tbl-wrap"><table class="tbl">
              <thead><tr><th>Date</th><th>Type</th><th>Account</th><th>Item</th><th>Comment</th><th style="text-align:right">Amount</th></tr></thead>
              <tbody id="cash-txns">${skeletonRows(8)}</tbody>
            </table></div>
          </div>
        </div>
      </div>`);
    await this._loadAll();
  },

  async _loadAll() {
    try {
      const _lp = (typeof App !== 'undefined' && App.locParam) ? App.locParam('?') : '';
      const [summary, accounts, flow] = await Promise.all([
        get('/api/cash/summary'),
        get(`/api/cash/accounts${_lp}`),
        get('/api/cash/flow-items'),
      ]);
      this._accounts = accounts.items;
      this._flowItems = flow.items;
      this._renderSummary(summary);
      this._renderAccounts(summary.accounts);
    } catch (e) { Toast.error(e.message); }
    this._loadBalances();
    this._loadTxns();
  },

  _renderSummary(s) {
    const el = document.getElementById('cash-summary');
    if (!el) return;
    el.innerHTML = `
      <div class="cash-stat"><div class="cs-label">Total cash on hand</div><div class="cs-value">${fmt.currency(s.total_cash)}</div></div>
      <div class="cash-stat"><div class="cs-label">Client debt outstanding</div><div class="cs-value" style="color:${s.client_debt>0?'var(--red)':'var(--tx-1)'}">${fmt.currency(s.client_debt)}</div></div>
      <div class="cash-stat"><div class="cs-label">Active accounts</div><div class="cs-value">${(s.accounts||[]).length}</div></div>`;
  },

  _renderAccounts(accounts) {
    const el = document.getElementById('cash-accounts');
    if (!el) return;
    if (!accounts.length) { el.innerHTML = `<div class="card" style="padding:16px;color:var(--tx-4);font-size:13px">No accounts yet. Add one to start taking payments.</div>`; return; }
    const optColor = { cash:'#3dca7a', cashless:'#4f8ef7', card:'#9b7bf4' };
    el.innerHTML = accounts.map(a => `
      <div class="acct-card ${this._activeAccount===a.id?'active':''}" onclick="CashPage.filterAccount('${a.id}')">
        <div class="acct-icon" style="background:${optColor[a.payment_option]||'#90a0c0'}22;color:${optColor[a.payment_option]||'#90a0c0'}">${icon(a.icon==='bank'?'bank':a.icon==='card'?'card':'wallet',18)}</div>
        <div class="acct-main">
          <div class="acct-name">${a.name}${a.is_company?' <span class="acct-tag">company</span>':''}${a.is_virtual?' <span class="acct-tag">virtual</span>':''}</div>
          <div class="acct-meta">${a.payment_option}${a.fee_pct?` · ${a.fee_pct}% fee`:''}</div>
        </div>
        <div class="acct-bal">${fmt.currency(a.balance)}</div>
        <button class="acct-edit" onclick="event.stopPropagation();CashPage.accountModal('${a.id}')">${icon('bolt',13)}</button>
      </div>`).join('');
  },

  filterAccount(id) {
    this._activeAccount = (this._activeAccount === id) ? '' : id;
    document.querySelectorAll('.acct-card').forEach(c => c.classList.remove('active'));
    if (this._activeAccount) document.querySelector(`.acct-card[onclick*="${id}"]`)?.classList.add('active');
    this._loadTxns();
  },

  async _loadBalances() {
    const el = document.getElementById('cash-balances');
    if (!el) return;
    try {
      const data = await get('/api/cash/balances');
      if (!data.items.length) { el.innerHTML = `<div style="padding:14px;color:var(--tx-4);font-size:12.5px">No outstanding balances.</div>`; return; }
      el.innerHTML = `<table class="tbl"><tbody>${data.items.map(c=>`
        <tr><td style="font-weight:700">${c.name}</td><td style="color:var(--tx-3);font-size:12px">${c.phone||''}</td>
        <td style="text-align:right;font-weight:800;color:${c.balance<0?'var(--red)':'var(--green)'}">${fmt.currency(c.balance)}</td></tr>`).join('')}</tbody></table>`;
    } catch (e) { el.innerHTML = ''; }
  },

  async _loadTxns() {
    const el = document.getElementById('cash-txns');
    if (!el) return;
    try {
      const data = await get(`/api/cash/transactions${this._activeAccount?'?account_id='+this._activeAccount:''}`);
      if (!data.items.length) { el.innerHTML = emptyRow(6,'receipt','No transactions','Payments and manual entries appear here'); return; }
      const typeColor = { income:'var(--green)', invoice_payment:'var(--green)', expense:'var(--red)', fee:'var(--amber)', transfer:'var(--blue)' };
      el.innerHTML = data.items.map(t => `
        <tr>
          <td style="color:var(--tx-3);font-size:12px">${fmt.reltime(t.actual_date)}${t.is_retroactive?' <span class="gen-tag">retro</span>':''}</td>
          <td><span style="font-weight:700;color:${typeColor[t.txn_type]||'var(--tx-2)'};text-transform:capitalize;font-size:12px">${t.txn_type.replace('_',' ')}</span></td>
          <td style="font-size:12.5px">${t.account||'—'}</td>
          <td style="color:var(--tx-3);font-size:12px">${t.cash_flow_item||'—'}</td>
          <td style="color:var(--tx-2);font-size:12px">${t.comment||'—'}</td>
          <td style="text-align:right;font-weight:800;font-family:var(--mono);color:${t.amount<0?'var(--red)':'var(--green)'}">${t.amount<0?'−':'+'}${fmt.currency(Math.abs(t.amount))}</td>
        </tr>`).join('');
    } catch (e) { Toast.error(e.message); }
  },

  /* ── account modal ── */
  accountModal(id = null) {
    const a = id ? this._accounts.find(x => x.id === id) : null;
    Modal.open(`
      <div class="modal-header"><span class="modal-title">${a?'Edit account':'New account'}</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Name *</label><input class="field-input" id="ac-name" value="${a?this._esc(a.name):''}" placeholder="e.g. Main Cash Drawer"></div>
        <div class="field"><label class="field-label">Payment type</label>
          <div class="type-chips">
            ${[['cash','Cash','#3dca7a'],['cashless','Cashless','#4f8ef7'],['card','Card','#9b7bf4']].map(([v,l,c])=>
              `<div class="type-chip${(a?a.payment_option:'cash')===v?' active':''}" data-opt="${v}" onclick="CashPage.pickOpt('${v}',this)" style="--tc:${c}"><span class="type-chip-dot" style="background:${c}"></span>${l}</div>`).join('')}
          </div>
        </div>
        <div class="field-row">
          <div class="field"><label class="field-label">Fee %</label><input class="field-input" id="ac-feepct" type="number" min="0" step="0.1" value="${a?.fee_pct||0}"></div>
          <div class="field"><label class="field-label">Fixed fee ₹</label><input class="field-input" id="ac-feefixed" type="number" min="0" value="${a?.fee_fixed||0}"></div>
        </div>
        <div style="display:flex;gap:18px;margin-top:6px">
          <label class="cash-check"><input type="checkbox" id="ac-company" ${a?.is_company?'checked':''}> Company-wide (all locations)</label>
          <label class="cash-check"><input type="checkbox" id="ac-virtual" ${a?.is_virtual?'checked':''}> Virtual (exclude from reports)</label>
        </div>
      </div>
      <div class="modal-footer">
        ${a?`<button class="btn btn-danger btn-sm" onclick="CashPage.delAccount('${a.id}')">Delete</button>`:''}
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="CashPage.saveAccount(${a?`'${a.id}'`:'null'})">${a?'Save':'Create'}</button>
      </div>`, 'modal-narrow');
    this._opt = a ? a.payment_option : 'cash';
  },

  _opt: 'cash',
  pickOpt(v, el) { this._opt = v; el.parentElement.querySelectorAll('.type-chip').forEach(c=>c.classList.remove('active')); el.classList.add('active'); },

  async saveAccount(id) {
    const name = document.getElementById('ac-name').value.trim();
    if (!name) return Toast.error('Name is required');
    const body = {
      name, payment_option: this._opt,
      icon: this._opt === 'cash' ? 'wallet' : this._opt === 'card' ? 'card' : 'bank',
      is_company: document.getElementById('ac-company').checked,
      is_virtual: document.getElementById('ac-virtual').checked,
      fee_pct: parseFloat(document.getElementById('ac-feepct').value) || 0,
      fee_fixed: parseFloat(document.getElementById('ac-feefixed').value) || 0,
    };
    try {
      if (id) { await put(`/api/cash/accounts/${id}`, body); Toast.success('Account updated'); }
      else { await post('/api/cash/accounts', body); Toast.success('Account created'); }
      Modal.close(); this._loadAll();
    } catch (e) { Toast.error(e.message); }
  },

  async delAccount(id) {
    const ok = await confirm2('Delete this account? Its history stays in the ledger.');
    if (!ok) return;
    try { await del(`/api/cash/accounts/${id}`); Toast.success('Account deleted'); Modal.close(); this._loadAll(); }
    catch (e) { Toast.error(e.message); }
  },

  /* ── income / expense modal ── */
  txnModal(type) {
    const items = this._flowItems.filter(f => f.direction === type);
    Modal.open(`
      <div class="modal-header"><span class="modal-title">${type==='income'?'Record income':'Record expense'}</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Account</label>
          <select class="field-select" id="tx-account">${this._accounts.map(a=>`<option value="${a.id}">${a.name} (${fmt.currency(a.balance)})</option>`).join('')}</select>
        </div>
        <div class="field"><label class="field-label">Amount ₹ *</label><input class="field-input" id="tx-amount" type="number" min="0" placeholder="0"></div>
        <div class="field"><label class="field-label">Category</label>
          <select class="field-select" id="tx-item"><option value="">—</option>${items.map(f=>`<option value="${f.id}">${f.name}</option>`).join('')}</select>
        </div>
        <div class="field"><label class="field-label">Date</label><input class="field-input" type="date" id="tx-date" value="${new Date().toISOString().slice(0,10)}"></div>
        <div class="field"><label class="field-label">Comment</label><input class="field-input" id="tx-comment" placeholder="Optional note"></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="CashPage.saveTxn('${type}')">Save</button>
      </div>`, 'modal-narrow');
  },

  async saveTxn(type) {
    const amount = parseFloat(document.getElementById('tx-amount').value);
    if (!amount || amount <= 0) return Toast.error('Enter an amount');
    try {
      await post('/api/cash/transactions', {
        account_id: document.getElementById('tx-account').value,
        txn_type: type, amount,
        cash_flow_item_id: document.getElementById('tx-item').value || null,
        comment: document.getElementById('tx-comment').value || null,
        actual_date: document.getElementById('tx-date').value || null,
      });
      Toast.success(type==='income'?'Income recorded':'Expense recorded');
      Modal.close(); this._loadAll();
    } catch (e) { Toast.error(e.message); }
  },

  /* ── transfer modal ── */
  transferModal() {
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Transfer between accounts</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">From</label>
          <select class="field-select" id="tr-from">${this._accounts.map(a=>`<option value="${a.id}">${a.name} (${fmt.currency(a.balance)})</option>`).join('')}</select></div>
        <div class="field"><label class="field-label">To</label>
          <select class="field-select" id="tr-to">${this._accounts.map((a,i)=>`<option value="${a.id}"${i===1?' selected':''}>${a.name} (${fmt.currency(a.balance)})</option>`).join('')}</select></div>
        <div class="field"><label class="field-label">Amount ₹ *</label><input class="field-input" id="tr-amount" type="number" min="0" placeholder="0"></div>
        <div class="field"><label class="field-label">Comment</label><input class="field-input" id="tr-comment" placeholder="Optional note"></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="CashPage.saveTransfer()">Transfer</button>
      </div>`, 'modal-narrow');
  },

  async saveTransfer() {
    const amount = parseFloat(document.getElementById('tr-amount').value);
    if (!amount || amount <= 0) return Toast.error('Enter an amount');
    const from = document.getElementById('tr-from').value, to = document.getElementById('tr-to').value;
    if (from === to) return Toast.error('Choose two different accounts');
    try {
      await post('/api/cash/transfer', { from_account_id: from, to_account_id: to, amount, comment: document.getElementById('tr-comment').value || null });
      Toast.success('Transfer complete');
      Modal.close(); this._loadAll();
    } catch (e) { Toast.error(e.message); }
  },

  _esc(s) { return (s || '').replace(/"/g, '&quot;'); },
};

Router.register('cash', () => CashPage.load());
