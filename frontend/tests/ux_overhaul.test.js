/* Builds 113–115 — Chats merge, Contacts client/supplier toggle, bulk submenu.
   Run:  NODE_PATH=../../node_modules node ux_overhaul.test.js  */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { JSDOM } = require('jsdom');

const results = [];
function check(name, cond, detail) {
  results.push({ ok: !!cond });
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${name}${cond ? '' : '  ↳ ' + (detail || '')}`);
}

function ctxFor(extra = {}) {
  const dom = new JSDOM('<!DOCTYPE html><body><div id="content"></div><div id="root"></div></body>',
    { runScripts: 'outside-only', url: 'https://shop.example.com' });
  const { window } = dom;
  window.HTMLElement.prototype.scrollIntoView = function () {};
  const ctx = {
    window, document: window.document, console, setTimeout, Date, JSON,
    navigator: { clipboard: { writeText: () => Promise.resolve() } },
    get: async () => ({ items: [], total: 0 }), post: async () => ({}), patch: async () => ({}), del: async () => ({}),
    Toast: { success() {}, error() {}, info() {} },
    setContent: (h) => { window.document.getElementById('content').innerHTML = `<div class="page-enter">${h}</div>`; },
    setTopbarActions: () => {}, icon: () => '', badge: () => '', emptyRow: () => '<tr></tr>', skeletonRows: () => '',
    injectCompanyTabs: () => {},
    fmt: { reltime: () => '', currency: (n) => '₹' + n, date: () => '', datetime: () => '', initials: () => '' },
    Modal: { open(h) { window.document.getElementById('root').innerHTML = h; }, close() {} },
    Router: { register() {}, go() {} },
    ...extra,
  };
  ctx.global = ctx;
  ctx.window.injectCompanyTabs = ctx.injectCompanyTabs;
  vm.createContext(ctx);
  return { ctx, window };
}
function load(ctx, window, file, name) {
  const src = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'pages', file), 'utf8');
  vm.runInContext(src + `\n;window.__P = ${name};`, ctx);
  return window.__P;
}

(async () => {
  // Build 113 — unified Chats (multi-channel, no separate Inbox).
  console.log('Chats merge (build 113):');
  {
    const { ctx, window } = ctxFor({
      get: async (u) => {
        if (u.includes('/threads?')) return { items: [
          { id:'t1', channel:'facebook', handle:'fb.user', preview:'hey', unread:false, status:'open', client_id:'c1' } ] };
        if (u.includes('/threads/')) return { thread:{ id:'t1', channel:'facebook', handle:'fb.user', status:'open', order_id:null }, messages:[] };
        return { items: [] };
      },
    });
    const P = load(ctx, window, 'chats.js', 'ChatsPage');
    await P.load();
    const html = window.document.getElementById('content').innerHTML;
    check('Chats offers all channels incl. Facebook/Instagram', /Facebook/.test(html) && /Instagram/.test(html));
    check('Chats has status filter', /Open/.test(html) && /Closed/.test(html));
    check('New chat opens a channel picker modal', (() => { P.newModal(); return /New conversation/.test(window.document.getElementById('root').innerHTML); })());
  }

  // Build 114 — Contacts client/supplier toggle.
  console.log('\nContacts toggle (build 114):');
  {
    const { ctx, window } = ctxFor({
      get: async (u) => {
        if (u.includes('role=supplier')) return { items: [{ id:'s1', name:'Acme', is_supplier:true, phone:'', email:'', order_count:0, balance:0 }], total:1 };
        return { items: [{ id:'c1', name:'Jo', is_supplier:false, phone:'', email:'', order_count:0, balance:0 }], total:1 };
      },
      // Contacts embeds SuppliersPage under the Suppliers tab (build 144). In the
      // real app suppliers.js is always loaded; stub it here so setRole('supplier')
      // exercises the ClientsPage branch without needing the full module.
      SuppliersPage: { _tab: null, mountInto: async () => {}, _embedBody: null },
    });
    const P = load(ctx, window, 'clients.js', 'ClientsPage');
    await P.load();
    let html = window.document.getElementById('content').innerHTML;
    check('has All / Clients / Suppliers tabs', /All<\/button>/.test(html) && /Clients<\/button>/.test(html) && /Suppliers<\/button>/.test(html));
    await P.setRole('supplier');
    check('switching to Suppliers sets role', P._role === 'supplier');
  }

  // Build 115 — bulk actions two-level menu.
  console.log('\nBulk actions submenu (build 115):');
  {
    const { ctx, window } = ctxFor();
    const P = load(ctx, window, 'orders.js', 'OrdersPage');
    P._STATUSES = ['new', 'done'];
    P._employees = [{ id: 'e1', name: 'Amit' }];
    P._statusName = (s) => s;
    // top-level menu
    let body = P._bulkMenuBody();
    check('top level shows Change status / Group invoice', /Change status/.test(body) && /Group invoice/.test(body));
    check('top level does NOT list every status flat', !/>new<\/button>/.test(body));
    // drill into status submenu
    P._bulkSub = 'status';
    body = P._bulkMenuBody();
    check('status submenu lists the statuses + back affordance',
      /bulk-menu-back/.test(body) && />new<\/button>/.test(body));
  }

  const ok = results.every((r) => r.ok);
  console.log(ok ? '\nALL PASS ✅' : '\nFAILURES ❌');
  process.exit(ok ? 0 : 1);
})();
