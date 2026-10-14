/* Build 144cd — mandatory 2FA frontend wiring.
   Run: node mandatory_mfa_144cd.test.js */
const fs = require('fs');
const path = require('path');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const R = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');
const af = R('static/js/auth-flows.js');
const app = R('static/js/app.js');
const staff = R('static/js/pages/staff.js');
const fb = R('static/js/pages/feedback.js');
const html = R('index.html');

check('MfaGate exists with enforce()', /window\.MfaGate\s*=/.test(af) && /enforce\s*\(\)/.test(af));
check('enforce triggers when required and not enrolled', /mfa_required && !App\.me\.mfa_enabled/.test(af));
check('enroll posts to /api/mfa/enroll', /post\('\/api\/mfa\/enroll'/.test(af));
check('activate posts to /api/mfa/activate', /post\('\/api\/mfa\/activate'/.test(af));
check('forced flow hides the close/cancel controls', /_forced \? '' :/.test(af));
check('recovery codes are shown after activation', /_renderRecovery/.test(af));
check('showApp() enforces the gate', /MfaGate\.enforce\(\)/.test(app));
check('Settings has a Require-2FA toggle', /g-require-mfa/.test(fb) && /require_mfa: document\.getElementById\('g-require-mfa'\)\.checked/.test(fb));
check('employee profile can reset 2FA', /StaffPage\.resetMfa/.test(staff) && /employees\/\$\{id\}\/reset-mfa/.test(staff));
check('Build bumped to 144cd', /build 144(c[d-z]|[d-z][a-z])/.test(html));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
