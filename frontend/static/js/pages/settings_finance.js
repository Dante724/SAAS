/* ════════════════════════════════════════════════════
   Settings → Finance & numbering
   Numbering templates · Legal entities · Tax stack
════════════════════════════════════════════════════ */
const FinanceSettingsPage = {
  async load() {
    setContent(`<section class="settings-pane"><div id="fin-set-root">Loading…</div></section>`);
    await this.render();
  },

  taxModal() {
    Modal.open(`
      <div class="modal-header"><span class="modal-title">New tax</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Name *</label><input class="field-input" id="tx-name" placeholder="VAT"></div>
        <div class="field"><label class="field-label">Name for document</label><input class="field-input" id="tx-doc" placeholder="VAT 13.5%"></div>
        <div class="field"><label class="field-label">Code</label><input class="field-input" id="tx-code" placeholder="VAT13"></div>
        <div class="field"><label class="field-label">Type</label>
          <select class="field-select" id="tx-mode">
            <option value="imposed">Exclusive (added on top)</option>
            <option value="included">Inclusive (included in price)</option>
          </select></div>
        <div class="field"><label class="field-label">Rate (%)</label><input class="field-input" id="tx-rate" type="number" step="0.01" value="0"></div>
        <label class="cash-check"><input type="checkbox" id="tx-default"> Use as the default tax</label>
      </div>
      <div class="modal-footer"><button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" id="tx-save">Create</button></div>`, '', true);
    document.getElementById('tx-save').addEventListener('click', async () => {
      const name = document.getElementById('tx-name').value.trim();
      if (!name) return Toast.error('Name required');
      try {
        await post('/api/catalog/taxes', {
          name, name_for_document: document.getElementById('tx-doc').value || null,
          code: document.getElementById('tx-code').value || null,
          mode: document.getElementById('tx-mode').value,
          rate: parseFloat(document.getElementById('tx-rate').value) || 0,
          is_default: document.getElementById('tx-default').checked });
        Modal.close(); Toast.success('Tax created'); this.render();
      } catch (e) { Toast.error(e.message || 'Failed'); }
    });
  },

  async render() {
    const root = document.getElementById('fin-set-root');
    if (!root) return;
    let numbering = { items: [] }, entities = { items: [] }, taxes = { items: [] }, stacks = { stacks: {} }, cashcats = { items: [], default_tickets_id: null, default_sales_id: null };
    try {
      [numbering, entities, taxes, stacks, cashcats] = await Promise.all([
        get('/api/settings/numbering'),
        get('/api/settings/legal-entities'),
        get('/api/catalog/taxes'),
        get('/api/settings/tax-rules'),
        get('/api/staff/cash-categories'),
      ]);
    } catch (e) { root.innerHTML = `<div class="empty">Couldn't load finance settings.</div>`; return; }

    const numRows = numbering.items.filter(n => n.scope_key === 'org').map(n => `
      <tr>
        <td style="font-weight:600">${n.doc_type}</td>
        <td><input class="field-input num-tmpl" data-doc="${n.doc_type}" value="${n.template}" style="width:160px;font-family:monospace"></td>
        <td><input class="field-input num-from" data-doc="${n.doc_type}" type="number" value="${n.next_value}" style="width:90px"></td>
        <td class="num-example" style="color:var(--tx-3);font-family:monospace">${n.example}</td>
        <td><button class="btn btn-primary btn-sm num-save" data-doc="${n.doc_type}">Save</button></td>
      </tr>`).join('');

    const entRows = entities.items.map(e => `
      <tr>
        <td>${e.is_default ? '● ' : ''}${e.name}</td>
        <td style="color:var(--tx-3)">${e.tax_id || '—'}</td>
        <td>${e.address || '—'}</td>
        <td><button class="btn btn-glass btn-sm ent-del" data-id="${e.id}">Remove</button></td>
      </tr>`).join('') || `<tr><td colspan="4" class="empty">No legal entities yet.</td></tr>`;

    const taxOpts = taxes.items.map(t => `${t.name} (${t.rate}%)`);
    const stack = (stacks.stacks && stacks.stacks.tickets_sales) || [];
    const stackNames = stack.map(id => {
      const t = taxes.items.find(x => x.id === id);
      return t ? `${t.name} ${t.rate}%` : id;
    });

    root.innerHTML = `
      <h2 style="margin:0 0 4px">Finance &amp; numbering</h2>
      <p class="muted" style="margin:0 0 22px">Document numbering, legal entities, taxes, and the default tax stack — everything that shapes how invoices and receipts are numbered and taxed.</p>

      <div class="set-section">
        <div class="ss-head">${icon('receipt',16)} Numbering templates</div>
        <div class="ss-desc">Control how each document is numbered. Tokens: <code>{D}</code> day · <code>{M}</code> month · <code>{Y}</code> year · <code>{C:n}</code> counter (n digits). Example: <code>INV-{Y}-{C:4}</code> → INV-2026-0001.</div>
        <div class="tbl-wrap"><table class="data-table" style="margin:0">
          <thead><tr><th>Document</th><th>Template</th><th>Begins from</th><th>Example</th><th></th></tr></thead>
          <tbody>${numRows}</tbody>
        </table></div>
      </div>

      <div class="set-section">
        <div class="ss-head">${icon('bank',16)} Legal entities</div>
        <div class="ss-desc">Issue invoices under a specific entity; each can carry its own invoice numbering.</div>
        <div style="display:flex;gap:8px;margin:0 0 12px;flex-wrap:wrap">
          <input class="field-input" id="ent-name" placeholder="Entity name" style="max-width:220px">
          <input class="field-input" id="ent-tax" placeholder="Tax / VAT ID" style="max-width:160px">
          <input class="field-input" id="ent-addr" placeholder="Address" style="flex:1;min-width:180px">
          <label style="display:flex;align-items:center;gap:5px;font-size:13px;font-weight:600;color:var(--tx-2)"><input type="checkbox" id="ent-def"> Default</label>
          <button class="btn btn-primary btn-sm" id="ent-add">Add</button>
        </div>
        <div class="tbl-wrap"><table class="data-table" style="margin:0">
          <thead><tr><th>Name</th><th>Tax ID</th><th>Address</th><th></th></tr></thead>
          <tbody>${entRows}</tbody>
        </table></div>
      </div>

      <div class="set-section">
        <div class="ss-head">${icon('coin',16)} Taxes</div>
        <div class="ss-desc">Create the taxes that apply to your business. <b>Inclusive</b> taxes are included in prices; <b>exclusive</b> are added on top.</div>
        <div style="margin:0 0 12px"><button class="btn btn-primary btn-sm" id="tax-new">+ New tax</button></div>
        <div class="tbl-wrap"><table class="data-table" style="margin:0">
          <thead><tr><th>Name</th><th>Doc label</th><th>Code</th><th>Type</th><th style="text-align:right">Rate</th><th></th></tr></thead>
          <tbody>${taxes.items.map(t=>`<tr>
            <td class="tbl-strong">${t.name}${t.is_default?' <span class="badge badge-est">default</span>':''}</td>
            <td style="color:var(--tx-3)">${t.name_for_document||'—'}</td>
            <td style="color:var(--tx-3)">${t.code||'—'}</td>
            <td>${t.mode==='included'?'<span class="badge b-new">Inclusive</span>':'<span class="badge">Exclusive</span>'}</td>
            <td style="text-align:right;font-weight:700;font-variant-numeric:tabular-nums">${t.rate}%</td>
            <td style="text-align:right"><button class="btn btn-glass btn-sm tax-del" data-id="${t.id}">Remove</button></td>
          </tr>`).join('')||'<tr><td colspan="6" class="empty">No taxes yet.</td></tr>'}</tbody>
        </table></div>
      </div>

      <div class="set-section">
        <div class="ss-head">${icon('list',16)} Default tax stack — Tickets &amp; Sales</div>
        <div class="ss-desc">Taxes applied by default, in order (first has highest priority). Tick the taxes to include, then save.</div>
        <div style="font-size:13px;color:var(--tx-2);margin:0 0 12px">Current: <b>${stackNames.length ? stackNames.join(' → ') : '(none)'}</b></div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px">
          ${taxes.items.map(t => `<label class="tax-chip"><input type="checkbox" class="tax-pick" value="${t.id}" ${stack.includes(t.id) ? 'checked' : ''}> ${t.name} <span style="color:var(--tx-3)">${t.rate}%</span></label>`).join('') || '<span style="color:var(--tx-4);font-size:13px">Create a tax above first.</span>'}
        </div>
        <button class="btn btn-primary btn-sm" id="stack-save">Save tax stack</button>
      </div>

      <div class="set-section">
        <div class="ss-head">${icon('wallet',16)} Default payment categories</div>
        <div class="ss-desc">Auto-label payments recorded from these document types.</div>
        <div style="display:grid;grid-template-columns:100px 260px;gap:12px;align-items:center;max-width:400px">
          <label style="font-weight:600;color:var(--tx-2)">Tickets</label>
          <select class="field-select" id="cc-tickets">${['<option value="">— none —</option>'].concat(cashcats.items.filter(x=>x.direction==='income').map(x=>`<option value="${x.id}" ${cashcats.default_tickets_id===x.id?'selected':''}>${x.name}</option>`)).join('')}</select>
          <label style="font-weight:600;color:var(--tx-2)">Sales</label>
          <select class="field-select" id="cc-sales">${['<option value="">— none —</option>'].concat(cashcats.items.filter(x=>x.direction==='income').map(x=>`<option value="${x.id}" ${cashcats.default_sales_id===x.id?'selected':''}>${x.name}</option>`)).join('')}</select>
        </div>
        <button class="btn btn-primary btn-sm" style="margin-top:14px" id="cc-save">Save categories</button>
      </div>
    `;

    // ── numbering: live example + save ──
    root.querySelectorAll('.num-tmpl, .num-from').forEach(inp => inp.addEventListener('input', async (e) => {
      const doc = e.target.dataset.doc;
      const tmpl = root.querySelector(`.num-tmpl[data-doc="${doc}"]`).value;
      const from = parseInt(root.querySelector(`.num-from[data-doc="${doc}"]`).value) || 1;
      try {
        const r = await post('/api/settings/numbering/preview', { doc_type: doc, template: tmpl, begins_from: from });
        e.target.closest('tr').querySelector('.num-example').textContent = r.example;
      } catch (_) {}
    }));
    root.querySelectorAll('.num-save').forEach(btn => btn.addEventListener('click', async () => {
      const doc = btn.dataset.doc;
      const tmpl = root.querySelector(`.num-tmpl[data-doc="${doc}"]`).value;
      const from = parseInt(root.querySelector(`.num-from[data-doc="${doc}"]`).value) || 1;
      try { await put('/api/settings/numbering', { doc_type: doc, template: tmpl, begins_from: from }); Toast.success(`${doc} numbering saved`); }
      catch (e) { Toast.error(e.message || 'Save failed'); }
    }));

    // ── legal entities ──
    root.querySelector('#ent-add').addEventListener('click', async () => {
      const name = root.querySelector('#ent-name').value.trim();
      if (!name) return Toast.error('Name required');
      try {
        await post('/api/settings/legal-entities', {
          name, tax_id: root.querySelector('#ent-tax').value || null,
          address: root.querySelector('#ent-addr').value || null,
          is_default: root.querySelector('#ent-def').checked });
        Toast.success('Legal entity added'); this.render();
      } catch (e) { Toast.error(e.message || 'Failed'); }
    });
    root.querySelectorAll('.ent-del').forEach(btn => btn.addEventListener('click', async () => {
      if (!(await confirm2('Remove this legal entity?'))) return;
      try { await del(`/api/settings/legal-entities/${btn.dataset.id}`); Toast.success('Removed'); this.render(); }
      catch (e) { Toast.error(e.message || 'Failed'); }
    }));

    // ── tax stack ──
    root.querySelector('#stack-save').addEventListener('click', async () => {
      const ids = [...root.querySelectorAll('.tax-pick:checked')].map(c => c.value);
      try { await put('/api/settings/tax-rules', { doc_family: 'tickets_sales', tax_ids: ids }); Toast.success('Tax stack saved'); this.render(); }
      catch (e) { Toast.error(e.message || 'Failed'); }
    });

    root.querySelector('#tax-new').addEventListener('click', () => this.taxModal());
    root.querySelectorAll('.tax-del').forEach(btn => btn.addEventListener('click', async () => {
      if (!(await confirm2('Remove this tax?'))) return;
      try { await del(`/api/catalog/taxes/${btn.dataset.id}`); Toast.success('Removed'); this.render(); }
      catch (e) { Toast.error(e.message || 'Failed'); }
    }));
    root.querySelector('#cc-save').addEventListener('click', async () => {
      try {
        await put('/api/staff/cash-categories/defaults', {
          default_tickets_id: root.querySelector('#cc-tickets').value || null,
          default_sales_id: root.querySelector('#cc-sales').value || null });
        Toast.success('Default categories saved');
      } catch (e) { Toast.error(e.message || 'Failed'); }
    });
  },
};
