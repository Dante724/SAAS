/* Build 144dj — inline S&P adder (no modal) + recommendation modal header fix. */
const fs=require('fs'),path=require('path'),assert=require('assert');
const o=fs.readFileSync(path.resolve(__dirname,'../static/js/pages/orders.js'),'utf8');
const css=fs.readFileSync(path.resolve(__dirname,'../static/css/design.css'),'utf8');
const idx=fs.readFileSync(path.resolve(__dirname,'../index.html'),'utf8');
assert(/wo-addbar/.test(o)&&/wo-add-tech/.test(o)&&/wo-add-search/.test(o),'inline add bar rendered on the tab');
assert(/woSearch\(id\b/.test(o)&&/woPick\(id, kind/.test(o)&&/woAddCustom\(id\)/.test(o),'inline add methods present');
assert(/catalog\/search\?q=/.test(o),'inline search hits the catalog endpoint');
assert(!/onclick="OrdersPage\.addLineModal\('\$\{id\}'\)">\+ Add service/.test(o),'old modal add button removed from the tab');
assert(/Add recommendation<\/div>/.test(o) && !/class="modal-head"><div class="modal-title">Add recommendation/.test(o),'rec modal uses modal-header (title not cut)');
assert(/\.wo-add-results/.test(css)&&/\.wo-addbar/.test(css),'inline add CSS present');
assert(/build 144(dj|d[k-z]|[e-z][a-z])/.test(idx),'index bumped to 144dj');
console.log('inline_add_144dj: OK');
