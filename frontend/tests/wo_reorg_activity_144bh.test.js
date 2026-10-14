/* Build 144bh — money reorg, tech-grouped lines, Activity filter, create throughput.
   Run: node wo_reorg_activity_144bh.test.js */
const fs = require('fs');
const path = require('path');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const R = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');
const orders = R('static/js/pages/orders.js');
const feed = R('static/js/feed.js');
const css = R('static/css/design.css');
const idx = R('index.html');

// WS1 — money actions off S&P, onto Invoices + header
check('S&P money strip removed', !/wo-pay-actions/.test(orders));
check('Currency moved to header', /currencyModal\('\$\{id\}'\)[^]*?Billing currency/.test(orders));
check('Take payment now in Invoices tab', /data-panel="invoices"[\s\S]*maybePaymentWindow\('\$\{id\}',true\)/.test(orders));
check('Redeem group (coupon/gift/points) in Invoices', /wo-redeem-bar/.test(orders) && /applyCouponModal[\s\S]*redeemGiftModal[\s\S]*redeemPointsModal/.test(orders));
check('Print stays in header only (single Print/document)', !/Print \/ document/.test(orders));

// WS4 — create-form throughput
check('Create & open + Save & create another', /submitNew\('open'\)/.test(orders) && /submitNew\('another'\)/.test(orders));
check('submitNew handles modes', /submitNew\(mode = 'create'\)/.test(orders) && /mode === 'open'/.test(orders) && /mode === 'another'/.test(orders));
check('Create & open opens the new record', /viewOrder\(created\.id\)/.test(orders));

// WS2 — group lines by technician
check('Lines grouped by technician', /_woLinesGrouped/.test(orders) && /wo-line-grp/.test(orders));
check('Row extracted to helper', /_woLineRow\(id, l\)/.test(orders));

// WS3 — Activity tab
check('Tab renamed to Activity', /woTab\(event,'files'\)">Activity/.test(orders));
check('All events / Comments filter', /feed-filter/.test(feed) && /setFilter\('comments'/.test(feed) && /_COMMENT_TYPES/.test(feed));
check('Before→after diff rendering', /_sysBody/.test(feed) && /feed-diff-b/.test(feed));

// styling + build + hygiene
check('Styled + build bumped', /\.wo-redeem-bar/.test(css) && /\.feed-fbtn/.test(css) && /\.wo-line-grp/.test(css) && /build 144(b[h-z]|[c-z][a-z])/.test(idx));
check('No emoji', !/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(orders) && !/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(feed));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
