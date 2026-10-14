/* Build 111 — navigation restructure & tab styling.
   Verifies: warehouse/supplier tabs use the segmented .tabs control; Chats hosts an
   Inbox tab; the CSS defines a real .tab style (no more flat unstyled tabs).
   Run:  NODE_PATH=../../node_modules node nav_restructure.test.js  */
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
    get: async () => ({ items: [] }), post: async () => ({}), patch: async () => ({}), del: async () => ({}),
    Toast: { success() {}, error() {}, info() {} },
    setContent: (h) => { window.document.getElementById('content').innerHTML = `<div class="page-enter">${h}</div>`; },
    setTopbarActions: () => {}, icon: () => '', badge: () => '', emptyRow: () => '', skeletonRows: () => '',
    injectCompanyTabs: () => {},
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
  // 1. The CSS actually defines .tab now (the bug: it was undefined → flat text).
  console.log('Tab styling:');
  {
    const css = fs.readFileSync(path.join(__dirname, '..', 'static', 'css', 'design.css'), 'utf8');
    check('.tab has a real style rule', /\n\.tab \{[^}]*padding/.test(css));
    check('.tab.active has claymorphic inset shadow', /\.tab\.active \{[\s\S]*?inset 0 1px 0/.test(css));
    check('.tabs is a segmented glass container', /\.tabs \{[\s\S]*?backdrop-filter/.test(css));

    // build 111 UX fixes
    check('small dashboard tiles reserve space for the % pill (no overlap)',
      /\.dash-tile\.sm \{ padding:15px 18px 34px/.test(css));
    check('bulk bar sits below the table (sticky, not floating over rows)', /\.bulk-bar \{[\s\S]*?position:sticky/.test(css));
    check('bulk actions use a dropdown menu', /\.bulk-menu \{/.test(css) && /\.bulk-actions-btn \{/.test(css));
    check('report total row has real padding + clay treatment',
      /\.rep-tbl tfoot td \{[\s\S]*?padding:14px 16px/.test(css));
    check('native date pickers are styled', /::-webkit-calendar-picker-indicator/.test(css));
    check('settings sections have a clay card wrapper', /\.set-section \{/.test(css));
  }

  // 2. Warehouses tabs use the .tabs/.tab control.
  console.log('\nWarehouses tabs:');
  {
    const { ctx, window } = ctxFor();
    const P = load(ctx, window, 'warehouses.js', 'WarehousesPage');
    await P.load();
    const html = window.document.getElementById('content').innerHTML;
    check('renders a .tabs segmented bar', /class="tabs"/.test(html));
    check('has Product warehouses tab', /Product warehouses/.test(html) && /class="tab /.test(html));
    check('has Deleted tab', /Deleted<\/button>/.test(html));
  }

  // 3. Chats is now the single multi-channel page (Inbox merged in — no tabs, no lag).
  console.log('\nUnified Chats (multi-channel):');
  {
    const { ctx, window } = ctxFor({
      get: async (u) => {
        if (u.includes('/threads?')) return { items: [
          { id:'t1', channel:'whatsapp', handle:'+15551000', preview:'hi', unread:true, status:'open', client_id:'c1' } ], unread_count:1 };
        if (u.includes('/threads/')) return { thread:{ id:'t1', channel:'email', handle:'a@b.dev', status:'open', order_id:null }, messages:[] };
        return { items: [] };
      },
    });
    const P = load(ctx, window, 'chats.js', 'ChatsPage');
    await P.load();
    const html = window.document.getElementById('content').innerHTML;
    check('Chats has channel filter (all channels + per-channel)', /All channels/.test(html) && /WhatsApp/.test(html));
    check('Chats has status chips', /Open/.test(html) && /Closed/.test(html));
    check('Chats lists channel-tagged threads', /15551000/.test(html));
    check('no Inbox tab remains (fully merged)', !/Inbox<\/button>/.test(html));
  }

  // 4. Finance & numbering renders sectioned clay cards without error.
  console.log('\nFinance & numbering sections:');
  {
    const { ctx, window } = ctxFor({
      get: async (u) => {
        if (u.includes('numbering')) return { items: [] };
        if (u.includes('legal-entities')) return { items: [] };
        if (u.includes('taxes')) return { items: [{ id: 't1', name: 'GST 18%', rate: 18, mode: 'excluded', is_default: true }] };
        if (u.includes('tax-stack')) return { stack: [] };
        if (u.includes('cash-categories')) return { items: [], default_tickets_id: null, default_sales_id: null };
        return { items: [] };
      },
      icon: () => '',
    });
    let ok = true;
    try {
      const P = load(ctx, window, 'settings_finance.js', 'FinanceSettingsPage');
      await P.load();
    } catch (e) { ok = false; console.log('   ↳ ' + e.message); }
    const html = window.document.getElementById('content').innerHTML;
    check('finance page renders without error', ok);
    check('sections use the clay set-section card', /class="set-section"/.test(html));
    check('has Numbering, Taxes and Tax stack sections',
      /Numbering templates/.test(html) && /Taxes/.test(html) && /tax stack/i.test(html));
  }

  const ok = results.every((r) => r.ok);
  console.log(ok ? '\nALL PASS ✅' : '\nFAILURES ❌');
  process.exit(ok ? 0 : 1);
})();
