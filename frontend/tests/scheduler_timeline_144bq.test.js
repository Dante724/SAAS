/* Build 144bq — internal scheduler timeline + drag reschedule.
   Run: node scheduler_timeline_144bq.test.js */
const fs = require('fs');
const path = require('path');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const R = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');
const bk = R('static/js/pages/bookings.js');
const css = R('static/css/design.css');
const idx = R('index.html');

check('List/Timeline segmented toggle', /setView\('timeline'\)/.test(bk) && /setView\('list'\)/.test(bk) && /class="seg"/.test(bk));
check('Timeline renders tech lanes + hour head', /renderTimeline/.test(bk) && /bkt-lane/.test(bk) && /bkt-head/.test(bk) && /bkt-rowname/.test(bk));
check('Blocks positioned by time', /_block\(b\)/.test(bk) && /left:\$\{left\}px/.test(bk) && /COL_W/.test(bk));
check('Pointer drag wired', /_wireDrag/.test(bk) && /pointerdown/.test(bk) && /setPointerCapture/.test(bk));
check('Drag computes new time + tech', /scheduled_for: `\$\{self\._date\}T/.test(bk) && /body\.employee_id = emp/.test(bk) && /SNAP_MIN/.test(bk));
check('Click (no drag) opens action menu', /if \(!moved\)/.test(bk) && /blockMenu\(/.test(bk));
check('Arrival → work order kept', /toOrder\(/.test(bk) && /to-order/.test(bk));
check('Namespaced CSS, no clash with sch-', /\.bkt-block/.test(css) && /\.bkt-lane/.test(css) && /\.seg-btn/.test(css));
check('No emoji in bookings.js', !/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(bk));
check('Build bumped', /build 144(b[q-z]|[c-z][a-z])/.test(idx));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
