/* Build 144ba — customer messages in feed + unread dots on board/list/tab.
   Run: NODE_PATH=/tmp/b144test/node_modules node client_messages.test.js */
const fs = require('fs');
const path = require('path');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const R = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');
const feed = R('static/js/feed.js');
const orders = R('static/js/pages/orders.js');
const css = R('static/css/design.css');
const track = R('track.html');
const portal = R('portal.html');

check('Feed renders a client_message (Customer tag, highlighted)', /client_message/.test(feed) && /feed-badge-cust/.test(feed) && /feed-msg/.test(feed));
check('Feed message is XSS-safe (uses _escape)', /isMsg \? `<div class="feed-msg">\$\{this\._escape/.test(feed));
check('Board card shows the unread dot', /client_msg_unread/.test(orders) && /msg-dot/.test(orders));
check('List row shows the unread dot', /msg-dot-inline/.test(orders));
check('Feed tab shows the unread count', /msg-dot-tab/.test(orders));
check('Dot styles present (light + dark)', /\.msg-dot/.test(css) && /\[data-theme="dark"\] \.feed-msg/.test(css));
check('Track page: message the shop', /Message the shop/.test(track) && /track\/' \+ token \+ '\/message/.test(track) && /sendMessage/.test(track));
check('Portal page: message the shop', /Message the shop/.test(portal) && /\/message'/.test(portal) && /sendMessage/.test(portal));
check('No emoji', !/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(feed) && !/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(orders));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
if (failed.length) process.exit(1);
