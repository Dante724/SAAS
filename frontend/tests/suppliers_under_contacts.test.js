/* Build 144c — Suppliers consolidated under Contacts.
   - No top-level "Suppliers" nav link.
   - Contacts > Suppliers tab renders the rich supplier workspace (list +
     Incoming stock), fed by /api/suppliers (not a second contact-filtered list).
   Run: NODE_PATH=<dir with jsdom> node suppliers_under_contacts.test.js         */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { JSDOM } = require('jsdom');

const results = [];
const check = (name, cond, detail) => {
  results.push(!!cond);
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${name}${cond ? '' : '  ↳ ' + (detail || '')}`);
};
const root = path.join(__dirname, '..');
const readJs = (p) => fs.readFileSync(path.join(root, 'static', 'js', p), 'utf8');

/* ── static: index.html no longer has a Suppliers nav link ── */
console.log('\nSidebar:');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
check('no top-level Suppliers nav link',
  !/data-page="suppliers"/.test(html));
check('Contacts nav link is still present', /data-page="clients"/.test(html));

/* ── behavioural: Contacts > Suppliers renders the rich supplier UI ── */
console.log('\nContacts > Suppliers tab:');
const dom = new JSDOM(`<!doctype html><html><body>
  <div id="content"></div><div id="topbar-actions"></div><div id="page-title"></div>
</body></html>`, { url: 'http://localhost', runScripts: 'outside-only' });
const w = dom.window;
w.Element.prototype.scrollIntoView = function () {};

const calls = [];
w.get = async (url) => {
  calls.push(url);
  if (url.includes('/api/suppliers/purchase-orders/incoming')) return { items: [] };
  if (url.startsWith('/api/suppliers')) return { items: [
    { id:'s1', name:'TechParts Wholesale', contact_name:'Sales', email:'sales@techparts.example',
      phone:'+91 98200 11223', lead_days:3, balance:0 } ] };
  if (url.startsWith('/api/clients')) return { items: [] };
  return { items: [] };
};
w.post = async () => ({}); w.put = async () => ({}); w.del = async () => ({});
w.icon = () => ''; w.avatarHtml = () => ''; w.skeletonRows = () => ''; w.emptyRow = () => '';
w.fmt = { currency:(n)=>'₹'+n, date:()=>'', datetime:()=>'', setBase(){} };
w.Toast = { success(){}, error(m){ console.log('TOAST ERR:', m); }, info(){} };
w.Modal = { open(){}, close(){}, closeAll(){} };
w.confirm2 = async () => true;
w.setContent = (h) => { w.document.getElementById('content').innerHTML = h; };
w.setTopbarActions = (h) => { w.document.getElementById('topbar-actions').innerHTML = h; };
const routed = [];
w.Router = { register(){}, go:(p)=>{ routed.push(p); }, current:'clients' };

const ctx = vm.createContext(w);
vm.runInContext(readJs('pages/suppliers.js'), ctx, { filename:'suppliers.js' });
vm.runInContext(readJs('pages/clients.js'), ctx, { filename:'clients.js' });
const ClientsPage = vm.runInContext('ClientsPage', ctx);
const SuppliersPage = vm.runInContext('SuppliersPage', ctx);

(async () => {
  let err = null;
  try { await ClientsPage.setRole('supplier'); await new Promise(r=>setTimeout(r,30)); }
  catch (e) { err = e; }
  check('rendering the Suppliers tab does not throw', !err, err && err.message);

  const content = w.document.getElementById('content').innerHTML;
  const topbar = w.document.getElementById('topbar-actions').innerHTML;

  check('the three contact tabs are still shown (All/Clients/Suppliers)',
    /All<\/button>/.test(content) && /Clients<\/button>/.test(content) && /Suppliers<\/button>/.test(content));
  check('rich supplier list is rendered (TechParts Wholesale)',
    /TechParts Wholesale/.test(content));
  check('Incoming stock sub-tab is present under Suppliers',
    /Incoming stock/.test(content));
  check('Price list / Balance actions present (rich page, not plain contact list)',
    /Price list/.test(content) && /SuppliersPage\.balanceModal/.test(content));
  check('topbar shows "+ New supplier"', /\+ New supplier/.test(topbar));
  check('data came from /api/suppliers (no duplicate contact-filtered call)',
    calls.some(u => u.startsWith('/api/suppliers')) &&
    !calls.some(u => /\/api\/clients\?.*role=supplier/.test(u)),
    calls.join(' | '));

  // switching to Incoming stock keeps everything inside the Contacts body
  await SuppliersPage.go('incoming');
  await new Promise(r=>setTimeout(r,20));
  const c2 = w.document.getElementById('content').innerHTML;
  check('Incoming stock view renders inside Contacts (embed intact)',
    /Nothing on order|Reserved for|Goods on their way/.test(c2) && /contacts-sup-body|Incoming stock/.test(c2));

  // going back to the All tab clears the embed target
  await ClientsPage.setRole('all');
  check('leaving Suppliers clears the embed target', SuppliersPage._embedBody === null);

  const failed = results.filter(r => !r).length;
  console.log(failed ? `\nFAILURES ❌ (${failed})` : '\nALL PASS ✅');
  process.exit(failed ? 1 : 0);
})();
