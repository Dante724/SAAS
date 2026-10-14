/* Build 144ay — Company Insights builder (frontend).
   Run: NODE_PATH=/tmp/b144test/node_modules node company_insights.test.js */
const fs = require('fs');
const path = require('path');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const R = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');
const ins = R('static/js/pages/insights.js');
const reports = R('static/js/pages/reports.js');
const idx = R('index.html');
const css = R('static/css/design.css');

check('InsightsPage registered as its own route', /Router\.register\('insights'/.test(ins) && /insights\.js/.test(idx));
check('Diagrams / Trends toggle + period', /setView\('diagrams'\)/.test(ins) && /setView\('trends'\)/.test(ins) && /_fillPeriod/.test(ins));
check('Reads meta + cards + live data', /insights\/meta/.test(ins) && /insights\/cards/.test(ins) && /insights\/data/.test(ins));
check('Add-diagram builder (subject/metric/group)', /addModal/.test(ins) && /ci-subject/.test(ins) && /ci-metric/.test(ins) && /ci-group/.test(ins) && /_syncBuilder/.test(ins));
check('Renders via Charts (bars + line) and table', /Charts\.bars/.test(ins) && /Charts\.line/.test(ins) && /_table\(/.test(ins));
check('Save + delete cards', /post\('\/api\/reports\/insights\/cards'/.test(ins) && /del\('\/api\/reports\/insights\/cards\//.test(ins));
check('Entry point from Reports (no new sidebar)', /Router\.go\('insights'\)/.test(reports));
check('Insights CSS present', /\.ci-grid/.test(css) && /\.ci-card/.test(css) && /\.ci-seg/.test(css));
check('No emoji', !/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(ins));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
if (failed.length) process.exit(1);
