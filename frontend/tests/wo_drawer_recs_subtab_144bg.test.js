/* Build 144bg — WO as right drawer, recs under Services&Products, catalog-or-manual add.
   Run: node wo_drawer_recs_subtab_144bg.test.js */
const fs = require('fs');
const path = require('path');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const R = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');
const orders = R('static/js/pages/orders.js');
const css = R('static/css/design.css');
const idx = R('index.html');

// drawer: WO detail + create both open as right-side slide-over
check('WO detail + create open as right drawer', (orders.match(/'modal-side-wide'/g) || []).length >= 2);
check('Drawer CSS exists (modal-side-wide)', /\.modal-side-wide/.test(css));

// recs demoted: no top-level Recommendations tab, block lives inside the lines panel
check('No top-level Recommendations tab', !/woTab\(event,'recs'\)/.test(orders));
check('Recs block inside Services&Products panel', /wo-recs-sec/.test(orders) && /data-panel="lines"[\s\S]*wo-recs-\$\{id\}/.test(orders));
check('Unread dot moved to Services&Products tab', /Services &amp; Products\$\{o\.rec_unread/.test(orders));
check('Dot cleared when opening lines tab', /panel === 'lines' && this\._recOrderId/.test(orders));

// catalog-or-manual add
check('Catalog search in rec modal', /rec-search/.test(orders) && /\/api\/catalog\/search/.test(orders));
check('Filter chips product/service/all', /_recFilter\('product'/.test(orders) && /_recFilter\('service'/.test(orders));
check('Choosing an item links ref_id', /_recChoose/.test(orders) && /this\._recRefId = it\.ref_id/.test(orders));
check('ref_id sent only on add', /if \(!recId && this\._recRefId\) body\.ref_id/.test(orders));
check('Linked vs manual shown on card', /rec-src/.test(orders) && /linked to catalog/.test(orders));

// styling + build + hygiene
check('Pick UI styled + build bumped', /\.rec-chip/.test(css) && /\.rec-result/.test(css) && /build 144(b[g-z]|[c-z][a-z])/.test(idx));
check('No emoji in JS', !/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(orders));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
