/* Builds 96-97 — inventory bulk edit, the missed-call automation toggle, and the
   scheduler being booking-aware.

   Run:  NODE_PATH=../../node_modules node reports_bulk_automation.test.js              */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { JSDOM } = require('jsdom');

const results = [];
function check(name, cond, detail) {
  results.push({ name, ok: !!cond });
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${name}${cond ? '' : '  ↳ ' + (detail || '')}`);
}

function ctxFor(extra = {}) {
  const dom = new JSDOM('<!DOCTYPE html><body><div id="root"></div><div id="page"></div></body>',
    { runScripts: 'outside-only' });
  const { window } = dom;
  window.HTMLElement.prototype.scrollIntoView = function () {};
  const ctx = {
    window, document: window.document, console, setTimeout, clearTimeout, Date, JSON, URL,
    get: async () => ({ items: [] }), post: async () => ({}), put: async () => ({}),
    patch: async () => ({}), del: async () => ({}),
    Toast: { success() {}, error() {}, info() {} }, confirm2: async () => true,
    fmt: { currency: n => '$' + n, date: d => String(d).slice(0, 10), datetime: d => String(d) },
    setContent: (html) => { window.document.getElementById('page').innerHTML = html; },
    setTopbarActions: () => {},
    skeletonRows: () => '', emptyRow: () => '<tr><td>empty</td></tr>',
    badge: (s) => `<span>${s}</span>`, icon: () => '', avatarHtml: () => '',
    Auth: { token: 'tok' },
    Router: { register() {}, go() {}, current: 'x', _went: null },
    Modal: { open(html) { window.document.getElementById('root').innerHTML = html; }, close() {} },
    ...extra,
  };
  ctx.Router.go = (p) => { ctx.Router._went = p; };
  ctx.global = ctx;
  vm.createContext(ctx);
  return { ctx, window };
}

function load(ctx, window, file, name) {
  const src = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'pages', file), 'utf8');
  vm.runInContext(src + `\n;window.__P = ${name};`, ctx);
  return window.__P;
}
function loadRoot(ctx, window, file, name) {
  const src = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', file), 'utf8');
  vm.runInContext(src + `\n;window.__P = ${name};`, ctx);
  return window.__P;
}

(async () => {
  console.log('Inventory bulk edit (build 96):');
  {
    const { ctx, window } = ctxFor();
    let posted = null;
    ctx.post = async (url, body) => { posted = { url, body }; return { changed: body.part_ids.length }; };
    const P = load(ctx, window, 'inventory.js', 'InventoryPage');
    const doc = window.document;
    P._visible = [{ id: 'p1' }, { id: 'p2' }, { id: 'p3' }];

    P.bulkModal();
    const modal = doc.getElementById('root').innerHTML;
    check('bulk dialog names the count', /3 product\(s\)/.test(modal));
    check('price change is offered as a percentage', /Adjust sell price by %/.test(modal));

    // default action is percentage — apply it
    doc.getElementById('blk-num').value = '10';
    await P.bulkApply();
    check('bulk applies to every filtered product',
      posted.url === '/api/inventory/parts/bulk'
      && posted.body.part_ids.length === 3
      && posted.body.action === 'adjust_price_pct'
      && posted.body.number === 10,
      JSON.stringify(posted.body));

    // switch to min-stock
    doc.getElementById('blk-action').value = 'set_min_stock';
    P._bulkFields();
    doc.getElementById('blk-num').value = '15';
    await P.bulkApply();
    check('min-stock action sends the number',
      posted.body.action === 'set_min_stock' && posted.body.number === 15,
      JSON.stringify(posted.body));

    // empty filter is refused
    let errored = '';
    ctx.Toast.error = (m) => { errored = m; };
    P._visible = [];
    P.bulkModal();
    check('bulk edit refuses an empty filter', /No products/.test(errored), errored);
  }

  console.log('\nAutomation rules + missed-call (build 144w):');
  {
    const { ctx, window } = ctxFor();
    let saved = null, posted = null;
    ctx.get = async (url) => {
      if (url.includes('/api/automations/meta')) return {
        events: [{ key: 'status_changed', label: 'Status changed to…' }, { key: 'order_created', label: 'Work order created' }],
        actions: [{ key: 'create_task', label: 'Create a task' }, { key: 'add_tag', label: 'Add a tag to the order' }],
        condition_fields: ['status', 'total'], ops: ['eq', 'gte', 'lt'] };
      if (url.replace(/\?.*$/, '').endsWith('/api/automations')) return { items: [] };
      if (url.includes('task_on_missed_call')) return {
        key: 'task_on_missed_call', enabled: false, config: { assign_to: null, priority: 'high' } };
      if (url.includes('/api/statuses')) return { items: [{ key: 'ready', name: 'Ready' }] };
      if (url.includes('/api/staff/employees')) return { items: [{ id: 'e1', name: 'Alex', active: true }] };
      return { items: [] };
    };
    ctx.put = async (url, body) => { saved = { url, body }; return body; };
    ctx.post = async (url, body) => { posted = { url, body }; return { id: 'r1', fired: 0 }; };
    const P = load(ctx, window, 'automations.js', 'AutomationsPage');
    const doc = window.document;

    await P.load();
    const body = doc.getElementById('auto-body').innerHTML;
    check('unified automation page renders the rules table', /No automations yet|<table/.test(body));
    check('shows the missed-call automation card', /Task from a missed call/.test(body));
    check('toggle reflects the disabled state', doc.getElementById('am-missed').checked === false);
    check('lists employees as fallback assignee', /Alex/.test(body));

    // missed-call save now goes through saveMissed()
    doc.getElementById('am-missed').checked = true;
    doc.getElementById('am-assign').value = 'e1';
    doc.getElementById('am-priority').value = 'urgent';
    await P.saveMissed();
    check('saveMissed posts toggle, assignee and priority',
      saved.url === '/api/comms/automations/task_on_missed_call'
      && saved.body.enabled === true
      && saved.body.config.assign_to === 'e1'
      && saved.body.config.priority === 'urgent',
      JSON.stringify(saved.body));

    // create a workflow rule through the editor
    P.edit();
    doc.getElementById('au-name').value = 'Ready → task';
    doc.getElementById('au-event').value = 'status_changed';
    doc.getElementById('au-action').value = 'create_task';
    P._toggle();
    if (doc.getElementById('au-cfg')) doc.getElementById('au-cfg').value = 'Call the customer';
    await P.save(null);
    check('creating a rule POSTs to /api/automations with the action',
      posted && posted.url === '/api/automations'
      && posted.body.event === 'status_changed'
      && posted.body.action_type === 'create_task',
      JSON.stringify(posted && posted.body));
  }

  console.log('\nScheduler is booking-aware (build 92 via the real scheduler):');
  {
    const { ctx, window } = ctxFor();
    let patched = null;
    ctx.get = async (url) => {
      if (url.includes('scheduler/day')) {
        return { items: [
          { id: 'o1', kind: 'order', order_number: 'WO-1', client_name: 'ACME',
            assigned_to_id: 'e1', scheduled_for: `${new Date().toISOString().slice(0, 10)}T10:00:00`,
            duration_min: 60, status: 'in_progress' },
          { id: 'b1', kind: 'booking', order_number: null, client_name: 'Jo',
            assigned_to_id: 'e1', scheduled_for: `${new Date().toISOString().slice(0, 10)}T13:00:00`,
            duration_min: 30, status: 'booked', order_id: null },
        ] };
      }
      if (url.includes('staff/employees')) return [{ id: 'e1', name: 'Tech', active: true }];
      return { items: [] };
    };
    ctx.patch = async (url, body) => { patched = { url, body }; return { ok: true }; };
    ctx.OrdersPage = { viewOrder(id) { ctx._viewed = id; } };
    const S = loadRoot(ctx, window, 'scheduler.js', 'Scheduler');
    const doc = window.document;

    S.mode = 'day';
    S.container = doc.getElementById('page');
    S.date = new Date().toISOString().slice(0, 10);
    await S._loadDay();
    const html = doc.getElementById('page').innerHTML;
    check('renders a work-order block', /WO-1/.test(html));
    check('renders a booking block labelled as such',
      /Booking/.test(html) && /Jo/.test(html), 'booking block missing');

    // dropping a booking reschedules against the bookings endpoint, not orders
    S._drag = { id: 'b1', dur: 30, kind: 'booking', moved: true };
    // fake a track drop at ~11:00
    const fakeEvt = {
      preventDefault() {}, clientX: 100,
      currentTarget: { getBoundingClientRect: () => ({ left: 0 }), scrollLeft: 0,
                       classList: { remove() {} } },
    };
    S._colW = 80; S.startHour = 8; S.endHour = 20;
    await S._drop(fakeEvt, 'e1');
    check('a booking reschedules via the bookings endpoint',
      patched && /\/api\/bookings\/b1/.test(patched.url) && patched.body.employee_id === 'e1',
      patched ? patched.url : 'no patch');

    // dropping an order still uses the order schedule endpoint
    patched = null;
    S._drag = { id: 'o1', dur: 60, kind: 'order', moved: true };
    await S._drop(fakeEvt, 'e1');
    check('an order still reschedules via the order endpoint',
      patched && /\/api\/orders\/o1\/schedule/.test(patched.url),
      patched ? patched.url : 'no patch');

    // clicking a booking opens the bookings page, not an order
    S._drag = { id: null, dur: 60, kind: 'order', moved: false };
    S._clickBlock({ target: { classList: { contains: () => false } } }, 'b1', 'booking', '');
    check('clicking a booking opens the bookings page', ctx.Router._went === 'bookings',
      String(ctx.Router._went));
  }

  const ok = results.every(r => r.ok);
  console.log(ok ? '\nALL PASS ✅' : '\nFAILURES ❌');
  process.exit(ok ? 0 : 1);
})();
