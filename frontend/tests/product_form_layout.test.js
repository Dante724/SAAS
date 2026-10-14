/* Build 144av — product form rebuilt to match the approved mockup exactly.
   Run: NODE_PATH=/tmp/b144test/node_modules node product_form_layout.test.js */
const fs = require('fs');
const path = require('path');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const p = fs.readFileSync(path.join(__dirname, '..', 'static/js/pages/products.js'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '..', 'static/css/design.css'), 'utf8');

check('Image drop-zone (drop or take a photo) + drag-drop', /pf-drop/.test(p) && /Drop image or <b>browse/.test(p) && /_dropImage\(/.test(p) && /\.pf-drop/.test(css));
check('Type + Category row with a "+ Category" button', /id="p-type"/.test(p) && /pf-catbtn/.test(p) && /addCategoryInline/.test(p));
check('Name + Description fields', /id="p-name"/.test(p) && /id="p-desc"/.test(p));
check('Unit / Code / SKU row (grouped unit label)', /pf-row3/.test(p) && /Quantity › pcs/.test(p) && /id="p-uom"/.test(p) && /id="p-code"/.test(p) && /id="p-sku"/.test(p));
check('Barcodes: + Barcode / Generate barcode / Generate QR', /toggleBarcode\(/.test(p) && /genCode\('code128'/.test(p) && /genCode\('qr'/.test(p));
check('Checkbox list matches mockup', /Set dimensions &amp; weight/.test(p) && /Warranty to client/.test(p) && /Expiration tracking/.test(p) && /Enable serial-number tracking/.test(p) && /Set default supplier/.test(p));
check('one-way tag on serial tracking', /pf-tag">one-way/.test(p));
check('Prices table (Cost / Sell)', /pf-prices/.test(p) && /id="p-cost"/.test(p) && /id="p-sell"/.test(p));
check('Submit sends description + default_supplier_id + dimensions', /description:\s*V\('p-desc'\)/.test(p) && /default_supplier_id:/.test(p) && /dimensions:/.test(p));
check('Brand + min stock preserved (hidden, no data loss)', /id="p-brand"/.test(p) && /id="p-min"/.test(p));
check('Serialized products keep the flag on save', /serEl \? serEl\.checked : true/.test(p));
check('No emoji in Products page', !/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(p));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
if (failed.length) process.exit(1);
