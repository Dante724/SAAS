const fs=require('fs');const html=fs.readFileSync(__dirname+'/../index.html','utf8');
let r=[];const ck=(n,c)=>r.push({n,ok:c});
ck('build bumped to 144cu',/build 144(c[u-z]|[d-z][a-z])/.test(html));
ck('demo hint has an id to target',/id="demo-hint"/.test(html));
ck('guard checks env via health',/\/api\/health/.test(html)&&/environment !== 'development'/.test(html));
ck('guard removes demo hint + clears password',/demo-hint'\)\?\.remove/.test(html)&&/a-pass'\); if \(p\) p\.value = ''/.test(html));
const bad=r.filter(x=>!x.ok);r.forEach(x=>console.log((x.ok?'ok ':'FAIL ')+x.n));
console.log(`\n${r.length-bad.length}/${r.length} passed`);process.exit(bad.length?1:0);
