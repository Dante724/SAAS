/* Build 144bs + 144bt — booking confirmations wired end-to-end; booking detail
   drawer with inline reschedule. Run: node booking_notify_drawer_144bs.test.js */
const fs = require('fs');
const path = require('path');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const R = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');
const bk = R('static/js/pages/bookings.js');
const pub = R('../backend/routers/public.py');
const bkr = R('../backend/routers/bookings.py');
const nr = R('../backend/services/notification_rules.py');
const idx = R('index.html');

// bs — the evaluate_booking loop is actually invoked from every write path
check('Public create fires confirmation', /evaluate_booking\(db, org_id=org\.id, booking_id=b\.id, event="created"\)/.test(pub));
check('Internal create fires confirmation', /event="created"/.test(bkr));
check('Reschedule fires changed', /event="changed"/.test(bkr));
check('Status change fires status_changed', /event="status_changed"/.test(bkr));
check('Message body is event-aware', /Appointment updated/.test(nr) && /Appointment cancelled/.test(nr));

// bt — booking detail drawer
check('Block opens a right drawer', /modal-side-wide/.test(bk));
check('Drawer has inline reschedule fields', /id="bd-date"/.test(bk) && /id="bd-time"/.test(bk) && /id="bd-emp"/.test(bk));
check('saveReschedule PATCHes booking', /saveReschedule\(id\)/.test(bk) && /scheduled_for: `\$\{d\}T\$\{t\}:00`/.test(bk));
check('Drawer surfaces WO conversion + notify hint', /→ Work order/.test(bk) && /Settings\s*→\s*Notifications/.test(bk));
check('Build bumped', /build 144(b[s-z]|[c-z][a-z])/.test(idx));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
