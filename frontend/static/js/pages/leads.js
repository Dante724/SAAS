/* Inquiries / Leads board — the pre-order pipeline (Orderry Lead Funnel). */
const LeadsPage = {
  async load() {
    setTopbarActions(`<button class="btn btn-primary" onclick="LeadsPage.newModal()">+ New inquiry</button>`);
    setContent(`
      <div style="font-size:13px;color:var(--tx-3);margin-bottom:14px;max-width:680px">
        Every inquiry carries a <b>respond-by SLA</b>. Win an inquiry to convert it into a work order —
        the client is created in CRM automatically (matched by phone first).
      </div>
      <div class="leads-board" id="leads-board"></div>`);
    await this.reload();
  },

  async reload() {
    const el = document.getElementById('leads-board');
    if (!el) return;
    try {
      const data = await get('/api/leads/board');
      el.innerHTML = data.columns.map(col => `
        <div class="leads-col">
          <div class="leads-col-head">
            <span class="leads-col-dot l-${col.key}"></span>
            <span class="leads-col-title">${col.label}</span>
            <span class="leads-col-count">${col.cards.length}</span>
          </div>
          <div class="leads-col-body">
            ${col.cards.map(c => this._card(c)).join('') || '<div class="leads-empty">No inquiries</div>'}
          </div>
        </div>`).join('');
    } catch (e) { Toast.error(e.message); }
  },

  _card(c) {
    const sla = c.sla_due_at && (c.status === 'new' || c.status === 'in_progress')
      ? (c.sla_overdue
          ? `<span class="lead-sla overdue">${icon('clock',12)} SLA overdue</span>`
          : `<span class="lead-sla">${icon('clock',12)} respond by ${new Date(c.sla_due_at).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}</span>`)
      : '';
    const converted = c.converted_order_id
      ? `<button class="conv-link" style="font-size:12px" onclick="Router.go('orders');setTimeout(()=>OrdersPage.viewOrder('${c.converted_order_id}'),120)">${c.converted_order_number || 'Open order'} →</button>`
      : '';
    const actions = (c.status === 'new' || c.status === 'in_progress') ? `
      <div class="lead-actions">
        ${c.status === 'new' ? `<button class="btn btn-glass btn-sm" onclick="LeadsPage.setStatus('${c.id}','in_progress')">Start</button>` : ''}
        <button class="btn btn-primary btn-sm" onclick="LeadsPage.convert('${c.id}')">Convert to order</button>
        <button class="btn btn-glass btn-sm" onclick="LeadsPage.setStatus('${c.id}','lost')">Lost</button>
      </div>` : '';
    return `
      <div class="lead-card">
        <div class="lead-top">
          <span class="lead-name">${c.name}</span>
          <span class="lead-src">${c.source.replace('_',' ')}</span>
        </div>
        ${c.phone ? `<div class="lead-meta">${icon('phone',12)} ${c.phone}</div>` : ''}
        ${c.inquiry ? `<div class="lead-inquiry">${c.inquiry}</div>` : ''}
        ${sla}
        ${converted}
        ${actions}
      </div>`;
  },

  async setStatus(id, status) {
    try { await patch(`/api/leads/${id}`, { status }); await this.reload(); }
    catch (e) { Toast.error(e.message); }
  },

  async convert(id) {
    try {
      const r = await post(`/api/leads/${id}/convert`, {});
      Toast.success(`Work order ${r.order_number} created`);
      await this.reload();
    } catch (e) { Toast.error(e.message); }
  },

  newModal() {
    Modal.open(`
      <div class="modal-header"><span class="modal-title">New inquiry</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Contact name</label>
          <input class="field-input" id="ld-name" placeholder="Who's asking?"></div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
          <div class="field"><label class="field-label">Phone</label>
            <input class="field-input" id="ld-phone" placeholder="+91..."></div>
          <div class="field"><label class="field-label">Email</label>
            <input class="field-input" id="ld-email" placeholder="optional"></div>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
          <div class="field"><label class="field-label">Source</label>
            <select class="field-select" id="ld-source">
              <option value="phone">Phone call</option><option value="walk_in">Walk-in</option>
              <option value="whatsapp">WhatsApp</option><option value="instagram">Instagram</option>
              <option value="web">Website</option><option value="other">Other</option>
            </select></div>
          <div class="field"><label class="field-label">Respond within (minutes)</label>
            <input class="field-input" id="ld-sla" type="number" value="60"></div>
        </div>
        <div class="field"><label class="field-label">What do they need?</label>
          <textarea class="field-textarea" id="ld-inquiry" rows="3" placeholder="Cracked iPhone screen, asking for a quote..."></textarea></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="LeadsPage.submitNew()">Create inquiry</button>
      </div>`);
  },

  async submitNew() {
    const name = document.getElementById('ld-name').value.trim();
    if (!name) return Toast.error('Contact name is required');
    try {
      await post('/api/leads', {
        name,
        phone: document.getElementById('ld-phone').value.trim() || null,
        email: document.getElementById('ld-email').value.trim() || null,
        source: document.getElementById('ld-source').value,
        inquiry: document.getElementById('ld-inquiry').value.trim() || null,
        sla_minutes: parseInt(document.getElementById('ld-sla').value) || 60,
      });
      Modal.close();
      Toast.success('Inquiry created');
      await this.reload();
    } catch (e) { Toast.error(e.message); }
  },
};

Router.register('leads', () => LeadsPage.load());
