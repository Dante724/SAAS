/* Builds 84-90 — Settings → Warehouses, the Tasks page, and booking availability.

   Run:  NODE_PATH=../../node_modules node warehouses_tasks.test.js               */
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
  const ctx = {
    window, document: window.document, console, setTimeout, clearTimeout, Date,
    get: async () => ({ items: [] }), post: async () => ({}), put: async () => ({}),
    patch: async () => ({}), del: async () => ({}),
    Toast: { success() {}, error() {}, info() {} }, confirm2: async () => true,
    fmt: { currency: n => '$' + n, date: d => String(d).slice(0, 10), datetime: d => String(d) },
    setContent: (html) => { window.document.getElementById('page').innerHTML = html; },
    setTopbarActions: () => {},
    skeletonRows: () => '', emptyRow: () => '<tr><td>empty</td></tr>',
    badge: (s) => `<span>${s}</span>`, icon: () => '', avatarHtml: () => '',
    Router: { register() {}, go() {}, current: 'x' },
    Modal: { open(html) { window.document.getElementById('root').innerHTML = html; }, close() {} },
    ...extra,
  };
  ctx.global = ctx;
  vm.createContext(ctx);
  return { ctx, window };
}

function load(ctx, window, file, name) {
  const src = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'pages', file), 'utf8');
  vm.runInContext(src + `\n;window.__P = ${name};`, ctx);
  return window.__P;
}

(async () => {
  console.log('Settings → Warehouses (build 84/89):');
  {
    const { ctx, window } = ctxFor();
    ctx.get = async (url) => {
      if (url.startsWith('/api/staff/locations')) return { items: [{ id: 'L1', name: 'Main' }] };
      if (url.includes('/access')) {
        return { items: [{ employee_id: 'e1', name: 'Ann', explicit: false, actions: [] }] };
      }
      return {
        items: [
          { id: 'w1', name: 'Main warehouse', kind: 'product', scope: 'location',
            location_id: 'L1', is_default: true, allowed_actions: ['post', 'writeoff'],
            balance: 12, active: true },
          { id: 'w2', name: 'Van', kind: 'product', scope: 'company', location_id: null,
            is_default: false, allowed_actions: ['post'], balance: 0, active: true },
        ],
        actions: ['post', 'writeoff', 'transfer', 'sell', 'stocktake'],
        asset_label: 'Vehicles',
      };
    };
    let posted = null;
    ctx.post = async (url, body) => { posted = { url, body }; return {}; };
    const P = load(ctx, window, 'warehouses.js', 'WarehousesPage');

    await P.load();
    const page = window.document.getElementById('page').innerHTML;
    check('renders the warehouse list', /Main warehouse/.test(page) && /Van/.test(page));
    check('marks the location default', /default/.test(page));
    check('distinguishes company-wide reach', /Company-wide/.test(page));
    check('asset tab is labelled from the industry template',
      /Vehicles warehouses/.test(page), page.match(/id="wh-asset-tab"[^>]*>([^<]*)/)?.[1]);
    // count real delete buttons (the "Deleted" tab label also contains "Delete")
    check('default warehouse offers no Delete button',
      (page.match(/WarehousesPage\.remove\(/g) || []).length === 1,
      String((page.match(/WarehousesPage\.remove\(/g) || []).length));

    // create dialog
    P.edit();
    const doc = window.document;
    check('new-warehouse dialog offers product and asset kinds',
      !!doc.getElementById('wh-kind')
      && /Product \(inventory\)/.test(doc.getElementById('root').innerHTML)
      && /Vehicles/.test(doc.getElementById('root').innerHTML));
    check('offers the allowed-actions checkboxes',
      doc.querySelectorAll('[data-act]').length === 5);

    // company scope hides the location picker
    doc.getElementById('wh-scope').value = 'company';
    P._scopeToggle();
    check('company scope hides the location picker',
      doc.getElementById('wh-loc-wrap').style.display === 'none');

    doc.getElementById('wh-name').value = 'Van 2';
    doc.querySelectorAll('[data-act]').forEach(cb => { cb.checked = cb.dataset.act === 'post'; });
    await P.save(null);
    check('creates a company warehouse with no location',
      posted && posted.body.scope === 'company' && posted.body.location_id === null,
      JSON.stringify(posted && posted.body));
    check('sends only the ticked actions',
      posted.body.allowed_actions.join() === 'post', JSON.stringify(posted.body.allowed_actions));

    // access tab
    await P.access('w1');
    check('access dialog lists employees + the warehouse actions',
      /Ann/.test(doc.getElementById('root').innerHTML)
      && !!doc.querySelector('[data-exp="e1"]'));
  }

  console.log('\nTasks page (build 86):');
  {
    const { ctx, window } = ctxFor();
    ctx.get = async (url) => {
      if (url.startsWith('/api/staff/employees')) return [{ id: 'e1', name: 'Ann' }];
      if (/\/api\/tasks\/[^/]+\/feed/.test(url)) {
        return { items: [{ id: 'f1', body: 'Supplier called', kind: 'comment',
                           author_name: 'Ann', created_at: '2026-09-01T10:00:00' }] };
      }
      if (/\/api\/tasks\/[^/?]+$/.test(url)) {
        return { id: 't1', title: 'Order the part', assignee_name: 'Ann',
                 author_name: 'Bob', order_number: 'WO-9', client_name: 'Jo', due_at: null };
      }
      return { items: [
        { id: 't1', title: 'Order the part', status: 'todo', assignee_name: 'Ann',
          order_id: 'o1', client_id: 'c1', due_at: '2026-01-01T09:00:00', overdue: true },
        { id: 't2', title: 'Call back', status: 'done', assignee_name: 'Ann',
          completed_on_time: false, due_at: null, overdue: false },
      ] };
    };
    let patched = null;
    ctx.patch = async (url, body) => { patched = { url, body }; return { completed_on_time: true }; };
    const P = load(ctx, window, 'tasks.js', 'TasksPage');

    await P.load();
    const page = window.document.getElementById('page').innerHTML;
    check('lists tasks', /Order the part/.test(page));
    check('flags overdue tasks', /overdue/.test(page));
    check('shows link chips for order + client',
      /work order/.test(page) && /client/.test(page));
    check('marks a late completion as late', /late/.test(page));
    check('offers My tasks and Overdue filters',
      !!window.document.getElementById('chip-mine')
      && !!window.document.getElementById('chip-overdue'));

    await P.complete('t1');
    check('completing posts status=done',
      patched && patched.body.status === 'done', JSON.stringify(patched && patched.body));

    await P.open('t1');
    const modal = window.document.getElementById('root').innerHTML;
    check('detail shows the linked order + client', /WO-9/.test(modal) && /Jo/.test(modal));
    check('detail shows the activity feed', /Supplier called/.test(modal));
  }

  console.log('\nBooking availability + →WO (build 85):');
  {
    const { ctx, window } = ctxFor();
    ctx.get = async (url) => {
      if (url.includes('/availability')) {
        return { slots: [{ start: '09:00', end: '10:00', start_iso: 'x' },
                         { start: '11:00', end: '17:00', start_iso: 'y' }] };
      }
      if (url.startsWith('/api/bookings')) {
        return { items: [{ id: 'b1', status: 'booked', client_name: 'Jo',
                           scheduled_for: '2026-09-01T10:00:00',
                           ends_at: '2026-09-01T11:00:00', employee_name: 'Ann' }] };
      }
      if (url.startsWith('/api/clients')) return { items: [] };
      if (url.startsWith('/api/staff/employees')) return [{ id: 'e1', name: 'Ann' }];
      return { items: [] };
    };
    let posted = null;
    ctx.post = async (url, body) => { posted = { url, body }; return { order_number: 'WO-12' }; };
    const P = load(ctx, window, 'bookings.js', 'BookingsPage');

    await P.load();
    await P.setView('list');   // build 144bq: default is Timeline; List shows row actions
    const page = window.document.getElementById('page').innerHTML;
    check('booked rows offer → Work order', /→ Work order/.test(page));

    await P.toOrder('b1');
    check('converts the booking to a work order',
      posted && posted.url === '/api/bookings/b1/to-order', posted && posted.url);

    await P.newModal();
    await P.loadSlots();
    const slots = window.document.getElementById('bk-slots').innerHTML;
    check('shows free slots from the availability endpoint',
      /09:00–10:00/.test(slots) && /11:00–17:00/.test(slots), slots);
  }

  console.log('\nDay-off shows no slots:');
  {
    const { ctx, window } = ctxFor();
    ctx.get = async (url) => {
      if (url.includes('/availability')) return { slots: [], reason: 'employee off this day' };
      if (url.startsWith('/api/clients')) return { items: [] };
      if (url.startsWith('/api/staff/employees')) return [{ id: 'e1', name: 'Ann' }];
      return { items: [] };
    };
    const P = load(ctx, window, 'bookings.js', 'BookingsPage');
    P._date = '2026-09-02';
    await P.newModal();
    await P.loadSlots();
    check('a day off says so instead of offering slots',
      /off on this day/.test(window.document.getElementById('bk-slots').innerHTML));
  }

  const ok = results.every(r => r.ok);
  console.log(ok ? '\nALL PASS ✅' : '\nFAILURES ❌');
  process.exit(ok ? 0 : 1);
})();
