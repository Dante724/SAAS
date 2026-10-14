/* Build 144k — richer, editable employee profile.
   Run: NODE_PATH=<dir with jsdom> node employee_profile.test.js                    */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { JSDOM } = require('jsdom');

const results = [];
const check = (name, cond, detail) => {
  results.push(!!cond);
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${name}${cond ? '' : '  ↳ ' + (detail || '')}`);
};
const src = fs.readFileSync(
  path.join(__dirname, '..', 'static', 'js', 'pages', 'staff.js'), 'utf8');

const dom = new JSDOM('<!doctype html><html><body></body></html>',
  { url: 'http://localhost', runScripts: 'outside-only' });
const w = dom.window;
let modalHtml = '';
w.Modal = { open(h){ modalHtml = h; }, close(){ modalHtml=''; } };
w.get = async () => ([]); w.post = async () => ({}); w.patch = async () => ({}); w.del = async () => ({});
w.icon = () => ''; w.avatarHtml = () => ''; w.skeletonRows = () => ''; w.emptyRow = () => '';
w.fmt = { currency:(n)=>'$'+n, date:(s)=>s };
w.Toast = { success(){}, error(m){ console.log('ERR', m); }, info(){} };
w.Router = { register(){}, go(){}, current:'settings' };
w.setContent = ()=>{}; w.setTopbarActions = ()=>{}; w.confirm2 = async()=>true;

const ctx = vm.createContext(w);
vm.runInContext(src, ctx, { filename: 'staff.js' });
const SP = vm.runInContext('StaffPage', ctx);
SP._roles = [{ id:'r1', name:'Technician', permissions:{} }];

const emp = { id:'e1', name:'David Jonson', email:'david@test.com', phone:'+1',
  role_id:'r1', position:'Technician', hired_at:'2021-05-26', tax_id:'ITN-9',
  public_name:'David J.', note:'Reliable', wage_type:'fixed', wage_value:1500,
  commission_labour:5, commission_parts:10, weekly_hours:40, hourly_basis:'labour',
  track_job_hours:true, track_attendance:true };
SP._employees = [emp];

console.log('\nEmployee profile (detail):');
SP.empProfile('e1');
check('profile shows a prominent "Edit profile" button that opens the edit modal',
  /Edit profile/.test(modalHtml) && /StaffPage\.empModal\('e1'\)/.test(modalHtml));
check('profile shows Position, Hire date, ITN, public name',
  /Technician/.test(modalHtml) && /Hire date/.test(modalHtml) &&
  /ITN-9/.test(modalHtml) && /David J\./.test(modalHtml));
check('profile shows the Note', /Reliable/.test(modalHtml));

console.log('\nEmployee edit modal:');
SP.empModal('e1');
check('edit modal has a General info section with the new fields',
  /General info/.test(modalHtml) && /ne-position/.test(modalHtml) &&
  /ne-hired/.test(modalHtml) && /ne-taxid/.test(modalHtml) &&
  /ne-pubname/.test(modalHtml) && /ne-note/.test(modalHtml));
check('edit modal pre-fills existing values',
  modalHtml.includes('value="Technician"') && modalHtml.includes('value="2021-05-26"') &&
  modalHtml.includes('value="ITN-9"'));
check('salary + clock + working-hours sections are still present (nothing removed)',
  /Salary &amp; commission/.test(modalHtml) && /Time tracking/.test(modalHtml) &&
  /Hourly pay based on/.test(modalHtml) && /Expected hours \/ week/.test(modalHtml));

// submit collects the new fields
console.log('\nSubmit collects the new fields:');
// build a DOM the submit reads from
const ids = { 'ne-name':'David Jonson','ne-email':'david@test.com','ne-phone':'+1',
  'ne-role':'r1','ne-wtype':'fixed','ne-wval':'1500','ne-clab':'5','ne-cpart':'10',
  'ne-whrs':'40','ne-hbasis':'labour','ne-position':'Lead Tech','ne-hired':'2022-02-02',
  'ne-taxid':'ITN-42','ne-pubname':'D. Jonson','ne-note':'Updated' };
const container = w.document.createElement('div');
Object.entries(ids).forEach(([id,val]) => {
  const el = w.document.createElement(id==='ne-note'?'textarea':'input');
  el.id = id; el.value = val; container.appendChild(el);
});
// checkboxes
['ne-track-job','ne-track-att'].forEach(id => { const c=w.document.createElement('input'); c.type='checkbox'; c.id=id; c.checked=true; container.appendChild(c); });
w.document.body.appendChild(container);
let sent = null;
w.patch = async (url, body) => { sent = body; return {}; };
(async () => {
  await SP.submitEmp('e1');
  check('submitted body includes position/hired_at/tax_id/public_name/note',
    sent && sent.position==='Lead Tech' && sent.hired_at==='2022-02-02' &&
    sent.tax_id==='ITN-42' && sent.public_name==='D. Jonson' && sent.note==='Updated',
    JSON.stringify(sent));

  const failed = results.filter(r => !r).length;
  console.log(failed ? `\nFAILURES ❌ (${failed})` : '\nALL PASS ✅');
  process.exit(failed ? 1 : 0);
})();
