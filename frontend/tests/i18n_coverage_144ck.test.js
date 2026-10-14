/* Build 144ck — Spanish dictionary full-coverage + live DOM translation.
   Run: node i18n_coverage_144ck.test.js */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const R = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');

const dom = new JSDOM('<!DOCTYPE html><body></body>', { runScripts: 'outside-only' });
global.window = dom.window; global.document = dom.window.document;
global.NodeFilter = dom.window.NodeFilter; global.MutationObserver = dom.window.MutationObserver;
global.localStorage = { _d:{}, getItem(k){return this._d[k]??null;}, setItem(k,v){this._d[k]=v;} };
// eslint-disable-next-line no-eval
eval(R('static/js/i18n.js'));
const I18n = window.I18n;
const es = I18n.DICT.es;

check('dictionary has broad coverage (300+ phrases)', Object.keys(es).length >= 300, `only ${Object.keys(es).length}`);
check('covers nav', es['Work orders'] === 'Órdenes de trabajo' && es['Inventory'] === 'Inventario');
check('covers actions & buttons', es['Save'] === 'Guardar' && es['+ New rule'] === '+ Nueva regla');
check('covers table headers', es['Qty'] === 'Cant.' && es['Amount'] === 'Importe');
check('covers toasts', es['Name is required'] === 'El nombre es obligatorio' && es['Rescheduled'] === 'Reprogramado');
check('covers weekdays', es['Mon'] === 'Lun' && es['Sun'] === 'Dom');

// live DOM translation of a rendered fragment
I18n.setLang('es');
const el = document.createElement('div');
el.innerHTML = `<button>Save</button><span>Work orders</span><input placeholder="Search…">`;
document.body.appendChild(el);
I18n.translateNode(el);
check('translates button text in DOM', /Guardar/.test(el.querySelector('button').textContent));
check('translates span text in DOM', /Órdenes de trabajo/.test(el.querySelector('span').textContent));
check('translates placeholder attribute', el.querySelector('input').getAttribute('placeholder') === 'Buscar…');
check('handles arrow suffix', I18n.t('Sign in →') === 'Iniciar sesión →');
I18n.lang = 'en';
check('English is a pass-through (no translation)', I18n.t('Work orders') === 'Work orders');

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
