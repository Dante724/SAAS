/* Build 144ad — inline-SVG charts util + Reports table↔chart toggle.
   Run: NODE_PATH=<jsdom dir> node charts_and_reports_view.test.js */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { JSDOM } = require('jsdom');

const results = [];
const check = (name, cond, d) => { results.push(!!cond); console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${name}${cond ? '' : '  ↳ ' + (d || '')}`); };
const readJs = (p) => fs.readFileSync(path.join(__dirname, '..', 'static', 'js', p), 'utf8');

/* ── Charts util (pure) ── */
console.log('\nCharts util:');
const dom = new JSDOM('<!doctype html><body><div id="content"></div><div id="rep-output"></div></body>',
  { url: 'http://localhost', runScripts: 'outside-only' });
const w = dom.window;
w.window = w;
w.matchMedia = () => ({ matches: false });
w.performance = { now: () => 0 };
w.requestAnimationFrame = () => 0;
const ctx = vm.createContext(w);
vm.runInContext(readJs('charts.js'), ctx, { filename: 'charts.js' });
const Charts = vm.runInContext('window.Charts', ctx);

const bars = Charts.bars([['Jan', 10], ['Feb', 25], ['Mar', 7]]);
check('bars() returns an <svg> with a <rect> per value', /<svg/.test(bars) && (bars.match(/<rect/g) || []).length >= 3);
check('bars() renders value + label text', /Feb/.test(bars) && /25/.test(bars));
const line = Charts.line([['2026-01-01', 5], ['2026-01-02', 8], ['2026-01-03', 6]]);
check('line() returns a polyline trend', /<polyline/.test(line) && /<polygon/.test(line));
check('sparkline() is a compact svg', /class="spark"/.test(Charts.sparkline([1, 3, 2, 5])));
check('empty data → graceful message, no throw', /Nothing to chart/.test(Charts.bars([['x', 'y']])));

/* ── countUp respects reduced-motion (sets final immediately) ── */
console.log('\nMotion:');
const el = w.document.createElement('div'); el.textContent = '₹4,82,300';
w.countUp(el);
check('countUp keeps the currency prefix + final value', el.textContent === '₹4,82,300', el.textContent);

/* ── Reports page: table↔chart toggle wiring ── */
console.log('\nReports view toggle:');
w.get = async () => ({ title: 'Sold products', tab: 'Assortment', row_count: 3,
  columns: ['Product', 'Qty', 'Revenue'],
  rows: [['Screen', 4, 4000], ['Battery', 9, 1800], ['Cable', 20, 1000]] });
w.icon = () => ''; w.fmt = { currency: (n) => '₹' + n, date: () => '' };
w.Toast = { error() {} };
w.setContent = (h) => { w.document.getElementById('content').innerHTML = h; };
w.setTopbarActions = () => {};
w.Router = { register() {}, go() {} };
w.App = { locParam: () => '' };
vm.runInContext(readJs('pages/reports.js'), ctx, { filename: 'reports.js' });
const RP = vm.runInContext('ReportsPage', ctx);
RP._from = '2026-08-01'; RP._to = '2026-08-06';

(async () => {
  RP._renderReportList = () => {};   // stub (no rep-reports node in this harness)
  await RP.run('sold_products', 'Sold products');
  const out = () => w.document.getElementById('rep-output').innerHTML;
  check('a chartable report shows the Table/Chart toggle', /rep-viewtoggle/.test(out()) && /Chart<\/button>/.test(out()));
  check('default view is the table', /<table/.test(out()));
  RP.setView('chart');
  check('switching to Chart renders an SVG chart (bars for text labels)',
    /rep-chart/.test(out()) && /<svg/.test(out()) && (out().match(/<rect/g) || []).length >= 3);
  check('chart caption names the axes (first value column)', /Qty by Product/.test(out()));
  RP.setView('table');
  check('switching back restores the table', /<table/.test(out()) && !/rep-chart">/.test(out()));

  const failed = results.filter(r => !r).length;
  console.log(failed ? `\nFAILURES ❌ (${failed})` : '\nALL PASS ✅');
  process.exit(failed ? 1 : 0);
})();
