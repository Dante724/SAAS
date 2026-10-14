/* Build 144bx — unified list toolbar (primary + tabs left; search + flash-gear +
   actions right), single WO column picker, capped report charts, settings actions
   in a left toolbar. Run: node list_polish_144bx.test.js */
const fs = require('fs');
const path = require('path');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const R = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');
const clients = R('static/js/pages/clients.js');
const assets = R('static/js/pages/assets.js');
const products = R('static/js/pages/products.js');
const orders = R('static/js/pages/orders.js');
const settings = R('static/js/pages/settings.js');
const css = R('static/css/design.css');

check('Contacts: one toolbar row (primary + tabs left, search+gear+actions right)',
  /list-toolbar/.test(clients) && /lt-left[\s\S]{0,200}\+ New contact[\s\S]{0,400}tab/.test(clients) &&
  /lt-search/.test(clients) && /ic-btn[\s\S]{0,60}columnsMenu/.test(clients) && /cl-more/.test(clients));
check('Contacts: Import/Export folded into one menu (no Columns text button)',
  /Import<\/div>/.test(clients) && /Export<\/div>/.test(clients) && !/btn-glass"[^>]*columnsMenu[^>]*>.{0,20}Columns/.test(clients));
check('Assets: primary + owner tabs + search + gear in one row',
  /list-toolbar/.test(assets) && /\+ Add asset/.test(assets) && /Company-owned/.test(assets) && /ic-btn[\s\S]{0,60}columnsMenu/.test(assets));
check('Products: flash-gear next to search; column menu-item removed',
  /ic-btn[\s\S]{0,60}ProductsPage\.columnsMenu/.test(products) && !/pd-mi"[^>]*columnsMenu/.test(products));
check('Work orders: single column control (old toggleColumns removed)',
  /ic-btn col-gear/.test(orders) && /toggleColMenu/.test(orders) && !/toggleColumns\(event\)/.test(orders) && !/OrdersPage\.toggleColumns/.test(orders));
check('Settings: sub-page actions render as a left toolbar (no right-side bar)',
  /_renderPaneToolbar/.test(settings) && /className = 'app-toolbar'/.test(settings) && /_paneActions/.test(settings));
check('Report charts capped in height', /\.rep-chart \.chart \{ height:300px/.test(css));
check('Shared toolbar + icon-button CSS', /\.list-toolbar/.test(css) && /\.ic-btn/.test(css));
check('Build bumped', /build 144(b[x-z]|[c-z][a-z])/.test(R('index.html')));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
