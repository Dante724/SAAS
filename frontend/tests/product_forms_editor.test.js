/* Build 144as — Product custom fields (Forms editor + product form render) and
   the shared field renderer gaining datetime + address.
   Run: NODE_PATH=/tmp/b144test/node_modules node product_forms_editor.test.js */
const fs = require('fs');
const path = require('path');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const forms = fs.readFileSync(path.join(__dirname, '..', 'static/js/pages/forms.js'), 'utf8');
const products = fs.readFileSync(path.join(__dirname, '..', 'static/js/pages/products.js'), 'utf8');
const core = fs.readFileSync(path.join(__dirname, '..', 'static/js/core.js'), 'utf8');

check('Forms editor lists a Product fields entity', /entRow\('product'/.test(forms));
check('Field modal offers value list / checkbox / date+time / number / address', /'select','Value list'/.test(forms) && /'datetime','Date & time'/.test(forms) && /'address','Address'/.test(forms));
check('Product form fetches its custom fields', /CustomFields\.fetch\('product'\)/.test(products));
check('Product form renders + collects custom fields', /CustomFields\.render\(this\._productFields/.test(products) && /CustomFields\.collect\(this\._productFields/.test(products));
check('Product form keeps the built-in fields (default form intact)', /id="p-name"/.test(products) && /id="p-sku"/.test(products) && /id="p-cf-wrap"/.test(products));
check('Shared renderer supports datetime', /type="datetime-local"/.test(core));
check('Shared renderer supports address', /field_type === 'address'/.test(core));
// 144at — Required + lock-after-create per field
check('Field modal has a "disable editing after creating" toggle', /id="fl-lock"/.test(forms) && /lock_after_create/.test(forms));
check('Field row shows a locked tag', /f\.lock_after_create\?/.test(forms));
check('Renderer disables locked fields when editing', /opts && opts\.editing/.test(core) && /f\.lock_after_create/.test(core) && /\$\{dis\}/.test(core));
check('Renderer validates required fields', /validate\(fields, prefix\)/.test(core));
check('Product form validates required + marks editing', /CustomFields\.validate\(this\._productFields/.test(products) && /editing:\s*!!p/.test(products));
check('No emoji in forms/products pages', !/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(forms) && !/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(products));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
if (failed.length) process.exit(1);
