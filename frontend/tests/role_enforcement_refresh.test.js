/* Build 144L — unchecking a permission and saving takes effect in the session.
   Covers: (1) the role editor no longer collides with the enforcement attribute,
   (2) saving persists unchecked = false, (3) App.refreshPerms re-applies live.
   Run: NODE_PATH=<dir with jsdom> node role_enforcement_refresh.test.js          */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { JSDOM } = require('jsdom');

const results = [];
const check = (name, cond, detail) => {
  results.push(!!cond);
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${name}${cond ? '' : '  ↳ ' + (detail || '')}`);
};
const read = (p) => fs.readFileSync(path.join(__dirname, '..', 'static', 'js', p), 'utf8');

const dom = new JSDOM(`<!doctype html><html><body>
  <div class="nav-link" data-page="clients">Contacts</div>
  <button data-perm="can_create_tickets">+ Order</button>
  <div id="modal-host"></div>
</body></html>`, { url: 'http://localhost', runScripts: 'outside-only' });
const w = dom.window;

// build the real App permission object from app.js
const appSrc = read('app.js');
const start = appSrc.indexOf('  perms: {},');
const end = appSrc.indexOf('  async boot()');
let mePerms = { permissions: {}, is_owner: false };
w.get = async (url) => {
  if (url.includes('/api/auth/me')) return mePerms;
  if (url.includes('/api/settings/rbac/catalog')) return { catalog: {
      General: [{ code: 'can_view_client_info', label: 'View the client info' },
                { code: 'can_create_tickets', label: 'Create tickets' }] }, templates: [] };
  if (url.includes('/api/settings/rbac/roles')) return { items: [
      { id: 'r1', name: 'admin', permissions: { all: 'write' } }] };
  if (url.includes('/api/staff/employees')) return [];
  return { items: [] };
};
vm.runInContext(`var App = { ${appSrc.slice(start, end)} };`, w);

// stubs for staff.js
let modalHtml = '';
w.Modal = { open(h){ modalHtml = h; w.document.getElementById('modal-host').innerHTML = h; }, close(){ modalHtml=''; } };
let putBody = null;
w.put = async (url, body) => { putBody = body; return {}; };
w.post = async () => ({}); w.del = async () => ({});
w.Toast = { success(){}, error(m){ console.log('ERR', m); }, info(){} };
w.icon = () => ''; w.avatarHtml = () => ''; w.skeletonRows = () => ''; w.emptyRow = () => '';
w.fmt = { currency:(n)=>'$'+n, date:()=>'' };
w.Router = { register(){}, go(){}, current:'settings' };
w.setContent = ()=>{}; w.setTopbarActions = ()=>{}; w.confirm2 = async()=>true;

vm.runInContext(read('pages/staff.js'), w);
const App = vm.runInContext('App', w);
const SP = vm.runInContext('StaffPage', w);
SP._roles = [{ id:'r1', name:'admin', permissions:{ all:'write' } }];
SP._catalog = { catalog: {
  General: [{ code:'can_view_client_info', label:'View the client info' },
            { code:'can_create_tickets', label:'Create tickets' }] }, templates: [] };

(async () => {
  console.log('\nRole editor no longer collides with enforcement:');
  SP.roleModal('r1');
  check('permission checkboxes use data-permcode (not data-perm)',
    /data-permcode="can_create_tickets"/.test(modalHtml) && !/data-perm="can_/.test(modalHtml));

  // enforcement must NOT hide the role-editor checkboxes even for a denied perm
  App.isOwner = false; App.perms = { can_create_tickets: false };
  App.applyPermissions(w.document);
  const cbLabel = [...w.document.querySelectorAll('.perm-row')]
    .find(l => l.querySelector('[data-permcode="can_create_tickets"]'));
  check('a denied permission\'s own checkbox stays visible in the editor',
    cbLabel && cbLabel.style.display !== 'none');
  check('the real +Order control (data-perm) IS hidden when denied',
    w.document.querySelector('[data-perm="can_create_tickets"]').style.display === 'none');

  console.log('\nSaving unchecked = false, then refreshPerms applies live:');
  // simulate the editor DOM: two checkboxes, "create tickets" unchecked
  w.document.getElementById('modal-host').innerHTML =
    '<input id="rl-name" value="admin">' +
    '<input type="checkbox" data-permcode="can_view_client_info" checked>' +
    '<input type="checkbox" data-permcode="can_create_tickets">' +
    '<select id="rl-contact"><option value="all" selected></option></select>' +
    '<select id="rl-order"><option value="all" selected></option></select>' +
    '<input id="rl-payvis" value="">';

  // when the role saves, /me will now report the denial
  mePerms = { permissions: { can_view_client_info: true, can_create_tickets: false }, is_owner: false };
  // reset visible state to prove refresh hides it
  w.document.querySelector('[data-perm="can_create_tickets"]').style.display = '';
  App.perms = {};

  await SP.submitRole('r1');
  await new Promise(r => setTimeout(r, 10));
  check('saved permissions record the unchecked box as false',
    putBody && putBody.permissions.can_create_tickets === false &&
    putBody.permissions.can_view_client_info === true, JSON.stringify(putBody && putBody.permissions));
  check('after save, App.perms reflects the new denial (refreshPerms ran)',
    App.perms.can_create_tickets === false);
  check('the denied control is now hidden in the live session',
    w.document.querySelector('[data-perm="can_create_tickets"]').style.display === 'none');

  const failed = results.filter(r => !r).length;
  console.log(failed ? `\nFAILURES ❌ (${failed})` : '\nALL PASS ✅');
  process.exit(failed ? 1 : 0);
})();
