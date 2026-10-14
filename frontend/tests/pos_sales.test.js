/* Build 144an — Quick POS Sales page.  Run: NODE_PATH=/tmp/b144test/node_modules node pos_sales.test.js */
const fs = require('fs');
const path = require('path');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const sales = fs.readFileSync(path.join(__dirname, '..', 'static/js/pages/sales.js'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '..', 'static/css/design.css'), 'utf8');

check('Three-zone POS layout', /class="pos"/.test(sales) && /\.pos\{[^}]*grid-template-columns/.test(css));
check('Category rail with counts', /renderRail/.test(sales) && /pos-cat/.test(css));
check('Product + service grid', /renderGrid/.test(sales) && /pos-tile/.test(css));
check('Search matches name/SKU/barcode/code', /\[p\.name,\s*p\.sku,\s*p\.barcode,\s*p\.code\]/.test(sales));
check('Scan resolves via /api/inventory/scan', /\/api\/inventory\/scan\?code=/.test(sales));
check('Enter in search triggers scan', /onkeydown="if\(event\.key==='Enter'\)SalesPage\.scan/.test(sales));
check('Keypad quantity toggle', /setView\('keypad'\)/.test(sales) && /pos-keypad/.test(css));
check('Live cart with steppers', /pos-step/.test(css) && /SalesPage\.step\(/.test(sales));
check('Client attach (pick + full form)', /pickClient/.test(sales) && /ClientsPage\._clientForm\(null,\s*{\s*stack:\s*true/.test(sales));
check('Order discount + hold + clear', /setDiscount/.test(sales) && /hold\(\)/.test(sales) && /clearCart/.test(sales));
check('Tender account + quick-cash change', /pos-acct/.test(sales) && /pickCash/.test(sales));
check('Take payment posts to /api/sales', /post\('\/api\/sales'/.test(sales) && /client_id:\s*this\._client\?\.id/.test(sales));
check('No emoji anywhere in POS', !/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(sales));
check('Recent sales still reachable', /openRecent/.test(sales));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
if (failed.length) process.exit(1);
