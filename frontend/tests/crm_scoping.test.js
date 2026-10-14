/* Builds 82/83 — the client import flow (column mapping + dry-run preview) and the
   role editor's Access conditions.

   Run:  NODE_PATH=../../node_modules node crm_scoping.test.js                    */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { JSDOM } = require('jsdom');

const results = [];
function check(name, cond, detail) {
  results.push({ name, ok: !!cond });
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${name}${cond ? '' : '  ↳ ' + (detail || '')}`);
}

function ctxFor(extraGlobals = {}) {
  const dom = new JSDOM('<!DOCTYPE html><body><div id="root"></div></body>', { runScripts: 'outside-only' });
  const { window } = dom;
  const ctx = {
    window, document: window.document, console, setTimeout, clearTimeout,
    get: async () => ({ items: [] }), post: async () => ({}), put: async () => ({}), del: async () => ({}),
    Toast: { success() {}, error() {}, info() {} }, confirm2: async () => true,
    fmt: { currency: n => '$' + n, date: d => d, datetime: d => d },
    setContent: () => {}, setTopbarActions: () => {},
    Router: { register() {}, go() {}, current: 'clients' },
    Modal: { open(html) { window.document.getElementById('root').innerHTML = html; }, close() {} },
    ...extraGlobals,
  };
  ctx.global = ctx;
  vm.createContext(ctx);
  return { ctx, window };
}

(async () => {
  console.log('Client import (build 82):');
  {
    const { ctx, window } = ctxFor();
    let posted = null;
    ctx.post = async (url, body) => {
      posted = { url, body };
      return { created: 2, updated: 1, skipped: 0, errors: [], dry_run: body.dry_run, preview: [] };
    };
    const src = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'pages', 'clients.js'), 'utf8');
    vm.runInContext(src + '\n;window.__C = ClientsPage;', ctx);
    const C = window.__C;
    C.reload = async () => {};

    C.importModal();
    const doc = window.document;
    doc.getElementById('imp-csv').value =
      'Full name,Mobile,Mail\nAnn Lee,+15551,ann@x.dev\nBob Ray,+15552,bob@x.dev';
    C.parseCsv();

    check('reads the header row into columns', C._importCols.length === 3, JSON.stringify(C._importCols));
    check('reads the data rows', C._importRows.length === 2);
    check('guesses name from "Full name"',
      doc.querySelector('[data-col="Full name"]').value === 'name');
    check('guesses phone-ish columns are not mis-mapped',
      doc.querySelector('[data-col="Mobile"]').value === '',
      doc.querySelector('[data-col="Mobile"]').value);

    // user maps the remaining columns by hand
    doc.querySelector('[data-col="Mobile"]').value = 'phone';
    doc.querySelector('[data-col="Mail"]').value = 'email';

    await C.runImport(true);   // dry run first
    check('preview posts dry_run=true', posted && posted.body.dry_run === true);
    check('sends the column mapping',
      posted.body.mapping['Full name'] === 'name' && posted.body.mapping.Mobile === 'phone',
      JSON.stringify(posted.body.mapping));
    check('sends the parsed rows', posted.body.rows.length === 2);
    check('preview box reports the outcome',
      /2 to create/.test(doc.getElementById('imp-preview').innerHTML));

    await C.runImport(false);  // real import
    check('import posts dry_run=false', posted.body.dry_run === false);

    // guard: refuse to import without a name column
    posted = null;
    doc.querySelector('[data-col="Full name"]').value = '';
    await C.runImport(false);
    check('refuses to import with no name column mapped', posted === null);
  }

  console.log('\nClient merge (build 82):');
  {
    const { ctx, window } = ctxFor();
    let posted = null;
    ctx.post = async (url, body) => { posted = { url, body }; return { merged: 2 }; };
    const src = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'pages', 'clients.js'), 'utf8');
    vm.runInContext(src + '\n;window.__C = ClientsPage;', ctx);
    const C = window.__C;
    C.reload = async () => {};

    C.mergeModal('t1');
    window.document.getElementById('mg-src').value = 'a1, b2';
    await C.runMerge('t1');
    check('merge posts to /api/clients/merge', posted && posted.url === '/api/clients/merge');
    check('sends target and sources',
      posted.body.target_id === 't1' && posted.body.source_ids.length === 2,
      JSON.stringify(posted.body));

    posted = null;
    C.mergeModal('t1');
    window.document.getElementById('mg-src').value = '';
    await C.runMerge('t1');
    check('refuses an empty merge', posted === null);
  }

  console.log('\nRole access conditions (build 83):');
  {
    const { ctx, window } = ctxFor({ PERMISSION_DEFS: [['can_view_orders', 'View orders']] });
    let put_ = null;
    ctx.put = async (url, body) => { put_ = { url, body }; return {}; };
    const src = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'pages', 'staff.js'), 'utf8');
    vm.runInContext(src + '\n;window.__S = StaffPage;', ctx);
    const S = window.__S;
    S.reload = async () => {};
    S._roles = [{ id: 'r1', name: 'Tech', permissions: {}, contact_scope: 'own',
                  order_scope: 'all', payroll_visibility_days: 30 }];

    S.roleModal('r1');
    const doc = window.document;
    check('role modal has a contact scope select', !!doc.getElementById('rl-contact'));
    check('role modal has an order scope select', !!doc.getElementById('rl-order'));
    check('role modal has payroll visibility', !!doc.getElementById('rl-payvis'));
    check('reflects the saved contact scope', doc.getElementById('rl-contact').value === 'own');
    check('reflects the saved payroll window', doc.getElementById('rl-payvis').value === '30');

    doc.getElementById('rl-order').value = 'own';
    doc.getElementById('rl-payvis').value = '';
    await S.submitRole('r1');
    check('saves the scopes', put_ && put_.body.order_scope === 'own'
      && put_.body.contact_scope === 'own', put_ && JSON.stringify(put_.body));
    check('blank payroll window means no limit (null)',
      put_.body.payroll_visibility_days === null, JSON.stringify(put_.body));
  }

  const ok = results.every(r => r.ok);
  console.log(ok ? '\nALL PASS ✅' : '\nFAILURES ❌');
  process.exit(ok ? 0 : 1);
})();
