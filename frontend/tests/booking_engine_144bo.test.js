/* Build 144bo — booking availability engine (FE settings surface + policy).
   Run: node booking_engine_144bo.test.js */
const fs = require('fs');
const path = require('path');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const R = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');
const pp = R('static/js/pages/publicpages.js');
const idx = R('index.html');

check('Lead-time control present', /id="pp-lead"/.test(pp) && /lead time/i.test(pp));
check('Booking-window control present', /id="pp-window"/.test(pp) && /Book up to/.test(pp));
check('Double-booking label clarified', /double-booking/i.test(pp));
check('Working-hours hint points to Locations', /working hours \(Settings\s*→\s*Locations\)/.test(pp));
check('Save posts lead + window', /booking_lead_min:\s*parseInt/.test(pp) && /booking_window_days:\s*parseInt/.test(pp));
check('Build bumped', /build 144(b[o-z]|[c-z][a-z])/.test(idx));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
