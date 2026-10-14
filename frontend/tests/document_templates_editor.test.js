/* Build 144n — full-page document editor + Labels merged under Document templates.
   Run: NODE_PATH=<dir with jsdom> node document_templates_editor.test.js            */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { JSDOM } = require('jsdom');

const results = [];
const check = (name, cond, detail) => {
  results.push(!!cond);
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${name}${cond ? '' : '  ↳ ' + (detail || '')}`);
};
const read = (p) => fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'pages', p), 'utf8');

/* static: settings rail merged */
console.log('\nSettings rail:');
const settingsSrc = read('settings.js');
check('no standalone "Labels & price tags" rail item',
  !/key:\s*'labels'/.test(settingsSrc) && !/label:\s*'Labels & price tags'/.test(settingsSrc));
check('Document templates entry mounts the hub',
  /Document templates.*DocumentsHubPage\.load|DocumentsHubPage\.load/.test(settingsSrc));

/* behavioural */
const dom = new JSDOM('<!doctype html><html><body><div id="content"></div><div id="topbar-actions"></div></body></html>',
  { url: 'http://localhost', runScripts: 'outside-only' });
const w = dom.window;
const calls = [];
let saved = null;
w.get = async (url) => {
  calls.push(url);
  if (url.includes('/api/documents/templates')) return {
    items: [{ id:'t1', name:'Estimate', doc_type:'estimate', body_html:'<h1>ESTIMATE #{Order Number}</h1>',
              auto_print_on:null, is_default:true, active:true, page_size:'a4', font_family:'Sans-Serif' },
            { id:'lbl1', name:'Price tag', doc_type:'price_tag', body_html:'{Price} {Barcode}',
              auto_print_on:null, is_default:true, active:true, page_size:'label', font_family:'Sans-Serif' }],
    variables: ['{Order Number}', '{Company Phone}'],
    variable_groups: [
      { group:'Company', items:['{Company Name}', '{Company Phone}'] },
      { group:'Order', items:['{Order Number}', '{Order Status}'] }] };
  if (url.includes('/api/staff/company')) return { logo_data: 'data:image/png;base64,AAA' };
  if (url.includes('/api/statuses')) return { items: [
    { key:'new', name:'New' }, { key:'done', name:'Done' }] };
  if (url.includes('/api/print/templates')) return { items: [], fields: [] };
  return { items: [] };
};
w.post = async () => ({ id:'new1' });
w.put = async (url, body) => { saved = { url, body }; return {}; };
w.del = async () => ({});
w.confirm2 = async () => true;
w.icon = () => ''; w.emptyRow = () => ''; w.skeletonRows = () => '';
w.fmt = { currency:(n)=>'$'+n, date:()=>'' };
w.Toast = { success(){}, error(m){ console.log('ERR', m); }, info(){} };
w.Modal = { open(){}, close(){} };
w.setContent = (h)=>{ w.document.getElementById('content').innerHTML = h; };
w.setTopbarActions = (h)=>{ w.document.getElementById('topbar-actions').innerHTML = h || ''; };
w.Router = { register(){}, go(){}, current:'settings' };
w.window = w;

const ctx = vm.createContext(w);
vm.runInContext(read('labels.js'), ctx, { filename:'labels.js' });
vm.runInContext(read('feedback.js'), ctx, { filename:'feedback.js' });
const Hub = vm.runInContext('DocumentsHubPage', ctx);
const Docs = vm.runInContext('DocsPage', ctx);

(async () => {
  console.log('\nDocument templates hub:');
  let err = null;
  try { await Hub.load(); await new Promise(r=>setTimeout(r,20)); } catch (e) { err = e; }
  check('hub loads without error', !err, err && err.message);
  const content = () => w.document.getElementById('content').innerHTML;
  check('unified hub: no separate Labels tab (labels live in the doc editor)',
    !/Labels &amp; price tags<\/button>/.test(content()));
  check('label templates appear in the unified selector, tagged as labels',
    /Price tag · label/.test(content()));

  console.log('\nFull-page editor:');
  check('toolbar has font, size, colour and formatting buttons',
    /fontName/.test(content()) && /fontSize/.test(content()) && /doc-tb-color/.test(content()) &&
    /DocsPage\.cmd\('bold'\)/.test(content()) && /justifyCenter/.test(content()));
  check('has insert image, logo, table and divider tools',
    /DocsPage\.insertImage/.test(content()) && /DocsPage\.insertLogo/.test(content()) &&
    /DocsPage\.insertTable/.test(content()) && /insertHorizontalRule/.test(content()));
  check('has a contenteditable canvas with the template body',
    /id="doc-canvas" contenteditable="true"/.test(content()) && /ESTIMATE #\{Order Number\}/.test(content()));
  check('has a page-size selector', /id="doc-pagesize"/.test(content()));
  check('grouped Variables side-panel present (Company, Order)',
    /doc-vars-title/.test(content()) && />Company</.test(content()) && />Order</.test(content()) &&
    /insertVar\('\{Company Phone\}'\)/.test(content()));

  // document settings modal carries the per-status print checkboxes
  let modalHtml = '';
  w.Modal.open = (h) => { modalHtml = h; };
  Docs.rename();
  check('document settings modal has "Allow printing in statuses" checkboxes',
    /Allow printing in statuses/.test(modalHtml) &&
    /data-pstatus="new"/.test(modalHtml) && /data-pstatus="done"/.test(modalHtml));

  console.log('\nEditing actions:');
  // insert a variable (jsdom has no execCommand → falls back to appending)
  Docs.insertVar('{Order Status}');
  check('insertVar puts the variable into the canvas',
    /\{Order Status\}/.test(w.document.getElementById('doc-canvas').innerHTML));

  // save posts body_html + page size
  w.document.getElementById('doc-canvas').innerHTML = '<p>Hello {Client Name}</p>';
  await Docs.save();
  check('save PUTs the edited body_html and page size',
    saved && saved.url.includes('/api/documents/templates/t1') &&
    /Hello \{Client Name\}/.test(saved.body.body_html) && saved.body.page_size === 'a4',
    JSON.stringify(saved && saved.body));

  // unified label printing: LabelPrint pulls label DocumentTemplates (not the retired
  // /api/print/templates), and offers them for the selected products.
  console.log('\nUnified label printing:');
  const LabelPrint = vm.runInContext('LabelPrint', ctx);
  let lblModal = '';
  w.Modal.open = (h) => { lblModal = h; };
  calls.length = 0;
  await LabelPrint.open(['p1', 'p2']);
  await new Promise(r=>setTimeout(r,10));
  check('LabelPrint reads label templates from the document system',
    calls.some(u => u.includes('/api/documents/templates')) &&
    !calls.some(u => u.includes('/api/print/templates')));
  check('LabelPrint offers the price-tag template for the products',
    /Price tag/.test(lblModal) && /render-labels|LabelPrint\.go/.test(lblModal));

  const failed = results.filter(r => !r).length;
  console.log(failed ? `\nFAILURES ❌ (${failed})` : '\nALL PASS ✅');
  process.exit(failed ? 1 : 0);
})();
