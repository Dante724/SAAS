/* Build 144as — Add-category side panel (parent-or-new).
   Run: NODE_PATH=/tmp/b144test/node_modules node category_add_panel.test.js */
const fs = require('fs');
const path = require('path');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const cm = fs.readFileSync(path.join(__dirname, '..', 'static/js/category-manager.js'), 'utf8');
const pr = fs.readFileSync(path.join(__dirname, '..', 'static/js/pages/products.js'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '..', 'static/css/design.css'), 'utf8');

check('CategoryManager.addPanel exists', /addPanel\s*\(/.test(cm) && /addPanelSubmit/.test(cm));
check('Panel opens as a side drawer', /'modal-side'/.test(cm) && /\.modal-side/.test(css));
check('Placement toggle: top-level vs under a parent', /apPlace\(/.test(cm) && /data-place="top"/.test(cm) && /data-place="under"/.test(cm));
check('Parent picker shown only for "under"', /cm-ap-parent-wrap/.test(cm) && /parent_id/.test(cm));
check('Preselects the clicked node as parent', /opts\.parentId/.test(cm));
check('Category rail uses the panel (shared CategoryTree)', /CategoryManager\.addPanel\(/.test(fs.readFileSync(path.join(__dirname, '..', 'static/js/category-tree.js'), 'utf8')));
check('Inline "+" in product form stacks the panel', /stack:\s*true/.test(pr));
check('No emoji in category manager', !/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(cm));
check('Drawer has slide-in animation', /@keyframes side-in/.test(css) && /seg-pill/.test(css));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
if (failed.length) process.exit(1);
