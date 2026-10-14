/* Build 144bf — Job Profitability report (Reports only).
   Run: node job_profitability_144bf.test.js */
const fs = require('fs');
const path = require('path');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const R = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');
const pf = R('static/js/pages/profitability.js');
const reports = R('static/js/pages/reports.js');
const idx = R('index.html');
const css = R('static/css/design.css');
const orders = R('static/js/pages/orders.js');

check('Page registered as route', /Router\.register\('profitability'/.test(pf) && /window\.ProfitPage/.test(pf));
check('Registered in index.html', /pages\/profitability\.js/.test(idx));
check('Entry from Reports toolbar', /Router\.go\('profitability'\)/.test(reports));
check('Hits job-profitability endpoint w/ filters', /\/api\/reports\/job-profitability\?/.test(pf) && /location_id/.test(pf));
check('KPI cards + margin sign', /pf-kpi/.test(pf) && /t\.margin/.test(pf));
check('Location rollup + WO table', /by_location/.test(pf) && /pf-table/.test(pf));
check('No-labour flag surfaced', /has_labour/.test(pf) && /no clocked labour/.test(pf));
check('Negative margin tint', /pf-neg/.test(pf) && /\.pf-neg/.test(css));
check('Row opens the WO', /OrdersPage\.viewOrder/.test(pf));
check('Read-only: no WO-page profitability tab added', !/data-panel="profit/.test(orders) && !/woTab\(event,'profit/.test(orders));
check('Styled + no emoji', /\.pf-kpi/.test(css) && !/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(pf));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
