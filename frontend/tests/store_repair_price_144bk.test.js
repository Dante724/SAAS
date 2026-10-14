/* Build 144bk — store vs repair price. Run: node store_repair_price_144bk.test.js */
const fs = require('fs');
const path = require('path');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const R = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');
const products = R('static/js/pages/products.js');
const picker = R('static/js/catalog-picker.js');
const orders = R('static/js/pages/orders.js');
const idx = R('index.html');

check('Product form has repair price field', /id="p-repair"/.test(products) && /Repair price/.test(products));
check('Repair price sent on save', /repair_price: parseFloat\(V\('p-repair'\)\)/.test(products));
check('Products list shows repair price', /repair \$|repair \${fmt\.currency\(p\.repair_price\)/.test(products) || /p\.repair_price !== p\.sell_price/.test(products));
check('Picker uses repair price when flagged', /repairPricing/.test(picker) && /it\.repair_price/.test(picker) && /it\.kind === 'product'/.test(picker));
check('WO contexts turn repair pricing on', (orders.match(/CatalogPicker\.repairPricing = true/g) || []).length >= 2);
check('Build bumped', /build 144(b[k-z]|[c-z][a-z])/.test(idx));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
