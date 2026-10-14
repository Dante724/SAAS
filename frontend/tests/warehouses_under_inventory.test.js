/* Build 144f — Warehouses merged into Settings → Inventory.
   - No standalone "Warehouses" rail item.
   - Inventory settings page embeds the full warehouse workspace (create +
     Product / Asset / Deleted tabs) where "Manage warehouses" used to be.
   Run: NODE_PATH=<dir with jsdom> node warehouses_under_inventory.test.js        */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { JSDOM } = require('jsdom');

const results = [];
const check = (name, cond, detail) => {
  results.push(!!cond);
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${name}${cond ? '' : '  ↳ ' + (detail || '')}`);
};
const jsDir = path.join(__dirname, '..', 'static', 'js', 'pages');
const read = (p) => fs.readFileSync(path.join(jsDir, p), 'utf8');

/* ── static checks ── */
console.log('\nSettings rail:');
const settingsSrc = read('settings.js');
check('no standalone "Warehouses" rail item',
  !/key:\s*'warehouses'/.test(settingsSrc) && !/label:\s*'Warehouses'/.test(settingsSrc));
check('Inventory rail item now covers warehouses',
  /Inventory & warehouses/.test(settingsSrc));
check('no leftover "Manage warehouses" button / go(\'warehouses\')',
  !/go\('warehouses'\)/.test(settingsSrc) && !/Manage warehouses/.test(settingsSrc));

/* ── behavioural: the inventory settings page embeds the warehouse workspace ── */
console.log('\nInventory settings embeds the warehouse workspace:');
const dom = new JSDOM('<!doctype html><html><body><div id="content"></div><div id="topbar-actions"></div></body></html>',
  { url: 'http://localhost', runScripts: 'outside-only' });
const w = dom.window;

const calls = [];
w.get = async (url) => {
  calls.push(url);
  if (url.startsWith('/api/staff/inventory-settings')) return {};
  if (url.startsWith('/api/staff/locations')) return { items: [{ id:'loc1', name:'Main' }] };
  if (url.startsWith('/api/settings/warehouses')) return {
    items: [{ id:'w1', name:'Main location warehouse', kind:'product', scope:'location',
              location_id:'loc1', is_default:true, allowed_actions:['post','sell'], balance:0, active:true }],
    actions: ['post','writeoff','transfer','sell','stocktake'],
    asset_label: 'Vehicles' };
  return { items: [] };
};
w.post = async () => ({}); w.put = async () => ({}); w.del = async () => ({});
w.icon = () => ''; w.confirm2 = async () => true;
w.Toast = { success(){}, error(m){ console.log('TOAST ERR:', m); }, info(){} };
w.Modal = { open(){}, close(){} };
w.setTopbarActions = (h) => { w.document.getElementById('topbar-actions').innerHTML = h || ''; };
w.setContent = (h) => { w.document.getElementById('content').innerHTML = h; };
w.SettingsPage = { go(){} };
w.Router = { register(){}, go(){}, current:'settings' };

const ctx = vm.createContext(w);
vm.runInContext(read('warehouses.js'), ctx, { filename:'warehouses.js' });
vm.runInContext(read('settings.js'), ctx, { filename:'settings.js' });
const InventorySettingsPage = vm.runInContext('InventorySettingsPage', ctx);
const WarehousesPage = vm.runInContext('WarehousesPage', ctx);

(async () => {
  let err = null;
  try { await InventorySettingsPage.load(); await new Promise(r=>setTimeout(r,40)); }
  catch (e) { err = e; }
  check('inventory settings page renders without error', !err, err && err.message);

  const content = w.document.getElementById('content').innerHTML;
  check('inventory settings form still present (Save button)',
    /Save inventory settings/.test(content));
  check('a Warehouses section is rendered', /Warehouses<\/div>|>Warehouses</.test(content));
  check('warehouse workspace is embedded (Product / Asset / Deleted tabs)',
    /Product warehouses/.test(content) && /Deleted/.test(content) &&
    /WarehousesPage\.go\('asset'\)/.test(content));
  check('a "+ New warehouse" create control is present inline',
    /\+ New warehouse/.test(content));
  check('existing warehouses are listed (Main location warehouse)',
    /Main location warehouse/.test(content));
  check('asset tab label follows the template (Vehicles)',
    /Vehicles warehouses/.test(content));
  check('warehouse data was fetched from /api/settings/warehouses',
    calls.some(u => u.startsWith('/api/settings/warehouses')));

  // switching to the Asset tab keeps everything inside the inventory page
  await WarehousesPage.go('asset');
  await new Promise(r=>setTimeout(r,20));
  const c2 = w.document.getElementById('content').innerHTML;
  check('switching warehouse sub-tab stays embedded (form still there)',
    /Save inventory settings/.test(c2) && /Product warehouses/.test(c2));

  const failed = results.filter(r => !r).length;
  console.log(failed ? `\nFAILURES ❌ (${failed})` : '\nALL PASS ✅');
  process.exit(failed ? 1 : 0);
})();
