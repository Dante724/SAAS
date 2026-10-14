/* Build 144bl — commission on approved recs. Run: node rec_commission_144bl.test.js */
const fs = require('fs');
const path = require('path');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const R = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');
const rec = R('../backend/routers/recommendations.py');
const orders = R('static/js/pages/orders.js');
const idx = R('index.html');

check('Approved line stamped with recommender', /technician_id=rec\.author_id/.test(rec));
check('Rec card shows the recommender', /r\.author_name/.test(orders) && /rec-src/.test(orders));
check('Build bumped', /build 144(b[l-z]|[c-z][a-z])/.test(idx));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
