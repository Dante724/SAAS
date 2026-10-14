/* Build 144cf — separate /admin operator portal.
   Run: node admin_portal_144cf.test.js */
const fs = require('fs');
const path = require('path');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const R = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');
const html = R('admin.html');
const main = R('../main.py');
const idx = R('index.html');

check('admin.html exists as its own page (own login)', /id="login"/.test(html) && /Operator/.test(html));
check('portal uses a separate admin token key', /ros_admin_token/.test(html));
check('login hits /api/admin/login', /\/api\/admin\/login/.test(html));
check('shows the six lifecycle segments', /SEG_META/.test(html) && /lost_trial/.test(html) && /lost_paid/.test(html) && /suspended/.test(html));
check('lists tenants from /api/admin/tenants', /\/api\/admin\/tenants/.test(html));
check('tenant drawer edits term (start/end) + status', /d-start/.test(html) && /d-end/.test(html) && /d-status/.test(html));
check('per-employee access windows editable', /es-\$\{e\.id\}|saveEmp/.test(html) && /access_start/.test(html));
check('discount + apply coupon in drawer', /d-disc/.test(html) && /apply-coupon/.test(html));
check('operator notes field', /admin_notes/.test(html) && /d-notes/.test(html));
check('coupon generation panel', /openCoupons/.test(html) && /\/api\/admin\/coupons/.test(html));
check('expires-soon flag on term', /_expiringSoon/.test(html));
check('server serves /admin route', /def serve_admin/.test(main) && /admin\.html/.test(main));
check('Build bumped to 144cf', /build 144(c[f-z]|[d-z][a-z])/.test(idx));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
