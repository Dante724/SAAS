/* Build 144j — the time-clock widget shows only the clocks the employee is set to see.
   Run: NODE_PATH=<dir with jsdom> node clock_visibility.test.js                     */
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
  path.join(__dirname, '..', 'static', 'js', 'pages', 'timesheets.js'), 'utf8');

const dom = new JSDOM('<!doctype html><html><body></body></html>',
  { url: 'http://localhost', runScripts: 'outside-only' });
const w = dom.window;
w.get = async (url) => url.includes('/shift/active') ? { shift: null } : { active: null };
w.post = async () => ({}); w.Toast = { success(){}, error(){}, info(){} };
w.Modal = { open(){}, close(){} }; w.Router = { current:null, register(){}, go(){} };
w.setContent = ()=>{}; w.setTopbarActions = ()=>{}; w.icon = ()=> ''; w.fmt = { currency:(n)=>''+n, date:()=>'' };
w.skeletonRows = ()=> ''; w.emptyRow = ()=> ''; w.confirm2 = async()=>true; w.get_ = null;
w.App = { me: null };
const ctx = vm.createContext(w);
vm.runInContext(src, ctx, { filename: 'timesheets.js' });
const TC = vm.runInContext('TimeClock', ctx);

async function renderWith(me) {
  w.App.me = me;
  // fresh widget element each time
  const old = w.document.getElementById('tc-widget');
  if (old) old.remove();
  const el = w.document.createElement('div');
  el.id = 'tc-widget'; el.className = 'tc-widget';
  w.document.body.appendChild(el);
  // 144dm: the clock labels live in the expandable popover — open it so the
  // visibility flags (which clocks appear) can be asserted against its markup.
  TC._open = true;
  await TC.refresh();
  await new Promise(r => setTimeout(r, 5));
  return el;
}

(async () => {
  // both clocks (default)
  let el = await renderWith({ track_job_hours: true, track_attendance: true });
  check('both on → job timer AND attendance both render',
    /No timer running/.test(el.innerHTML) && /Not clocked in|On shift/.test(el.innerHTML) &&
    el.style.display !== 'none');

  // attendance only
  el = await renderWith({ track_job_hours: false, track_attendance: true });
  check('attendance only → shows the shift clock, hides the job timer',
    !/No timer running/.test(el.innerHTML) && /Not clocked in|On shift/.test(el.innerHTML));

  // job only
  el = await renderWith({ track_job_hours: true, track_attendance: false });
  check('job only → shows the job timer, hides the shift clock',
    /No timer running/.test(el.innerHTML) && !/Not clocked in|On shift/.test(el.innerHTML));

  // neither → widget hidden entirely
  el = await renderWith({ track_job_hours: false, track_attendance: false });
  check('both off → the whole widget is hidden', el.style.display === 'none');

  // unknown (me not loaded) → default to showing both (no regression)
  el = await renderWith(null);
  check('flags unknown → defaults to showing both clocks',
    /No timer running/.test(el.innerHTML) && /Not clocked in|On shift/.test(el.innerHTML));

  const failed = results.filter(r => !r).length;
  console.log(failed ? `\nFAILURES ❌ (${failed})` : '\nALL PASS ✅');
  process.exit(failed ? 1 : 0);
})();
