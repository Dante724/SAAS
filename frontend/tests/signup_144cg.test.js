/* Build 144cg — Orderry-style signup screen.
   Run: node signup_144cg.test.js */
const fs = require('fs');
const path = require('path');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const R = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');
const html = R('index.html');
const af = R('static/js/auth-flows.js');

check('login card links to Create an account', /AuthFlows\.showSignup\(\)/.test(html));
check('signup card exists with company + template + region', /id="auth-signup"/.test(html) && /su-template/.test(html) && /su-region/.test(html));
check('signup collects team size + phone', /su-employees/.test(html) && /su-phone/.test(html));
check('showSignup loads options endpoint', /get\('\/api\/auth\/signup-options'\)/.test(af));
check('region change applies currency default', /regionChanged/.test(af) && /region_defaults/.test(af));
check('submitSignup posts to register with onboarding fields', /post\('\/api\/auth\/register'/.test(af) && /industry_template/.test(af) && /employee_count/.test(af) && /region/.test(af));
check('signup enforces 8+ char password', /length < 8/.test(af));
check('auth-signup is in the card set', /'auth-signup'/.test(af));
check('Build bumped to 144cg', /build 144(c[g-z]|[d-z][a-z])/.test(html));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
