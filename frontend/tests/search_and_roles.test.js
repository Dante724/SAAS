/* Build 144 fixes — (1) command-palette results are actually selectable,
   (2) role permissions hide the controls they deny.
   Run:  NODE_PATH=<dir with jsdom> node search_and_roles.test.js            */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { JSDOM } = require('jsdom');

const results = [];
function check(name, cond, detail) {
  results.push({ ok: !!cond });
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${name}${cond ? '' : '  ↳ ' + (detail || '')}`);
}
const jsDir = path.join(__dirname, '..', 'static', 'js');
const read = (p) => fs.readFileSync(path.join(jsDir, p), 'utf8');

/* ── 1. Command palette: a result must be selectable ─────────────────── */
console.log('\nCommand palette — results are selectable:');
{
  const dom = new JSDOM('<!doctype html><html><body></body></html>',
    { url: 'http://localhost', runScripts: 'outside-only' });
  const w = dom.window;
  w.Element.prototype.scrollIntoView = function () {};   // not implemented by jsdom
  // minimal stubs the palette touches
  w.icon = () => '';
  w.get = async () => ({ items: [] });
  const navigated = [];
  w.Router = { go: (p) => navigated.push(p), current: null };
  const ctx = vm.createContext(w);
  vm.runInContext(read('command-palette.js'), ctx);

  // command-palette.js declares `const CommandPalette`, which lives in the
  // context's lexical scope rather than on window — read it back from there.
  const CP = vm.runInContext('CommandPalette', ctx);
  CP.open();

  const list = w.document.getElementById('cmdk-list');
  check('palette renders rows', list && list.querySelectorAll('.cmdk-row').length > 0,
    'no rows rendered');

  // Simulate the exact real-mouse sequence that used to fail: hovering a row
  // (which previously re-rendered the list) and then pressing on it.
  const rows = () => list.querySelectorAll('.cmdk-row');
  const targetIdx = rows().length - 1;                 // a "Go to" nav row
  const label = rows()[targetIdx].textContent.trim();
  const nodeBeforeHover = rows()[targetIdx];

  rows()[targetIdx].dispatchEvent(new w.MouseEvent('mouseover', { bubbles: true }));
  const nodeAfterHover = rows()[targetIdx];
  check('hovering does not rebuild the row (node identity kept)',
    nodeBeforeHover === nodeAfterHover,
    'the row was replaced on hover — this is what swallowed the click');

  check('hover moves the highlight', nodeAfterHover.classList.contains('on'),
    'highlight did not follow the mouse');

  nodeAfterHover.dispatchEvent(new w.MouseEvent('mousedown', { bubbles: true }));
  check('pressing a result triggers navigation', navigated.length === 1,
    `expected 1 navigation, got ${navigated.length} (row: "${label}")`);

  // keyboard path still works
  navigated.length = 0;
  CP.open();
  CP.onKey({ key: 'Enter', preventDefault() {} });
  check('Enter still activates the selected row', navigated.length === 1,
    `expected 1 navigation, got ${navigated.length}`);
}

/* ── 2. Role permissions hide denied controls ────────────────────────── */
console.log('\nRole permissions take effect in the UI:');
{
  const html = `<!doctype html><html><body>
    <div class="nav-link" data-page="clients">Contacts</div>
    <div class="nav-link" data-page="finance">Finance</div>
    <div class="nav-link" data-page="cash">Cash</div>
    <div class="nav-link" data-page="settings">Settings</div>
    <div class="nav-link" data-page="orders">Work orders</div>
    <div id="pane">
      <button data-perm="can_create_tickets">+ Order</button>
      <button data-perm="can_create_estimates">+ Estimate</button>
      <button data-perm="can_view_invoices_tab">Invoices &amp; payments</button>
      <button>Always visible</button>
    </div></body></html>`;
  const dom = new JSDOM(html, { url: 'http://localhost', runScripts: 'outside-only' });
  const w = dom.window;
  const ctx = vm.createContext(w);
  // App is a big object; pull just the permission members out of app.js so the
  // test exercises the real shipped implementation rather than a copy.
  const src = read('app.js');
  const start = src.indexOf('  perms: {},');
  const end = src.indexOf('  async boot()');
  vm.runInContext(`var App = { ${src.slice(start, end)} };`, ctx);
  const App = w.App;

  const vis = (sel) => w.document.querySelector(sel).style.display !== 'none';

  // (a) full-access role: nothing hidden
  App.isOwner = false; App.perms = { all: 'write' };
  App.applyPermissions(w.document);
  check('full-access role hides nothing',
    vis('[data-page="clients"]') && vis('[data-page="finance"]') &&
    vis('[data-perm="can_create_tickets"]') && vis('[data-perm="can_view_invoices_tab"]'),
    'something was hidden for a role that denies nothing');

  // (b) deny a few things -> those exact items disappear
  App.perms = {
    can_view_client_info: false,
    can_view_cashflow: false,
    can_create_tickets: false,
    can_view_invoices_tab: false,
  };
  App.applyPermissions(w.document);
  check('unchecking "view client info" hides Contacts', !vis('[data-page="clients"]'));
  check('unchecking "view cash flow" hides Finance and Cash',
    !vis('[data-page="finance"]') && !vis('[data-page="cash"]'));
  check('unchecking "create tickets" hides + Order', !vis('[data-perm="can_create_tickets"]'));
  check('unchecking "view invoices tab" hides the Invoices tab',
    !vis('[data-perm="can_view_invoices_tab"]'));
  check('permissions left checked stay visible',
    vis('[data-perm="can_create_estimates"]') && vis('[data-page="settings"]') &&
    vis('[data-page="orders"]'),
    'hid something that was not denied');

  // (c) re-checking restores it (no reload needed)
  App.perms = {};
  App.applyPermissions(w.document);
  check('re-checking a permission brings the item back',
    vis('[data-page="clients"]') && vis('[data-perm="can_create_tickets"]'));

  // (d) owner bypasses everything
  App.isOwner = true; App.perms = { can_view_client_info: false };
  App.applyPermissions(w.document);
  check('owner (no role) is never restricted', vis('[data-page="clients"]'));
}

const failed = results.filter(r => !r.ok).length;
console.log(failed ? `\nFAILURES ❌  (${failed})` : '\nALL PASS ✅');
process.exit(failed ? 1 : 0);
