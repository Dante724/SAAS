/* Browser-free UI test (jsdom) for the Duplicate → schedule flow and the
   forms-editor "Work order" scheduling section.

   1. OrdersPage.duplicateOrder() opens a modal with "Schedule from" / "Schedule to"
      datetime-local inputs, prefilled from the source order, and submitDuplicate()
      POSTs those values to /duplicate.
   2. FormsPage.loadScheduleFields() renders the two built-in scheduling fields.

   Loads the REAL orders.js and forms.js.

   Run:  NODE_PATH=../../node_modules node schedule_fields.test.js
   (requires jsdom; in CI add it to devDependencies)                            */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { JSDOM } = require('jsdom');

function baseCtx(bodyHtml) {
  const dom = new JSDOM(`<!DOCTYPE html><body>${bodyHtml}</body>`, { runScripts: 'outside-only' });
  const { window } = dom;
  const ctx = {
    window, document: window.document, console, setTimeout, clearTimeout, setInterval, clearInterval,
    get: async () => ({}), post: async () => ({}), put: async () => ({}), del: async () => ({}),
    patch: async () => ({}),
    icon: () => '', Toast: { success() {}, error() {}, info() {}, show() {} },
    confirm2: async () => true, fmt: { currency: n => '' + n, date: d => d, datetime: d => d },
    setContent: () => {}, setTopbarActions: () => {},
    Router: { register() {} }, Scheduler: { load() {}, render() {} },
    ClientPicker: { mount() {}, value: null }, CatalogPicker: { mount: async () => {}, payload: () => [] },
    Modal: { open(html) { window.document.getElementById('modal-root').innerHTML = html; }, close() {} },
    CustomFields: { fetch: async () => [], render: () => '', collect: () => ({}) },
  };
  ctx.global = ctx;
  vm.createContext(ctx);
  return { ctx, window };
}

const results = [];
function check(name, cond, detail) {
  results.push({ name, ok: !!cond, detail });
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${name}${cond ? '' : '  ↳ ' + (detail || '')}`);
}

async function testDuplicateModal() {
  const { ctx, window } = baseCtx('<div id="modal-root"></div>');
  // source order carries an existing schedule → modal should prefill it
  ctx.get = async (url) => (url === '/api/orders/o1'
    ? { id: 'o1', order_number: 'WO-1', scheduled_for: '2026-08-01T09:00:00', scheduled_to: '2026-08-01T11:30:00' }
    : {});
  let posted = null;
  ctx.post = async (url, body) => { posted = { url, body }; return { id: 'o2', order_number: 'WO-2', line_count: 3, scheduled_for: body.scheduled_for, scheduled_to: body.scheduled_to }; };

  const src = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'pages', 'orders.js'), 'utf8');
  vm.runInContext(src + '\n;window.__OrdersPage = OrdersPage;', ctx);
  const OrdersPage = window.__OrdersPage;
  OrdersPage.viewOrder = () => {};   // stub navigation

  await OrdersPage.duplicateOrder('o1');
  await new Promise(r => setTimeout(r, 10));
  const doc = window.document;
  const from = doc.getElementById('dup-from');
  const to = doc.getElementById('dup-to');

  check('duplicate modal has Schedule from input', from && from.type === 'datetime-local', 'missing #dup-from');
  check('duplicate modal has Schedule to input', to && to.type === 'datetime-local', 'missing #dup-to');
  check('Schedule from prefilled from source', from && from.value === '2026-08-01T09:00', from && from.value);
  check('Schedule to prefilled from source', to && to.value === '2026-08-01T11:30', to && to.value);

  // edit the values, submit, assert the POST body carries them
  from.value = '2026-09-10T08:00';
  to.value = '2026-09-10T10:00';
  await OrdersPage.submitDuplicate('o1');
  check('submit POSTs to /duplicate', posted && posted.url === '/api/orders/o1/duplicate', posted && posted.url);
  check('POST carries scheduled_for', posted && posted.body.scheduled_for === '2026-09-10T08:00', posted && JSON.stringify(posted.body));
  check('POST carries scheduled_to', posted && posted.body.scheduled_to === '2026-09-10T10:00', posted && JSON.stringify(posted.body));

  // reversed schedule is rejected client-side (no POST)
  posted = null;
  const { window: w2, ctx: c2 } = (() => { const b = baseCtx('<div id="modal-root"></div>'); return { window: b.window, ctx: b.ctx }; })();
  c2.get = async () => ({ id: 'o1', scheduled_for: null, scheduled_to: null });
  c2.post = async () => { posted = 'CALLED'; return {}; };
  vm.runInContext(src + '\n;window.__OrdersPage = OrdersPage;', c2);
  const OP2 = w2.__OrdersPage; OP2.viewOrder = () => {};
  await OP2.duplicateOrder('o1');
  await new Promise(r => setTimeout(r, 10));
  w2.document.getElementById('dup-from').value = '2026-09-10T10:00';
  w2.document.getElementById('dup-to').value = '2026-09-10T08:00';
  await OP2.submitDuplicate('o1');
  check('reversed schedule blocks the POST', posted === null, `posted=${posted}`);
}

function testFormsScheduleSection() {
  const { ctx, window } = baseCtx('<div id="forms-types"></div><div id="forms-fields"></div>');
  const src = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'pages', 'forms.js'), 'utf8');
  vm.runInContext(src + '\n;window.__FormsPage = FormsPage;', ctx);
  const FormsPage = window.__FormsPage;

  FormsPage.loadScheduleFields();
  const html = window.document.getElementById('forms-fields').innerHTML;
  check('forms schedule section renders "Schedule from"', /Schedule from/.test(html), 'not found');
  check('forms schedule section renders "Schedule to"', /Schedule to/.test(html), 'not found');
  check('forms schedule fields marked built-in', /built-in/.test(html), 'not found');

  // the sidebar exposes a "Work order" entity row, and _entity() maps it to 'order'
  FormsPage.types = [];
  FormsPage.activeType = 'entity:order';
  FormsPage._drawTypes();
  const side = window.document.getElementById('forms-types').innerHTML;
  check('sidebar lists a "Work order" entity row', /Work order/.test(side) && /entity:order/.test(side), 'not found');
  check('_entity() resolves order', FormsPage._entity() === 'order', FormsPage._entity());
}

(async () => {
  console.log('Duplicate → schedule modal:');
  await testDuplicateModal();
  console.log('\nForms editor → Work order scheduling:');
  testFormsScheduleSection();
  const allOk = results.every(r => r.ok);
  console.log(allOk ? '\nALL PASS ✅' : '\nFAILURES ❌');
  process.exit(allOk ? 0 : 1);
})();
