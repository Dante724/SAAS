/* Build 144by — Services + Inventory Stock toolbar order.
   Run: node toolbar_polish_144by.test.js */
const fs = require('fs');
const path = require('path');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const R = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');
const sv = R('static/js/pages/services.js');
const inv = R('static/js/pages/inventory.js');

check('Services: no Taxes/Manage buttons in the corner', /setTopbarActions\('\'\)/.test(sv) || /setTopbarActions\(''\)/.test(sv));
check('Services: + Service is left of the search box',
  /pd-toolbar[\s\S]{0,200}newModal\(\)[^]{0,60}Service[\s\S]{0,120}search-box/.test(sv));
check('Services: flash-gear opens categories on the far right',
  /ic-btn[\s\S]{0,80}manageCategories/.test(sv) && /margin-left:auto/.test(sv));
check('Services: Taxes button removed', !/taxesModal\(\)">.{0,20}Taxes/.test(sv));
check('Inventory Stock: + Product is first, before Warehouse filter',
  /inv-filters[\s\S]{0,120}newModal\(\)">\+ Product<\/button>[\s\S]{0,120}Warehouse: All/.test(inv));
check('Inventory Stock: + Product no longer in the right actions cluster',
  !/inv-actions[\s\S]{0,200}newModal\(\)">\+ Product/.test(inv));
check('Build bumped', /build 144(b[y-z]|[c-z][a-z])/.test(R('index.html')));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
