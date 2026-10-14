/* Build 144ax — shared collapsible CategoryTree (Products + Bundles), list thumbnails.
   Run: NODE_PATH=/tmp/b144test/node_modules node category_tree_and_thumbs.test.js */
const fs = require('fs');
const path = require('path');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const R = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');
const ct = R('static/js/category-tree.js');
const css = R('static/css/design.css');
const idx = R('index.html');
const products = R('static/js/pages/products.js');
const bundles = R('static/js/pages/bundles.js');
const cm = R('static/js/category-manager.js');
const core = R('static/js/core.js');
const inv = R('static/js/pages/inventory.js');
const assets = R('static/js/pages/assets.js');

// tree component
check('CategoryTree registered + collapsible', /window\.CategoryTree/.test(ct) && /idx|toggle\(/.test(ct) && /_collapsed/.test(ct) && /ct-chev/.test(css));
check('Pinned All row with count', /ct-all/.test(ct) && /All bundles/.test(ct) && /All products/.test(ct) && /\.ct-all/.test(css));
check('Counts + double-click rename + drag reparent', /ct-count/.test(ct) && /ondblclick="CategoryTree\.rename/.test(ct) && /parent_id: targetId/.test(ct));
check('Registered in index.html', /category-tree\.js/.test(idx));

// both pages use it
check('Products mounts the tree (entity product)', /CategoryTree\.mount\([\s\S]*entity: 'product'/.test(products) && !/_renderTree/.test(products));
check('Bundles mounts the tree (entity bundle)', /CategoryTree\.mount\([\s\S]*entity: 'bundle'/.test(bundles) && /id="bd-tree"/.test(bundles));
check('Bundles form has category + inline add + submit', /id="bd-cat"/.test(bundles) && /addCategoryInline/.test(bundles) && /category_id:/.test(bundles));
check('CategoryManager is entity-aware', /this\._entity/.test(cm) && /this\._apEntity/.test(cm));

// thumbnails
check('Shared thumbImg helper + placeholder', /window\.thumbImg/.test(core) && /asset-thumb-pop/.test(core) && /\.thumb-empty/.test(css));
check('Thumbs in products / inventory / assets lists', /thumbImg\(p\.image_data\)/.test(products) && /thumbImg\(p\.image_data\)/.test(inv) && /thumbImg\(a\.image_data\)/.test(assets));

// toolbar moved left
check('+ Product + search left-aligned', /\.pd-toolbar\s*\{[^}]*flex-start/.test(css));

check('No emoji in new files', !/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(ct) && !/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(bundles));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
if (failed.length) process.exit(1);
