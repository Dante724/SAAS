/* Build 144dp — checklists v2 frontend: settings builder + WO fill helper. */
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const root = path.resolve(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(root, p), 'utf8');
const cl = read('static/js/pages/checklists.js');
const orders = read('static/js/pages/orders.js');
const settings = read('static/js/pages/settings.js');
const idx = read('index.html');
const css = read('static/css/design.css');

assert(/build 144(dp|d[q-z]|[e-z][a-z])/.test(idx), 'index bumped to 144dp');
assert(/pages\/checklists\.js/.test(idx), 'checklists.js is included');

// Settings builder exists and is wired into the settings rail.
assert(/const ChecklistTemplatesPage/.test(cl), 'ChecklistTemplatesPage defined');
assert(/ChecklistTemplatesPage\.load\(\)/.test(settings), 'checklists sub in settings rail');
assert(/can_manage_checklist_templates/.test(cl), 'builder gated by template perm');
assert(/put\('\/api\/checklist-templates\/'/.test(cl) && /post\('\/api\/checklist-templates'/.test(cl),
  'create + update template');

// Field kinds cover the Orderry set.
['check_result', 'yes_no', 'dropdown', 'multiselect', 'file', 'date', 'textarea'].forEach(k =>
  assert(cl.includes(k), `field kind ${k} present`));

// WO fill helper: tab wired in the order drawer, attach + typed fill + fail→rec.
assert(/const WOChecklist/.test(cl), 'WOChecklist helper defined');
assert(/data-panel="checklists"/.test(orders) && /woTab\(event,'checklists'\)/.test(orders),
  'Checklists tab wired into WO drawer');
assert(/WOChecklist\.load\(/.test(orders), 'tab switch mounts the checklist');
assert(/\/api\/orders\/'.*'\/checklists'/.test(cl.replace(/\n/g, ' ')) || /'\/checklists'/.test(cl),
  'attaches checklists to the order');
assert(/patch\('\/api\/orders\/'/.test(cl), 'fills items via PATCH');
assert(/\/recommend'/.test(cl) && /recommend\(cid, iid\)/.test(cl), 'fail → recommendation action');
assert(/can_create_checklist/.test(cl) && /can_edit_checklist/.test(cl) && /can_delete_checklist/.test(cl),
  'fill UI respects the checklist permissions');

// Styled.
assert(/\.cl-step\s*\{/.test(css) && /\.cl-cr button\.fail\.on/.test(css), 'builder + fill styled');

console.log('checklists_v2_144dp: OK');
