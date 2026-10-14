/* Build 144bi — returning-customer redeem loop (clickable client/device, balance,
   generate coupon on client, redeem picker). Run: node client_coupon_loop_144bi.test.js */
const fs = require('fs');
const path = require('path');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const R = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');
const orders = R('static/js/pages/orders.js');
const clients = R('static/js/pages/clients.js');
const css = R('static/css/design.css');
const idx = R('index.html');

// clickable client/device + balance
check('Client name links to profile', /wo-link[\s\S]{0,80}OrdersPage\.openClient/.test(orders) && /openClient\(cid\)/.test(orders));
check('Device links to asset', /OrdersPage\.openAsset/.test(orders) && /AssetsPage\.viewAsset/.test(orders));
check('Balance shown on WO client card', /wo-bal/.test(orders) && /owes us/.test(orders) && /o\.client\.balance/.test(orders));

// generate coupon on client
check('Generate coupon button + modal', /ClientsPage\.generateCoupon/.test(clients) && /saveCoupon/.test(clients));
check('Mints via client coupons endpoint', /\/api\/clients\/\$\{id\}\/coupons/.test(clients));
check('Loyalty tab lists this client\'s coupons', /Coupons for this client/.test(clients) && /ccoupon/.test(clients));

// redeem picker on WO
check('Redeem lists client coupons + manual code', /This customer's coupons/.test(orders) && /OrdersPage\.applyCoupon/.test(orders));
check('applyCoupon posts code', /apply-coupon`, \{ code \}/.test(orders));

// styling + build + hygiene
check('Styled + build bumped', /\.wo-link/.test(css) && /\.ccoupon/.test(css) && /build 144(b[i-z]|[c-z][a-z])/.test(idx));
check('No emoji in WO drawer code', !/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(orders));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
