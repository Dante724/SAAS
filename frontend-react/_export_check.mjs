import * as XLSX from "xlsx";
import assert from "node:assert";

// Mirror ReportsHub: cols + rows, a search filter, a sort, then AOA + totals.
const cols = [{key:"employee",label:"Employee"},{key:"base",label:"Base"},{key:"note",label:"Note"}];
const rows = [
  {employee:"Admin", base:100, note:"a, b"},      // note has a comma → must be quoted
  {employee:"Bob",   base:50,  note:'say "hi"'},   // embedded quotes → must be escaped
  {employee:"Cara",  base:75,  note:"plain"},
];
const cellVal = (r,c) => r[c.key];

// --- filter (search "a") + sort by base desc, as the component does ---
const q = "a";
const filtered = rows.filter(r => cols.some(c => String(cellVal(r,c)??"").toLowerCase().includes(q)));
// "a" matches Admin(note "a, b"), Cara(name Cara + note plain? 'plain' has no a... 'Cara' has 'a') -> Admin, Cara
const sort = {i:1, dir:-1}; // base desc
const viewRows = [...filtered].sort((a,b)=>{const av=cellVal(a,cols[1]),bv=cellVal(b,cols[1]);return (av-bv)*sort.dir;});

// numeric totals over filtered (col 1 = base)
const totals = cols.map((c,ci)=>{const vals=filtered.map(r=>cellVal(r,c)).filter(v=>typeof v==="number");return vals.length?vals.reduce((s,v)=>s+v,0):null;});
const hasTotals = totals.some(t=>t!=null);

const aoa = [cols.map(c=>c.label)];
for (const r of viewRows) aoa.push(cols.map(c=>{const v=cellVal(r,c);return v==null?"":v;}));
if (hasTotals && viewRows.length) aoa.push(cols.map((_c,ci)=>ci===0?"Total":(totals[ci]??"")));

const ws = XLSX.utils.aoa_to_sheet(aoa);
const csv = XLSX.utils.sheet_to_csv(ws);
console.log("CSV:\n"+csv);

const lines = csv.trim().split("\n");
assert.strictEqual(lines[0], "Employee,Base,Note", "header");
// filtered = Admin, Cara; sorted base desc → Admin(100), Cara(75)
assert.ok(lines[1].startsWith("Admin,100,"), "first row Admin");
assert.ok(lines[2].startsWith("Cara,75,"), "second row Cara");
assert.ok(!csv.includes("Bob"), "Bob filtered out (search-aware export)");
assert.ok(csv.includes('"a, b"'), "comma cell quoted");
assert.ok(csv.includes('"say ""hi"""'), "embedded quotes escaped");
assert.ok(lines[3].startsWith("Total,175,"), "totals row = 100+75");
console.log("\nALL ASSERTIONS PASSED");
