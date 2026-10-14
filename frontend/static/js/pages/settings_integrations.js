/* ════════════════════════════════════════════════════
   Settings > Integrations / API / Webhooks
   Orderry-style: Integrations hub for connectors, an API page for
   keys + docs, and a Webhooks page (event-driven push) — the home for
   all future integration wiring (Zapier/Make, QuickBooks, payments, AI, chat).
════════════════════════════════════════════════════ */

/* ---- Integrations hub ---- */
const IntegrationsSettingsPage = {
  async load() {
    // Payments provider (A1): per-org gateway selection
    setTimeout(async () => {
      try {
        const host = document.getElementById('integ-wrap'); if (!host) return;
        if (document.getElementById('pay-provider-card')) return;
        const [cfg, company] = await Promise.all([get('/api/payments/config'), get('/api/staff/company')]);
        const opts = [['auto','Auto (first configured)'],['stripe','Stripe'],['square','Square'],['sumup','SumUp'],['simulated','Simulated (testing)']];
        const badge = (k) => cfg.available[k] ? '<span style="color:var(--ok,#34d399);font-size:11px;font-weight:700">configured</span>' : '<span style="color:var(--tx-4);font-size:11px">no keys</span>';
        const card = document.createElement('div');
        card.id = 'pay-provider-card';
        card.className = 'card';
        card.style.cssText = 'padding:16px;margin:0 0 14px';
        card.innerHTML = `
          <div style="font-weight:800;margin-bottom:4px">Payments provider</div>
          <div style="font-size:12px;color:var(--tx-3);margin-bottom:10px">Which gateway payment links and checkout use. Keys are set via environment (STRIPE_*, SQUARE_*, SUMUP_*). Currently live: <b>${cfg.gateway}</b></div>
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <select class="field-select" id="pay-provider" style="max-width:280px">
              ${opts.map(([k,l])=>`<option value="${k}" ${company.payment_provider===k?'selected':''}>${l}</option>`).join('')}
            </select>
            <button class="btn btn-primary btn-sm" onclick="(async()=>{try{await put('/api/staff/company',{payment_provider:document.getElementById('pay-provider').value});Toast.success('Payment provider saved')}catch(e){Toast.error(e.message||'Save failed')}})()">Save</button>
            <span style="font-size:12px">Stripe ${badge('stripe')} · Square ${badge('square')} · SumUp ${badge('sumup')}</span>
          </div>`;
        host.prepend(card);
      } catch (e) {}
    }, 60);

    setTopbarActions('');
    setContent(`<div id="integ-wrap"><div class="card" style="padding:24px;color:var(--tx-3)">Loading integrations…</div></div>`);
    const [qbo, pay, ai, email] = await Promise.all([
      get('/api/integrations/qbo/status').catch(() => ({})),
      get('/api/payments/config').catch(() => ({})),
      get('/api/ai/status').catch(() => ({})),
      get('/api/integrations/email/status').catch(() => ({ providers: {}, accounts: [] })),
    ]);
    const badge = (ok, on, off) => ok ? `<span class="badge b-done">${on}</span>` : `<span class="badge b-new">${off}</span>`;
    const card = (title, desc, status, action) => `
      <div class="card" style="padding:18px;display:flex;flex-direction:column;gap:8px">
        <div style="display:flex;justify-content:space-between;align-items:center">
          <span style="font-weight:800">${title}</span>${status}
        </div>
        <p style="color:var(--tx-3);font-size:13px;margin:0">${desc}</p>
        <div style="margin-top:6px">${action || ''}</div>
      </div>`;
    document.getElementById('integ-wrap').innerHTML = `
      <p style="color:var(--tx-2);margin:0 0 14px">Connect RepairOS to the tools you already use. Credential-gated connectors go live the moment their keys are set on the server; everything below is ready to wire.</p>
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px">
        ${card('QuickBooks Online', 'Two-way sync of invoices, payments and contacts.',
          badge(qbo.connected, 'Connected', qbo.configured ? 'Not connected' : 'Not configured'),
          qbo.connected
            ? `<div style="display:flex;gap:8px;flex-wrap:wrap">
                 <button class="btn btn-glass btn-sm" onclick="IntegrationsSettingsPage.qboLogs()">Sync logs</button>
                 <button class="btn btn-glass btn-sm" onclick="IntegrationsSettingsPage.qboDisconnect()">Disconnect</button>
               </div>`
            : (qbo.configured
              ? `<button class="btn btn-primary btn-sm" onclick="IntegrationsSettingsPage.qboConnect()">Connect</button>`
              : `<span style="color:var(--tx-4);font-size:12px">Set QBO_CLIENT_ID / SECRET on the server</span>`))}
        ${card('Online payments (Stripe)', 'Hosted checkout & payment links; reconciles into your cashboxes.',
          badge(pay.live, 'Live', 'Simulated'),
          `<span style="color:var(--tx-4);font-size:12px">Gateway: ${pay.gateway || 'simulated'} · set STRIPE_SECRET_KEY to go live</span>`)}
        ${card('WhatsApp / SMS (Twilio)', 'Two-way chat; inbound webhook creates & links threads.',
          `<span class="badge b-new">Server-keyed</span>`,
          `<span style="color:var(--tx-4);font-size:12px">Inbound URL: <code>/api/chats/webhook/twilio?org_id=…</code></span>`)}
        ${card('Workflow AI (Anthropic)', 'Order summaries, estimate drafting, reply suggestions.',
          badge(ai.live, 'Live: ' + (ai.model || ''), 'Stub'),
          `<span style="color:var(--tx-4);font-size:12px">Set ANTHROPIC_API_KEY to use a live model</span>`)}
        ${card('No-code (Zapier / Make.com)', 'Trigger 1000s of apps from RepairOS events via webhooks.',
          `<span class="badge b-done">Available</span>`,
          `<button class="btn btn-glass btn-sm" onclick="SettingsPage.go('webhooks')">Open Webhooks</button>`)}
        ${card('Public REST API', 'Build custom integrations against orders, contacts, inventory & more.',
          `<span class="badge b-done">Available</span>`,
          `<button class="btn btn-glass btn-sm" onclick="SettingsPage.go('api')">Manage API keys</button>`)}
        ${card('E-commerce (Shopify / WooCommerce)', 'Sync products, orders and customers from your store.',
          `<span class="badge b-new">Planned</span>`, '')}
        ${card('Website status widget', 'Embed a repair-status checker on your own site — customers look up a job with their order number and phone.',
          `<span class="badge b-done">Available</span>`,
          `<button class="btn btn-primary btn-sm" onclick="IntegrationsSettingsPage.widgetModal()">Get embed code</button>`)}
      </div>
      ${this._emailSection(email)}`;
  },

  widgetModal() {
    const origin = window.location.origin;
    const snippet =
      `<div id="repairos-status"></div>\n` +
      `<script src="${origin}/api/public/widget.js" data-api="${origin}"></script>`;
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Website status widget</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <p style="color:var(--tx-3);font-size:13px;margin:0 0 14px">
          Paste this snippet into your website where you want the checker to appear — no build step,
          no iframe. Customers look up a repair with their <b>order number and phone</b>; both must
          match, so one customer can never see another's job.
        </p>
        <div style="font-size:10.5px;font-weight:800;color:var(--tx-3);text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px">Embed snippet</div>
        <textarea id="wg-snippet" readonly rows="3"
          style="width:100%;box-sizing:border-box;font-family:Consolas,monospace;font-size:12.5px;padding:12px;border:1px solid var(--g-border);border-radius:8px;background:var(--g-2,#f6f8fc);resize:none">${snippet.replace(/</g, '&lt;')}</textarea>
        <button class="btn btn-primary btn-sm" style="margin-top:10px" onclick="IntegrationsSettingsPage.copyWidget()">Copy snippet</button>

        <div style="font-size:10.5px;font-weight:800;color:var(--tx-3);text-transform:uppercase;letter-spacing:.06em;margin:20px 0 8px">How it works</div>
        <ol style="color:var(--tx-3);font-size:13px;margin:0;padding-left:18px;line-height:1.7">
          <li>Copy the snippet above.</li>
          <li>Paste it into any page on your website (inside the <code>&lt;body&gt;</code>).</li>
          <li>A "Check your repair status" box appears where you pasted it.</li>
          <li>Customers enter their order number + phone; the widget shows live status.</li>
        </ol>

        <div style="font-size:10.5px;font-weight:800;color:var(--tx-3);text-transform:uppercase;letter-spacing:.06em;margin:20px 0 8px">Live preview</div>
        <div style="font-family:system-ui,sans-serif;max-width:420px;border:1px solid var(--g-border);border-radius:12px;padding:18px">
          <div style="font-weight:700;font-size:16px;margin-bottom:10px">Check your repair status</div>
          <input placeholder="Order number" disabled style="width:100%;box-sizing:border-box;padding:9px;margin-bottom:8px;border:1px solid var(--g-border);border-radius:8px">
          <input placeholder="Phone number" disabled style="width:100%;box-sizing:border-box;padding:9px;margin-bottom:10px;border:1px solid var(--g-border);border-radius:8px">
          <button class="btn btn-primary" style="width:100%" disabled>Check status</button>
        </div>
      </div>
      <div class="modal-footer"><button class="btn btn-glass" onclick="Modal.close()">Close</button></div>`,
      'modal-wide');
  },

  copyWidget() {
    const el = document.getElementById('wg-snippet');
    el.select();
    try { navigator.clipboard.writeText(el.value); Toast.success('Snippet copied'); }
    catch (e) { document.execCommand('copy'); Toast.success('Snippet copied'); }
  },

  _emailSection(email) {
    const providers = email.providers || {};
    const accounts = email.accounts || [];
    const connectBtn = (prov, label) => {
      if (!providers[prov]?.configured) {
        return `<span style="color:var(--tx-4);font-size:12px">${label}: set ${prov === 'gmail' ? 'GMAIL' : 'OUTLOOK'}_CLIENT_ID / SECRET on the server</span>`;
      }
      return `<button class="btn btn-primary btn-sm" onclick="IntegrationsSettingsPage.emailConnect('${prov}')">Connect ${label}</button>`;
    };
    const rows = accounts.length ? accounts.map(a => `
      <div style="display:flex;align-items:center;gap:10px;padding:10px 0;border-top:1px solid var(--g-border)">
        <div style="flex:1">
          <div style="font-weight:700">${a.email || '(address pending)'} ${a.is_default ? '<span class="badge b-done" style="margin-left:6px">default</span>' : ''}</div>
          <div style="color:var(--tx-4);font-size:12px">${a.provider}${a.sender_name ? ' · ' + a.sender_name : ''} · ${a.connected ? 'connected' : 'pending'}</div>
        </div>
        ${a.connected && !a.is_default ? `<button class="btn btn-glass btn-sm" onclick="IntegrationsSettingsPage.emailSetDefault('${a.id}')">Make default</button>` : ''}
        <button class="btn btn-glass btn-sm" onclick="IntegrationsSettingsPage.emailDisconnect('${a.id}')">Disconnect</button>
      </div>`).join('')
      : `<div style="color:var(--tx-4);font-size:13px;padding:8px 0">No email accounts connected yet. The default account sends customer messages; RepairOS falls back to SMTP if none is connected.</div>`;
    return `
      <div class="card" style="padding:18px;margin-top:16px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
          <span style="font-weight:800">Email sending (Gmail / Outlook)</span>
        </div>
        <p style="color:var(--tx-3);font-size:13px;margin:0 0 10px">Send customer messages from your shop's own address. Tokens are encrypted at rest; the default account is used first, with SMTP as a fallback.</p>
        <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:6px">
          ${connectBtn('gmail', 'Gmail')} ${connectBtn('outlook', 'Outlook')}
        </div>
        ${rows}
      </div>`;
  },

  async emailConnect(provider) {
    try {
      const r = await post('/api/integrations/email/connect', { provider });
      if (r.authorize_url) window.open(r.authorize_url, '_blank', 'noopener');
    } catch (e) { Toast.error(e.message || 'Could not start connection'); }
  },
  async emailSetDefault(id) {
    try { await post(`/api/integrations/email/${id}/default`, {}); await this.load(); Toast.success('Default sender updated'); }
    catch (e) { Toast.error(e.message); }
  },
  async emailDisconnect(id) {
    if (!(await confirm2('Disconnect this email account?'))) return;
    try { await del(`/api/integrations/email/${id}`); await this.load(); }
    catch (e) { Toast.error(e.message); }
  },

  async qboConnect() {
    try {
      const r = await get('/api/integrations/qbo/connect');
      if (r.auth_url) window.open(r.auth_url, '_blank', 'noopener');
    } catch (e) { Toast.error(e.message); }
  },
  async qboDisconnect() {
    if (!(await confirm2('Disconnect QuickBooks?'))) return;
    try { await post('/api/integrations/qbo/disconnect', {}); await this.load(); } catch (e) { Toast.error(e.message); }
  },
  async qboLogs() {
    let items = [];
    try { items = (await get('/api/integrations/qbo/logs')).items || []; }
    catch (e) { return Toast.error(e.message); }
    const badge = (s) => s === 'ok' ? '<span class="badge b-done">ok</span>'
      : s === 'skipped' ? '<span class="badge b-new">skipped</span>'
      : `<span class="badge" style="background:var(--red,#e5484d);color:#fff">${s}</span>`;
    const rows = items.length ? items.map(x => `
      <tr>
        <td>${x.created_at ? new Date(x.created_at).toLocaleString() : ''}</td>
        <td>${x.entity_type}</td>
        <td>${badge(x.status)}</td>
        <td style="color:var(--tx-4);font-size:12px">${x.remote_id || x.error || ''}</td>
      </tr>`).join('')
      : `<tr><td colspan="4" style="text-align:center;color:var(--tx-4);padding:20px">No sync attempts yet.</td></tr>`;
    Modal.open(`
      <div class="modal-header"><span class="modal-title">QuickBooks sync logs</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="tbl-wrap"><table class="tbl">
          <thead><tr><th>When</th><th>Entity</th><th>Status</th><th>Detail</th></tr></thead>
          <tbody>${rows}</tbody>
        </table></div>
      </div>
      <div class="modal-footer"><button class="btn btn-glass" onclick="Modal.close()">Close</button></div>`, 'modal-wide');
  },
};

/* ---- API keys ---- */
const ApiKeysSettingsPage = {
  async load() {
    setTopbarActions(`<button class="btn btn-primary" onclick="ApiKeysSettingsPage.createModal()">+ New API key</button>`);
    setContent(`
      <p style="color:var(--tx-2);margin:0 0 12px">Keys authenticate the public REST API (<code>Authorization: Bearer rok_…</code>). The full key is shown once at creation. Base path <code>/api/v1</code>; discovery at <code>GET /api/v1</code>.</p>
      <div class="card"><div class="tbl-wrap"><table class="tbl">
        <thead><tr><th>Name</th><th>Key prefix</th><th>Scopes</th><th>Last used</th><th></th></tr></thead>
        <tbody id="ak-tbody">${skeletonRows(4)}</tbody>
      </table></div></div>`);
    await this.reload();
  },
  async reload() {
    const tbody = document.getElementById('ak-tbody');
    if (!tbody) return;
    try {
      const data = await get('/api/api-keys');
      if (!data.items.length) { tbody.innerHTML = emptyRow(5, 'bolt', 'No API keys', 'Create one to start integrating'); return; }
      tbody.innerHTML = data.items.map(k => `<tr>
        <td style="font-weight:800">${k.name}</td>
        <td><code>${k.prefix}…</code></td>
        <td>${(k.scopes || '').split(',').map(s => `<span class="badge b-new">${s}</span>`).join(' ')}</td>
        <td style="color:var(--tx-3)">${k.last_used_at ? fmt.date(k.last_used_at) : 'never'}</td>
        <td><button class="btn btn-glass btn-sm" onclick="ApiKeysSettingsPage.revoke('${k.id}')">Revoke</button></td>
      </tr>`).join('');
    } catch (e) { Toast.error(e.message); }
  },
  createModal() {
    Modal.open(`
      <div class="modal-header"><span class="modal-title">New API key</span><button class="modal-close" onclick="Modal.close()">&times;</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Name</label><input class="field-input" id="ak-name" placeholder="e.g. Website integration"></div>
        <div class="field"><label class="field-label">Scopes</label>
          <select class="field-select" id="ak-scopes">
            <option value="read,write">Read &amp; write</option>
            <option value="read">Read only</option>
          </select></div>
      </div>
      <div class="modal-footer"><button class="btn btn-ghost" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="ApiKeysSettingsPage.create()">Create</button></div>`);
  },
  async create() {
    const name = document.getElementById('ak-name').value.trim() || 'API key';
    const scopes = document.getElementById('ak-scopes').value;
    try {
      const r = await post('/api/api-keys', { name, scopes });
      Modal.close();
      Modal.open(`
        <div class="modal-header"><span class="modal-title">Copy your key now</span><button class="modal-close" onclick="Modal.close()">&times;</button></div>
        <div class="modal-body">
          <p style="color:var(--amber);font-weight:700">${r.note}</p>
          <div class="field"><label class="field-label">API key</label>
            <input class="field-input" readonly value="${r.key}" onclick="this.select()" style="font-family:monospace"></div>
        </div>
        <div class="modal-footer"><button class="btn btn-primary" onclick="Modal.close()">Done</button></div>`);
      await this.reload();
    } catch (e) { Toast.error(e.message); }
  },
  async revoke(id) {
    if (!(await confirm2('Revoke this key? Apps using it will stop working.'))) return;
    try { await del(`/api/api-keys/${id}`); await this.reload(); } catch (e) { Toast.error(e.message); }
  },
};

/* ---- Webhooks ---- */
const WebhooksSettingsPage = {
  events: [],
  async load() {
    setTopbarActions(`<button class="btn btn-primary" onclick="WebhooksSettingsPage.createModal()">+ New webhook</button>`);
    setContent(`
      <p style="color:var(--tx-2);margin:0 0 12px">Webhooks push real-time events to your endpoint as signed HTTP POSTs (JSON) — the way to drive Zapier, Make.com, or your own server. Use HTTPS endpoints.</p>
      <div class="card" style="margin-bottom:16px"><div class="tbl-wrap"><table class="tbl">
        <thead><tr><th>Endpoint URL</th><th>Events</th><th>Secret</th><th></th></tr></thead>
        <tbody id="wh-tbody">${skeletonRows(3)}</tbody>
      </table></div></div>
      <div style="font-weight:800;margin:0 0 8px">Recent deliveries</div>
      <div class="card"><div class="tbl-wrap"><table class="tbl">
        <thead><tr><th>Event</th><th>Status</th><th>When</th></tr></thead>
        <tbody id="wh-deliv">${skeletonRows(3)}</tbody>
      </table></div></div>`);
    await this.reload();
  },
  async reload() {
    try {
      const data = await get('/api/webhooks');
      this.events = data.available_events || [];
      const tbody = document.getElementById('wh-tbody');
      tbody.innerHTML = data.items.length ? data.items.map(e => `<tr>
        <td style="font-weight:700;word-break:break-all">${e.url}</td>
        <td>${e.events === '*' ? '<span class="badge b-done">All events</span>' : (e.events || '').split(',').map(x => `<span class="badge b-new">${x}</span>`).join(' ')}</td>
        <td><code>${(e.secret || '').slice(0, 10)}…</code></td>
        <td><button class="btn btn-glass btn-sm" onclick="WebhooksSettingsPage.remove('${e.id}')">Delete</button></td>
      </tr>`).join('') : emptyRow(4, 'bolt', 'No webhooks', 'Add an endpoint to receive events');
      const deliv = await get('/api/webhooks/deliveries').catch(() => ({ items: [] }));
      const dl = (deliv.items || deliv || []);
      const dbody = document.getElementById('wh-deliv');
      dbody.innerHTML = dl.length ? dl.slice(0, 15).map(d => {
        const ok = (d.status === 'delivered' || d.status === 'ok' || (d.response_code >= 200 && d.response_code < 300));
        return `<tr><td>${d.event}</td>
          <td><span class="badge ${ok ? 'b-done' : 'b-drop'}">${d.status || d.response_code || 'failed'}</span></td>
          <td style="color:var(--tx-3)">${d.created_at ? fmt.datetime(d.created_at) : '—'}</td></tr>`;
      }).join('') : emptyRow(3, 'clock', 'No deliveries yet', 'They appear once an event fires');
    } catch (e) { Toast.error(e.message); }
  },
  createModal() {
    Modal.open(`
      <div class="modal-header"><span class="modal-title">New webhook</span><button class="modal-close" onclick="Modal.close()">&times;</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Destination URL (HTTPS)</label>
          <input class="field-input" id="wh-url" placeholder="https://hook.make.com/..."></div>
        <div class="field"><label class="field-label">Events</label>
          <label class="field-check"><input type="checkbox" id="wh-all" checked onchange="WebhooksSettingsPage.toggleAll()"> All events</label>
          <div id="wh-events" style="display:none;flex-wrap:wrap;gap:8px;margin-top:8px">
            ${this.events.map(ev => `<label class="field-check" style="margin:0"><input type="checkbox" class="wh-ev" value="${ev}"> ${ev}</label>`).join('')}
          </div></div>
      </div>
      <div class="modal-footer"><button class="btn btn-ghost" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="WebhooksSettingsPage.create()">Create</button></div>`);
  },
  toggleAll() {
    const all = document.getElementById('wh-all').checked;
    document.getElementById('wh-events').style.display = all ? 'none' : 'flex';
  },
  async create() {
    const url = document.getElementById('wh-url').value.trim();
    if (!url.startsWith('http')) { Toast.error('Enter a valid http(s) URL'); return; }
    const all = document.getElementById('wh-all').checked;
    const events = all ? '*' : Array.from(document.querySelectorAll('.wh-ev:checked')).map(c => c.value).join(',');
    if (!all && !events) { Toast.error('Select at least one event'); return; }
    try {
      const r = await post('/api/webhooks', { url, events });
      Modal.close();
      if (r.secret) Toast.success('Webhook created — signing secret: ' + r.secret.slice(0, 12) + '…');
      await this.reload();
    } catch (e) { Toast.error(e.message); }
  },
  async remove(id) {
    if (!(await confirm2('Delete this webhook endpoint?'))) return;
    try { await del(`/api/webhooks/${id}`); await this.reload(); } catch (e) { Toast.error(e.message); }
  },
};
