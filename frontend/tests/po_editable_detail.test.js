/* Build 144al — editable Purchase Order detail (2 tabs, charges, clickable everywhere).

   Run:  NODE_PATH=/tmp/b144test/node_modules node po_editable_detail.test.js   */
const fs = require('fs');
const path = require('path');

const results = [];
function check(name, cond, detail) {
  results.push({ name, ok: !!cond });
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${name}${cond ? '' : '  ↳ ' + (detail || '')}`);
}

const root = path.join(__dirname, '..');
const inv = fs.readFileSync(path.join(root, 'static/js/pages/inventory.js'), 'utf8');
const sup = fs.readFileSync(path.join(root, 'static/js/pages/suppliers.js'), 'utf8');

// clickable rows open the detail
check('PO tab rows open detail', /onclick="InventoryPage\.openPo\('\$\{po\.id\}'\)"/.test(inv));
check('Suppliers incoming rows open the PO', /InventoryPage\.openPo\('\$\{i\.po_id\}'\)/.test(sup));
check('Product PO list rows open the PO', /onclick="InventoryPage\.openPo\('\$\{po\.id\}'\)"/.test(inv));

// two tabs
check('openPo defined', /async openPo\(id\)/.test(inv));
check('General tab present', /poTab\(event,'general'\)/.test(inv) && /Supplier invoice \/ bill no/.test(inv));
check('Products tab present', /poTab\(event,'products'\)/.test(inv) && /Products to order/.test(inv));

// editable lines + charges + totals
check('Add product to order', /poAddLine\(\)/.test(inv));
check('Charges add/remove', /poAddCharge\(\)/.test(inv) && /poRmCharge\(/.test(inv));
check('Landed-cost toggle present', /po-tocost/.test(inv) && /landed cost/.test(inv));
check('Live totals recompute', /po-totals/.test(inv) && /Subtotal:/.test(inv));
check('Customizable charge types', /poManageChargeTypes/.test(inv) && /po-charge-types/.test(inv));

// persistence + receive
check('Save PATCHes the PO', /patch\(`\/api\/inventory\/po\/\$\{id\}`/.test(inv));
check('Receive from detail saves first then receives', /_savePoQuiet\(id\)[\s\S]*\/receive`/.test(inv));
check('Received PO is read-only', /received — locked/.test(inv));
check('Print uses authed helper + correct /api/print path',
  /openPrintPdf\('\/api\/print\/purchase-order\//.test(inv));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
if (failed.length) process.exit(1);
