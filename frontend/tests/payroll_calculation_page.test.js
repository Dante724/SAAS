/* Build 144h — Finance → Payroll calculation page.
   Run: NODE_PATH=<dir with jsdom> node payroll_calculation_page.test.js           */
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

/* static: Finance page wires the payroll tab */
console.log('\nFinance tab wiring:');
const otherSrc = read('other.js');
check('Finance has a Payroll calculation tab that mounts PayrollCalcPage',
  /Payroll calculation/.test(otherSrc) && /PayrollCalcPage\.mountInto/.test(otherSrc));

/* behavioural: the payroll page renders and supports the drawer + actions */
console.log('\nPayroll calculation page:');
const dom = new JSDOM('<!doctype html><html><body><div id="content"></div><div id="fin-body"></div><div id="topbar-actions"></div></body></html>',
  { url: 'http://localhost', runScripts: 'outside-only' });
const w = dom.window;

const posted = [];
const calc = { month:'2026-06', items:[
  { employee_id:'e1', name:'Brian', position:'Technician', base_salary:0, commissions_total:65.72,
    penalties_total:0, bonuses_total:0, computed_total:65.72, accrual_status:null },
  { employee_id:'e2', name:'David', position:'Technician', base_salary:1500, commissions_total:0,
    penalties_total:0, bonuses_total:0, computed_total:1500, accrual_status:null },
], totals:{ base_salary:1500, commissions_total:65.72, penalties_total:0, bonuses_total:0, computed_total:1565.72 } };
let detail = { employee_id:'e1', name:'Brian', month:'2026-06', base_salary:0,
  commissions:[ { id:'c1', date:'2026-06-29', amount:30.93, note:'Order E212 (Car Audi A1)' } ],
  commissions_total:65.72, hourly_total:0, penalties:[], penalties_total:0,
  bonuses:[], bonuses_total:0, computed_total:65.72 };

w.get = async (url) => {
  if (url.includes('/api/payroll/calculation/detail')) return detail;
  if (url.includes('/api/payroll/calculation')) return calc;
  return { items: [] };
};
w.post = async (url, body) => { posted.push({ url, body });
  if (url.includes('/adjustment')) { detail = { ...detail, bonuses:[{ id:'b1', date:'2026-06-15', amount:200, note:body.note }], bonuses_total:200, computed_total:265.72 }; }
  return { id:'x' }; };
w.del = async () => ({}); w.put = async () => ({});
w.confirm2 = async () => true;
w.fmt = { currency:(n)=>'$'+Number(n||0).toFixed(2), date:(s)=>s };
w.Toast = { success(){}, error(m){ console.log('TOAST ERR:', m); }, info(){} };
let modalHtml = '';
w.Modal = { open(html){ modalHtml = html; }, close(){ modalHtml=''; } };
w.setContent = (h)=>{ w.document.getElementById('content').innerHTML = h; };
w.setTopbarActions = ()=>{};
w.icon = ()=> '';

const ctx = vm.createContext(w);
vm.runInContext(read('payrollcalc.js'), ctx, { filename:'payrollcalc.js' });
const PC = vm.runInContext('PayrollCalcPage', ctx);

(async () => {
  PC._month = '2026-06';
  let err = null;
  try { await PC.mountInto('fin-body'); await new Promise(r=>setTimeout(r,20)); } catch (e) { err = e; }
  check('payroll page renders without error', !err, err && err.message);

  const body = w.document.getElementById('fin-body').innerHTML;
  check('table has Base / Commission / Penalties / Bonuses / Total columns',
    /Base<\/th>/.test(body) && /Commission<\/th>/.test(body) && /Penalties<\/th>/.test(body) &&
    /Bonuses<\/th>/.test(body) && /Total<\/th>/.test(body));
  check('employees are listed with their totals', /Brian/.test(body) && /David/.test(body));
  check('a totals footer row is shown', /Total — 2/.test(body) && /1565\.72|1565\.72/.test(body.replace(/,/g,'')));
  check('month label is shown', /June 2026/.test(body));

  // open the per-employee drawer
  err = null;
  try { await PC.openDetail('e1'); await new Promise(r=>setTimeout(r,10)); } catch (e) { err = e; }
  check('opening an employee drawer does not throw', !err, err && err.message);
  check('drawer itemizes commissions (Order E212)', /Order E212/.test(modalHtml));
  check('drawer has +Penalty and +Bonus buttons', /\+ Penalty/.test(modalHtml) && /\+ Bonus/.test(modalHtml));
  check('drawer has a Create accrual action', /Create accrual/.test(modalHtml));

  // add a bonus via the small modal
  PC.adjModal('e1', 'bonus');
  check('add-bonus modal has amount + comment fields', /adj-amt/.test(modalHtml) && /adj-note/.test(modalHtml));
  // simulate typing then saving
  w.document.getElementById('content').insertAdjacentHTML('beforeend',
    '<input id="adj-amt" value="200"><input id="adj-note" value="Great month">');
  await PC.saveAdj('e1', 'bonus');
  await new Promise(r=>setTimeout(r,10));
  check('saving a bonus POSTs to /api/payroll/adjustment with kind+amount+month',
    posted.some(p => p.url.includes('/adjustment') && p.body.kind==='bonus' &&
      p.body.amount===200 && p.body.month==='2026-06'), JSON.stringify(posted));

  // accrual posts to the accrue endpoint
  posted.length = 0;
  await PC.accrue('e2');
  check('Create accrual POSTs to /api/payroll/calculation/accrue',
    posted.some(p => p.url.includes('/calculation/accrue') && p.body.employee_id==='e2'));

  const failed = results.filter(r => !r).length;
  console.log(failed ? `\nFAILURES ❌ (${failed})` : '\nALL PASS ✅');
  process.exit(failed ? 1 : 0);
})();
