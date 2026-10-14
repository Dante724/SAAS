/* Build 144bu — services get categories like products/bundles; booking wizard
   groups services by category. Run: node service_categories_144bu.test.js */
const fs = require('fs');
const path = require('path');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const R = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');
const sv = R('static/js/pages/services.js');
const book = R('book.html');
const tree = R('static/js/category-tree.js');
const mgr = R('static/js/category-manager.js');
const cat = R('../backend/routers/catalog.py');
const idx = R('index.html');

// FE services page mirrors products
check('Services page has category rail (same as products)', /class="pd"/.test(sv) && /id="sv-tree"/.test(sv) && /CategoryTree\.mount/.test(sv) && /entity: 'service'/.test(sv));
check('Manage categories + inline add', /manageCategories\(\)/.test(sv) && /CategoryManager\.open\(\{ entity: 'service'/.test(sv) && /addCategoryInline/.test(sv));
check('Form uses a category select (not free text)', /id="sv-cat-sel"/.test(sv) && !/id="sv-cat"[^-]/.test(sv));
check('Filter + submit use category_id', /category_id=' \+ this\._catFilter/.test(sv) && /category_id: document\.getElementById\('sv-cat-sel'\)/.test(sv));
check('+ Service button on toolbar like products', /pd-toolbar[\s\S]{0,400}ServicesPage\.newModal\(\)/.test(sv));

// Booking wizard groups by category
check('Wizard builds category chips', /pickCat\(/.test(book) && /new Set\(svcs\.map\(s=>s\.category\)/.test(book));
check('Wizard filters services by category', /state\.cat \? svcs\.filter/.test(book));

// shared components know the service noun
check('CategoryTree labels All services', /All services/.test(tree));
check('CategoryManager service noun', /'service' \? 'Service'/.test(mgr));

// backend contract
check('Services API returns + accepts category_id', /"category_id": s\.category_id/.test(cat) && /category_id: Optional\[str\] = None/.test(cat));
check('Build bumped', /build 144(b[u-z]|[c-z][a-z])/.test(idx));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
