/* Build 144cb — password reset + email verification frontend wiring.
   Run: node auth_flows_144cb.test.js */
const fs = require('fs');
const path = require('path');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const R = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');
const af = R('static/js/auth-flows.js');
const html = R('index.html');
const app = R('static/js/app.js');
const staff = R('static/js/pages/staff.js');

check('AuthFlows exposes intercept for deep links', /intercept\s*\(\)\s*\{/.test(af));
check('intercept handles /reset-password', /\/reset-password/.test(af) && /_resetToken/.test(af));
check('intercept handles /verify-email + auto-verify', /\/verify-email/.test(af) && /_runVerify/.test(af));
check('sendForgot posts to password/forgot', /post\('\/api\/auth\/password\/forgot'/.test(af));
check('submitReset posts to password/reset', /post\('\/api\/auth\/password\/reset'/.test(af));
check('verify posts to email/verify', /post\('\/api\/auth\/email\/verify'/.test(af));
check('reset requires 8+ chars and match', /length < 8/.test(af) && /do not match/.test(af));
check('boot() lets AuthFlows intercept first', /AuthFlows\.intercept\(\)/.test(app));
check('login card shows Forgot password link', /AuthFlows\.showForgot\(\)/.test(html));
check('auth-flows.js is loaded before app.js', html.indexOf('auth-flows.js') < html.indexOf('js/app.js'));
check('staff profile can email a reset link', /StaffPage\.sendReset/.test(staff) && /employees\/\$\{id\}\/send-reset/.test(staff));
check('Build bumped to 144cb+', /build 144(c[b-z]|[d-z][a-z])/.test(html));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
