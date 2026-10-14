/* Build 144aj — Sales uses the searchable ClientPicker; client profile has a
   searchable Purchases tab (what they bought / what we sold).

   Run:  NODE_PATH=/tmp/b144test/node_modules node sales_picker_and_purchases.test.js   */
const fs = require('fs');
const path = require('path');

const results = [];
function check(name, cond, detail) {
  results.push({ name, ok: !!cond });
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${name}${cond ? '' : '  ↳ ' + (detail || '')}`);
}

const root = path.join(__dirname, '..');
const sales = fs.readFileSync(path.join(root, 'static/js/pages/sales.js'), 'utf8');
const clients = fs.readFileSync(path.join(root, 'static/js/pages/clients.js'), 'utf8');

// Sales POS (144an): Add-client opens the full customizable Contacts client form
check('Sales has an Add client flow', /SalesPage\.addClient\(\)/.test(sales));
check('addClient opens the full client form (stacked)',
  /ClientsPage\._clientForm\(null,\s*{\s*stack:\s*true/.test(sales));
check('New client attaches to the POS sale', /onCreated:[\s\S]*this\._client\s*=\s*{\s*id:\s*cli\.id/.test(sales));
check('POS sale posts the attached client id', /client_id:\s*this\._client\?\.id/.test(sales));
// The client form itself is customizable via the Forms editor (custom fields)
check('Client form renders custom fields', /CustomFields\.render\(this\._clientFields/.test(clients));
check('_clientForm supports stack + onCreated callback',
  /_clientForm\(c,\s*opts\s*=\s*{}\)/.test(clients) && /this\._createCb\s*=\s*opts\.onCreated/.test(clients));

// Client profile: Purchases tab
check('Purchases tab button present', /cTab\(event,'purchases'\)/.test(clients));
check('Purchases panel container present', /data-cpanel="purchases"/.test(clients));
check('cTab lazy-loads purchases', /panel === 'purchases'\)\s*this\._loadPurchases\(\)/.test(clients));
check('_loadPurchases fetches the endpoint', /get\(`\/api\/clients\/\$\{this\._activeClient\}\/purchases`\)/.test(clients));
check('Purchases tab is searchable', /cpur-search/.test(clients) && /_renderPurchases/.test(clients));
check('Shows spend summary (total spent)', /Total spent/.test(clients));
check('Row can jump to its source work order', /OrdersPage\.viewOrder\('\$\{p\.source_id\}'\)/.test(clients));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
if (failed.length) process.exit(1);
