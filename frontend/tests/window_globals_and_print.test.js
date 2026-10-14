/* Build 144am — regressions:
   1) page objects are top-level `const`, so `window.XxxPage` is always undefined;
      guards must use bare refs / typeof, or Add-client & links silently die.
   2) authed print/export endpoints must be fetched with the bearer token (openPrintPdf),
      not raw window.open (which 401s → blank tab), and use the real /api/print prefix.

   Run:  NODE_PATH=/tmp/b144test/node_modules node window_globals_and_print.test.js  */
const fs = require('fs');
const path = require('path');

const results = [];
function check(name, cond, detail) {
  results.push({ name, ok: !!cond });
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${name}${cond ? '' : '  ↳ ' + (detail || '')}`);
}
const R = (p) => fs.readFileSync(path.join(__dirname, '..', 'static/js', p), 'utf8');

const sales = R('pages/sales.js');
const feedback = R('pages/feedback.js');
const suppliers = R('pages/suppliers.js');
const core = R('core.js');
const files = ['pages/inventory.js', 'pages/staff.js', 'pages/transfers.js', 'pages/stocktake.js',
  'pages/payrollcalc.js', 'pages/clients.js', 'pages/orders.js', 'pages/sales.js',
  'pages/suppliers.js', 'pages/feedback.js', 'command-palette.js'];

// 1) no broken window.<constGlobal> guards remain
const badWindow = /window\.(ClientsPage|InventoryPage|SalesPage|OrdersPage|Scheduler|ClientPicker|TimeClock|App)\b/;
let offenders = files.filter(f => badWindow.test(R(f)));
check('No window.<pageGlobal> guards remain', offenders.length === 0, offenders.join(', '));

check('Sales Add-client uses typeof ClientsPage guard', /typeof ClientsPage === 'undefined'/.test(sales));
check('Sales Add-client opens the full form', /ClientsPage\._clientForm\(null,\s*{/.test(sales));
check('Suppliers incoming row opens PO (bare ref)', /onclick="InventoryPage\.openPo/.test(suppliers));
check('Clock toggle refresh uses typeof App / TimeClock', /typeof App !== 'undefined'/.test(feedback) && /typeof TimeClock !== 'undefined'/.test(feedback));

// 2) authed print helper + no raw window.open to authed endpoints
check('openPrintPdf helper defined (fetch + bearer)', /function openPrintPdf/.test(core) && /Authorization: `Bearer/.test(core));
const rawPrint = /window\.open\((['`])\/api\/(print|clients\/export|stocktake\/[^'`]*export)/;
offenders = files.filter(f => rawPrint.test(R(f)));
check('No raw window.open to authed print/export endpoints', offenders.length === 0, offenders.join(', '));
check('PO print uses /api/print prefix (not /api/printing)', !/\/api\/printing\//.test(R('pages/inventory.js')));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
if (failed.length) process.exit(1);
