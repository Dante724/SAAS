/* Browser-free UI test (jsdom) for Phase 4 — the work-order create modal must
   render template-driven fields: the right asset groups and the right identifier
   label (IMEI for phones, VIN for cars, HIN for vessels), and template-driven
   button/title wording. Loads the REAL orders.js.

   Run:  NODE_PATH=../../node_modules node industry_modal.test.js
   (requires jsdom; in CI add it to devDependencies)                            */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { JSDOM } = require('jsdom');

// ── per-template backend fixtures (mirror the real registry) ────────────────
const TEMPLATES = {
  mobile_phone: {
    asset_label: 'Device', wo_button: '+ Ticket', wo_noun: 'ticket',
    identifier: { primary: 'imei', allowed: ['imei', 'serial', 'barcode'] },
    groups: [{ key: 'phone', label: 'Phone' }, { key: 'tablet', label: 'Tablet' }, { key: 'smartwatch', label: 'Smartwatch' }],
    rules: { '': 'imei', phone: 'imei', tablet: 'serial', smartwatch: 'serial' },
    expectLabel: 'IMEI', expectTitle: 'New ticket',
  },
  auto: {
    asset_label: 'Vehicle', wo_button: '+ Order', wo_noun: 'order',
    identifier: { primary: 'vin', allowed: ['vin'] },
    groups: [{ key: 'car', label: 'Car' }, { key: 'truck', label: 'Truck' }, { key: 'suv', label: 'SUV' }, { key: 'van', label: 'Van' }],
    rules: { '': 'vin' },
    expectLabel: 'VIN', expectTitle: 'New work order',
  },
  marine: {
    asset_label: 'Vessel', wo_button: '+ Order', wo_noun: 'order',
    identifier: { primary: 'hin', allowed: ['hin', 'vin', 'engine_serial'] },
    groups: [{ key: 'boat', label: 'Boat' }, { key: 'trailer', label: 'Trailer' }],
    rules: { '': 'hin', boat: 'hin', trailer: 'vin' },
    expectLabel: 'HIN', expectTitle: 'New work order',
  },
};

function idFor(tk, gkey) { return `${tk}:${gkey}`; }

function buildContext(tk) {
  const T = TEMPLATES[tk];
  const dom = new JSDOM(`<!DOCTYPE html><body><div id="content"></div><div id="topbar-actions"></div><div id="modal-root"></div></body>`,
    { runScripts: 'outside-only' });
  const { window } = dom;

  // stubbed API — route by URL, return the fixture for this template
  async function get(url) {
    if (url === '/api/settings/industry-template') return { current: tk, describe: descOf(tk) };
    if (url.startsWith('/api/settings/asset-groups')) return { items: T.groups.map(g => ({ id: idFor(tk, g.key), key: g.key, label: g.label })) };
    if (url.startsWith('/api/settings/asset-identifier-rules')) {
      const items = Object.entries(T.rules).map(([gk, kind]) => ({ asset_group_id: gk ? idFor(tk, gk) : null, primary_kind: kind }));
      return { items };
    }
    if (url.startsWith('/api/settings/asset-brands')) return { items: [{ id: 'b1', name: 'BrandA' }, { id: 'b2', name: 'BrandB' }] };
    if (url.startsWith('/api/settings/asset-models')) return { items: [{ id: 'm1', name: 'ModelA' }] };
    if (url === '/api/staff/employees') return [];
    if (url === '/api/forms/types') return { items: [] };
    if (url === '/api/resources') return { items: [] };
    if (url === '/api/auth/me') return { location_id: 'loc1' };
    return { items: [] };
  }
  function descOf(k) {
    const x = TEMPLATES[k];
    return { key: k, label: k, asset_label: x.asset_label, asset_label_plural: x.asset_label + 's',
      wo_button: x.wo_button, wo_title: 'Create', wo_noun: x.wo_noun,
      groups: x.groups, identifier: x.identifier, fields: [] };
  }

  // minimal globals orders.js touches
  const ctx = {
    window, document: window.document, console, setTimeout, clearTimeout, setInterval, clearInterval,
    get, post: async () => ({}), put: async () => ({}), del: async () => ({}),
    icon: () => '', Toast: { success() {}, error() {}, info() {}, show() {} },
    confirm2: async () => true, fmt: { currency: n => '' + n, date: d => d, datetime: d => d },
    setContent: () => {}, setTopbarActions: () => {},
    Router: { register() {} }, Scheduler: { load() {} },
    ClientPicker: { mount() {}, value: null }, CatalogPicker: { mount: async () => {}, payload: () => [] },
    Modal: { open(html) { window.document.getElementById('modal-root').innerHTML = html; }, close() {} },
    CustomFields: { fetch: async () => [], render: () => '', collect: () => ({}) },
  };
  ctx.global = ctx;
  vm.createContext(ctx);
  return { ctx, window };
}

async function testTemplate(tk) {
  const T = TEMPLATES[tk];
  const { ctx, window } = buildContext(tk);
  const src = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'pages', 'orders.js'), 'utf8');
  vm.runInContext(src + '\n;window.__OrdersPage = OrdersPage;', ctx);
  const OrdersPage = window.__OrdersPage;
  window.__tpl = null;                       // force fresh template load

  await OrdersPage.newModal();
  await new Promise(r => setTimeout(r, 20));  // let groupChanged() settle

  const doc = window.document;
  const title = doc.querySelector('.modal-title')?.textContent?.trim();
  const groupOpts = [...doc.querySelectorAll('#no-asset-group option')].map(o => o.textContent.trim());
  const identLabel = doc.getElementById('no-ident-label')?.textContent?.trim();
  const woBtn = OrdersPage._woBtn();

  const errs = [];
  const wantGroups = T.groups.map(g => g.label);
  if (JSON.stringify(groupOpts) !== JSON.stringify(wantGroups)) errs.push(`groups ${JSON.stringify(groupOpts)} != ${JSON.stringify(wantGroups)}`);
  if (identLabel !== T.expectLabel) errs.push(`ident label "${identLabel}" != "${T.expectLabel}"`);
  if (title !== T.expectTitle) errs.push(`title "${title}" != "${T.expectTitle}"`);
  if (woBtn !== T.wo_button) errs.push(`woBtn "${woBtn}" != "${T.wo_button}"`);

  const ok = errs.length === 0;
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${tk.padEnd(13)} groups=${groupOpts.join('/')}  ident=${identLabel}  btn=${woBtn}  title="${title}"`);
  errs.forEach(e => console.log(`         ↳ ${e}`));
  return ok;
}

(async () => {
  console.log('Create-modal template rendering:');
  let allOk = true;
  for (const tk of Object.keys(TEMPLATES)) allOk = (await testTemplate(tk)) && allOk;
  console.log(allOk ? '\nALL PASS ✅' : '\nFAILURES ❌');
  process.exit(allOk ? 0 : 1);
})();
