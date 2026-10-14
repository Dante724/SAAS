/* Build 144di — Orders & Estimates unified into one list (type filter, not a tab);
   estimate converts to a work order IN PLACE. */
const fs=require('fs'),path=require('path'),assert=require('assert');
const R=p=>fs.readFileSync(path.resolve(__dirname,'..',p),'utf8');
const orders=R('static/js/pages/orders.js'), idx=R('index.html');
const be=fs.readFileSync(path.resolve(__dirname,'../../backend/routers/orders.py'),'utf8');
// FE: 3-way type filter replaces the 2-way tab
assert(/data-dt="all"/.test(orders)&&/data-dt="wo"/.test(orders)&&/data-dt="est"/.test(orders),'All/WO/Est filter present');
assert(/this\._docType === 'wo'/.test(orders)&&/this\._docType === 'est'/.test(orders),'query constrains type only when specific');
assert(!/setDocType\(true\)/.test(orders)&&!/setDocType\(false\)/.test(orders),'old boolean toggle removed from markup');
assert(/build 144(di|d[j-z]|[e-z][a-z])/.test(idx),'index bumped to 144di');
// BE: list returns both when is_estimate omitted; conversion is in place
assert(/is_estimate: Optional\[bool\] = None/.test(be),'list is_estimate optional (unified)');
assert(/if is_estimate is not None:/.test(be),'type filter applied only when provided');
assert(/estimate\.is_estimate = False/.test(be)&&/estimate\.order_number = await next_number/.test(be),'conversion flips + renumbers the same record');
console.log('unified_orders_144di: OK');
