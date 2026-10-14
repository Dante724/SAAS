/* Build 144ah — inline add-client in Sales, live clock-toggle refresh, modern checkbox CSS.

   Run:  NODE_PATH=/tmp/b144test/node_modules node inline_client_and_clock.test.js            */
const fs = require('fs');
const path = require('path');

const results = [];
function check(name, cond, detail) {
  results.push({ name, ok: !!cond });
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${name}${cond ? '' : '  ↳ ' + (detail || '')}`);
}

const root = path.join(__dirname, '..');
const sales = fs.readFileSync(path.join(root, 'static/js/pages/sales.js'), 'utf8');
const feedback = fs.readFileSync(path.join(root, 'static/js/pages/feedback.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'static/css/design.css'), 'utf8');

// Fix 1 — Sales add-client (Build 144ak: opens the full, customizable client form
// used by Contacts — the one that renders custom fields from the Forms editor).
check('Sales has an Add-client flow', /SalesPage\.addClient\(\)/.test(sales));
check('addClient opens the full ClientsPage._clientForm', /ClientsPage\._clientForm\(null,\s*{/.test(sales));
check('POS sale sends the attached client id', /client_id:\s*this\._client\?\.id/.test(sales));

// Fix 2 — clock toggle takes effect live
check('GeneralPage.save refreshes cached profile', /App\.refreshPerms\(\)/.test(feedback));
check('GeneralPage.save re-renders the time-clock widget', /TimeClock\.refresh\(\)/.test(feedback));

// Fix 3 — modern glass/clay checkboxes
check('Global checkbox restyle present', /input\[type="checkbox"\]\s*{[^}]*appearance:\s*none/.test(css));
check('Checked state uses gradient fill', /input\[type="checkbox"\]:checked\s*{[\s\S]*?linear-gradient/.test(css));
check('Checkmark drawn via clip-path', /clip-path:\s*polygon/.test(css));
check('Dark-mode checkbox override present', /\[data-theme="dark"\]\s*input\[type="checkbox"\]/.test(css));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
if (failed.length) process.exit(1);
