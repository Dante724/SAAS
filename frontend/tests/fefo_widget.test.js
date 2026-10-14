/* Build 109 — expiring-stock modal + website widget settings.
   Run:  NODE_PATH=../../node_modules node fefo_widget.test.js  */
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
  const dom = new JSDOM('<!DOCTYPE html><body><div id="page"></div><div id="root"></div></body>',
    { runScripts: 'outside-only', url: 'https://shop.example.com' });
  const { window } = dom;
  window.HTMLElement.prototype.scrollIntoView = function () {};
  const ctx = {
    window, document: window.document, console, setTimeout, Date, JSON,
    navigator: { clipboard: { writeText: () => Promise.resolve() } },
    get: async () => ({ items: [] }), post: async () => ({}), patch: async () => ({}),
    Toast: { success() {}, error() {}, info() {} },
    setContent: (h) => { window.document.getElementById('page').innerHTML = h; },
    setTopbarActions: () => {}, icon: () => '', emptyRow: () => '', skeletonRows: () => '',
    Modal: { open(h) { window.document.getElementById('root').innerHTML = h; }, close() {} },
    Router: { register() {}, go() {} },
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
  console.log('Website widget in Integrations (build 111):');
  {
    const { ctx, window } = ctxFor({ badge: () => '' });
    const P = load(ctx, window, 'settings_integrations.js', 'IntegrationsSettingsPage');
    P.widgetModal();
    const modal = window.document.getElementById('root').innerHTML;
    check('modal embeds the widget script tag with the origin',
      /widget\.js/.test(modal) && /shop\.example\.com/.test(modal));
    check('modal shows a copyable snippet box', /wg-snippet/.test(modal));
    check('modal explains how it works', /How it works/.test(modal));
    check('modal renders a live preview', /Check your repair status/.test(modal));
    let copied = false;
    ctx.navigator.clipboard.writeText = () => { copied = true; return Promise.resolve(); };
    P.copyWidget();
    check('copy uses the clipboard', copied);
  }

  console.log('\nExpiring-stock clearance (build 109):');
  {
    const { ctx, window } = ctxFor();
    ctx.get = async (url) => {
      if (url.includes('suggestions')) {
        return { items: [
          { part_name: 'Milk', warehouse_name: 'Main', qty: 8, expiry_date: '2026-07-01',
            days_left: -5, expired: true, value_at_risk: 24 },
          { part_name: 'Bread', warehouse_name: 'Main', qty: 3, expiry_date: '2026-08-01',
            days_left: 12, expired: false, value_at_risk: 6 } ], total_value: 30 };
      }
      return { items: [] };
    };
    const P = load(ctx, window, 'inventory.js', 'InventoryPage');
    await P.expiringModal();
    const modal = window.document.getElementById('root').innerHTML;
    check('lists expiring batches', /Milk/.test(modal) && /Bread/.test(modal));
    check('flags expired ones', /expired/.test(modal));
    check('shows total at risk', /30\.00/.test(modal));
  }

  const ok = results.every(r => r.ok);
  console.log(ok ? '\nALL PASS ✅' : '\nFAILURES ❌');
  process.exit(ok ? 0 : 1);
})();
