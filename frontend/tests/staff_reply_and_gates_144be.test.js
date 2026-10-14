/* Build 144be — publish-perm vanish, staff reply composer + toggle + role gate.
   Run: node staff_reply_and_gates_144be.test.js */
const fs = require('fs');
const path = require('path');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const R = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');
const orders = R('static/js/pages/orders.js');
const feed = R('static/js/feed.js');
const pub = R('static/js/pages/publicpages.js');
const css = R('static/css/design.css');
const idx = R('index.html');

// role vanish via data-perm (Modal.open auto-applies)
check('Rec add gated can_recommend', /data-perm="can_recommend" onclick="OrdersPage.addRecModal/.test(orders));
check('Publish buttons gated can_publish_recommend', (orders.match(/data-perm="can_publish_recommend"/g) || []).length >= 2);
check('Rec list re-applies perms after render', /App\.applyPermissions\(el\)/.test(orders));

// staff reply composer
check('Reply box gated by toggle + perm', /o\.allow_staff_reply !== false/.test(orders) && /class="reply-box" data-perm="can_reply_customer"/.test(orders));
check('sendCustomerReply hits customer-reply endpoint', /sendCustomerReply/.test(orders) && /\/customer-reply`/.test(orders));
check('Reply reloads feed', /Feed\.reload\(\)/.test(orders));

// feed renders staff_reply
check('Feed renders staff_reply item', /staff_reply/.test(feed));

// settings toggle
check('Public-pages staff-reply toggle', /oe-staff-reply/.test(pub) && /oe_allow_staff_reply/.test(pub));

// styling + build
check('Reply box styled + build bumped', /\.reply-box/.test(css) && /build 144(b[e-z]|[c-z][a-z])/.test(idx));

// hygiene
check('No emoji in new JS', !/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(orders) && !/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(feed));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
