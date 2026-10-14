/* Build 144bj — prior-visits badge, WO tech notes/conclusion, configurable columns.
   Run: node wo_notes_visits_columns_144bj.test.js */
const fs = require('fs');
const path = require('path');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const R = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');
const orders = R('static/js/pages/orders.js');
const css = R('static/css/design.css');
const idx = R('index.html');

// P1 prior visits
check('Prior-visits badge on device', /wo-visits/.test(orders) && /visit_count/.test(orders) && /prior visit/.test(orders));

// P2 tech notes + conclusion
check('Notes textareas on S&P', /wo-tech-notes/.test(orders) && /wo-conclusion/.test(orders));
check('saveNotes hits /notes', /saveNotes\(id\)/.test(orders) && /\/notes`/.test(orders));

// P3 configurable columns
check('Columns gear + menu', /toggleColMenu/.test(orders) && /wo-col-menu/.test(orders));
check('Column set persisted', /ros_wo_cols_hidden/.test(orders) && /_hiddenCols/.test(orders));
check('applyCols toggles hide classes', /_applyCols/.test(orders) && /hide-' \+ c/.test(orders));
check('Hide-column CSS present', /#wo-list-table\.hide-device \.col-device/.test(css));

// build + hygiene
check('Build bumped + no emoji', /build 144(b[j-z]|[c-z][a-z])/.test(idx) && !/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(orders));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
