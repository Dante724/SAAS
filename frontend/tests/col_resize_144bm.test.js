/* Build 144bm — drag-to-resize columns app-wide + gear icon top-right.
   Run: node col_resize_144bm.test.js */
const fs = require('fs');
const path = require('path');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const R = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');
const ct = R('static/js/col-tools.js');
const orders = R('static/js/pages/orders.js');
const clients = R('static/js/pages/clients.js');
const products = R('static/js/pages/products.js');
const assets = R('static/js/pages/assets.js');
const css = R('static/css/design.css');
const idx = R('index.html');

check('ColTools resizable + persists widths', /window\.ColTools/.test(ct) && /resizable\(tableId, key\)/.test(ct) && /localStorage\.setItem/.test(ct));
check('Drag handle + pointer resize', /col-rz/.test(ct) && /pointerdown/.test(ct) && /pointermove/.test(ct) && /col-resize/.test(css));
check('Registered in index', /col-tools\.js/.test(idx));
check('Resize wired to all 4 lists',
  /ColTools\.resizable\('wo-list-table', 'workflows'\)/.test(orders) &&
  /ColTools\.resizable\('cl-list-table', 'contacts'\)/.test(clients) &&
  /ColTools\.resizable\('pr-list-table', 'products'\)/.test(products) &&
  /ColTools\.resizable\('a-list-table', 'assets'\)/.test(assets));
check('Tables carry ids', /id="cl-list-table"/.test(clients) && /id="pr-list-table"/.test(products) && /id="a-list-table"/.test(assets));
check('Workflows gear is icon-only, top-right', /ic-btn col-gear/.test(orders) && /toggleColMenu/.test(orders) && !/OrdersPage.toggleColumns/.test(orders));
check('Build bumped', /build 144(b[m-z]|[c-z][a-z])/.test(idx));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
