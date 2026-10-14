/* Build 144g — Payroll rules moved under Settings → Employees & roles.
   Run: NODE_PATH=<dir with jsdom> node payroll_rules_under_employees.test.js     */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { JSDOM } = require('jsdom');

const results = [];
const check = (name, cond, detail) => {
  results.push(!!cond);
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${name}${cond ? '' : '  ↳ ' + (detail || '')}`);
};
const jsDir = path.join(__dirname, '..', 'static', 'js', 'pages');
const read = (p) => fs.readFileSync(path.join(jsDir, p), 'utf8');

/* ── static: standalone rail item gone ── */
console.log('\nSettings rail:');
const settingsSrc = read('settings.js');
check('no standalone "Payroll rules" rail item',
  !/key:\s*'payroll'/.test(settingsSrc) && !/label:\s*'Payroll rules'/.test(settingsSrc));

/* ── behavioural: Employees & roles has a Payroll rules tab ── */
console.log('\nEmployees & roles → Payroll rules tab:');
const dom = new JSDOM('<!doctype html><html><body><div id="content"></div><div id="topbar-actions"></div></body></html>',
  { url: 'http://localhost', runScripts: 'outside-only' });
const w = dom.window;

const calls = [];
w.get = async (url) => {
  calls.push(url);
  if (url.startsWith('/api/payroll/rules')) return { items: [
    { id:'r1', name:'Labor commission', event:'labor_performed', calc_type:'percent',
      percent_basis:'total', amount:10, coefficient:1, employee_id:null, category:null,
      calc_timing:'on_close', term_condition:'any', rule_level:'generic' } ], events:[
    'order_created','order_closed','labor_performed','product_sold','assignee_labor','assignee_product','task_closed'] };
  if (url.startsWith('/api/staff/employees')) return [];
  if (url.startsWith('/api/settings/rbac/roles')) return { items: [] };
  if (url.startsWith('/api/settings/rbac/catalog')) return { catalog:{}, templates:[] };
  if (url.startsWith('/api/statuses')) return { items: [] };
  if (url.startsWith('/api/forms/types')) return { items: [] };
  return { items: [] };
};
w.post = async () => ({}); w.put = async () => ({}); w.del = async () => ({});
w.icon = () => ''; w.skeletonRows = () => ''; w.emptyRow = () => ''; w.avatarHtml = () => '';
w.fmt = { currency:(n)=>'₹'+n, date:()=>'', datetime:()=>'' };
w.Toast = { success(){}, error(m){ console.log('TOAST ERR:', m); }, info(){} };
w.Modal = { open(){}, close(){} };
w.confirm2 = async () => true;
w.setTopbarActions = (h) => { w.document.getElementById('topbar-actions').innerHTML = h || ''; };
w.setContent = (h) => { w.document.getElementById('content').innerHTML = h; };
w.Router = { register(){}, go(){}, current:'settings' };

const ctx = vm.createContext(w);
vm.runInContext(read('payrollsettings.js'), ctx, { filename:'payrollsettings.js' });
vm.runInContext(read('staff.js'), ctx, { filename:'staff.js' });
const StaffPage = vm.runInContext('StaffPage', ctx);
const PayrollSettingsPage = vm.runInContext('PayrollSettingsPage', ctx);

(async () => {
  // default (Employees) still works
  let err = null;
  try { await StaffPage.load(); await new Promise(r=>setTimeout(r,20)); } catch (e) { err = e; }
  check('Employees & roles page still loads', !err, err && err.message);
  const tabsHtml = w.document.getElementById('content').innerHTML;
  check('the Payroll rules tab is present next to Employees / Roles',
    /Employees<\/button>/.test(tabsHtml) && /Roles/.test(tabsHtml) && /Payroll rules<\/button>/.test(tabsHtml));

  // switch to Payroll rules
  err = null;
  try { await StaffPage.setTab('payroll'); await new Promise(r=>setTimeout(r,20)); } catch (e) { err = e; }
  check('switching to Payroll rules does not throw', !err, err && err.message);
  const body = w.document.getElementById('content').innerHTML;
  check('the commission-rules UI renders inside the page (rule shown)',
    /Labor commission/.test(body) && /Commission rules accrue automatically/.test(body));
  check('payroll rules were fetched from /api/payroll/rules',
    calls.some(u => u.startsWith('/api/payroll/rules')));
  check('the top-bar action becomes "+ New rule" on this tab',
    /New rule/.test(w.document.getElementById('topbar-actions').innerHTML));
  check('primaryAction on the payroll tab opens the rule modal',
    typeof StaffPage.primaryAction === 'function');

  // switching back to Employees clears the embed target
  await StaffPage.setTab('employees'); await new Promise(r=>setTimeout(r,20));
  check('leaving Payroll rules clears the embed target',
    PayrollSettingsPage._embedBody === null);

  const failed = results.filter(r => !r).length;
  console.log(failed ? `\nFAILURES ❌ (${failed})` : '\nALL PASS ✅');
  process.exit(failed ? 1 : 0);
})();
