/* Build 144ao — managed categories: manager component, product inline-add, POS wiring.
   Run: NODE_PATH=/tmp/b144test/node_modules node category_manager.test.js */
const fs = require('fs');
const path = require('path');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const R = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');

const cm = R('static/js/category-manager.js');
const products = R('static/js/pages/products.js');
const sales = R('static/js/pages/sales.js');
const index = R('index.html');

// component
check('CategoryManager defined', /const CategoryManager\s*=/.test(cm));
check('Create posts to /api/catalog/categories', /post\('\/api\/catalog\/categories'/.test(cm));
check('Rename PATCHes the category', /patch\(`\/api\/catalog\/categories\/\$\{id\}`/.test(cm));
check('Delete removes the category', /del\(`\/api\/catalog\/categories\/\$\{id\}`/.test(cm));
check('quickCreate returns the new category', /quickCreate/.test(cm));
check('Template-scoped list', /template_key=/.test(cm));
check('Registered in index.html', /category-manager\.js/.test(index));

// products page
check('Products has Manage categories button', /ProductsPage\.manageCategories\(\)/.test(products));
check('Product form inline add', /addCategoryInline/.test(products) && /CategoryManager\.addPanel\(/.test(products));

// POS
check('POS loads managed categories', /_loadCats\(\)/.test(sales) && /\/api\/catalog\/categories\?entity_type=product/.test(sales));
check('POS rail uses managed cats (catid)', /catid:'\s*\+\s*c\.id/.test(sales) && /this\._cats\.map/.test(sales));
check('POS filters grid by category_id', /p\.category_id === catId/.test(sales));
check('POS has Manage categories action', /SalesPage\.manageCats\(\)/.test(sales));
check('POS template-scopes categories', /_tplKey/.test(sales) && /industry-template/.test(sales));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
if (failed.length) process.exit(1);
