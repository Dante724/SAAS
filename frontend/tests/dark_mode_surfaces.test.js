/* Build 144az — dark-mode fix: no light "grey block" surfaces in the product form / panels.
   Run: NODE_PATH=/tmp/b144test/node_modules node dark_mode_surfaces.test.js */
const fs = require('fs');
const path = require('path');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const css = fs.readFileSync(path.join(__dirname, '..', 'static/css/design.css'), 'utf8');
const dark = (sel) => new RegExp('\\[data-theme="dark"\\]\\s*' + sel).test(css);

check('Image drop-zone darkened', dark('\\.pf-drop'));
check('Option checkbox cards darkened', dark('\\.pf-check'));
check('Prices table darkened', dark('\\.pf-prices'));
check('Placement seg-pill darkened', dark('\\.seg-pill'));
check('Insights seg toggle darkened', dark('\\.ci-seg b.on'));
// no bright-white surface left without a dark counterpart among these components
check('No bare #fff on seg-pill/ci-seg without dark override',
  (css.match(/\[data-theme="dark"\] \.seg-pill button.on/) || []).length === 1 &&
  (css.match(/\[data-theme="dark"\] \.ci-seg b.on/) || []).length === 1);

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
if (failed.length) process.exit(1);
