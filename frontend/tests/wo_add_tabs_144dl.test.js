/* Build 144dl — the inline Services & Products add dropdown is Orderry-style:
   category tabs (All items / Services / Products / Bundles) and results grouped
   under their catalog category headers. */
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const root = path.resolve(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(root, p), 'utf8');
const orders = read('static/js/pages/orders.js');
const css = read('static/css/design.css');
const idx = read('index.html');

assert(/build 144(dl|d[m-z]|[e-z][a-z])/.test(idx), 'index bumped to 144dl');

// Tab state + switcher exist.
assert(/_woTab:\s*'all'/.test(orders), 'woSearch has a default tab state');
assert(/woSetTab\(id,\s*tab\)/.test(orders), 'a woSetTab(id, tab) switcher exists');

// Dropdown renders the four Orderry tabs.
['All items', 'Services', 'Products', 'Bundles'].forEach(l =>
  assert(orders.includes(l), `tab label "${l}" is rendered`));
assert(/\['all','All items'\]/.test(orders) && /\['bundle','Bundles'\]/.test(orders),
  'tab values map all/service/product/bundle');

// Search passes the active tab through as the catalog type filter.
assert(/type\s*=\s*tab\s*===\s*'all'\s*\?\s*'all'\s*:\s*tab/.test(orders),
  'the active tab drives the catalog search type');
assert(/catalog\/search\?q=\$\{encodeURIComponent\(q\)\}&type=\$\{type\}/.test(orders),
  'catalog search is called with q + type');

// Results are grouped by category with headers.
assert(/it\.category\s*\|\|\s*'Uncategorized'/.test(orders), 'results grouped by category');
assert(/wo-add-group-head/.test(orders) && /wo-add-group-head/.test(css),
  'category group headers render and are styled');

// The tab bar is pinned and the results scroll below it.
assert(/\.wo-add-tabs\s*\{/.test(css), 'tab bar styled');
assert(/\.wo-add-tab\.on\s*\{/.test(css), 'active tab styled');
assert(/\.wo-add-scroll\s*\{[^}]*overflow-y:\s*auto/.test(css), 'scroll region styled');

// Opens on focus (browse without typing) and closes on outside click.
assert(/onfocus="OrdersPage\.woSearch\(/.test(orders), 'dropdown opens on focus');
assert(/_woOutside/.test(orders), 'outside-click closes the dropdown');

// Custom-line fallback is preserved (only when there is a query).
assert(/Add "\$\{q\.replace/.test(orders), 'custom add-line fallback preserved');

console.log('wo_add_tabs_144dl: OK');
