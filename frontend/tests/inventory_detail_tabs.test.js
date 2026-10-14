/* Build 119 — inventory product & asset detail modals have lifecycle tabs.
   Run:  NODE_PATH=../../node_modules node inventory_detail_tabs.test.js  */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { JSDOM } = require('jsdom');

const results = [];
function check(name, cond, detail) {
  results.push({ ok: !!cond });
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${name}${cond ? '' : '  ↳ ' + (detail || '')}`);
}

function ctxFor(getImpl) {
  const dom = new JSDOM('<!DOCTYPE html><body><div id="content"></div><div id="root"></div></body>',
    { runScripts: 'outside-only', url: 'https://x.test' });
  const { window } = dom;
  window.HTMLElement.prototype.scrollIntoView = function () {};
  let modalHtml = '';
  const ctx = {
    window, document: window.document, console, setTimeout, Date, JSON, Math,
    get: getImpl, post: async () => ({}), patch: async () => ({}),
    Toast: { success() {}, error() {}, info() {} },
    icon: () => '', badge: (s) => s, emptyRow: () => '', skeletonRows: () => '',
    fmt: { currency: (n) => '₹' + n, date: () => 'd', datetime: () => 'dt', reltime: () => 'r' },
    setContent: () => {}, setTopbarActions: () => {},
    Modal: { open(h) { modalHtml = h; window.document.getElementById('root').innerHTML = h; }, close() {} },
    Router: { register() {}, go() {} }, LabelPrint: { open() {} },
    getModal: () => modalHtml,
  };
  ctx.global = ctx;
  vm.createContext(ctx);
  return { ctx, window };
}

function load(ctx, window) {
  const src = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'pages', 'inventory.js'), 'utf8');
  vm.runInContext(src + '\n;window.__I = InventoryPage;', ctx);
  return window.__I;
}

(async () => {
  console.log('Product detail tabs:');
  {
    const { ctx, window } = ctxFor(async (u) => {
      if (u.endsWith('/stock')) return { total_available: 3, items: [{ location: 'Main', available: 3, reserved: 0 }] };
      if (u.includes('/movements')) return [{ type: 'receive', qty: 3, at: '2026-07-01', ref_type: 'posting', location: 'Main', unit_cost: 5 }];
      if (u.includes('/purchase-orders')) return { items: [] };
      return { id: 'p1', name: 'Screen', sku: 'S-1', brand: 'Apple', category: 'Screens',
               cost_price: 100, sell_price: 180, margin_pct: 44, qty_available: 3, min_stock: 5, low_stock: true };
    });
    const I = load(ctx, window);
    await I.viewPart('p1');
    const html = window.document.getElementById('root').innerHTML;
    check('product modal has General/Stock/History/Purchase-order tabs',
      /General/.test(html) && /Stock/.test(html) && /History/.test(html) && /Purchase orders/.test(html));
    // switch to History tab
    const histBtn = [...window.document.querySelectorAll('.wo-tab')].find(b => /History/.test(b.textContent));
    I.partTab({ target: histBtn }, 'history');
    await I._loadIpHistory();
    check('history tab loads movements with location', /Main/.test(window.document.getElementById('ip-history').innerHTML));
  }

  console.log('\nAsset detail tabs:');
  {
    const { ctx, window } = ctxFor(async (u) => {
      if (u.includes('/movements')) return { items: [
        { event_type:'create', from_zone:null, to_zone:'Client', note:'Asset created', by:'Admin', at:'2026-06-01' } ] };
      return {
        id: 'a1', brand: 'Apple', model: 'iPad Air 5', asset_type: 'Device', owner_type: 'client',
        owner_name: 'Kavya', serial: 'SN1', imei: null, asset_status: 'in_use', condition: 'Good',
        created_at: '2026-06-01', converted_to_part_id: null,
        history: [{ id: 'o1', order_number: 'WO-1', status: 'ready', total: 500, created_at: '2026-06-05' }],
      };
    });
    const I = load(ctx, window);
    await I.viewAsset('a1');
    const html = window.document.getElementById('root').innerHTML;
    check('asset modal has General/Work-orders/History tabs',
      /General/.test(html) && /Work orders/.test(html) && /History/.test(html));
    check('work-orders tab lists the linked order', /WO-1/.test(html));
    check('asset modal has Transfer and Write off actions',
      /Transfer</.test(html) && /Write off</.test(html));
    const histBtn = [...window.document.querySelectorAll('.wo-tab')].find(b => /^History/.test(b.textContent.trim()));
    I.assetTab({ target: histBtn }, 'history');
    await I._loadAssetHistory();
    check('history tab shows lifecycle events', /Created/.test(window.document.getElementById('ia-history').innerHTML));
  }

  const ok = results.every(r => r.ok);
  console.log(ok ? '\nALL PASS ✅' : '\nFAILURES ❌');
  process.exit(ok ? 0 : 1);
})();
