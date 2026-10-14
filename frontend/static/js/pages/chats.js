/* Chats — the single unified multi-channel messaging page (build 113).

   Backed entirely by /api/inbox, which threads WhatsApp, SMS, email, Facebook, Instagram
   and calls per customer and links each to a client. There is no separate Inbox any more:
   channel filtering happens inline here, so switching channels never reloads the page or
   lags. Outbound transport is simulated until provider keys are connected. */
const ChatsPage = {
  _threads: [], _active: null, _filter: 'all', _channel: '',

  CH: { whatsapp:'WhatsApp', sms:'SMS', email:'Email', facebook:'Facebook', instagram:'Instagram', call:'Call' },

  async load() {
    setTopbarActions(`<button class="btn btn-primary" onclick="ChatsPage.newModal()">+ New chat</button>`);
    setContent(`
      <div style="display:flex;gap:10px;align-items:center;margin-bottom:12px;flex-wrap:wrap">
        <div class="chip-row" style="margin:0">
          ${['all','open','snoozed','closed'].map(s=>`<div class="chip ${this._filter===s?'active':''}" onclick="ChatsPage.setFilter('${s}')">${s[0].toUpperCase()+s.slice(1)}</div>`).join('')}
        </div>
        <select class="field-select" style="width:auto;margin-left:auto" onchange="ChatsPage.setChannel(this.value)">
          <option value="">All channels</option>
          ${Object.entries(this.CH).map(([k,v])=>`<option value="${k}" ${this._channel===k?'selected':''}>${v}</option>`).join('')}
        </select>
      </div>
      <div style="font-size:12px;color:var(--tx-3);margin-bottom:12px">
        Outbound messages are <b>simulated</b> (logged, not transmitted) until channel provider keys are connected.
      </div>
      <div style="display:grid;grid-template-columns:330px 1fr;gap:16px;align-items:start;height:calc(100vh - 270px)">
        <div class="card" style="padding:0;overflow-y:auto;height:100%" id="ch-threads"></div>
        <div class="card" style="padding:0;display:flex;flex-direction:column;height:100%" id="ch-pane">
          <div style="flex:1;display:flex;align-items:center;justify-content:center;color:var(--tx-4);font-size:13px">Pick a conversation</div>
        </div>
      </div>`);
    await this.reloadThreads();
  },

  async setFilter(f) { this._filter = f; await this.load(); },
  async setChannel(c) { this._channel = c; await this.reloadThreads(); },

  async reloadThreads() {
    const box = document.getElementById('ch-threads');
    if (!box) return;
    try {
      const q = `status=${this._filter}` + (this._channel ? `&channel=${this._channel}` : '');
      const data = await get(`/api/inbox/threads?${q}`);
      this._threads = data.items || [];
    } catch (e) { return Toast.error(e.message); }

    box.innerHTML = this._threads.map(t => `
      <div class="ch-row ${this._active===t.id?'active':''}" onclick="ChatsPage.open('${t.id}')"
           style="padding:12px 14px;border-bottom:1px solid var(--g-border);cursor:pointer;${this._active===t.id?'background:var(--g-hover,#f2f6ff)':''}">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:3px">
          <span class="badge">${this.CH[t.channel]||t.channel}</span>
          <span style="font-weight:${t.unread?'800':'700'};flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${t.handle||t.subject||'—'}</span>
          ${t.unread?'<span style="width:8px;height:8px;border-radius:50%;background:var(--blue)"></span>':''}
        </div>
        <div style="font-size:12px;color:var(--tx-3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${t.preview||''}</div>
        <div style="font-size:10.5px;color:var(--tx-4);margin-top:3px">${t.client_id?'● in CRM':''} ${t.last_message_at?('· '+fmt.reltime(t.last_message_at)):''}</div>
      </div>`).join('') || `<div style="padding:24px;text-align:center;color:var(--tx-4);font-size:13px">No ${this._filter==='all'?'':this._filter+' '}conversations</div>`;
  },

  async open(id) {
    this._active = id;
    const pane = document.getElementById('ch-pane');
    pane.innerHTML = '<div style="flex:1;display:flex;align-items:center;justify-content:center;color:var(--tx-4)">Loading…</div>';
    let data;
    try { data = await get(`/api/inbox/threads/${id}`); }
    catch (e) { return Toast.error(e.message); }
    const t = data.thread;
    await this.reloadThreads();

    pane.innerHTML = `
      <div style="display:flex;align-items:center;gap:10px;padding:14px 16px;border-bottom:1px solid var(--g-border)">
        <span class="badge">${this.CH[t.channel]||t.channel}</span>
        <b style="flex:1">${t.handle||t.subject||'Conversation'}</b>
        <div style="display:flex;gap:6px">
          ${t.order_id?`<span class="badge b-done">Order created</span>`
            :`<button class="btn btn-glass btn-sm" onclick="ChatsPage.toOrder('${t.id}')">→ Work order</button>`}
          <button class="btn btn-glass btn-sm" onclick="ChatsPage.setStatus('${t.id}','${t.status==='closed'?'open':'closed'}')">${t.status==='closed'?'Reopen':'Close'}</button>
          ${t.status==='open'?`<button class="btn btn-glass btn-sm" onclick="ChatsPage.setStatus('${t.id}','snoozed')">Snooze</button>`:''}
        </div>
      </div>
      <div id="ch-msgs" style="flex:1;overflow-y:auto;display:flex;flex-direction:column;gap:8px;padding:16px">
        ${data.messages.map(m=>`
          <div style="align-self:${m.direction==='outbound'?'flex-end':'flex-start'};max-width:72%">
            <div style="padding:9px 13px;border-radius:13px;font-size:13px;line-height:1.45;background:${m.direction==='outbound'?'var(--blue)':'var(--g-2,#f1f3f7)'};color:${m.direction==='outbound'?'#fff':'inherit'}">
              ${(m.body||'').replace(/</g,'&lt;')}${m.media_url?`<div style="margin-top:4px"><a href="${m.media_url}" target="_blank" style="color:inherit;text-decoration:underline">attachment</a></div>`:''}
            </div>
            <div style="font-size:10px;color:var(--tx-4);margin-top:2px;text-align:${m.direction==='outbound'?'right':'left'}">${new Date(m.created_at).toLocaleString()}</div>
          </div>`).join('')||'<div style="margin:auto;color:var(--tx-4);font-size:13px">No messages yet</div>'}
      </div>
      <div style="display:flex;gap:8px;padding:12px 16px;border-top:1px solid var(--g-border)">
        <input class="field-input" id="ch-reply" placeholder="Type a reply…" style="flex:1"
               onkeydown="if(event.key==='Enter'){event.preventDefault();ChatsPage.reply('${t.id}')}">
        <button class="btn btn-primary" onclick="ChatsPage.reply('${t.id}')">Send</button>
      </div>`;
    const msgs = document.getElementById('ch-msgs');
    if (msgs) msgs.scrollTop = msgs.scrollHeight;
  },

  async reply(id) {
    const el = document.getElementById('ch-reply');
    const body = (el.value || '').trim();
    if (!body) return;
    try { await post(`/api/inbox/threads/${id}/reply`, { body }); el.value=''; await this.open(id); }
    catch (e) { Toast.error(e.message || 'Could not send'); }
  },

  async setStatus(id, status) {
    try { await patch(`/api/inbox/threads/${id}?status=${status}`); await this.open(id); }
    catch (e) { Toast.error(e.message); }
  },

  async toOrder(id) {
    try { const r = await post(`/api/inbox/threads/${id}/to-order`, {}); Toast.success(`Work order ${r.order_number} created`); await this.open(id); }
    catch (e) { Toast.error(e.message || 'Could not create order'); }
  },

  newModal() {
    Modal.open(`
      <div class="modal-header"><span class="modal-title">New conversation</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Channel</label>
          <select class="field-select" id="nc-channel">
            ${Object.entries(this.CH).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')}
          </select></div>
        <div class="field"><label class="field-label">To (phone / email / handle)</label>
          <input class="field-input" id="nc-handle" placeholder="+1555… or name@email"></div>
        <div class="field"><label class="field-label">Message</label>
          <textarea class="field-textarea" id="nc-body" placeholder="Type your message…"></textarea></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="ChatsPage.createChat()">Start</button>
      </div>`);
  },

  async createChat() {
    const channel = document.getElementById('nc-channel').value;
    const handle = document.getElementById('nc-handle').value.trim();
    const body = document.getElementById('nc-body').value.trim();
    if (!handle) return Toast.error('Enter a recipient');
    try {
      const r = await post('/api/inbox/threads', { channel, handle, body: body || null });
      Modal.close();
      await this.load();
      await this.open(r.thread_id);
    } catch (e) { Toast.error(e.message || 'Could not start chat'); }
  },
};

Router.register('chats', () => ChatsPage.load());
