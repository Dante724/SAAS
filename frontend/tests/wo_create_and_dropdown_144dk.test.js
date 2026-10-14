/* Build 144dk — create a WO from step 1 (no lines) + inline results dropdown shows above the modal. */
const fs=require('fs'),path=require('path'),assert=require('assert');
const o=fs.readFileSync(path.resolve(__dirname,'../static/js/pages/orders.js'),'utf8');
const css=fs.readFileSync(path.resolve(__dirname,'../static/css/design.css'),'utf8');
const idx=fs.readFileSync(path.resolve(__dirname,'../index.html'),'utf8');
// step-1 create button
assert(/Create \$\{verb1\}/.test(o),'step 1 offers a Create button (no lines required)');
assert(/CatalogPicker\.lines = \[\]/.test(o),'newModal resets the picker so step-1 create carries no stale lines');
// dropdown above modal + not clipped
const dd=css.slice(css.indexOf('.wo-add-results { display:none;'), css.indexOf('.wo-add-results.open'));
assert(/position:fixed/.test(dd)&&/z-index:600/.test(dd),'results dropdown is fixed + above the modal (z 600)');
assert(/box\.style\.top = \(ir\.bottom/.test(o),'results positioned under the input at runtime');
assert(/build 144(dk|d[l-z]|[e-z][a-z])/.test(idx),'index bumped to 144dk');
console.log('wo_create_and_dropdown_144dk: OK');
