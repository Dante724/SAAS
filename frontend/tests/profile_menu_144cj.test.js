/* Build 144cj — per-user profile menu holds the language switch (not the topbar).
   Functional (jsdom) + wiring. Run: node profile_menu_144cj.test.js */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const R = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');
const app = R('static/js/app.js');
const idx = R('index.html');

// ── wiring checks ──
check('user pod opens the profile menu', /class="user-pod"[^>]*onclick="App\.toggleProfileMenu/.test(idx));
check('topbar language host removed', !/id="lang-host"/.test(idx));
check('toggleProfileMenu builds the menu', /toggleProfileMenu\s*\(/.test(app) && /id = 'profile-menu'/.test(app));
check('menu includes the language switcher', /I18n\.switcherHtml\(\)/.test(app));
check('menu includes My payroll', /openMyPayroll/.test(app));
check('My payroll opens the current user statement', /payroll-statement\/\$\{this\.me\.id\}/.test(app));
check('menu includes Sign out', /App\.logout\(\)/.test(app));
check('showApp no longer mounts a topbar switcher', !/getElementById\('lang-host'\)/.test(app));
check('Build bumped to 144cj', /build 144(c[j-z]|[d-z][a-z])/.test(idx));

// ── functional: menu renders items + a language <select> ──
const dom = new JSDOM(`<!DOCTYPE html><body>
  <div class="sidebar"><div class="sidebar-footer"><div class="user-pod"></div></div></div>
</body>`, { runScripts: 'outside-only' });
const { window } = dom;
global.window = window; global.document = window.document;
global.localStorage = window.localStorage = { _d:{}, getItem(k){return this._d[k]??null;}, setItem(k,v){this._d[k]=v;}, removeItem(k){delete this._d[k];} };
global.NodeFilter = window.NodeFilter; global.MutationObserver = window.MutationObserver;
// minimal stubs the menu references (as globals so eval'd code resolves them)
global.avatarHtml = () => '<span class="av"></span>';
global.icon = () => '';
// eslint-disable-next-line no-eval
eval(R('static/js/i18n.js'));
global.I18n = window.I18n;
// build a tiny App with just the profile-menu methods extracted
window.App = { me: { name: 'Jane', role: 'admin', id: 'e1' } };
const openFn = app.match(/toggleProfileMenu\(ev\)\s*\{[\s\S]*?\n {2}\},/);
check('toggleProfileMenu source is extractable', !!openFn);
if (openFn) {
  // eslint-disable-next-line no-eval
  window.App.toggleProfileMenu = eval('(function ' + openFn[0].replace(/,$/, '').replace('toggleProfileMenu(ev)', '(ev)') + ')');
  window.App.toggleProfileMenu();
  const menu = window.document.getElementById('profile-menu');
  check('menu element is created', !!menu);
  check('menu contains a language <select>', !!(menu && menu.querySelector('select.lang-switcher')));
  check('menu shows the user name', !!(menu && /Jane/.test(menu.textContent)));
}

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
