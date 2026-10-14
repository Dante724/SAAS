/* Build 144ap — Products rebuild: left tree, multi-select, Actions menu, labels/barcode/QR.
   Run: NODE_PATH=/tmp/b144test/node_modules node products_rebuild.test.js */
const fs = require('fs');
const path = require('path');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const p = fs.readFileSync(path.join(__dirname, '..', 'static/js/pages/products.js'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '..', 'static/css/design.css'), 'utf8');

check('Left category rail via shared CategoryTree', /CategoryTree\.mount\(/.test(p) && /\.ct-node/.test(css) && /id="pr-tree"/.test(p));
check('Tree replaces the old dropdown', !/id="pr-cat"/.test(p));
check('Row + select-all checkboxes', /toggleAll\(this\.checked\)/.test(p) && /toggleRow\('\$\{p\.id\}'/.test(p));
check('Selection Actions hidden until checked', /_renderTopbar\(0\)/.test(p) && /_renderTopbar\(this\._selected\.size\)/.test(p));
check('One Actions dropdown (page + selection)', /pd-menu/.test(css) && /toggleMenu\(/.test(p));
check('Menu closes on outside click', /_bindMenuClose/.test(p));
check('Bulk generate barcode + QR', /bulkCode\('code128'\)/.test(p) && /bulkCode\('qr'\)/.test(p) && /barcode\/generate\?type=/.test(p));
check('Print labels via /render-labels', /printLabels/.test(p) && /\/api\/documents\/render-labels/.test(p));
check('Bulk move category / price / delete', /\/api\/catalog\/products\/bulk/.test(p) && /moveCategory/.test(p) && /bulkPrice/.test(p) && /bulkDelete/.test(p));
check('New product still opens the full form', /editModal\(\)/.test(p));
// 144au — Orderry-placed "+ Product" primary button, slide-in product form, shorter search
check('+ Product button sits beside the search (left-aligned toolbar)', /pd-toolbar/.test(p) && /btn btn-primary" onclick="ProductsPage\.editModal\(\)/.test(p) && /\.pd-toolbar\s*\{[^}]*flex-start/.test(css));
check('Product form opens as a slide-in side panel', /'modal-side-wide'/.test(p) && /\.modal-side-wide/.test(css));
check('Search + button share the toolbar row (order-agnostic, Build 144db)', /pd-toolbar[\s\S]*search-box/.test(p) && /pd-toolbar[\s\S]*ProductsPage\.editModal\(\)/.test(p));
check('No emoji in Products page', !/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(p));
// 144ax — collapsible tree adds + / + sub-category via the shared component
check('Tree has + Category and + sub-category', /CategoryTree\.add\(/.test(fs.readFileSync(path.join(__dirname, '..', 'static/js/category-tree.js'), 'utf8')) && /addSub\(/.test(fs.readFileSync(path.join(__dirname, '..', 'static/js/category-tree.js'), 'utf8')));
check('quickCreate accepts a parent', /quickCreate\(templateKey, parentId\)/.test(fs.readFileSync(path.join(__dirname, '..', 'static/js/category-manager.js'), 'utf8')));
check('Top-bar gets its own stacking context (dropdown clickable)',
  /\.topbar\s*\{[^}]*z-index:\s*100/.test(fs.readFileSync(path.join(__dirname, '..', 'static/css/shell.css'), 'utf8')));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
if (failed.length) process.exit(1);
