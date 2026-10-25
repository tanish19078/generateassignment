// Offline UI contract tests; run with `node tests/test_cn_frontend.cjs`.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const nodes = new Map();
function node(id) {
  if (!nodes.has(id)) nodes.set(id, {
    value: '', innerHTML: '', textContent: '', style: {}, dataset: {}, checked: false,
    classList: { add(){}, remove(){}, toggle(){} }, addEventListener(){},
    appendChild(){}, remove(){}, insertAdjacentHTML(){}, scrollHeight: 0, scrollTop: 0,
    querySelector(){return node('child');}, setAttribute(){}, click(){}
  });
  return nodes.get(id);
}
const stored = new Map();
const context = vm.createContext({
  console, setTimeout, clearTimeout, Blob, URL, TextEncoder, Response,
  document: { getElementById:node, querySelector:s=>node(s.replace(/^#/,'')), querySelectorAll:()=>[], addEventListener(){}, createElement:()=>node('created'), body:{style:{setProperty(){}}} },
  window: {innerWidth:1200, innerHeight:800},
  localStorage:{getItem:k=>stored.get(k),setItem:(k,v)=>stored.set(k,v)},
  sessionStorage:{getItem:k=>stored.get(k),setItem:(k,v)=>stored.set(k,v),removeItem:k=>stored.delete(k)},
  requestAnimationFrame:fn=>fn(),
});
let source=fs.readFileSync('public/script.js','utf8');
// Init is DOM-specific; exercise actual controller functions with a minimal DOM.
source=source.replace(/\ninit\(\);\s*$/, '\n');
vm.runInContext(source,context);
const run=code=>vm.runInContext(code,context);
run("selectMode('cn')");
assert.equal(node('model-options').style.display,'none');
assert.equal(node('cn-options').style.display,'block');
assert.equal(run("parseAims('1. Configure RIP\\n2. Configure OSPF').length"),2);
assert.equal(run("parseAims('Experiment No. 1: Configure RIP\\n  with three routers\\nExperiment 2: Configure OSPF').length"),2);
assert.equal(run("parseAims('Configure RIP\\n---\\nConfigure OSPF').length"),2);
assert.equal(run("parseAims('Configure RIP\\nConfigure OSPF').length"),2);
context.cnFixture={mode:'cn',profile:{name:'<script>alert(1)</script>',roll:'42',section:'A'},figures:[{caption:'IP settings',data_url:'https://untrusted.example/image.png'}],requirements:['2 PCs'],procedure:['Assign IPs'],configurations:[],result:'Expected connectivity'};
run("state.experiments=[{aim:'Configure peer to peer connectivity',cn:cnFixture,detectedMode:'cn',status:'complete',number:'001'}]");
const html=run('renderCnReview(state.experiments[0],0)');
assert.ok(html.includes('&lt;script&gt;'));
assert.ok(!html.includes('<script>') && !html.includes('https://untrusted'));
const exported=JSON.parse(run('JSON.stringify(getDownloadExperiments(EXPORT_PROFILES[0]))'));
assert.equal(exported[0].mode,'cn');
assert.equal(exported[0].profile.roll,'42');
assert.equal(exported[0].figures,undefined);
run('persistSession(); state.experiments=[]; restoreSession()');
assert.equal(run('state.experiments[0].cn.profile.roll'),'42');
assert.equal(node('cnRoll').value,'42');
run("selectMode('coding')");
assert.equal(node('model-options').style.display,'block');
assert.equal(node('cn-options').style.display,'none');
console.log('CN frontend contracts passed (mode, index parsing, previews, export and restore).');

// Optional transport integration against a locally running Flask app.
if (process.env.PRACTIGEN_TEST_URL) {
  (async () => {
    const base=process.env.PRACTIGEN_TEST_URL;
    context.fetch=(url,options)=>fetch(new URL(url,base),options);
    const response=await fetch(new URL('/api/cn/catalog',base));
    const catalog=await response.json();
    context.liveAims=catalog.experiments.map(e=>e.aim).join('\n---\n');
    node('cnName').value='Tanish Singla';node('cnRoll').value='2510990000';node('cnSection').value='CSE 3A';
    run("selectMode('cn'); state.aimsText=liveAims;");
    await run('startGeneration()');
    assert.equal(run("state.experiments.filter(e=>e.status==='complete').length"),8);
    run("navigate('review')");
    assert.ok(node('reviewAccordion').innerHTML.includes('Expected Connectivity Output'));
    assert.ok(node('reviewAccordion').innerHTML.includes('Tanish Singla'));
    const payload=JSON.parse(run('JSON.stringify({mode:"cn",experiments:getDownloadExperiments(EXPORT_PROFILES[0]),settings:getSettings()})'));
    const document=await fetch(new URL('/api/download',base),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    assert.equal(document.status,200);
    const bytes=Buffer.from(await document.arrayBuffer());
    assert.equal(bytes.subarray(0,2).toString(),'PK');
    assert.ok(bytes.length>10000);
    run("navigate('export')");
    assert.equal(node('classroom-panel').style.display,'none');
    console.log('CN transport integration passed: eight aims → generation → review → Word export.');
  })().catch(error=>{console.error(error);process.exitCode=1;});
}
