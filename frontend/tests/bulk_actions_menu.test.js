/* Build 118 — work-order bulk Actions menu is interactive.
   Guards the regression where menu items did nothing / the menu closed on click.
   Run:  NODE_PATH=../../node_modules node bulk_actions_menu.test.js  */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { JSDOM } = require('jsdom');

const results = [];
function check(name, cond, detail) {
  results.push({ ok: !!cond });
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${name}${cond ? '' : '  ↳ ' + (detail || '')}`);
}

const dom = new JSDOM('<!DOCTYPE html><body><div id="content"></div></body>',
  { runScripts: 'outside-only', url: 'https://x.test' });
const { window } = dom;
window.HTMLElement.prototype.scrollIntoView = function () {};

const posted = [];
const ctx = {
  window, document: window.document, console, setTimeout, Date, JSON, Math,
  post: async (url, body) => { posted.push(body); return { updated: body.order_ids.length }; },
  get: async () => ({ items: [] }),
  Toast: { success() {}, error() {}, info() {} },
  icon: () => '', badge: () => '', fmt: { currency: (n) => '₹' + n, datetime: () => '', reltime: () => '' },
  setContent: () => {}, setTopbarActions: () => {}, Modal: { open() {}, close() {} },
  Router: { register() {}, go() {} },
};
ctx.global = ctx;
vm.createContext(ctx);

// Load just the OrdersPage object.
const src = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'pages', 'orders.js'), 'utf8');
vm.runInContext(src + '\n;window.__O = OrdersPage;', ctx);
const O = window.__O;

// Seed selection + the bar container, then render the bar.
O._selected = new Set(['o1', 'o2']);
O._STATUSES = ['new', 'in_repair', 'ready'];
O._statusName = (s) => s;
O._employees = [{ id: 'e1', name: 'Tech One' }];
O._lastOrders = [{ id: 'o1' }, { id: 'o2' }];

const bar = window.document.createElement('div');
bar.id = 'o-bulk-bar';
bar.className = 'bulk-bar';
window.document.getElementById('content').appendChild(bar);

// Render the bar (the method that fills #o-bulk-bar).
if (typeof O._renderBulkBar === 'function') O._renderBulkBar();
else if (typeof O.syncBulkBar === 'function') O.syncBulkBar();
else if (typeof O._syncBulk === 'function') O._syncBulk();

console.log('Bulk Actions menu:');
check('bar renders an Actions button', /Actions/.test(bar.innerHTML));

// Open the menu.
O.toggleBulkMenu({ stopPropagation() {} });
let menu = window.document.getElementById('o-bulk-menu');
check('menu opens', menu && menu.style.display === 'block');
check('top level lists Change status / Group invoice',
  /Change status/.test(menu.innerHTML) && /Group invoice/.test(menu.innerHTML));

// Drill into the status submenu.
O.bulkSubmenu('status');
menu = window.document.getElementById('o-bulk-menu');
check('status submenu shows the statuses + a back button',
  /in_repair/.test(menu.innerHTML) && /Change status/.test(menu.innerHTML));

// Apply a status to the selection.
(async () => {
  await O.bulkSet('set_status', 'ready');
  check('applying a status posts the bulk action', posted.some(p => p.action === 'set_status' && p.value === 'ready'));

  // Group invoice posts the group action.
  posted.length = 0;
  O._selected = new Set(['o1', 'o2']);
  await O.bulkInvoice(true);
  check('group invoice posts group_invoice', posted.some(p => p.action === 'group_invoice'));

  const ok = results.every(r => r.ok);
  console.log(ok ? '\nALL PASS ✅' : '\nFAILURES ❌');
  process.exit(ok ? 0 : 1);
})();
