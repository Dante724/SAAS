/* ════════════════════════════════════════════════════
   Clients & Devices page
════════════════════════════════════════════════════ */

const ClientsPage = {

  async load() {
    const isSup = this._role === 'supplier';
    const tabs = `
      <div class="tabs" style="margin-bottom:14px">
        <button class="tab ${this._role==='all'||!this._role?'active':''}" onclick="ClientsPage.setRole('all')">All</button>
        <button class="tab ${this._role==='client'?'active':''}" onclick="ClientsPage.setRole('client')">Clients</button>
        <button class="tab ${isSup?'active':''}" onclick="ClientsPage.setRole('supplier')">Suppliers</button>
      </div>`;
    if (isSup) {
      // Suppliers live under Contacts now — render the full supplier workspace
      // (list + Incoming stock) right here. SuppliersPage owns the topbar button.
      setContent(`${tabs}<div id="contacts-sup-body"><div class="card" style="padding:18px;color:var(--tx-4)">Loading…</div></div>`);
      SuppliersPage._tab = SuppliersPage._tab || 'suppliers';
      await SuppliersPage.mountInto('contacts-sup-body');
      return;
    }
    // leaving the supplier tab: drop the embed target so a stray reload can't
    // write into a container that no longer exists.
    if (typeof SuppliersPage !== 'undefined') SuppliersPage._embedBody = null;
    setTopbarActions('');
    setContent(`
      <div class="list-toolbar">
        <div class="lt-left">
          <button class="btn btn-primary" onclick="ClientsPage.newModal()">+ New contact</button>
          <div class="tabs">
            <button class="tab ${this._role==='all'||!this._role?'active':''}" onclick="ClientsPage.setRole('all')">All</button>
            <button class="tab ${this._role==='client'?'active':''}" onclick="ClientsPage.setRole('client')">Clients</button>
            <button class="tab ${this._role==='supplier'?'active':''}" onclick="ClientsPage.setRole('supplier')">Suppliers</button>
          </div>
        </div>
        <div class="lt-right">
          <div class="search-box lt-search">
            <span class="search-icon">${icon('search',14)}</span>
            <input class="field-input search-input" id="cl-search" placeholder="Search name, phone or email…" oninput="ClientsPage.reload()">
          </div>
          <button class="ic-btn" title="Columns & views" onclick="ClientsPage.columnsMenu()">${icon('bolt',16)}</button>
          <div class="pd-menuwrap"><button class="ic-btn" title="Import / Export" onclick="ClientsPage.moreMenu(event)">${icon('list',16)}</button>
            <div class="pd-menu" id="cl-more" style="display:none">
              <div class="pd-mi" onclick="ClientsPage.importModal()">${icon('paperclip',14)} Import</div>
              <div class="pd-mi" onclick="ClientsPage.exportCsv()">${icon('file',14)} Export</div>
            </div></div>
        </div>
      </div>
      <div class="card">
        <div class="tbl-wrap"><table class="tbl" id="cl-list-table">
          <thead><tr><th style="width:34px"><input type="checkbox" id="cl-all" onclick="ClientsPage.toggleAll(this.checked)"></th><th>Client</th><th>Phone</th><th>Email</th><th>Tier</th><th>Balance</th><th>Devices</th><th>Orders</th><th>Joined</th><th></th></tr></thead>
          <tbody id="cl-tbody">${skeletonRows(10)}</tbody>
        </table></div>
      </div>`);
    await this.reload();
  },

  async setRole(r) { this._role = r; await this.load(); },

  async reload() {
    const s = document.getElementById('cl-search')?.value || '';
    const role = this._role && this._role !== 'all' ? '&role=' + this._role : '';
    const tbody = document.getElementById('cl-tbody');
    if (!tbody) return;
    try {
      const data = await get(`/api/clients?limit=200${role}${s ? '&search=' + encodeURIComponent(s) : ''}`);
      this._items = data.items;
      if (!this._sel) this._sel = new Set();
      if (!data.items.length) { tbody.innerHTML = emptyRow(10,'user',`No ${this._role==='supplier'?'suppliers':this._role==='client'?'clients':'contacts'} yet`,'Add your first contact'); this._syncBulk(); return; }
      const tierStyle = {
        vip:       'background:rgba(155,123,244,.13);color:#6830c8;border-color:rgba(155,123,244,.25)',
        corporate: 'background:rgba(44,212,192,.13);color:#0f7068;border-color:rgba(44,212,192,.25)',
        standard:  'background:rgba(79,142,247,.12);color:#2855c8;border-color:rgba(79,142,247,.22)',
      };
      tbody.innerHTML = data.items.map(c => `
        <tr data-rid="${c.id}" class="${this._sel.has(c.id)?'row-sel':''}" onclick="ClientsPage.viewClient('${c.id}')">
          <td onclick="event.stopPropagation()"><input type="checkbox" ${this._sel.has(c.id)?'checked':''} onclick="ClientsPage.toggleRow('${c.id}',this.checked)"></td>
          <td><div style="display:flex;align-items:center;gap:9px">
            ${avatarHtml(c.name, 28)}
            <div><div style="font-weight:800;font-size:13px">${c.name}</div>${c.blacklisted?`<div style="font-size:10px;color:var(--red);font-weight:800">Blacklisted</div>`:''}</div>
          </div></td>
          <td style="color:var(--tx-2);font-family:var(--mono);font-size:12px">${c.phone||'—'}</td>
          <td style="color:var(--tx-2);font-size:12px">${c.email||'—'}</td>
          <td><span class="badge" style="${tierStyle[c.loyalty_tier]||tierStyle.standard}">${c.loyalty_tier}</span></td>
          <td><span class="tbl-strong">${fmt.currency(c.balance)}</span></td>
          <td style="color:var(--tx-3);font-weight:700">${c.devices?.length||0}</td>
          <td style="color:var(--tx-3);font-weight:700">${c.order_count||0}</td>
          <td style="color:var(--tx-3);font-size:12px">${fmt.date(c.created_at)}</td>
          <td><button class="btn btn-glass btn-sm" onclick="event.stopPropagation();ClientsPage.viewClient('${c.id}')">View →</button></td>
        </tr>`).join('');
      if (window.ColTools) ColTools.resizable('cl-list-table', 'contacts');
      if (window.ListExtras) {
        const byId = {}; data.items.forEach(c => { byId[c.id] = c; });
        ListExtras.attach('cl-list-table', 'client', byId);
      }
      const all = document.getElementById('cl-all');
      if (all) all.checked = this._items.length > 0 && this._items.every(c => this._sel.has(c.id));
      this._syncBulk();
    } catch (e) { Toast.error(e.message); }
  },
  toggleRow(id, ck) {
    if (!this._sel) this._sel = new Set();
    ck ? this._sel.add(id) : this._sel.delete(id);
    const all = document.getElementById('cl-all');
    if (all) all.checked = this._items.length > 0 && this._items.every(c => this._sel.has(c.id));
    const tr = document.querySelector(`#cl-tbody tr[data-rid="${id}"]`);
    if (tr) tr.classList.toggle('row-sel', ck);
    this._syncBulk();
  },
  toggleAll(ck) { this._sel = new Set(ck ? (this._items||[]).map(c => c.id) : []); this.reload(); },
  _syncBulk() {
    if (typeof BulkBar === 'undefined') return;
    const n = (this._sel && this._sel.size) || 0;
    BulkBar.show(n, `<button class="btn btn-glass btn-sm" onclick="ClientsPage.exportSelected()">Export CSV</button>`,
                 () => { this._sel = new Set(); this.reload(); });
  },
  exportSelected() {
    const rows = (this._items || []).filter(c => this._sel.has(c.id));
    if (!rows.length) return;
    const esc = v => `"${String(v ?? '').replace(/"/g, '""')}"`;
    const head = ['Name', 'Phone', 'Email', 'Tier', 'Balance', 'Devices', 'Orders'];
    const body = rows.map(c => [c.name, c.phone, c.email, c.loyalty_tier, c.balance, c.devices?.length||0, c.order_count||0].map(esc).join(','));
    const csv = [head.join(','), ...body].join('\n');
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
    a.download = `contacts-${new Date().toISOString().slice(0, 10)}.csv`; a.click(); URL.revokeObjectURL(a.href);
    if (typeof Toast !== 'undefined') Toast.success(`Exported ${rows.length} contact(s)`);
  },
  columnsMenu() { if (window.ListExtras) ListExtras.openMenu('client', () => this.reload()); },
  moreMenu(e) { if (e) e.stopPropagation(); const m = document.getElementById('cl-more'); if (m) m.style.display = m.style.display === 'none' ? 'block' : 'none';
    if (!this._moreBound) { this._moreBound = true; document.addEventListener('click', ev => { if (!ev.target.closest('.pd-menuwrap')) { const mm = document.getElementById('cl-more'); if (mm) mm.style.display = 'none'; } }); } },

  async viewClient(id) {
    this._activeClient = id;
    let c, docs = [], employees = [];
    try { c = await get(`/api/clients/${id}`); } catch (e) { Toast.error(e.message); return; }
    try { docs = (await get(`/api/clients/${id}/documents`)).items; } catch {}
    try { employees = await get('/api/staff/employees'); } catch {}
    const mgr = employees.find(e => e.id === c.manager_id);
    const typeBadge = c.client_type === 'organization' ? 'Organization' : 'Person';
    Modal.open(`
      <div class="modal-header">
        <div style="display:flex;align-items:center;gap:12px">${avatarHtml(c.name,36)}
          <div><span class="modal-title">${c.name}</span>
            <span class="badge b-new" style="margin-left:8px">${typeBadge}</span>
            ${c.is_supplier?`<span class="badge b-ready" style="margin-left:4px">Supplier</span>`:''}
            ${c.blacklisted?`<span class="badge b-overdue" style="margin-left:4px">Blacklisted</span>`:''}
          </div>
        </div>
        <button class="modal-close" onclick="Modal.close()">×</button>
      </div>
      <div class="wo-tabs" style="padding:0 22px">
        <button class="wo-tab active" onclick="ClientsPage.cTab(event,'general')">General</button>
        <button class="wo-tab" onclick="ClientsPage.cTab(event,'balance')">Balance</button>
        <button class="wo-tab" onclick="ClientsPage.cTab(event,'docs')">Documents (${docs.length})</button>
        <button class="wo-tab" onclick="ClientsPage.cTab(event,'purchases')">Purchases</button>
        <button class="wo-tab" onclick="ClientsPage.cTab(event,'payments')">Payments</button>
        <button class="wo-tab" onclick="ClientsPage.cTab(event,'loyalty')">Loyalty</button>
        <button class="wo-tab" onclick="ClientsPage.cTab(event,'advance')">Advance / Payout</button>
        <button class="wo-tab" onclick="ClientsPage.cTab(event,'feed')">Communication</button>
      </div>
      <div class="modal-body">
        <div class="detail-grid" style="margin-bottom:16px">
          <div class="detail-blob"><div class="d-label">Phone</div><div class="d-value">${c.phone||'—'}</div></div>
          <div class="detail-blob"><div class="d-label">Email</div><div class="d-value" style="font-size:12.5px">${c.email||'—'}</div></div>
          <div class="detail-blob"><div class="d-label">Balance</div><div class="d-value" style="font-size:18px;font-weight:900;color:${c.balance>0?'var(--green)':c.balance<0?'var(--red)':'var(--tx-1)'}">${fmt.currency(c.balance)}</div></div>
          <div class="detail-blob"><div class="d-label">Manager</div><div class="d-value">${mgr?mgr.name:'—'}</div></div>
        </div>

        <div class="c-panel active" data-cpanel="general">
          <div class="detail-grid">
            <div class="detail-blob"><div class="d-label">Type</div><div class="d-value">${typeBadge}${c.is_supplier?' · Supplier':''}</div></div>
            <div class="detail-blob"><div class="d-label">Lead source</div><div class="d-value">${c.source||'—'}</div></div>
            <div class="detail-blob"><div class="d-label">Loyalty tier</div><div class="d-value">${c.loyalty_tier}</div></div>
            <div class="detail-blob"><div class="d-label">Discount card</div><div class="d-value">${c.discount_card||'—'}</div></div>
            <div class="detail-blob" style="grid-column:1/-1"><div class="d-label">Address</div><div class="d-value" style="font-size:13px;font-weight:500">${c.address||'—'}</div></div>
          </div>
          ${c.notes?`<div class="detail-blob" style="margin-top:12px"><div class="d-label">Notes</div><div class="d-value" style="font-size:13px;font-weight:500;line-height:1.5">${c.notes}</div></div>`:''}
          <div style="margin-top:14px">
            <div class="d-label" style="margin-bottom:8px">Assets / devices</div>
            <button class="btn btn-glass btn-sm" style="margin-bottom:10px" onclick="ClientsPage.addDeviceModal('${id}')">+ Add device / asset</button>
            ${c.devices?.length
              ? c.devices.map(d=>`<div class="card-sm" style="padding:12px 14px;margin-bottom:8px;display:flex;align-items:center;gap:12px">
                  <div style="color:var(--blue)">${icon('device',22)}</div>
                  <div style="flex:1"><div style="font-weight:800;font-size:13px">${d.brand||''} ${d.model||''}</div>
                  <div style="font-size:11.5px;color:var(--tx-3);margin-top:2px">SN: ${d.serial||'—'} · IMEI: ${d.imei||'—'} · <span style="font-weight:700">${d.condition||'—'}</span></div></div>
                </div>`).join('')
              : '<div style="color:var(--tx-4);font-size:13px;padding:4px 0">No devices on file</div>'}
          </div>
        </div>

        <div class="c-panel" data-cpanel="balance" style="display:none">
          <div id="client-balance"><div style="color:var(--tx-4);font-size:13px;padding:14px;text-align:center">Loading…</div></div>
        </div>

        <div class="c-panel" data-cpanel="docs" style="display:none">
          ${docs.length ? `<div class="tbl-wrap"><table class="tbl">
            <thead><tr><th>Order #</th><th>Status</th><th>Total</th><th>Created</th></tr></thead>
            <tbody>${docs.map(o=>`<tr style="cursor:pointer" onclick="Modal.close();Router.go('orders');setTimeout(()=>OrdersPage.viewOrder('${o.id}'),300)">
              <td><span class="tbl-num tbl-strong">${o.order_number}</span></td>
              <td>${badge(o.status)}</td><td>${fmt.currency(o.total)}</td>
              <td style="color:var(--tx-3);font-size:12px">${fmt.datetime(o.created_at)}</td></tr>`).join('')}</tbody>
          </table></div>` : '<div style="color:var(--tx-4);font-size:13px;padding:14px;text-align:center">No work orders yet.</div>'}
        </div>

        <div class="c-panel" data-cpanel="purchases" style="display:none">
          <div id="client-purchases"><div style="color:var(--tx-4);font-size:13px;padding:14px;text-align:center">Loading…</div></div>
        </div>

        <div class="c-panel" data-cpanel="payments" style="display:none">
          <div id="client-payments"><div style="color:var(--tx-4);font-size:13px;padding:14px;text-align:center">Loading…</div></div>
        </div>

        <div class="c-panel" data-cpanel="loyalty" style="display:none">
          <div id="client-loyalty"><div style="color:var(--tx-4);font-size:13px;padding:14px;text-align:center">Loading…</div></div>
        </div>

        <div class="c-panel" data-cpanel="advance" style="display:none">
          <div style="max-width:420px">
            <p style="color:var(--tx-3);font-size:13px;margin:0 0 14px">Adjust this contact's balance. An <b>advance</b> records money the client pays in; a <b>payout</b> records money returned to them.</p>
            <div class="field"><label class="field-label">Action</label>
              <select class="field-select" id="bal-kind">
                <option value="advance">Advance (client pays in)</option>
                <option value="payout">Payout (return to client)</option>
                <option value="adjustment">Manual adjustment</option>
              </select></div>
            <div class="field"><label class="field-label">Amount</label>
              <input class="field-input" id="bal-amount" type="number" step="0.01" placeholder="0.00"></div>
            <div class="field"><label class="field-label">Note (optional)</label>
              <input class="field-input" id="bal-note" placeholder="Reason / reference"></div>
            <button class="btn btn-primary" onclick="ClientsPage.postBalance('${id}')">Post to balance</button>
          </div>
        </div>

        <div class="c-panel" data-cpanel="feed" style="display:none">
          <div style="display:flex;gap:8px;align-items:flex-end;margin-bottom:8px">
            <div class="field" style="margin:0;max-width:140px"><label class="field-label">Channel</label>
              <select class="field-select" id="comm-channel">
                ${[['email','Email'],['sms','SMS'],['whatsapp','WhatsApp'],['facebook','Facebook'],['instagram','Instagram'],['call','Call note']].map(([v,l])=>`<option value="${v}">${l}</option>`).join('')}
              </select></div>
            <div class="field" style="margin:0;flex:1"><label class="field-label">Message</label>
              <input class="field-input" id="comm-body" placeholder="Type a message to the client…" onkeydown="if(event.key==='Enter')ClientsPage.sendComm('${id}')"></div>
            <button class="btn btn-primary" onclick="ClientsPage.sendComm('${id}')">Send</button>
          </div>
          <div style="display:flex;gap:8px;align-items:center;margin-bottom:14px;flex-wrap:wrap">
            <select class="field-select" id="comm-template" style="max-width:220px;padding:6px 30px 6px 11px" onchange="ClientsPage.insertTemplate()"><option value="">Insert template…</option></select>
            <button class="btn btn-glass btn-sm" onclick="ClientsPage.aiSuggest('${id}')">${icon('bolt',13)} AI suggest reply</button>
            <span id="comm-ai-note" style="font-size:11px;color:var(--tx-4)"></span>
          </div>
          <div id="client-comms"><div style="color:var(--tx-4);font-size:13px;padding:14px;text-align:center">Loading…</div></div>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-danger btn-sm" onclick="ClientsPage.deleteClient('${id}')">Delete</button>
        <button class="btn btn-glass" onclick="ClientsPage.editModal('${id}')">Edit</button>
        <button class="btn btn-primary" onclick="Modal.close()">Close</button>
      </div>`, 'modal-wide');
  },

  cTab(e, panel) {
    document.querySelectorAll('.wo-tab').forEach(t=>t.classList.remove('active'));
    e.target.classList.add('active');
    document.querySelectorAll('.c-panel').forEach(p=>p.style.display = p.dataset.cpanel===panel?'block':'none');
    if (panel === 'balance') this._loadBalance();
    if (panel === 'purchases') this._loadPurchases();
    if (panel === 'payments') this._loadPayments();
    if (panel === 'feed') this._loadComms();
    if (panel === 'loyalty') this._loadLoyalty();
  },

  // Build 144aj — everything this client has bought (POS sales + work-order billing),
  // searchable, with a spend summary. "What they bought / what we sold."
  async _loadPurchases() {
    const box = document.getElementById('client-purchases');
    if (!box || !this._activeClient) return;
    try {
      const d = await get(`/api/clients/${this._activeClient}/purchases`);
      this._purchases = d.items || [];
      const s = d.summary || {};
      box.innerHTML = `
        <div class="detail-grid" style="margin-bottom:14px">
          <div class="detail-blob"><div class="d-label">Total spent</div>
            <div class="d-value" style="font-size:20px;font-weight:900;color:var(--green)">${fmt.currency(s.total_spent || 0)}</div></div>
          <div class="detail-blob"><div class="d-label">Items bought</div><div class="d-value" style="font-size:18px;font-weight:800">${s.item_count || 0}</div></div>
          <div class="detail-blob"><div class="d-label">Transactions</div><div class="d-value" style="font-size:18px;font-weight:800">${s.transaction_count || 0}</div></div>
          <div class="detail-blob"><div class="d-label">Last purchase</div><div class="d-value" style="font-size:12.5px">${s.last_at ? fmt.datetime(s.last_at) : '—'}</div></div>
        </div>
        <div class="search-box" style="margin-bottom:10px">
          <span class="search-icon">${icon('search', 14)}</span>
          <input class="field-input search-input" id="cpur-search" placeholder="Search an item…" oninput="ClientsPage._renderPurchases()">
        </div>
        <div id="cpur-rows"></div>`;
      this._renderPurchases();
    } catch (e) {
      box.innerHTML = `<div style="color:var(--red);padding:14px">${e.message}</div>`;
    }
  },

  _renderPurchases() {
    const el = document.getElementById('cpur-rows');
    if (!el) return;
    const q = (document.getElementById('cpur-search')?.value || '').toLowerCase().trim();
    const rows = (this._purchases || []).filter(p => !q || (p.item || '').toLowerCase().includes(q));
    if (!rows.length) {
      el.innerHTML = `<div style="color:var(--tx-4);font-size:13px;padding:14px;text-align:center">${(this._purchases || []).length ? 'No items match your search.' : 'Nothing purchased yet.'}</div>`;
      return;
    }
    const srcJump = (p) => p.source_type === 'order'
      ? `onclick="Modal.close();Router.go('orders');setTimeout(()=>OrdersPage.viewOrder('${p.source_id}'),300)" style="cursor:pointer"`
      : '';
    el.innerHTML = `<div class="tbl-wrap"><table class="tbl">
      <thead><tr><th>Date</th><th>Item</th><th>Source</th><th style="text-align:center">Qty</th><th style="text-align:right">Price</th><th style="text-align:right">Total</th></tr></thead>
      <tbody>${rows.map(p => `<tr>
        <td style="color:var(--tx-3);font-size:12px">${fmt.date(p.date)}</td>
        <td><span style="font-weight:700">${p.item}</span> <span class="cat-kind cat-kind-${p.kind === 'service' ? 'svc' : 'prod'}" style="margin-left:4px">${p.kind}</span></td>
        <td><span class="tbl-num" ${srcJump(p)}>${p.source_type === 'sale' ? 'Sale' : 'WO'} ${p.source_ref}</span></td>
        <td style="text-align:center;color:var(--tx-3)">${p.qty}</td>
        <td style="text-align:right">${fmt.currency(p.unit_price)}</td>
        <td style="text-align:right;font-weight:800">${fmt.currency(p.total)}</td>
      </tr>`).join('')}</tbody>
    </table></div>`;
  },

  async _loadLoyalty() {
    const box = document.getElementById('client-loyalty');
    if (!box || !this._activeClient) return;
    const id = this._activeClient;
    try {
      const [d, gcAll, cpAll] = await Promise.all([
        get(`/api/clients/${id}/loyalty`),
        get('/api/giftcards').catch(() => ({ items: [] })),
        get(`/api/clients/${id}/coupons`).catch(() => ({ items: [] })),
      ]);
      const worth = (d.point_value && d.points) ? d.points * d.point_value : 0;
      const cards = (gcAll.items || []).filter(g => g.client_id === id);
      const coupons = cpAll.items || [];
      const couponVal = (c) => c.kind === 'pct' ? `${c.value}%` : fmt.currency(c.value);
      box.innerHTML = `
        <div class="detail-grid" style="margin-bottom:16px">
          <div class="detail-blob"><div class="d-label">Points balance</div>
            <div class="d-value" style="font-size:22px;font-weight:900;color:var(--blue)">${d.points ?? 0}</div>
            ${worth ? `<div style="font-size:11.5px;color:var(--tx-4);margin-top:2px">≈ ${fmt.currency(worth)} at redemption</div>` : ''}</div>
          <div class="detail-blob"><div class="d-label">Tier</div><div class="d-value" style="text-transform:capitalize">${d.tier || 'standard'}</div></div>
          <div class="detail-blob"><div class="d-label">Lifetime earned</div><div class="d-value">${d.lifetime_points ?? 0}</div></div>
          <div class="detail-blob"><div class="d-label">Program</div><div class="d-value">${d.enabled ? `${d.earn_rate} pt / ${fmt.currency(1)}` : 'Off'}</div></div>
        </div>

        <div style="display:flex;align-items:center;justify-content:space-between;margin:4px 0 8px">
          <div style="font-size:10.5px;font-weight:800;color:var(--tx-3);text-transform:uppercase;letter-spacing:.06em">Gift cards</div>
          <button class="btn btn-glass btn-sm" onclick="ClientsPage.issueGiftCard('${id}')">+ Issue gift card</button>
        </div>
        ${cards.length ? `<div class="tbl-wrap" style="margin-bottom:16px"><table class="tbl">
          <thead><tr><th>Code</th><th style="text-align:right">Balance</th><th style="text-align:right">Issued</th><th>Status</th></tr></thead>
          <tbody>${cards.map(g=>`<tr>
            <td class="tbl-num tbl-strong">${g.code}</td>
            <td style="text-align:right;font-weight:700">${fmt.currency(g.balance)}</td>
            <td style="text-align:right;color:var(--tx-3)">${fmt.currency(g.initial_value)}</td>
            <td>${g.active&&g.balance>0?'<span class="badge b-done">active</span>':'<span class="badge b-muted">spent</span>'}</td></tr>`).join('')}</tbody>
        </table></div>` : '<div style="color:var(--tx-4);font-size:13px;padding:10px 0 16px">No gift cards for this client yet.</div>'}

        <div style="display:flex;align-items:center;justify-content:space-between;margin:4px 0 8px">
          <div style="font-size:10.5px;font-weight:800;color:var(--tx-3);text-transform:uppercase;letter-spacing:.06em">Coupons for this client</div>
          <button class="btn btn-glass btn-sm" onclick="ClientsPage.generateCoupon('${id}')">+ Generate coupon</button>
        </div>
        ${coupons.length ? coupons.map(c=>`<div class="ccoupon ${c.spent?'spent':''}">
            <div><span class="ccoupon-code">${c.code}</span> <span class="ccoupon-sub">· ${couponVal(c)} off${c.min_total?` · min ${fmt.currency(c.min_total)}`:''}</span></div>
            <span class="badge ${c.spent?'b-muted':'b-done'}">${c.spent?'redeemed':'available'}</span></div>`).join('')
          : '<div style="color:var(--tx-4);font-size:13px;padding:10px 0">No coupons issued to this client yet.</div>'}

        <div style="color:var(--tx-4);font-size:11.5px;line-height:1.6;margin-top:14px">Generate a coupon to offer a returning customer — it's redeemed against their next work order at checkout.</div>`;
    } catch (e) { box.innerHTML = `<div style="color:var(--tx-4);font-size:13px;padding:14px;text-align:center">${e.message==='Client not found'?'No loyalty data yet.':e.message}</div>`; }
  },

  async issueGiftCard(id) {
    const v = window.prompt('Gift card value:');
    if (v == null) return;
    const value = parseFloat(v);
    if (isNaN(value) || value <= 0) return Toast.error('Enter a valid amount');
    try {
      const r = await post('/api/giftcards', { value, client_id: id });
      Toast.success(`Gift card ${r.code} issued`);
      this._activeClient = id;
      await this._loadLoyalty();
    } catch (e) { Toast.error(e.message || 'Could not issue'); }
  },

  // 144bi — mint a coupon for a returning client, offered now, redeemed on next WO.
  generateCoupon(id) {
    Modal.open(`
      <div class="modal-head"><div class="modal-title">Generate coupon</div><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="rec-form-row">
          <div class="field"><label class="field-label">Type</label>
            <select class="field-input" id="cp-kind"><option value="pct">Percent %</option><option value="fixed">Fixed amount</option></select></div>
          <div class="field"><label class="field-label">Value</label>
            <input class="field-input" id="cp-value" type="number" min="0" step="0.5" value="10"></div>
          <div class="field"><label class="field-label">Min order total</label>
            <input class="field-input" id="cp-min" type="number" min="0" step="1" value="0"></div>
        </div>
        <div class="field"><label class="field-label">Label (optional)</label>
          <input class="field-input" id="cp-name" placeholder="Returning customer"></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="ClientsPage.saveCoupon('${id}')">Generate</button>
      </div>`, '', true);
  },

  async saveCoupon(id) {
    const value = parseFloat(document.getElementById('cp-value').value);
    if (isNaN(value) || value <= 0) return Toast.error('Enter a value above zero');
    try {
      const r = await post(`/api/clients/${id}/coupons`, {
        kind: document.getElementById('cp-kind').value,
        value,
        min_total: parseFloat(document.getElementById('cp-min').value) || 0,
        name: document.getElementById('cp-name').value.trim() || '',
      });
      Modal.close(true);
      Toast.success(`Coupon ${r.code} generated`);
      this._activeClient = id;
      await this._loadLoyalty();
    } catch (e) { Toast.error(e.message || 'Could not generate'); }
  },

  async _loadCommTemplates() {
    const sel = document.getElementById('comm-template');
    if (!sel) return;
    try {
      this._commTemplates = (await get('/api/clients/message-templates/all')).items || [];
      sel.innerHTML = '<option value="">Insert template…</option>' +
        this._commTemplates.map((t, i) => `<option value="${i}">${(t.name || '').replace(/</g, '&lt;')}</option>`).join('');
    } catch (e) { this._commTemplates = []; }
  },
  insertTemplate() {
    const sel = document.getElementById('comm-template');
    const body = document.getElementById('comm-body');
    const t = this._commTemplates && this._commTemplates[parseInt(sel.value, 10)];
    if (t && body) { body.value = body.value ? body.value + ' ' + t.body : t.body; body.focus(); }
    sel.value = '';
  },
  async aiSuggest(id) {
    const note = document.getElementById('comm-ai-note');
    if (note) note.textContent = 'Drafting…';
    try {
      const r = await post(`/api/clients/${id}/communication/suggest`, {});
      const body = document.getElementById('comm-body');
      if (body) { body.value = r.reply || ''; body.focus(); }
      if (note) note.textContent = `AI drafted (${r.provider}) — review before sending`;
    } catch (e) { if (note) note.textContent = ''; Toast.error(e.message || 'Could not draft'); }
  },

  async _loadComms() {
    const box = document.getElementById('client-comms');
    if (!box || !this._activeClient) return;
    this._loadCommTemplates();
    try {
      const d = await get(`/api/clients/${this._activeClient}/communication`);
      const chLabel = { email:'Email', sms:'SMS', whatsapp:'WhatsApp', facebook:'Facebook', instagram:'Instagram', call:'Call' };
      box.innerHTML = d.items.length ? d.items.map(m => `
        <div style="display:flex;gap:10px;padding:10px 0;border-bottom:1px solid var(--line)">
          <div style="width:6px;border-radius:3px;background:${m.direction==='outbound'?'var(--blue)':'var(--green)'};flex-shrink:0"></div>
          <div style="flex:1;min-width:0">
            <div style="font-size:11.5px;color:var(--tx-3);margin-bottom:2px">
              <span style="font-weight:700;color:var(--tx-2)">${m.direction==='outbound'?(m.author||'You'):'Client'}</span>
              · ${chLabel[m.channel]||m.channel||'—'} · ${fmt.datetime(m.created_at)}
              ${m.direction==='outbound'?'<span style="color:var(--tx-4)"> → sent</span>':'<span style="color:var(--tx-4)"> ← received</span>'}</div>
            <div style="font-size:13px;color:var(--tx-1);white-space:pre-wrap;word-break:break-word">${(m.body||'').replace(/</g,'&lt;')}</div>
          </div>
        </div>`).join('')
        : '<div style="color:var(--tx-4);font-size:13px;padding:14px;text-align:center">No messages yet. Send one above to start the conversation.</div>';
    } catch (e) { box.innerHTML = `<div style="color:var(--red);padding:14px">${e.message}</div>`; }
  },

  async sendComm(id) {
    const channel = document.getElementById('comm-channel').value;
    const bodyEl = document.getElementById('comm-body');
    const body = (bodyEl.value || '').trim();
    if (!body) return Toast.error('Type a message');
    try {
      await post(`/api/clients/${id}/communication`, { channel, body });
      bodyEl.value = '';
      Toast.success('Message logged');
      this._activeClient = id;
      await this._loadComms();
    } catch (e) { Toast.error(e.message || 'Could not send'); }
  },

  async _loadBalance() {
    const box = document.getElementById('client-balance');
    if (!box || !this._activeClient) return;
    try {
      const d = await get(`/api/clients/${this._activeClient}/balance`);
      const kindLabel = { advance:'Advance', payout:'Payout', charge:'Charge', income:'Income',
        prepayment:'Prepayment', refund:'Refund', adjustment:'Adjustment',
        posting:'Posting', purchase_return:'Return' };
      const cur = (d.currency && d.currency.code) ? d.currency : null;
      const M = (n) => cur ? fmt.money(n, cur) : fmt.currency(n);
      box.innerHTML = `
        <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:14px">
          <div class="detail-blob" style="flex:1"><div class="d-label">Current balance${cur?` (${cur.code})`:''}</div>
            <div class="d-value" style="font-size:22px;font-weight:900;color:${d.balance>0?'var(--green)':d.balance<0?'var(--red)':'var(--tx-1)'}">${M(d.balance)}</div>
            <div style="font-size:11.5px;color:var(--tx-4);margin-top:2px">${d.balance>0?'Client owes you':d.balance<0?'You owe client':'Settled'}${cur?` · shown in ${cur.code}`:''}</div></div>
          <div style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end">
            <button class="btn btn-glass btn-sm" onclick="ClientsPage.adjustModal('advance')">+ Advance</button>
            <button class="btn btn-glass btn-sm" onclick="ClientsPage.adjustModal('payout')">− Payout</button>
            <button class="btn btn-primary btn-sm" onclick="ClientsPage.adjustModal('adjustment')">+ Adjustment</button>
          </div>
        </div>
        ${d.entries.length ? `<div class="tbl-wrap"><table class="tbl">
          <thead><tr><th>Type</th><th>Note</th><th style="text-align:right">Amount</th><th>Date</th></tr></thead>
          <tbody>${d.entries.map(e=>`<tr>
            <td>${kindLabel[e.entry_type]||e.entry_type}</td>
            <td style="color:var(--tx-3)">${e.note||'—'}</td>
            <td style="text-align:right;font-weight:700;font-variant-numeric:tabular-nums;color:${e.amount>=0?'var(--green)':'var(--red)'}">${e.amount>=0?'+':''}${M(e.amount)}</td>
            <td style="color:var(--tx-3);font-size:12px">${fmt.datetime(e.created_at)}</td></tr>`).join('')}</tbody>
        </table></div>` : '<div style="color:var(--tx-4);font-size:13px;padding:14px;text-align:center">No balance movements yet.</div>'}`;
    } catch (e) { box.innerHTML = `<div style="color:var(--red);padding:14px">${e.message}</div>`; }
  },

  async _loadPayments() {
    const box = document.getElementById('client-payments');
    if (!box || !this._activeClient) return;
    try {
      const d = await get(`/api/clients/${this._activeClient}/payments`);
      const kindLabel = { payment:'Payment', prepayment:'Prepayment', refund:'Refund' };
      box.innerHTML = d.items.length ? `<div class="tbl-wrap"><table class="tbl">
        <thead><tr><th>Type</th><th>Order</th><th>Method</th><th style="text-align:right">Amount</th><th>Date</th></tr></thead>
        <tbody>${d.items.map(p=>`<tr>
          <td>${kindLabel[p.kind]||p.kind}</td>
          <td>${p.order_number ? `<span class="tbl-num tbl-strong" style="cursor:pointer" onclick="Modal.close();Router.go('orders');setTimeout(()=>OrdersPage.viewOrder('${p.order_id}'),300)">${p.order_number}</span>` : '<span style="color:var(--tx-4)">—</span>'}</td>
          <td style="color:var(--tx-3);text-transform:capitalize">${p.method||'—'}</td>
          <td style="text-align:right;font-weight:700;font-variant-numeric:tabular-nums;color:${p.kind==='refund'?'var(--red)':'var(--tx-1)'}">${p.kind==='refund'?'-':''}${fmt.currency(p.amount)}</td>
          <td style="color:var(--tx-3);font-size:12px">${fmt.datetime(p.paid_at)}</td></tr>`).join('')}</tbody>
      </table></div>` : '<div style="color:var(--tx-4);font-size:13px;padding:14px;text-align:center">No payments recorded yet.</div>';
    } catch (e) { box.innerHTML = `<div style="color:var(--red);padding:14px">${e.message}</div>`; }
  },

  async postBalance(id) {
    const kind = document.getElementById('bal-kind').value;
    const amount = parseFloat(document.getElementById('bal-amount').value);
    const note = document.getElementById('bal-note').value.trim();
    if (!amount || amount <= 0) return Toast.error('Enter an amount');
    try {
      await post(`/api/clients/${id}/balance`, { kind, amount, note: note || null });
      Toast.success('Balance updated');
      document.getElementById('bal-amount').value = '';
      document.getElementById('bal-note').value = '';
      // refresh the balance tab + reflect in the list underneath
      this._activeClient = id;
      await this._loadBalance();
      this.reload();
    } catch (e) { Toast.error(e.message || 'Could not post'); }
  },

  adjustModal(kind) {
    const id = this._activeClient;
    if (!id) return;
    const titles = { advance: 'Advance — client pays in', payout: 'Payout — return to client', adjustment: 'Manual adjustment' };
    const help = {
      advance: 'Records money the client pays in (reduces what they owe).',
      payout: 'Records money returned to the client (increases what they owe).',
      adjustment: 'A signed correction. Use a negative amount to write down what they owe.',
    };
    Modal.open(`
      <div class="modal-header"><span class="modal-title">${titles[kind]}</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <p style="color:var(--tx-3);font-size:12.5px;margin:0 0 12px">${help[kind]}</p>
        <div class="field"><label class="field-label">Amount</label>
          <input class="field-input" id="adj-amount" type="number" step="0.01" placeholder="0.00" autofocus></div>
        <div class="field"><label class="field-label">Note (optional)</label>
          <input class="field-input" id="adj-note" placeholder="Reason / reference"></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="ClientsPage.submitAdjust('${id}','${kind}')">Post</button>
      </div>`, 'modal-narrow');
  },

  async submitAdjust(id, kind) {
    const amount = parseFloat(document.getElementById('adj-amount').value);
    const note = (document.getElementById('adj-note').value || '').trim();
    if (isNaN(amount) || amount === 0) return Toast.error('Enter an amount');
    if (kind !== 'adjustment' && amount <= 0) return Toast.error('Enter a positive amount');
    try {
      await post(`/api/clients/${id}/balance`, { kind, amount, note: note || null });
      Toast.success('Balance updated');
      Modal.close();
      this._activeClient = id;
      await this._loadBalance();
      this.reload();
    } catch (e) { Toast.error(e.message || 'Could not post'); }
  },

  async addDeviceModal(clientId) {
    await OrdersPage._loadTpl();
    const t = window.__tpl;
    const assetLabel = (t && t.asset_label) || 'Device';
    const groups = (t && t.groups) || [];
    const ident0 = OrdersPage.IDENT_LABELS[t ? OrdersPage._grpKind(groups[0]?.id || '') : 'serial'] || 'Serial number';
    this._dvFields = await CustomFields.fetch('device');
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Add ${assetLabel.toLowerCase()}</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field-row">
          <div class="field"><label class="field-label">Group</label>
            <select class="field-select" id="dv-group" onchange="ClientsPage.dvGroupChanged()">
              ${groups.length ? groups.map(g=>`<option value="${g.id}">${g.label}</option>`).join('') : '<option value="">—</option>'}
            </select></div>
          <div class="field"><label class="field-label">Brand</label>
            <input class="field-input" id="dv-brand" list="dv-brand-list" placeholder="Type or pick…" oninput="ClientsPage.dvBrandChanged()">
            <datalist id="dv-brand-list"></datalist></div>
        </div>
        <div class="field-row">
          <div class="field"><label class="field-label">Model</label>
            <input class="field-input" id="dv-model" list="dv-model-list" placeholder="Type or pick…">
            <datalist id="dv-model-list"></datalist></div>
          <div class="field"><label class="field-label" id="dv-ident-label">${ident0}</label>
            <div style="display:flex;gap:6px">
              <input class="field-input" id="dv-ident" style="flex:1" placeholder="Scan or type…">
              <button class="btn btn-glass btn-sm" type="button" title="Scan / look up" onclick="ClientsPage.dvScanResolve()">${icon('search',13)}</button>
            </div></div>
        </div>
        <div class="field-row">
          <div class="field"><label class="field-label">Color</label><input class="field-input" id="dv-color" placeholder="Space Black"></div>
          <div class="field"><label class="field-label">Condition</label>
            <select class="field-select" id="dv-cond"><option>Good</option><option>Fair</option><option>Poor</option><option>New</option></select>
          </div>
        </div>
        ${CustomFields.render(this._dvFields, {}, 'dv-cf')}
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="ClientsPage.submitDevice('${clientId}')">Add ${assetLabel.toLowerCase()}</button>
      </div>`, 'modal-narrow');
    this.dvGroupChanged();
  },

  async dvGroupChanged() {
    const t = window.__tpl; if (!t) return;
    const groupId = document.getElementById('dv-group')?.value || '';
    const lbl = document.getElementById('dv-ident-label');
    if (lbl) lbl.textContent = OrdersPage.IDENT_LABELS[OrdersPage._grpKind(groupId)] || 'Serial number';
    const dl = document.getElementById('dv-brand-list');
    if (dl) {
      let url = `/api/settings/asset-brands?template_key=${t.current}`;
      if (groupId) url += `&asset_group_id=${groupId}`;
      try { dl.innerHTML = ((await get(url)).items || []).map(b => `<option value="${b.name}">`).join(''); } catch (e) {}
    }
    const md = document.getElementById('dv-model-list'); if (md) md.innerHTML = '';
  },

  async dvBrandChanged() {
    const t = window.__tpl; if (!t) return;
    const brand = (document.getElementById('dv-brand')?.value || '').trim();
    const md = document.getElementById('dv-model-list'); if (!md || !brand) return;
    const groupId = document.getElementById('dv-group')?.value || '';
    let url = `/api/settings/asset-brands?template_key=${t.current}`;
    if (groupId) url += `&asset_group_id=${groupId}`;
    try {
      const brands = (await get(url)).items || [];
      const b = brands.find(x => x.name.toLowerCase() === brand.toLowerCase());
      if (!b) { md.innerHTML = ''; return; }
      const models = (await get(`/api/settings/asset-models?template_key=${t.current}&brand_id=${b.id}`)).items || [];
      md.innerHTML = models.map(m => `<option value="${m.name}">`).join('');
    } catch (e) {}
  },

  async dvScanResolve() {
    const t = window.__tpl;
    const val = (document.getElementById('dv-ident')?.value || '').trim();
    if (!val) return Toast.error('Enter an identifier first');
    const groupId = document.getElementById('dv-group')?.value || '';
    try {
      const body = { identifier: val, template_key: t?.current };
      if (groupId) body.asset_group_id = groupId;
      const r = await post('/api/assets/scan-resolve', body);
      if (r.matched && r.asset) {
        if (r.asset.brand) document.getElementById('dv-brand').value = r.asset.brand;
        if (r.asset.model) document.getElementById('dv-model').value = r.asset.model;
        return Toast.success(`Matched existing ${(t && t.asset_label) || 'asset'}: ${[r.asset.brand, r.asset.model].filter(Boolean).join(' ')}`.trim());
      }
      if (r.valid === false) return Toast.error(`Invalid ${(r.kind || '').toUpperCase()}`);
      const pf = r.prefill || {};
      if (pf.make || pf.brand) document.getElementById('dv-brand').value = pf.make || pf.brand;
      if (pf.model) document.getElementById('dv-model').value = pf.model;
      Toast.success(`${(r.kind || '').toUpperCase()} valid${pf.make ? ' · ' + pf.make : ''}`);
    } catch (e) { Toast.error(e.message || 'Lookup failed'); }
  },

  async submitDevice(clientId) {
    const groupSel = document.getElementById('dv-group');
    const groupId = groupSel?.value || null;
    const kind = OrdersPage._grpKind(groupId || '');
    const identVal = document.getElementById('dv-ident')?.value || null;
    try {
      await post(`/api/clients/${clientId}/devices`, {
        brand: document.getElementById('dv-brand').value || null,
        model: document.getElementById('dv-model').value || null,
        serial: ['serial', 'asset_tag', 'barcode', 'engine_serial'].includes(kind) ? identVal : null,
        imei: kind === 'imei' ? identVal : null,
        vin: kind === 'vin' ? identVal : null,
        hin: kind === 'hin' ? identVal : null,
        template_key: (window.__tpl && window.__tpl.current) || null,
        asset_group_id: groupId,
        asset_type: groupSel?.selectedOptions?.[0]?.textContent?.trim() || null,
        color: document.getElementById('dv-color').value || null,
        condition: document.getElementById('dv-cond').value,
        custom_data: CustomFields.collect(this._dvFields || [], 'dv-cf'),
      });
      Toast.success('Device added'); Modal.close(); this.viewClient(clientId);
    } catch (e) { Toast.error(e.message); }
  },

  async deleteClient(id) {
    const ok = await confirm2('Delete this client and all their records?');
    if (!ok) return;
    try { await del(`/api/clients/${id}`); Toast.success('Client deleted'); Modal.close(); this.reload(); }
    catch (e) { Toast.error(e.message); }
  },

  async newModal() { this._clientForm(null); },
  async editModal(id) {
    let c; try { c = await get(`/api/clients/${id}`); } catch (e) { return Toast.error(e.message); }
    this._clientForm(c);
  },

  // opts.stack — layer over an existing modal (e.g. New sale) instead of replacing it.
  // opts.onCreated(client) — called with the new client instead of reloading Contacts.
  async _clientForm(c, opts = {}) {
    this._createCb = opts.onCreated || null;
    this._formStack = !!opts.stack;
    let employees = [], ptypes = [], orgcur = { currency_code: 'INR', currencies: [] };
    try { employees = await get('/api/staff/employees'); } catch {}
    try { ptypes = (await get('/api/pricing/types')).items; } catch {}
    try { orgcur = await get('/api/staff/company'); } catch {}
    this._clientFields = await CustomFields.fetch('client');
    const v = c || {};
    const curOpts = [['', `Default (${orgcur.currency_code || 'INR'})`]]
      .concat((orgcur.currencies || []).map(x => [x.code, `${x.code} (${x.symbol || ''})`]));
    Modal.open(`
      <div class="modal-header"><span class="modal-title">${c?'Edit client':'New client'}</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Contact type</label>
          <div class="type-chips">
            <div class="type-chip ${(v.client_type||'person')==='person'?'active':''}" onclick="ClientsPage._pickType('person',this)">Person</div>
            <div class="type-chip ${v.client_type==='organization'?'active':''}" onclick="ClientsPage._pickType('organization',this)">Organization</div>
          </div>
        </div>
        <div class="field-row">
          <div class="field"><label class="field-label">${v.client_type==='organization'?'Organization name':'Full name'} *</label><input class="field-input" id="nc-name" value="${(v.name||'').replace(/"/g,'&quot;')}" placeholder="Name"></div>
          <div class="field"><label class="field-label">Phone</label><input class="field-input" id="nc-phone" value="${v.phone||''}" placeholder="+91-9876543210"></div>
        </div>
        <div class="field-row">
          <div class="field"><label class="field-label">Email</label><input class="field-input" id="nc-email" type="email" value="${v.email||''}" placeholder="email@..."></div>
          <div class="field"><label class="field-label">How they found us (lead source)</label>
            <select class="field-select" id="nc-source">${['','walk_in','referral','google_ads','instagram','facebook','whatsapp'].map(s=>`<option value="${s}" ${v.source===s?'selected':''}>${s||'—'}</option>`).join('')}</select>
          </div>
        </div>
        <div class="field-row">
          <div class="field"><label class="field-label">Manager (responsible employee)</label>
            <select class="field-select" id="nc-manager"><option value="">—</option>${employees.map(e=>`<option value="${e.id}" ${v.manager_id===e.id?'selected':''}>${e.name}</option>`).join('')}</select>
          </div>
          <div class="field"><label class="field-label">Discount card #</label><input class="field-input" id="nc-card" value="${v.discount_card||''}" placeholder="scan or type"></div>
        </div>
        <div class="field-row">
          <div class="field"><label class="field-label">Loyalty tier</label>
            <select class="field-select" id="nc-tier">${[['standard','Standard'],['vip','VIP'],['corporate','Corporate']].map(([k,l])=>`<option value="${k}" ${v.loyalty_tier===k?'selected':''}>${l}</option>`).join('')}</select>
          </div>
          <div class="field"><label class="field-label">Billing currency</label>
            <select class="field-select" id="nc-currency">${curOpts.map(([k,l])=>`<option value="${k}" ${(v.currency_code||'')===k?'selected':''}>${l}</option>`).join('')}</select>
          </div>
        </div>
        <div class="field"><label class="field-label">Address</label><input class="field-input" id="nc-addr" value="${v.address||''}" placeholder="Street, City"></div>
        <label class="cash-check"><input type="checkbox" id="nc-supplier" ${v.is_supplier?'checked':''}> This contact is also a supplier/vendor</label>
        <div style="margin:14px 0 4px;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--tx-3)">Discounts in orders &amp; invoices</div>
        <div class="field-row">
          <div class="field"><label class="field-label">Services &amp; labor</label>
            <div style="display:flex;gap:8px">
              <select class="field-select" id="nc-dsm" onchange="ClientsPage._discMode('s')" style="flex:1">
                ${[['none','No discount'],['percent','Fixed %'],['price_type','By price type']].map(([k,l])=>`<option value="${k}" ${ (v.disc_services_mode||'none')===k?'selected':''}>${l}</option>`).join('')}
              </select>
              <input class="field-input" id="nc-dsv" type="number" value="${v.disc_services_value||''}" placeholder="%" style="width:80px;display:${v.disc_services_mode==='percent'?'block':'none'}">
              <select class="field-select" id="nc-dsp" style="flex:1;display:${v.disc_services_mode==='price_type'?'block':'none'}">
                ${ptypes.map(p=>`<option value="${p.id}" ${v.disc_services_price_type_id===p.id?'selected':''}>${p.name} (+${p.markup_pct}%)</option>`).join('')}
              </select>
            </div>
          </div>
          <div class="field"><label class="field-label">Materials &amp; parts</label>
            <div style="display:flex;gap:8px">
              <select class="field-select" id="nc-dmm" onchange="ClientsPage._discMode('m')" style="flex:1">
                ${[['none','No discount'],['percent','Fixed %'],['price_type','By price type']].map(([k,l])=>`<option value="${k}" ${ (v.disc_materials_mode||'none')===k?'selected':''}>${l}</option>`).join('')}
              </select>
              <input class="field-input" id="nc-dmv" type="number" value="${v.disc_materials_value||''}" placeholder="%" style="width:80px;display:${v.disc_materials_mode==='percent'?'block':'none'}">
              <select class="field-select" id="nc-dmp" style="flex:1;display:${v.disc_materials_mode==='price_type'?'block':'none'}">
                ${ptypes.map(p=>`<option value="${p.id}" ${v.disc_materials_price_type_id===p.id?'selected':''}>${p.name} (+${p.markup_pct}%)</option>`).join('')}
              </select>
            </div>
          </div>
        </div>
        <div class="field" style="margin-top:10px"><label class="field-label">Internal notes</label><textarea class="field-textarea" id="nc-notes" placeholder="Anything the team should know…">${v.notes||''}</textarea></div>
        ${CustomFields.render(this._clientFields, v.custom_data || {}, 'nc-cf')}
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="ClientsPage.submitClient(${c?`'${c.id}'`:'null'})">${c?'Save changes':'Add client'}</button>
      </div>`, 'modal-wide', this._formStack);
    this._ctype = v.client_type || 'person';
  },

  _discMode(which) {
    const mode = document.getElementById(which==='s'?'nc-dsm':'nc-dmm').value;
    document.getElementById(which==='s'?'nc-dsv':'nc-dmv').style.display = mode==='percent'?'block':'none';
    document.getElementById(which==='s'?'nc-dsp':'nc-dmp').style.display = mode==='price_type'?'block':'none';
  },

  _ctype: 'person',
  _pickType(t, el) {
    this._ctype = t;
    el.parentElement.querySelectorAll('.type-chip').forEach(c=>c.classList.remove('active'));
    el.classList.add('active');
  },

  async submitClient(id) {
    const name = document.getElementById('nc-name').value.trim();
    if (!name) return Toast.error('Name is required');
    const body = {
      name, client_type: this._ctype,
      phone: document.getElementById('nc-phone').value||null,
      email: document.getElementById('nc-email').value||null,
      source: document.getElementById('nc-source').value||null,
      loyalty_tier: document.getElementById('nc-tier').value,
      address: document.getElementById('nc-addr').value||null,
      notes: document.getElementById('nc-notes').value||null,
      manager_id: document.getElementById('nc-manager').value||null,
      discount_card: document.getElementById('nc-card').value||null,
      disc_services_mode: document.getElementById('nc-dsm').value,
      disc_services_value: parseFloat(document.getElementById('nc-dsv').value)||0,
      disc_services_price_type_id: document.getElementById('nc-dsm').value==='price_type' ? (document.getElementById('nc-dsp').value||null) : null,
      disc_materials_mode: document.getElementById('nc-dmm').value,
      disc_materials_value: parseFloat(document.getElementById('nc-dmv').value)||0,
      disc_materials_price_type_id: document.getElementById('nc-dmm').value==='price_type' ? (document.getElementById('nc-dmp').value||null) : null,
      is_supplier: document.getElementById('nc-supplier').checked,
      currency_code: document.getElementById('nc-currency').value || null,
      custom_data: CustomFields.collect(this._clientFields || [], 'nc-cf'),
    };
    try {
      if (id) { await put(`/api/clients/${id}`, body); Toast.success('Client updated'); Modal.close(); this.viewClient(id); }
      else {
        const r = await post('/api/clients', body);
        Toast.success('Client added');
        Modal.close();  // pops only this form (stacked over the sale when opened there)
        // Build 144ak: when opened from another flow (e.g. New sale), hand the new
        // client back to it instead of reloading the Contacts list.
        if (this._createCb) { const cb = this._createCb; this._createCb = null; this._formStack = false; cb({ id: r.id, name: r.name || body.name }); }
        else this.reload();
      }
    } catch (e) { Toast.error(e.message); }
  },
};

Router.register('clients', () => ClientsPage.load());

// (Device identifier + brand/model are now template-driven — see
// ClientsPage.dvGroupChanged / dvBrandChanged / dvScanResolve above, which reuse
// the industry-template helpers on OrdersPage. The old __assetMeta-based
// helpers and the /api/integrations/lookup/templates preload were retired here.)

/* ── Build 82: CRM data ops — import (with column mapping + dry-run preview),
   export, and duplicate merge. ── */
Object.assign(ClientsPage, {
  IMPORT_FIELDS: ['name', 'phone', 'email', 'address', 'notes', 'source', 'discount_card'],
  _importRows: [], _importCols: [],

  exportCsv() { openPrintPdf('/api/clients/export/csv','clients.csv'); },

  importModal() {
    this._importRows = []; this._importCols = [];
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Import contacts</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <p style="color:var(--tx-3);font-size:13px;margin:0 0 12px">
          Paste CSV (first row = column names). Existing contacts are matched on phone,
          then email, and updated in place rather than duplicated.
        </p>
        <textarea class="field-input" id="imp-csv" rows="7"
          placeholder="name,phone,email&#10;Ann Lee,+15551,ann@example.com"></textarea>
        <button class="btn btn-glass btn-sm" style="margin-top:8px"
          onclick="ClientsPage.parseCsv()">Read columns</button>
        <div id="imp-map" style="margin-top:14px"></div>
        <div id="imp-preview" style="margin-top:12px"></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-glass" id="imp-dry" onclick="ClientsPage.runImport(true)" disabled>Preview</button>
        <button class="btn btn-primary" id="imp-go" onclick="ClientsPage.runImport(false)" disabled>Import</button>
      </div>`, 'modal-wide');
  },

  parseCsv() {
    const text = document.getElementById('imp-csv').value.trim();
    if (!text) return Toast.error('Paste some CSV first');
    const lines = text.split(/\r?\n/).filter(Boolean);
    if (lines.length < 2) return Toast.error('Need a header row and at least one data row');
    const split = (l) => l.split(',').map(x => x.trim());
    this._importCols = split(lines[0]);
    this._importRows = lines.slice(1).map(l => {
      const cells = split(l);
      const row = {};
      this._importCols.forEach((c, i) => { row[c] = cells[i] ?? ''; });
      return row;
    });
    // guess the mapping from the column name
    const guess = (c) => this.IMPORT_FIELDS.find(f => c.toLowerCase().includes(f)) || '';
    document.getElementById('imp-map').innerHTML = `
      <div style="font-weight:800;font-size:12px;margin-bottom:6px">Map columns</div>
      ${this._importCols.map(c => `
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
          <div style="flex:1;font-size:13px">${c}</div>
          <select class="field-select" data-col="${c}" style="flex:1">
            <option value="">— skip —</option>
            ${this.IMPORT_FIELDS.map(f =>
              `<option value="${f}" ${guess(c) === f ? 'selected' : ''}>${f}</option>`).join('')}
          </select>
        </div>`).join('')}`;
    document.getElementById('imp-dry').disabled = false;
    document.getElementById('imp-go').disabled = false;
    Toast.success(`${this._importRows.length} row(s) read`);
  },

  _mapping() {
    const m = {};
    document.querySelectorAll('[data-col]').forEach(sel => {
      if (sel.value) m[sel.dataset.col] = sel.value;
    });
    return m;
  },

  async runImport(dryRun) {
    const mapping = this._mapping();
    if (!Object.values(mapping).includes('name')) return Toast.error('Map a column to "name"');
    try {
      const r = await post('/api/clients/import', {
        rows: this._importRows, mapping, update_existing: true, dry_run: dryRun,
      });
      const box = document.getElementById('imp-preview');
      box.innerHTML = `
        <div class="card" style="padding:12px">
          <b>${dryRun ? 'Preview' : 'Imported'}:</b>
          ${r.created} to create · ${r.updated} to update · ${r.skipped} skipped
          ${r.errors.length ? `<div style="color:var(--red,#e5484d);font-size:12px;margin-top:6px">
            ${r.errors.slice(0, 5).map(e => `Row ${e.row}: ${e.error}`).join('<br>')}</div>` : ''}
        </div>`;
      if (!dryRun) { Toast.success(`Imported ${r.created + r.updated} contact(s)`); await this.reload(); }
    } catch (e) { Toast.error(e.message || 'Import failed'); }
  },

  mergeModal(targetId) {
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Merge duplicate contacts</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <p style="color:var(--tx-3);font-size:13px;margin:0 0 12px">
          Devices, orders, invoices, payments and feedback move to this contact. Blank fields
          here are filled from the duplicates; existing values are never overwritten.
          The duplicates are removed once their history has moved.
        </p>
        <div class="field"><label class="field-label">Duplicate contact IDs (comma-separated)</label>
          <input class="field-input" id="mg-src" placeholder="id-1, id-2"></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="ClientsPage.runMerge('${targetId}')">Merge</button>
      </div>`);
  },

  async runMerge(targetId) {
    const ids = document.getElementById('mg-src').value.split(',')
      .map(x => x.trim()).filter(Boolean);
    if (!ids.length) return Toast.error('List at least one duplicate to merge in');
    try {
      const r = await post('/api/clients/merge', { target_id: targetId, source_ids: ids });
      Modal.close();
      Toast.success(`Merged ${r.merged} contact(s)`);
      await this.reload();
    } catch (e) { Toast.error(e.message || 'Merge failed'); }
  },
});
