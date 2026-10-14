/* Build 144dd — API error toasts show readable text, not "[object Object]". */
const fs=require('fs'),path=require('path'),assert=require('assert');
const core=fs.readFileSync(path.resolve(__dirname,'../static/js/core.js'),'utf8');
const src=core.slice(core.indexOf('function errMessage'), core.indexOf('\n}', core.indexOf('function errMessage'))+2);
const errMessage=new Function(src+'\nreturn errMessage;')();
// string detail passes through
assert.strictEqual(errMessage({detail:'You do not have permission'},403),'You do not have permission');
// 422 array -> "field: msg"
assert.strictEqual(errMessage({detail:[{loc:['body','location_id'],msg:'Field required'}]},422),'location_id: Field required');
// multiple 422 errors joined
assert(/qty: .*; unit_price:/.test(errMessage({detail:[{loc:['body','qty'],msg:'x'},{loc:['body','unit_price'],msg:'y'}]},422)));
// object detail -> its msg
assert.strictEqual(errMessage({detail:{msg:'bad'}},400),'bad');
// nothing -> HTTP code, never "[object Object]"
assert.strictEqual(errMessage({},500),'HTTP 500');
assert(!/\[object Object\]/.test(errMessage({detail:[{loc:['body','x'],msg:'m'}]},422)));
console.log('err_message_144dd: OK');
