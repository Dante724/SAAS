/* ════════════════════════════════════════════════════
   Statuses — configurable work-order statuses + triggers
════════════════════════════════════════════════════ */

const StatusesPage = {
  _items: [],
  _groups: [],
  _GROUP_LABELS: {
    new: 'New', in_progress: 'In progress', pending: 'Pending',
    ready: 'Ready', closed: 'Closed', dropped: 'Dropped off',
  },

  _isEstimate: false,
  async load() {
    setTopbarActions(`<button class="btn btn-glass btn-sm" onclick="StatusesPage.rulesModal()">${icon('refresh',14)} Transition rules</button><button class="btn btn-primary" onclick="StatusesPage.editModal()">+ New status</button>`);
    setContent(`
      <div style="font-size:13px;color:var(--tx-3);margin-bottom:14px;max-width:680px">
        Statuses belong to a <b>group</b> (New, In progress, Pending, Ready, Closed, Dropped off) — reports and automation key off the group.
        Each status can <b>trigger</b> actions when an order reaches it: auto-create an invoice, send a notification, open the payment window, or lock edits.
        <b>Estimates</b> use a separate status set; the status flagged <b>"converts to work order"</b> turns an accepted estimate into a real work order.
      </div>
      <div class="o-doctype-seg" style="margin-bottom:16px">
        <button class="ods-btn ${!this._isEstimate?'active':''}" onclick="StatusesPage.setDocType(false)">Work order statuses</button>
        <button class="ods-btn ${this._isEstimate?'active':''}" onclick="StatusesPage.setDocType(true)">Estimate statuses</button>
      </div>
      <div id="st-list"></div>`);
    await this.reload();
  },

  setDocType(est) {
    this._isEstimate = est;
    document.querySelectorAll('.ods-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.ods-btn')[est ? 1 : 0].classList.add('active');
    this.reload();
  },

  async reload() {
    const el = document.getElementById('st-list');
    if (!el) return;
    try {
      const data = await get(`/api/statuses?is_estimate=${this._isEstimate ? 'true' : 'false'}`);
      this._items = data.items;
      this._groups = data.groups;
      el.innerHTML = `
        <div class="card" style="padding:0">
          <div class="tbl-wrap"><table class="tbl">
            <thead><tr><th style="width:30px"></th><th>Status</th><th>Group</th><th>Triggers</th><th>Default</th></tr></thead>
            <tbody id="st-tbody">
              ${this._items.map((s,i)=>this._row(s,i)).join('')}
            </tbody>
          </table></div>
        </div>`;
    } catch (e) { Toast.error(e.message); }
  },

  _row(s, i) {
    const trig = [];
    if (s.triggers_invoice) trig.push('<span class="trig-tag t-inv">invoice</span>');
    if (s.triggers_notification) trig.push('<span class="trig-tag t-not">notify</span>');
    if (s.triggers_payment) trig.push('<span class="trig-tag t-pay">payment</span>');
    if (s.locks_edits) trig.push('<span class="trig-tag t-lock">lock</span>');
    if (s.is_terminal) trig.push('<span class="trig-tag t-term">terminal</span>');
    if (s.converts_to_order) trig.push('<span class="trig-tag t-inv">converts→WO</span>');
    return `<tr style="cursor:pointer" ondblclick="StatusesPage.editModal('${s.id}')">
      <td style="text-align:center;color:var(--tx-4);cursor:grab" title="Drag to reorder"
          draggable="true"
          ondragstart="StatusesPage._dragStart(event,${i})"
          ondragover="event.preventDefault()"
          ondrop="StatusesPage._drop(event,${i})">${icon('grid',13)}</td>
      <td><span class="st-dot" style="background:${s.color}"></span><span style="font-weight:800">${s.name}</span></td>
      <td><span class="st-group">${this._GROUP_LABELS[s.status_group]||s.status_group}</span></td>
      <td>${trig.join(' ')||'<span style="color:var(--tx-4)">—</span>'}</td>
      <td>${s.is_default?'<span class="gen-tag">default</span>':''}</td>
    </tr>`;
  },

  _dragStart(e, i) { this._dragIdx = i; },
  async _drop(e, i) {
    e.preventDefault();
    if (this._dragIdx == null || this._dragIdx === i) return;
    const arr = [...this._items];
    const [moved] = arr.splice(this._dragIdx, 1);
    arr.splice(i, 0, moved);
    this._items = arr;
    document.getElementById('st-tbody').innerHTML = arr.map((s,idx)=>this._row(s,idx)).join('');
    try { await post('/api/statuses/reorder', { ordered_ids: arr.map(s=>s.id) }); }
    catch (err) { Toast.error(err.message); }
    this._dragIdx = null;
  },

  editModal(id = null) {
    const s = id ? this._items.find(x => x.id === id) : null;
    const colors = ['#4f8ef7','#f5a623','#f06daa','#9b7bf4','#2cd4c0','#3dca7a','#f0655a','#90a0c0'];
    Modal.open(`
      <div class="modal-header"><span class="modal-title">${s?'Edit status':'New status'}</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field-row">
          <div class="field"><label class="field-label">Name *</label><input class="field-input" id="st-name" value="${s?this._esc(s.name):''}" placeholder="e.g. Awaiting approval"></div>
          <div class="field"><label class="field-label">Group</label>
            <select class="field-select" id="st-group">
              ${this._groups.map(g=>`<option value="${g}"${s&&s.status_group===g?' selected':''}>${this._GROUP_LABELS[g]||g}</option>`).join('')}
            </select>
          </div>
        </div>
        <div class="field"><label class="field-label">Colour</label>
          <div class="color-row" id="st-colors">
            ${colors.map(c=>`<button type="button" class="color-dot${(s?s.color:'#4f8ef7')===c?' active':''}" data-c="${c}" style="background:${c}" onclick="StatusesPage.pickColor('${c}',this)"></button>`).join('')}
          </div>
        </div>
        <div class="field">
          <label class="field-label">Triggers — when an order reaches this status</label>
          <div class="trig-list">
            <label class="trig-opt"><input type="checkbox" id="st-t-inv" ${s?.triggers_invoice?'checked':''}> <b>Auto-create invoice</b><span>generate a draft invoice from the order total</span></label>
            <label class="trig-opt"><input type="checkbox" id="st-t-not" ${s?.triggers_notification?'checked':''}> <b>Send notification</b><span>fire matching SMS/email templates</span></label>
            <label class="trig-opt"><input type="checkbox" id="st-t-pay" ${s?.triggers_payment?'checked':''}> <b>Open payment window</b><span>prompt to take payment / close on debt</span></label>
            <label class="trig-opt"><input type="checkbox" id="st-t-lock" ${s?.locks_edits?'checked':''}> <b>Lock edits</b><span>order can no longer be modified</span></label>
          </div>
        </div>
        <label class="cash-check" style="margin-top:6px"><input type="checkbox" id="st-default" ${s?.is_default?'checked':''}> Use as the initial status for new ${this._isEstimate?'estimates':'orders'}</label>
        ${this._isEstimate ? `
        <label class="cash-check" style="margin-top:6px"><input type="checkbox" id="st-converts" ${s?.converts_to_order?'checked':''}> <b>Converts to work order</b> — accepting an estimate at this status creates a real work order</label>
        <label class="cash-check" style="margin-top:6px"><input type="checkbox" id="st-accept" ${s?.shows_accept?'checked':''}> Show accept/decline buttons on the public estimate page</label>
        ` : ''}
        <div class="field-row" style="margin-top:12px">
          <div class="field"><label class="field-label">Time limit (minutes)</label>
            <input class="field-input" type="number" id="st-tlimit" min="0" value="${s?.time_limit_min ?? ''}" placeholder="none">
            <div style="font-size:10.5px;color:var(--tx-4);margin-top:3px">Overdue-by-status if exceeded</div>
          </div>
          <div class="field"><label class="field-label">Move asset to (zone)</label>
            <input class="field-input" id="st-asset-zone" value="${s?.moves_asset_to || ''}" placeholder="e.g. Workbench">
            <div style="font-size:10.5px;color:var(--tx-4);margin-top:3px">Auto-moves the asset on this status</div>
          </div>
        </div>
        <div style="font-size:11px;color:var(--tx-4);margin-top:8px">Statuses in the <b>Closed</b> or <b>Dropped off</b> groups are terminal — they finalise the order and (with the payment trigger) prompt for payment.</div>
      </div>
      <div class="modal-footer" style="display:flex;justify-content:space-between;align-items:center">
        <div style="display:flex;gap:8px">
          <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
          <button class="btn btn-primary" onclick="StatusesPage.submit(${s?`'${s.id}'`:'null'})">${s?'Save':'Create'}</button>
        </div>
        ${s&&!s.is_default?`<button class="btn btn-danger btn-sm" onclick="StatusesPage.del('${s.id}')">Delete</button>`:''}
      </div>`, 'modal-wide');
    this._color = s ? s.color : '#4f8ef7';
  },

  _color: '#4f8ef7',
  pickColor(c, el) {
    this._color = c;
    el.parentElement.querySelectorAll('.color-dot').forEach(d=>d.classList.remove('active'));
    el.classList.add('active');
  },

  async submit(id) {
    const name = document.getElementById('st-name').value.trim();
    if (!name) return Toast.error('Name is required');
    const body = {
      name, status_group: document.getElementById('st-group').value, color: this._color,
      is_default: document.getElementById('st-default').checked,
      triggers_invoice: document.getElementById('st-t-inv').checked,
      triggers_notification: document.getElementById('st-t-not').checked,
      triggers_payment: document.getElementById('st-t-pay').checked,
      locks_edits: document.getElementById('st-t-lock').checked,
      time_limit_min: parseInt(document.getElementById('st-tlimit').value) || null,
      moves_asset_to: document.getElementById('st-asset-zone').value.trim() || null,
      is_estimate: this._isEstimate,
      converts_to_order: document.getElementById('st-converts')?.checked || false,
      shows_accept: document.getElementById('st-accept')?.checked || false,
    };
    try {
      if (id) { await put(`/api/statuses/${id}`, body); Toast.success('Status updated'); }
      else { await post('/api/statuses', body); Toast.success('Status created'); }
      Modal.close(); this.reload();
    } catch (e) { Toast.error(e.message); }
  },

  async del(id) {
    const ok = await confirm2('Delete this status? Existing orders keep their current value.');
    if (!ok) return;
    try { await del(`/api/statuses/${id}`); Toast.success('Status deleted'); Modal.close(); this.reload(); }
    catch (e) { Toast.error(e.message); }
  },

  _esc(s) { return (s || '').replace(/"/g, '&quot;'); },

  /* ── Transition rules (Phase B.7) ── */
  async rulesModal() {
    let rules = [], statuses = this._items;
    try { rules = (await get('/api/statuses/transitions')).items; } catch {}
    const name = id => (statuses.find(s => s.id === id) || {}).name || '?';
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Status transition rules</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div style="font-size:12px;color:var(--tx-3);margin-bottom:14px">Define which moves are allowed. If a status has <b>no</b> rules, it can move to any status. Once you add a rule from a status, only the listed targets are allowed from it.</div>
        <div id="rules-list">${this._ruleRows(rules, name)}</div>
        <div style="margin-top:16px;padding-top:14px;border-top:1px solid var(--line,rgba(0,0,0,.07))">
          <div style="font-size:10.5px;font-weight:800;color:var(--tx-3);text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px">Add a rule</div>
          <div style="display:flex;gap:8px;align-items:end;flex-wrap:wrap">
            <label class="cat-f"><span>From</span><select class="field-select" id="rule-from">${statuses.map(s => `<option value="${s.id}">${s.name}</option>`).join('')}</select></label>
            <span style="padding-bottom:8px">→</span>
            <label class="cat-f"><span>To</span><select class="field-select" id="rule-to">${statuses.map(s => `<option value="${s.id}">${s.name}</option>`).join('')}</select></label>
            <button class="btn btn-primary" onclick="StatusesPage.addRule()">Add</button>
          </div>
        </div>
      </div>
      <div class="modal-footer"><button class="btn btn-glass" onclick="Modal.close()">Close</button></div>`, 'modal-wide');
  },

  _ruleRows(rules, name) {
    if (!rules.length) return '<div style="color:var(--tx-4);font-size:13px;padding:8px">No rules — all moves allowed.</div>';
    return `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>From</th><th>To</th><th></th></tr></thead><tbody>
      ${rules.map(r => `<tr><td>${name(r.from_status_id)}</td><td>→ ${name(r.to_status_id)}</td>
        <td><button class="cat-line-del" onclick="StatusesPage.delRule('${r.id}')">${icon('x', 13)}</button></td></tr>`).join('')}
    </tbody></table></div>`;
  },

  async addRule() {
    const from_status_id = document.getElementById('rule-from').value;
    const to_status_id = document.getElementById('rule-to').value;
    if (from_status_id === to_status_id) return Toast.error('Pick two different statuses');
    try {
      await post('/api/statuses/transitions', { from_status_id, to_status_id });
      Toast.success('Rule added');
      this.rulesModal();
    } catch (e) { Toast.error(e.message); }
  },

  async delRule(id) {
    try { await del(`/api/statuses/transitions/${id}`); Toast.success('Rule removed'); this.rulesModal(); }
    catch (e) { Toast.error(e.message); }
  },
};

Router.register('statuses', () => StatusesPage.load());
