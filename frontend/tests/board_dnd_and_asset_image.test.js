/* Build 144ai — pointer-based board drag-drop, asset image across views, reschedule.

   Run:  NODE_PATH=/tmp/b144test/node_modules node board_dnd_and_asset_image.test.js       */
const fs = require('fs');
const path = require('path');

const results = [];
function check(name, cond, detail) {
  results.push({ name, ok: !!cond });
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${name}${cond ? '' : '  ↳ ' + (detail || '')}`);
}

const orders = fs.readFileSync(path.join(__dirname, '..', 'static/js/pages/orders.js'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '..', 'static/css/design.css'), 'utf8');

// Board drag-drop is now pointer-based (native HTML5 DnD removed)
check('Kanban card uses pointerdown', /onpointerdown="OrdersPage\._kanDown/.test(orders));
check('Old HTML5 dragstart handler removed', !/ondragstart="OrdersPage\._kanDragStart/.test(orders));
check('Pointer move/up drag handlers defined',
  /_kanDown\s*\(/.test(orders) && /_kanMove\s*\(/.test(orders) && /_kanUp\s*\(/.test(orders));
check('Drop resolves target via elementFromPoint', /elementFromPoint/.test(orders) && /data-target-status/.test(orders));
check('Tap (no drag) still opens the order', /if \(!wasActive\)\s*{\s*if \(id\) this\.viewOrder/.test(orders));
check('Floating ghost class used', /kan-ghost/.test(orders) && /\.kan-ghost/.test(css));

// Asset image across views
check('Board card renders device thumbnail', /c\.device_image/.test(orders) && /kan-thumb/.test(orders));
check('List row shows asset thumb', /_assetThumb\(o\.device\)/.test(orders));
check('List thumb has hover-enlarge popover CSS', /\.asset-thumb:hover \.asset-thumb-pop/.test(css));
check('General info has clickable asset photo', /changeAssetPhoto\(/.test(orders) && /\.asset-photo/.test(css));
check('Asset photo saved via PUT /api/assets (shared picker downscales)', /PhotoInput\.capture/.test(orders) && /put\(`\/api\/assets\/\$\{deviceId\}`/.test(orders));

// Reschedule available in list + general info
check('rescheduleModal defined', /rescheduleModal\(orderId\)/.test(orders));
check('Reschedule posts to /schedule', /patch\(`\/api\/orders\/\$\{orderId\}\/schedule`/.test(orders));
check('List row has a reschedule button', /title="Reschedule"/.test(orders));
check('General-info has a schedule block + reschedule button', /Schedule<\/div>/.test(orders) && /rescheduleModal\('\$\{id\}'\)/.test(orders));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
if (failed.length) process.exit(1);
