/* Build 144bd — recommendations (WO tab + customer approve surface + toggle + dots).
   Run: NODE_PATH=/tmp/b144test/node_modules node recommendations_144bd.test.js */
const fs = require('fs');
const path = require('path');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const R = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');
const orders = R('static/js/pages/orders.js');
const track = R('../frontend/track.html');
const portal = R('../frontend/portal.html');
const pub = R('static/js/pages/publicpages.js');
const css = R('static/css/design.css');
const idx = R('index.html');

// staff WO tab
check('Recommendations section in WO (under Services&Products since 144bg)', /wo-recs-sec/.test(orders) && /id="wo-recs-\$\{id\}"/.test(orders));
check('Add + publish-all buttons', /addRecModal\(/.test(orders) && /publishAllRecs\(/.test(orders));
check('Rec CRUD wired to API', /\/recommendations`/.test(orders) && /\/publish`/.test(orders) && /recommendations\/\$\{recId\}`/.test(orders));
check('Draft/sent/approved/declined status meta', /rec-draft/.test(orders) && /rec-sent/.test(orders) && /rec-approved/.test(orders) && /rec-declined/.test(orders));
check('Clear dot on tab open', /_clearRecDot/.test(orders) && /recommendations\/seen/.test(orders));

// customer surfaces
check('track: checklist + multi approve', /recsHtml\(d\)/.test(track) && /approve_ids/.test(track) && /submitRecs\(/.test(track));
check('track: gated by allow_recommend', /allow_recommend === false/.test(track));
check('portal: checklist + decide endpoint', /recsHtmlP\(o\)/.test(portal) && /recommendations\/decide/.test(portal));
check('portal: gated by allow_recommend', /allow_recommend === false/.test(portal));

// settings toggle
check('Public-pages recommend toggle', /oe-recommend/.test(pub) && /oe_allow_recommend/.test(pub));

// notif dots
check('Board + list rec dot', /msg-dot-rec/.test(orders) && /rec_unread/.test(orders));
check('Rec dot + card styled', /\.msg-dot-rec/.test(css) && /\.rec-card/.test(css) && /build 144(b[d-z]|[c-z][a-z])/.test(idx));

// hygiene
check('No emoji in new JS', !/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(orders));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
