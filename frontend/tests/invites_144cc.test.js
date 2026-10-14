/* Build 144cc — teammate invite (Settings → Employees) + accept screen.
   Run: node invites_144cc.test.js */
const fs = require('fs');
const path = require('path');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const R = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');
const staff = R('static/js/pages/staff.js');
const af = R('static/js/auth-flows.js');
const html = R('index.html');

check('Employees tab has an Invite teammate button', /StaffPage\.inviteModal\(\)/.test(staff));
check('inviteModal posts to /api/staff/invites', /post\('\/api\/staff\/invites'/.test(staff));
check('pending invites are loaded', /get\('\/api\/staff\/invites'\)/.test(staff));
check('invite can be revoked', /del\(`\/api\/staff\/invites\/\$\{id\}`\)/.test(staff));
check('invite can be resent', /resendInvite/.test(staff));
check('accept-invite deep link handled', /\/accept-invite/.test(af) && /_inviteToken/.test(af));
check('accept previews the invite', /post\('\/api\/auth\/invite\/preview'/.test(af));
check('accept posts to invite/accept', /post\('\/api\/auth\/invite\/accept'/.test(af));
check('accept enforces 8+ char password', /length < 8/.test(af));
check('accept-invite card exists in index', /id="auth-invite"/.test(html));
check('Build bumped to 144cc', /build 144(c[c-z]|[d-z][a-z])/.test(html));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
