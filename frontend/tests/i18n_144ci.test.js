/* Build 144ci — i18n framework: functional (jsdom) + wiring checks.
   Run: node i18n_144ci.test.js */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const R = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');

// ── functional: load i18n.js into a jsdom window and translate a real DOM ──
const dom = new JSDOM(`<!DOCTYPE html><body>
  <div class="nav-link">Work orders</div>
  <button>Sign in →</button>
  <label>Name</label>
  <input placeholder="Search…">
  <span>Contacts</span>
</body>`, { runScripts: 'outside-only' });
const { window } = dom;
global.window = window; global.document = window.document;
global.localStorage = window.localStorage = { _d: {}, getItem(k){return this._d[k]??null;}, setItem(k,v){this._d[k]=v;}, removeItem(k){delete this._d[k];} };
global.NodeFilter = window.NodeFilter; global.MutationObserver = window.MutationObserver;
// eslint-disable-next-line no-eval
eval(R('static/js/i18n.js'));
const I18n = window.I18n;

check('defaults to English', I18n.lang === 'en');
I18n.lang = 'es';
I18n.translateNode(document.body);
check('translates nav text (Work orders → Órdenes de trabajo)', document.querySelector('.nav-link').textContent === 'Órdenes de trabajo');
check('translates a button with trailing arrow (Sign in → )', /Iniciar sesión →/.test(document.querySelector('button').textContent));
check('translates a label (Name → Nombre)', document.querySelector('label').textContent === 'Nombre');
check('translates placeholder attribute (Search… → Buscar…)', document.querySelector('input').getAttribute('placeholder') === 'Buscar…');
check('t() returns Spanish for a known key', I18n.t('Contacts') === 'Contactos');
check('t() falls back to English for unknown key', I18n.t('Zxq unknown') === 'Zxq unknown');
check('does not re-translate an already-translated node', (() => {
  const before = document.querySelector('.nav-link').textContent;
  I18n.translateNode(document.body);
  return document.querySelector('.nav-link').textContent === before;
})());
check('switcherHtml offers English + Spanish', /value="en"/.test(I18n.switcherHtml()) && /value="es"/.test(I18n.switcherHtml()));
check('Arabic scaffolded as rtl (no catalogue yet, not offered)', I18n.LANGS.ar.dir === 'rtl' && !/value="ar"/.test(I18n.switcherHtml()));

// ── wiring checks ──
const idx = R('index.html'); const app = R('static/js/app.js'); const core = R('static/js/core.js'); const prefs = R('../backend/routers/prefs.py');
check('i18n.js loaded before app.js', idx.indexOf('js/i18n.js') > -1 && idx.indexOf('js/i18n.js') < idx.indexOf('js/app.js'));
check('setContent applies translation', /I18n\.apply\(c\)/.test(app));
check('showApp mounts switcher + boots i18n', /I18n\.switcherHtml\(\)/.test(app) && /I18n\.boot\(\)/.test(app));
check('fmt is locale-aware', /_locale/.test(core) && /toLocaleString\(this\._locale/.test(core));
check('server allows the lang pref key', /_ALLOWED_EXACT/.test(prefs) && /"lang"/.test(prefs));
check('Build bumped to 144ci', /build 144(c[i-z]|[d-z][a-z])/.test(idx));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
