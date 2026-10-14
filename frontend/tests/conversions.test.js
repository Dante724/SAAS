/* Build 77 — the conversion modals.

   Product → Asset prefills from the stock unit and can hand ownership to a client.
   Asset → Product surfaces possible duplicates so the user links instead of
   duplicating, and never auto-fills the SKU from the serial.

   Run:  NODE_PATH=../../node_modules node conversions.test.js                    */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { JSDOM } = require('jsdom');

const results = [];
function check(name, cond, detail) {
  results.push({ name, ok: !!cond });
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${name}${cond ? '' : '  ↳ ' + (detail || '')}`);
}

function ctxFor() {
  const dom = new JSDOM('<!DOCTYPE html><body><div id="root"></div><div id="cv-list"></div></body>',
    { runScripts: 'outside-only' });
  const { window } = dom;
  const ctx = {
    window, document: window.document, console, setTimeout, clearTimeout,
    get: async () => ({}), post: async () => ({}), put: async () => ({}), del: async () => ({}),
    Toast: { success() {}, error() {}, info() {} }, confirm2: async () => true,
    fmt: { currency: n => '$' + n }, setContent: () => {}, setTopbarActions: () => {},
    Router: { register() {}, go() {}, current: 'conversions' },
    Modal: { open(html) { window.document.getElementById('root').innerHTML = html; }, close() {} },
  };
  ctx.global = ctx;
  vm.createContext(ctx);
  return { ctx, window };
}

function loadPage(ctx, window) {
  const src = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'pages', 'conversions.js'), 'utf8');
  vm.runInContext(src + '\n;window.__C = ConversionsPage;', ctx);
  return window.__C;
}

(async () => {
  console.log('Product → Asset modal:');
  {
    const { ctx, window } = ctxFor();
    ctx.get = async () => ({
      serialized_item_id: 'u1', part_id: 'p1', product_name: 'Laptop X',
      owner_type: 'company', brand: 'Acme', model: 'X1', serial: 'SN-1', condition: 'used',
    });
    let posted = null;
    ctx.post = async (url, body) => { posted = { url, body }; return { ok: true }; };
    const C = loadPage(ctx, window);

    await C.productToAsset('u1');
    const doc = window.document;
    check('prefills brand from the unit', doc.getElementById('pa-brand').value === 'Acme');
    check('prefills model', doc.getElementById('pa-model').value === 'X1');
    check('shows the serial', /SN-1/.test(doc.getElementById('root').innerHTML));
    check('client picker hidden for company owner',
      doc.getElementById('pa-client-wrap').style.display === 'none');

    // switching owner to client reveals the client field
    doc.getElementById('pa-owner').value = 'client';
    C._ownerToggle();
    check('choosing client owner reveals client field',
      doc.getElementById('pa-client-wrap').style.display !== 'none');

    doc.getElementById('pa-client').value = 'c9';
    await C._submitPA('u1');
    check('posts to product-to-asset',
      posted && posted.url === '/api/conversions/product-to-asset', posted && posted.url);
    check('sends the unit id', posted.body.serialized_item_id === 'u1');
    check('sends owner_type + client_id',
      posted.body.owner_type === 'client' && posted.body.client_id === 'c9',
      JSON.stringify(posted.body));
  }

  console.log('\nAsset → Product modal:');
  {
    const { ctx, window } = ctxFor();
    ctx.get = async () => ({
      device_id: 'd1', product_name: 'Sony A7', brand: 'Sony', model: 'A7',
      serial_identifier: 'AS-1', suggested_sku: null, cost_price: 300, sell_price: 500,
      possible_duplicates: [{ id: 'p7', name: 'Sony A7', sku: 'SA7' }],
    });
    let posted = null;
    ctx.post = async (url, body) => { posted = { url, body }; return { ok: true }; };
    const C = loadPage(ctx, window);

    await C.assetToProduct('d1');
    const doc = window.document;
    check('prefills the product name', doc.getElementById('ap-name').value === 'Sony A7');
    check('SKU is left blank (serial must not auto-become SKU)',
      doc.getElementById('ap-sku').value === '', doc.getElementById('ap-sku').value);
    check('surfaces possible duplicates', /Sony A7/.test(doc.getElementById('root').innerHTML)
      && !!doc.querySelector('input[name="ap-link"][value="p7"]'));
    check('offers an opt-in stock unit', !!doc.getElementById('ap-stock'));

    // default: create new (no link), no stock unit
    await C._submitAP('d1');
    check('posts to asset-to-product',
      posted && posted.url === '/api/conversions/asset-to-product', posted && posted.url);
    check('creates new product by default (part_id null)', posted.body.part_id === null);
    check('does not create a stock unit unless ticked', posted.body.create_stock_unit === false);

    // choosing the duplicate links it instead
    doc.querySelector('input[name="ap-link"][value="p7"]').checked = true;
    doc.getElementById('ap-stock').checked = true;
    await C._submitAP('d1');
    check('linking a duplicate sends its part_id', posted.body.part_id === 'p7');
    check('ticking the box requests a stock unit', posted.body.create_stock_unit === true);
  }

  console.log('\nConversion log:');
  {
    const { ctx, window } = ctxFor();
    ctx.get = async () => ({ items: [
      { id: 'c1', direction: 'product_to_asset', part_id: 'p1', device_id: 'd1',
        product_name: 'Laptop X', asset_name: 'Acme X1', serial_identifier: 'SN-1',
        owner_before: 'company', owner_after: 'company', employee_name: 'Ann',
        status: 'reversed', created_at: '2026-08-01T10:00:00' },
    ] });
    const C = loadPage(ctx, window);
    await C.reload();
    const html = window.document.getElementById('cv-list').innerHTML;
    check('log renders the direction', /Product → Asset/.test(html));
    check('log shows the identifier', /SN-1/.test(html));
    check('log marks reversed documents', /reversed/.test(html));
    check('log has no delete action (documents are immutable)', !/[Dd]elete/.test(html));
  }

  const ok = results.every(r => r.ok);
  console.log(ok ? '\nALL PASS ✅' : '\nFAILURES ❌');
  process.exit(ok ? 0 : 1);
})();
