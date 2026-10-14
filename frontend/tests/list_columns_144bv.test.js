/* Build 144bv — custom-field columns + sort + saved views on lists.
   Run: node list_columns_144bv.test.js */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const R = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');

const dom = new JSDOM(`<!DOCTYPE html><table id="t"><thead><tr><th>Name</th><th></th></tr></thead>
  <tbody>
    <tr data-rid="a"><td>Bravo</td><td>x</td></tr>
    <tr data-rid="b"><td>Alpha</td><td>x</td></tr>
    <tr data-rid="c"><td>Charlie</td><td>x</td></tr>
  </tbody></table>`, { pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document; global.Node = window.Node;
const store = {};
const ls = { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: k => { delete store[k]; } };
window.localStorage = ls; global.localStorage = ls;
// stub the app globals ListExtras uses
window.get = global.get = async (url) => ({ items: [
  { id: 'f1', field_label: 'Warranty', field_type: 'text' },
  { id: 'f2', field_label: 'Priority', field_type: 'number' },
] });
window.Toast = global.Toast = { error() {}, success() {} };
window.Modal = global.Modal = { open() {}, close() {} };
window.icon = global.icon = () => '';

eval(R('static/js/list-extras.js'));
const LX = window.ListExtras;

const byId = {
  a: { id: 'a', custom_data: { f1: 'W-1', f2: 3 } },
  b: { id: 'b', custom_data: { f1: 'W-2', f2: 1 } },
  c: { id: 'c', custom_data: { f1: 'W-3', f2: 2 } },
};

(async () => {
  // choose one custom column
  store['ros_lx_client'] = JSON.stringify({ cols: ['f2'] });
  await LX.attach('t', 'client', byId);

  const headTexts = [...document.querySelectorAll('#t thead th')].map(th => th.textContent.replace(/\s+/g, ' ').trim());
  check('Custom column injected into header', headTexts.some(t => t.startsWith('Priority')), headTexts.join('|'));
  const firstRowCells = document.querySelectorAll('#t tbody tr')[0].cells.length;
  check('Custom cell injected into rows', firstRowCells === 3, 'cells=' + firstRowCells);

  // sort by the Priority (numeric) column ascending → order b(1),c(2),a(3)
  LX._toggleSort('client', 'cf:f2');
  let order = [...document.querySelectorAll('#t tbody tr')].map(r => r.dataset.rid);
  check('Numeric sort ascending', order.join('') === 'bca', order.join(''));
  LX._toggleSort('client', 'cf:f2');  // flip → desc a,c,b
  order = [...document.querySelectorAll('#t tbody tr')].map(r => r.dataset.rid);
  check('Sort flips to descending', order.join('') === 'acb', order.join(''));

  // sort by built-in Name column ascending → Alpha,Bravo,Charlie = b,a,c
  LX._toggleSort('client', 'c:Name');
  order = [...document.querySelectorAll('#t tbody tr')].map(r => r.dataset.rid);
  check('Built-in text sort works', order.join('') === 'bac', order.join(''));

  // saved views persist
  store['ros_lx_client'] = JSON.stringify({ cols: ['f1'], sort: { key: 'c:Name', dir: 'asc' } });
  LX._saveView && (document.body.innerHTML += '<input id="lx-vn" value="My view">');
  LX._saveView('client');
  const views = JSON.parse(store['ros_lxviews_client'] || '[]');
  check('Saved view stored', views.length === 1 && views[0].name === 'My view', JSON.stringify(views));

  // static wiring checks
  check('Registered in index', /list-extras\.js/.test(R('index.html')));
  check('Contacts wired', /ListExtras\.attach\('cl-list-table', 'client'/.test(R('static/js/pages/clients.js')) && /ClientsPage\.columnsMenu/.test(R('static/js/pages/clients.js')));
  check('Products wired', /ListExtras\.attach\('pr-list-table', 'product'/.test(R('static/js/pages/products.js')) && /ProductsPage.columnsMenu()/.test(R('static/js/pages/products.js')));
  check('Assets wired', /ListExtras\.attach\('a-list-table', 'device'/.test(R('static/js/pages/assets.js')) && /AssetsPage\.columnsMenu/.test(R('static/js/pages/assets.js')));
  check('Assets serializer returns custom_data', /"custom_data": getattr\(d, "custom_data"/.test(R('../backend/routers/assets.py')));
  check('Build bumped', /build 144(b[v-z]|[c-z][a-z])/.test(R('index.html')));

  const failed = results.filter(r => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} passed`);
  process.exit(failed.length ? 1 : 0);
})();
