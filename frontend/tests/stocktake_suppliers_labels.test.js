/* Builds 91-95 — stocktake scanning & filters, transfer verification, supplier
   price-list import, and label templates.

   Run:  NODE_PATH=../../node_modules node stocktake_suppliers_labels.test.js         */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { JSDOM } = require('jsdom');

const results = [];
function check(name, cond, detail) {
  results.push({ name, ok: !!cond });
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${name}${cond ? '' : '  ↳ ' + (detail || '')}`);
}

function ctxFor(extra = {}) {
  const dom = new JSDOM('<!DOCTYPE html><body><div id="root"></div><div id="page"></div></body>',
    { runScripts: 'outside-only' });
  const { window } = dom;
  // jsdom has no layout engine, so scrollIntoView doesn't exist there
  window.HTMLElement.prototype.scrollIntoView = function () {};
  const ctx = {
    window, document: window.document, console, setTimeout, clearTimeout, Date, JSON, URL,
    get: async () => ({ items: [] }), post: async () => ({}), put: async () => ({}),
    patch: async () => ({}), del: async () => ({}),
    Toast: { success() {}, error() {}, info() {} }, confirm2: async () => true,
    fmt: { currency: n => '$' + n, date: d => String(d).slice(0, 10), datetime: d => String(d) },
    setContent: (html) => { window.document.getElementById('page').innerHTML = html; },
    setTopbarActions: () => {},
    skeletonRows: () => '', emptyRow: () => '<tr><td>empty</td></tr>',
    badge: (s) => `<span>${s}</span>`, icon: () => '', avatarHtml: () => '',
    Auth: { token: 'tok' },
    Router: { register() {}, go() {}, current: 'x' },
    Modal: { open(html) { window.document.getElementById('root').innerHTML = html; }, close() {} },
    ...extra,
  };
  ctx.global = ctx;
  vm.createContext(ctx);
  return { ctx, window };
}

function load(ctx, window, file, name) {
  const src = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'pages', file), 'utf8');
  vm.runInContext(src + `\n;window.__P = ${name};`, ctx);
  return window.__P;
}

(async () => {
  console.log('Stocktake depth (build 91):');
  {
    const { ctx, window } = ctxFor();
    let posted = null;
    let lastUrl = '';
    ctx.get = async (url) => {
      lastUrl = url;
      if (url.startsWith('/api/staff/locations')) return { items: [{ id: 'L1', name: 'Main' }] };
      if (url.startsWith('/api/bins')) return { items: [] };
      if (url.startsWith('/api/settings/warehouses')) {
        return { items: [{ id: 'w1', name: 'Main warehouse', is_default: true }] };
      }
      if (url.includes('/settlement')) {
        return { lines: [{ part_name: 'Widget', system_qty: 5, counted_qty: 3, variance: -2,
                           value: -10, is_unknown: false, serial_mismatch: false }],
                 surplus_value: 0, shortage_value: -10, net_value: -10 };
      }
      if (url.includes('/lines')) {
        return { id: 'st1', status: 'open', kind: 'partial',
                 items: [{ id: 'l1', part_name: 'Widget', system_qty: 5, counted_qty: 3,
                           variance: -2, is_unknown: false, serial_mismatch: true,
                           note: 'filed elsewhere' },
                         { id: 'l2', part_name: 'Found thing', system_qty: 0, counted_qty: 2,
                           variance: 2, is_unknown: true, serial_mismatch: false }],
                 summary: { total: 2, counted: 2, uncounted: 0, surplus: 1, shortage: 1,
                            unknown: 1, serial_mismatch: 1 } };
      }
      return { items: [] };
    };
    ctx.post = async (url, body) => {
      posted = { url, body };
      if (url.includes('/scan')) {
        return { part_name: 'Widget', counted_qty: 4, variance: -1,
                 is_unknown: false, serial_mismatch: false };
      }
      if (url.includes('/import')) return { applied: 1, errors: [] };
      return { id: 'st1', lines: 0, kind: body && body.kind };
    };
    const P = load(ctx, window, 'stocktake.js', 'StocktakePage');
    const doc = window.document;

    await P.load();
    await P.open('st1');
    const page = doc.getElementById('page').innerHTML;
    check('scan bar is offered while the count is open', /stk-scan/.test(page));
    check('flags a product found on the shelf', /new/.test(page));
    check('flags a serial filed elsewhere', /serial\?/.test(page));
    check('shows difference filter chips with counts',
      /Surplus 1/.test(page) && /Shortage 1/.test(page));

    // scanning
    doc.getElementById('stk-scan').value = 'BC-1';
    doc.getElementById('stk-scan-qty').value = '2';
    doc.getElementById('stk-scan-unknown').checked = true;
    await P.scan('st1');
    check('scan posts the code, qty and register-unknown flag',
      posted.url === '/api/stocktake/st1/scan' && posted.body.code === 'BC-1'
      && posted.body.qty === 2 && posted.body.register_unknown === true,
      JSON.stringify(posted.body));

    // filters re-query the server
    await P.setDiff('surplus');
    check('difference filter is sent to the server', /diff=surplus/.test(lastUrl), lastUrl);

    // settlement act
    await P.settlement('st1');
    const modal = doc.getElementById('root').innerHTML;
    check('settlement act values the shortage',
      /-10\.00/.test(modal) && /Widget/.test(modal));

    // start modal offers full vs partial
    await P.startModal();
    const start = doc.getElementById('root').innerHTML;
    check('start dialog offers full and partial counts',
      /Full —/.test(start) && /Partial —/.test(start));
    doc.getElementById('stk-kind').value = 'partial';
    await P.start();
    check('partial count is requested by warehouse',
      posted.body.kind === 'partial' && posted.body.warehouse_id === 'w1',
      JSON.stringify(posted.body));

    // count-sheet import
    P.importModal('st1');
    doc.getElementById('stk-imp').value = 'code,counted_qty\nWS-1,8';
    await P.runImport('st1');
    check('import sends parsed rows as totals',
      posted.url === '/api/stocktake/st1/import'
      && posted.body.rows[0].code === 'WS-1' && posted.body.rows[0].counted_qty === '8',
      JSON.stringify(posted.body.rows));
  }

  console.log('\nTransfer verification (build 93):');
  {
    const { ctx, window } = ctxFor();
    let posted = null;
    ctx.get = async (url) => {
      if (/\/api\/transfers\/[^/]+$/.test(url)) {
        return { id: 't1', transfer_number: 'TR-1', status: 'in_transit',
                 lines: [{ id: 'ln1', part_name: 'Widget', qty: 10, qty_received: null }] };
      }
      return { items: [] };
    };
    ctx.post = async (url, body) => { posted = { url, body }; return { discrepancies: 1 }; };
    const P = load(ctx, window, 'transfers.js', 'TransfersPage');
    const doc = window.document;

    await P.verify('t1');
    const modal = doc.getElementById('root').innerHTML;
    check('verify dialog lists shipped quantities', /Widget/.test(modal) && /10/.test(modal));
    check('received input defaults to what was shipped',
      doc.querySelector('[data-line="ln1"]').value === '10');

    doc.querySelector('[data-line="ln1"]').value = '8';
    await P.saveVerify('t1');
    check('confirms the real arrival count',
      posted.url === '/api/transfers/t1/receive'
      && posted.body.lines[0].qty_received === 8,
      JSON.stringify(posted.body));
  }

  console.log('\nSuppliers & price list (build 94):');
  {
    const { ctx, window } = ctxFor();
    let posted = null;
    ctx.get = async (url) => {
      if (url.includes('incoming')) {
        return { items: [
          { po_id: 'p1', po_number: 'PO-1', line_id: 'l1', qty_incoming: 2,
            expected_at: '2026-01-01', delayed: true, reserved_for: 'WO-9' },
          { po_id: 'p2', po_number: 'PO-2', line_id: 'l2', qty_incoming: 1,
            expected_at: '2026-12-01', delayed: false, reserved_for: null },
        ] };
      }
      return { items: [{ id: 's1', name: 'Acme', contact_name: 'Bo', email: 'b@a.dev',
                         phone: null, lead_days: 3, balance: 25, currency: '$' }] };
    };
    ctx.post = async (url, body) => {
      posted = { url, body };
      return { updated: 1, created: 0, skipped: 0, errors: [] };
    };
    const P = load(ctx, window, 'suppliers.js', 'SuppliersPage');
    const doc = window.document;

    await P.load();
    let page = doc.getElementById('page').innerHTML;
    check('lists suppliers with what we owe', /Acme/.test(page) && /we owe/.test(page));

    await P.go('incoming');
    page = doc.getElementById('page').innerHTML;
    check('incoming stock flags an overdue PO', /overdue/.test(page));
    check('incoming stock shows what is already reserved', /WO-9/.test(page));
    check('and what is not', /unallocated/.test(page));

    // price list: parse → map → import
    P.priceList('s1');
    doc.getElementById('pl-csv').value = 'Code,Cost\nWS-1,3.50';
    P.parsePL();
    check('guesses the column mapping',
      doc.querySelector('[data-plc="Code"]').value === 'sku'
      && doc.querySelector('[data-plc="Cost"]').value === 'cost_price');

    await P.runPL('s1', true);
    check('preview runs as a dry run first',
      posted.body.dry_run === true && posted.body.match_on === 'sku',
      JSON.stringify(posted.body).slice(0, 90));
    check('preview reports what would change',
      /1 to update/.test(doc.getElementById('pl-result').innerHTML));

    await P.runPL('s1', false);
    check('import sends the parsed rows for real',
      posted.body.dry_run === false && posted.body.rows[0].Code === 'WS-1',
      JSON.stringify(posted.body.rows));

    // refuse a mapping with nothing to match on
    let errored = '';
    ctx.Toast.error = (m) => { errored = m; };
    doc.querySelector('[data-plc="Code"]').value = '';
    await P.runPL('s1', true);
    check('refuses a mapping with no match key', /sku/.test(errored), errored);
  }

  console.log('\nUnified label printing (build 144v):');
  {
    // Labels are now DocumentTemplates; LabelPrint pulls them from the document
    // system and renders via /api/documents/render-labels (no /api/print/*).
    const { ctx, window } = ctxFor();
    const calls = [];
    let posted = null;
    ctx.get = async (url) => {
      calls.push(url);
      return { items: [
        { id: 'est', name: 'Estimate', doc_type: 'estimate', is_default: false },
        { id: 't1', name: 'Shelf tag', doc_type: 'price_tag', is_default: true },
        { id: 't2', name: 'Barcode label', doc_type: 'label', is_default: false },
      ] };
    };
    ctx.post = async (url, body) => { posted = { url, body }; return { html: '<div>x</div>', name: 'Shelf tag', count: 6 }; };
    ctx.window.open = () => ({ document: { write() {}, close() {} }, focus() {}, print() {} });
    const P = load(ctx, window, 'labels.js', 'LabelPrint');
    const doc = window.document;

    await P.open(['p1', 'p2']);
    const root = doc.getElementById('root').innerHTML;
    check('label dialog reads templates from the document system',
      calls.some(u => u.includes('/api/documents/templates')) &&
      !calls.some(u => u.includes('/api/print/templates')));
    check('only label/price-tag templates are offered (not the estimate)',
      /Shelf tag/.test(root) && /Barcode label/.test(root) && !/Estimate/.test(root));
    check('print dialog names the chosen products', /2 product\(s\)/.test(root));

    doc.getElementById('lp-tpl').value = 't1';
    doc.getElementById('lp-copies').value = '3';
    await P.go(['p1', 'p2']);
    check('renders via /api/documents/render-labels with parts + copies',
      posted.url === '/api/documents/render-labels' && posted.body.copies === 3 &&
      posted.body.part_ids.length === 2 && posted.body.template_id === 't1',
      JSON.stringify(posted && posted.body));
  }

  const ok = results.every(r => r.ok);
  console.log(ok ? '\nALL PASS ✅' : '\nFAILURES ❌');
  process.exit(ok ? 0 : 1);
})();
