/* Build 144m — Finance → Payroll accruals tab.
   Run: NODE_PATH=<dir with jsdom> node payroll_accruals_page.test.js               */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { JSDOM } = require('jsdom');

const results = [];
const check = (name, cond, detail) => {
  results.push(!!cond);
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${name}${cond ? '' : '  ↳ ' + (detail || '')}`);
};
const read = (p) => fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'pages', p), 'utf8');

/* static: Finance wires the accruals tab */
console.log('\nFinance tab wiring:');
const otherSrc = read('other.js');
check('Finance has a Payroll accruals tab that mounts PayrollAccrualsPage',
  /Payroll accruals/.test(otherSrc) && /PayrollAccrualsPage\.mountInto/.test(otherSrc));

/* behavioural */
console.log('\nPayroll accruals page:');
const dom = new JSDOM('<!doctype html><html><body><div id="content"></div><div id="fin-body"></div></body></html>',
  { url: 'http://localhost', runScripts: 'outside-only' });
const w = dom.window;
const posted = [];
let runs = { items: [
  { id:'run1', employee_id:'e1', employee_name:'Acc Ann', period_start:'2026-06-01', period_end:'2026-06-30',
    base_salary:1000, commissions_total:0, hourly_total:0, bonuses_total:100, penalties_total:40,
    gross_total:1060, status:'accrued', accrued_at:'2026-07-01', paid_at:null },
  { id:'run2', employee_id:'e2', employee_name:'Paid Pat', period_start:'2026-05-01', period_end:'2026-05-31',
    base_salary:800, commissions_total:0, hourly_total:0, bonuses_total:0, penalties_total:0,
    gross_total:800, status:'paid', accrued_at:'2026-06-01', paid_at:'2026-06-05' },
], totals:{ base_salary:1800, commissions_total:0, hourly_total:0, bonuses_total:100, penalties_total:40, gross_total:1860 } };

w.get = async () => runs;
w.post = async (url) => { posted.push(url); return {}; };
w.confirm2 = async () => true;
w.fmt = { currency:(n)=>'$'+Number(n||0).toFixed(2), date:(s)=>s };
w.Toast = { success(){}, error(m){ console.log('ERR', m); }, info(){} };
w.setContent = (h)=>{ w.document.getElementById('content').innerHTML = h; };
w.icon = ()=> '';

const ctx = vm.createContext(w);
vm.runInContext(read('payrollcalc.js'), ctx, { filename:'payrollcalc.js' });
const PA = vm.runInContext('PayrollAccrualsPage', ctx);

(async () => {
  let err = null;
  try { await PA.mountInto('fin-body'); await new Promise(r=>setTimeout(r,10)); } catch (e) { err = e; }
  check('accruals page renders without error', !err, err && err.message);
  const body = w.document.getElementById('fin-body').innerHTML;
  check('columns include Base/Commission/Hourly/Bonuses/Penalties/Gross/Status',
    /Base<\/th>/.test(body) && /Commission<\/th>/.test(body) && /Hourly<\/th>/.test(body) &&
    /Bonuses<\/th>/.test(body) && /Penalties<\/th>/.test(body) && /Gross<\/th>/.test(body) &&
    /Status<\/th>/.test(body));
  check('accrued run shows employee, gross and a Mark paid button',
    /Acc Ann/.test(body) && /1060\.00/.test(body) && /Mark paid/.test(body));
  check('a paid run shows the paid state, not a Mark paid button',
    /Paid Pat/.test(body) && /paid 2026-06-05/.test(body));
  check('totals footer present', /Total — 2/.test(body) && /1860\.00/.test(body));

  await PA.markPaid('run1');
  check('Mark paid posts to /api/payroll/runs/{id}/pay',
    posted.some(u => u === '/api/payroll/runs/run1/pay'), posted.join(','));

  const failed = results.filter(r => !r).length;
  console.log(failed ? `\nFAILURES ❌ (${failed})` : '\nALL PASS ✅');
  process.exit(failed ? 1 : 0);
})();
