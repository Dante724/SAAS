/* Build 144bb — Settings → Public pages feature toggles + public-page gating.
   Run: NODE_PATH=/tmp/b144test/node_modules node public_toggles.test.js */
const fs = require('fs');
const path = require('path');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const R = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');
const pp = R('static/js/pages/publicpages.js');
const track = R('track.html');
const portal = R('portal.html');

check('Settings shows the two toggles', /oe-approval/.test(pp) && /oe-messages/.test(pp) && /Customer actions/.test(pp));
check('Save sends both flags', /oe_allow_approval: document\.getElementById\('oe-approval'\)\.checked/.test(pp) && /oe_allow_messages: document\.getElementById\('oe-messages'\)\.checked/.test(pp));
check('Track page hides message box when disabled', /d\.allow_messages !== false \?/.test(track));
check('Portal hides message box when disabled', /o\.allow_messages !== false/.test(portal));
check('No emoji', !/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(pp));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
if (failed.length) process.exit(1);
