/* Build 70 — the commission-rule form exposes the new conditions (timing,
   trigger status, execution term, WO type, cancel-if-deleted) and submits them.

   Run:  NODE_PATH=../../node_modules node payroll_conditions.test.js            */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { JSDOM } = require('jsdom');

const results = [];
function check(name, cond, detail) {
  results.push({ name, ok: !!cond });
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${name}${cond ? '' : '  ↳ ' + (detail || '')}`);
}

function ctxFor(bodyHtml) {
  const dom = new JSDOM(`<!DOCTYPE html><body>${bodyHtml}</body>`, { runScripts: 'outside-only' });
  const { window } = dom;
  const ctx = {
    window, document: window.document, console, setTimeout, clearTimeout,
    get: async () => ({ items: [] }), post: async () => ({}), put: async () => ({}), del: async () => ({}),
    Toast: { success() {}, error() {}, info() {} }, confirm2: async () => true,
    fmt: { currency: n => '$' + n }, setContent: () => {}, setTopbarActions: () => {},
    Modal: { open(html) { window.document.getElementById('root').innerHTML = html; }, close() {} },
  };
  ctx.global = ctx;
  vm.createContext(ctx);
  return { ctx, window };
}

(async () => {
  console.log('Payroll rule conditions:');
  const { ctx, window } = ctxFor('<div id="root"></div><div id="pr-list"></div>');
  let posted = null;
  ctx.post = async (url, body) => { posted = { url, body }; return {}; };

  const src = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'pages', 'payrollsettings.js'), 'utf8');
  vm.runInContext(src + '\n;window.__P = PayrollSettingsPage;', ctx);
  const P = window.__P;

  // seed the lookups the form renders from
  P._statuses = [{ key: 'inprog', name: 'In progress' }, { key: 'done', name: 'Done' }];
  P._types = [{ id: 't1', name: 'Warranty' }];
  P._employees = [];
  P._events = ['labor_performed', 'product_sold'];
  P.reload = async () => {};

  P.ruleModal(null);
  const doc = window.document;
  check('form has calculation-timing select', !!doc.getElementById('pr-timing'));
  check('form has execution-term select', !!doc.getElementById('pr-term'));
  check('form has work-order-type select', !!doc.getElementById('pr-wotype'));
  check('form has cancel-if-deleted checkbox', !!doc.getElementById('pr-cancel'));
  check('cancel-if-deleted defaults to on', doc.getElementById('pr-cancel').checked);
  check('trigger-status hidden until on_status', doc.getElementById('pr-trigger-wrap').style.display === 'none');

  // choosing on_status reveals the trigger picker
  doc.getElementById('pr-timing').value = 'on_status';
  P._toggleTrigger();
  check('choosing on_status reveals trigger status', doc.getElementById('pr-trigger-wrap').style.display !== 'none');

  // fill and submit
  doc.getElementById('pr-name').value = 'Pay at in-progress';
  doc.getElementById('pr-trigger').value = 'inprog';
  doc.getElementById('pr-term').value = 'urgent';
  doc.getElementById('pr-wotype').value = 't1';
  doc.getElementById('pr-cancel').checked = false;
  await P.submit(null);

  check('submits to /api/payroll/rules', posted && posted.url === '/api/payroll/rules', posted && posted.url);
  const b = posted ? posted.body : {};
  check('sends calc_timing=on_status', b.calc_timing === 'on_status', JSON.stringify(b));
  check('sends trigger_status_key', b.trigger_status_key === 'inprog', JSON.stringify(b));
  check('sends term_condition=urgent', b.term_condition === 'urgent');
  check('sends wo_type_id', b.wo_type_id === 't1');
  check('sends cancel_if_deleted=false', b.cancel_if_deleted === false);

  // on_status without a trigger is blocked client-side
  posted = null;
  P.ruleModal(null);
  doc.getElementById('pr-timing').value = 'on_status';
  P._toggleTrigger();
  doc.getElementById('pr-trigger').innerHTML = '';   // no statuses to pick
  await P.submit(null);
  check('on_status with no trigger blocks submit', posted === null);

  // conditions summary renders in the table
  const summary = P._conds({ calc_timing: 'on_status', trigger_status_key: 'inprog',
                             term_condition: 'urgent', wo_type_id: 't1', cancel_if_deleted: true });
  check('conditions summary mentions status/term/type',
        /In progress/.test(summary) && /urgent/.test(summary) && /Warranty/.test(summary), summary);

  const ok = results.every(r => r.ok);
  console.log(ok ? '\nALL PASS ✅' : '\nFAILURES ❌');
  process.exit(ok ? 0 : 1);
})();
