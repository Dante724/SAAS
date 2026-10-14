/* Build 144de — Services & Products spacing: totals card + Estimated profit + roomier rows. */
const fs=require('fs'),path=require('path'),assert=require('assert');
const R=p=>fs.readFileSync(path.resolve(__dirname,'..',p),'utf8');
const css=R('static/css/design.css'), orders=R('static/js/pages/orders.js'), idx=R('index.html');
const tot=css.slice(css.indexOf('.inv-totals {'), css.indexOf('.inv-trow .v'));
assert(/border:0\.5px solid/.test(tot)&&/border-radius:14px/.test(tot)&&/box-shadow/.test(tot),'totals is a bordered card');
assert(/Estimated profit/.test(orders),'totals include estimated profit');
assert(/profitTotal = o\.lines\.reduce/.test(orders),'profit computed from lines');
assert(/padding:13px 16px/.test(orders),'line rows roomier (13px)');
assert(/build 144(de|d[f-z]|[e-z][a-z])/.test(idx),'index bumped to 144de');
console.log('wo_lines_polish_144de: OK');
