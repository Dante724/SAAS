/* Build 144cw — bulk-select bar (Orderry action-rule parity, safe slice).
   String/DOM checks: BulkBar helper exists and shows/hides; Suppliers wires it. */
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const { JSDOM } = require('jsdom');

const root = path.resolve(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(root, p), 'utf8');

const bulkbar = read('static/js/bulkbar.js');
const suppliers = read('static/js/pages/suppliers.js');
const clients = read('static/js/pages/clients.js');
const assets = read('static/js/pages/assets.js');
const indexHtml = read('index.html');
const appJs = read('static/js/app.js');

// 1) BulkBar helper is a real, self-registering global.
assert(/window\.BulkBar\s*=\s*BulkBar/.test(bulkbar), 'bulkbar.js must expose window.BulkBar');
assert(/show\s*\(count, actionsHTML, onClear\)/.test(bulkbar), 'BulkBar.show signature present');
assert(/hide\s*\(\)/.test(bulkbar), 'BulkBar.hide present');

// 2) index.html includes the script.
assert(/static\/js\/bulkbar\.js/.test(indexHtml), 'index.html includes bulkbar.js');

// 3) Navigation clears any lingering bar.
assert(/BulkBar\.hide\(\)/.test(appJs), 'setContent hides the bulk bar on page change');

// 4) Every wired list page: selection plumbing + a non-destructive export.
const pages = [
  { src: suppliers, all: 'sup-all', label: 'suppliers' },
  { src: clients, all: 'cl-all', label: 'clients' },
  { src: assets, all: 'a-all', label: 'assets' },
];
for (const { src, all, label } of pages) {
  for (const needle of [all, 'toggleAll', 'toggleRow', '_syncBulk', 'exportSelected', 'BulkBar.show']) {
    assert(src.includes(needle), `${label}.js wires ${needle}`);
  }
  // Export must be non-destructive: isolate the exportSelected() body and confirm
  // it only builds a CSV blob — no delete/DELETE/POST/PATCH calls.
  const exStart = src.indexOf('exportSelected() {');   // the method def, not the onclick ref
  const exBody = src.slice(exStart, src.indexOf('\n  },', exStart));
  assert(/createObjectURL|Blob/.test(exBody), `${label} exportSelected builds a CSV blob download`);
  assert(!/\bdel\(|method:\s*['"](DELETE|POST|PATCH)|\bpost\(|\bpatch\(/.test(exBody),
    `${label} exportSelected must not perform destructive/write calls`);
}

// 5) Runtime behaviour: show builds a bar, hide removes it.
const dom = new JSDOM('<!doctype html><html><body></body></html>');
global.window = dom.window; global.document = dom.window.document;
eval(bulkbar);
window.BulkBar.show(3, '<button>Export CSV</button>', () => {});
let bar = document.getElementById('bulk-bar');
assert(bar && /3 selected/.test(bar.textContent), 'show renders count');
assert(/Export CSV/.test(bar.innerHTML), 'show renders actions');
window.BulkBar.show(0);   // zero -> auto-hide
assert(!document.getElementById('bulk-bar'), 'show(0) hides the bar');
window.BulkBar.show(2, '', null);
window.BulkBar.hide();
assert(!document.getElementById('bulk-bar'), 'hide removes the bar');

console.log('bulkbar_144cx: OK');
