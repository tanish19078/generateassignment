// Offline Project File UI contracts; run with `node tests/test_project_frontend.cjs`.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const nodes = new Map();
function node(id) {
  if (!nodes.has(id)) {
    const classes = new Set();
    nodes.set(id, {
      value: '', innerHTML: '', textContent: '', style: {}, dataset: {}, checked: false,
      disabled: false, className: '', files: [], scrollHeight: 0, scrollTop: 0,
      classList: {
        add(...names) { names.forEach(name => classes.add(name)); },
        remove(...names) { names.forEach(name => classes.delete(name)); },
        toggle(name, force) {
          if (force === true) classes.add(name);
          else if (force === false) classes.delete(name);
          else if (classes.has(name)) classes.delete(name);
          else classes.add(name);
          return classes.has(name);
        },
        contains(name) { return classes.has(name); },
      },
      insertAdjacentHTML(where, html) { this.innerHTML += html; },
      addEventListener() {}, appendChild() {}, remove() {}, setAttribute() {}, select() {}, click() {},
      querySelector() { return node('child'); },
    });
  }
  return nodes.get(id);
}

const normalized = {
  schema_version: 1,
  submode: 'frontend',
  project: { title: 'Portal <script>alert(1)</script>', results: [{ title: 'Dashboard' }] },
  frontend: { stack: [{ name: 'JavaScript' }], pages: [{ name: 'Dashboard' }] },
  dbms: null,
  figures: [],
  report_sections: {},
};
const validation = {
  valid: true,
  normalized,
  errors: [],
  missing_fields: [],
  warnings: ['Upload screenshots before final export.'],
  generation_needed: ['abstract'],
};

const stored = new Map();
const context = vm.createContext({
  console, Blob, URL, TextEncoder, Response, AbortController,
  setTimeout: (fn) => { fn(); return 1; }, clearTimeout() {},
  requestAnimationFrame: fn => fn(),
  fetch: async (url) => {
    if (String(url).startsWith('/api/project/prompt/')) {
      return new Response(JSON.stringify({ prompt: 'READ ONLY PROJECT COLLECTOR' }), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      });
    }
    if (url === '/api/project/validate') {
      return new Response(JSON.stringify(validation), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      });
    }
    throw new Error(`Unexpected fetch: ${url}`);
  },
  navigator: { clipboard: { writeText: async value => { context.copied = value; } } },
  document: {
    getElementById: node,
    querySelector: selector => node(selector.replace(/^#/, '')),
    querySelectorAll: () => [],
    addEventListener() {},
    createElement: () => node('created'),
    execCommand: () => true,
    body: { style: { setProperty() {} }, appendChild() {} },
  },
  window: { innerWidth: 1200, innerHeight: 800, URL },
  localStorage: { getItem: key => stored.get(key), setItem: (key, value) => stored.set(key, value) },
  sessionStorage: {
    getItem: key => stored.get(key), setItem: (key, value) => stored.set(key, value),
    removeItem: key => stored.delete(key),
  },
});

let source = fs.readFileSync('public/script.js', 'utf8');
source = source.replace(/\ninit\(\);\s*$/, '\n');
vm.runInContext(source, context);
const run = code => vm.runInContext(code, context);
context.normalized = normalized;

(async () => {
  run('init()');
  const html = fs.readFileSync('public/workspace.html', 'utf8');
  assert.equal((html.match(/class="mode-card"/g) || []).length, 3);
  assert.ok(!html.includes('provider-grid'));
  run("selectMainMode('lab')");
  assert.equal(run('state.config.mode'), 'coding');
  run("selectMainMode('custom'); selectMode('cn')");
  assert.equal(run('state.config.mainMode'), 'custom');
  assert.equal(node('model-options').style.display, 'none');
  run("selectMainMode('project')");
  assert.equal(node('project-options').style.display, 'block');
  assert.equal(node('quality-options').style.display, 'none');
  assert.equal(node('formatting-shell').style.display, 'none');
  assert.equal(node('standard-aims-panel').style.display, 'none');
  assert.equal(node('stepDataLabel').textContent, 'Project Data');

  run("selectProjectSubmode('frontend')");
  await run('loadProjectPrompt()');
  assert.equal(node('projectCollectorPrompt').textContent, 'READ ONLY PROJECT COLLECTOR');
  await run('copyProjectPrompt()');
  assert.equal(context.copied, 'READ ONLY PROJECT COLLECTOR');

  node('projectContextInput').value = '{"schema_version":1}';
  await run('validateProjectContext()');
  assert.equal(run('state.projectFile.validation.status'), 'valid');
  assert.equal(node('projectGenerateBtn').disabled, false);
  assert.equal(run('state.projectFile.normalized.project.title'), normalized.project.title);

  const normalFetch = context.fetch;
  let finishSameModeValidation;
  context.fetch = async url => {
    if (url !== '/api/project/validate') return normalFetch(url);
    return new Promise(resolve => { finishSameModeValidation = resolve; });
  };
  const sameModeValidation = run('validateProjectContext()');
  run("selectProjectSubmode('frontend')");
  finishSameModeValidation(new Response(JSON.stringify(validation), {
    status: 200, headers: { 'Content-Type': 'application/json' },
  }));
  await sameModeValidation;
  assert.equal(run('state.projectFile.validation.status'), 'valid');

  const pendingValidations = [];
  context.fetch = async url => {
    if (url !== '/api/project/validate') return normalFetch(url);
    return new Promise(resolve => pendingValidations.push(resolve));
  };
  node('projectContextInput').value = '{"request":"first"}';
  const firstValidation = run('validateProjectContext()');
  node('projectContextInput').value = '{"request":"second"}';
  run("markProjectContextStale('{\"request\":\"second\"}')");
  const secondValidation = run('validateProjectContext()');
  const secondNormalized = { ...normalized, project: { ...normalized.project, title: 'Second context' } };
  pendingValidations[1](new Response(JSON.stringify({ ...validation, normalized: secondNormalized }), {
    status: 200, headers: { 'Content-Type': 'application/json' },
  }));
  await secondValidation;
  pendingValidations[0](new Response(JSON.stringify(validation), {
    status: 200, headers: { 'Content-Type': 'application/json' },
  }));
  await firstValidation;
  assert.equal(run('state.projectFile.normalized.project.title'), 'Second context');
  context.fetch = normalFetch;

  context.report = {
    schema_version: 1,
    submode: 'frontend',
    title: normalized.project.title,
    context: normalized,
    report_sections: {
      abstract: '<img src=x onerror=alert(1)>',
      introduction_background: 'Background',
      introduction_significance: 'Significance',
      problem_definition: 'Problem',
      design_methodology: 'Design',
      results_summary: 'Results',
    },
    generation: { source: 'hybrid', generated_fields: ['abstract'] },
  };

  let finishGeneration;
  context.fetch = async url => {
    if (url !== '/api/project/generate') return normalFetch(url);
    return new Promise(resolve => { finishGeneration = resolve; });
  };
  run('state.projectFile.report=null; state.projectFile.normalized=normalized; state.projectFile.validation.status="valid"; state.projectFile.validation.generationNeeded=[]');
  const canceledGeneration = run('startProjectGeneration()');
  run('stopGeneration()');
  finishGeneration(new Response(JSON.stringify({ report: context.report, warnings: [] }), {
    status: 200, headers: { 'Content-Type': 'application/json' },
  }));
  await canceledGeneration;
  assert.equal(run('state.projectFile.report'), null);
  context.fetch = normalFetch;

  run('state.projectFile.report=report; renderProjectReview()');
  assert.ok(node('reviewAccordion').innerHTML.includes('&lt;script&gt;'));
  assert.ok(node('reviewAccordion').innerHTML.includes('&lt;img'));
  assert.ok(!node('reviewAccordion').innerHTML.includes('<script>'));
  assert.equal(run('projectOutputFilename()'), 'Portal_scriptalert1script.docx');

  // Fail the second section, restore the session, and resume without repeating the first.
  node('apiEndpoint').value = 'https://custom.example/v1';
  node('customModel').value = 'custom-model';
  node('apiKey').value = 'user-key';
  run('state.projectFile.report=null; state.projectFile.validation.generationNeeded=["abstract", "design_methodology"]; state.projectFile.completedSections={};');
  const sectionCalls = [];
  let failDesign = true;
  context.fetch = async (url, options) => {
    const body = JSON.parse(options.body);
    if (url === '/api/project/section') {
      sectionCalls.push(body.section);
      assert.equal(body.base_url, 'https://custom.example/v1');
      assert.equal(body.model, 'custom-model');
      assert.equal(body.api_key, 'user-key');
      if (body.section === 'design_methodology' && failDesign) {
        return new Response(JSON.stringify({ error: 'Try again' }), { status: 502 });
      }
      return new Response(JSON.stringify({ section: body.section, text: 'Verified section prose describing the supplied implementation.' }));
    }
    if (url === '/api/project/generate') {
      assert.equal(Object.keys(body.completed_sections).length, 2);
      return new Response(JSON.stringify({ report: context.report, warnings: [] }));
    }
    if (url === '/api/project/download') return new Response(new Blob(['mock-docx']));
    return normalFetch(url);
  };
  await run('startProjectGeneration()');
  assert.equal(run('state.projectFile.generationStatus'), 'failed');
  assert.ok(run('state.projectFile.completedSections.abstract'));
  run('state.projectFile=createEmptyProjectFileState(); restoreSession()');
  assert.equal(node('apiEndpoint').value, 'https://custom.example/v1');
  failDesign = false;
  await run('startProjectGeneration()');
  assert.deepEqual(sectionCalls, ['abstract', 'design_methodology', 'design_methodology']);
  assert.equal(run('state.projectFile.generationStatus'), 'complete');
  run('launchConfetti = () => {};');
  await run('downloadProjectDocument()');
  assert.equal(node('downloadBtn').disabled, false);
  assert.equal(node('created').download, 'Portal_scriptalert1script.docx');
  context.fetch = normalFetch;

  // Save a successful part, fail the next, then restore and resume with the newly selected model.
  run('state.projectFile.report=null; state.projectFile.validation.generationNeeded=["introduction_background"]; state.projectFile.completedSections={}; state.projectFile.sectionParts={};');
  const partCalls = [];
  let failPart = true;
  const partText = index => `Verified background part ${index + 1} describes the documented project and its scope.`;
  context.fetch = async (url, options) => {
    const body = JSON.parse(options.body);
    if (url === '/api/project/section') {
      partCalls.push(body.part_index);
      assert.deepEqual(body.previous_parts, Array.from({ length: body.part_index }, (_, i) => partText(i)));
      if (!failPart) assert.equal(body.model, 'new-model');
      if (body.part_index === 1 && failPart) {
        return new Response(JSON.stringify({ error: 'Timed out', code: 'llm_timeout', request_id: 'probe-id' }), { status: 504 });
      }
      return new Response(JSON.stringify({ section: body.section, text: partText(body.part_index),
        part_index: body.part_index, part_count: 3 }));
    }
    if (url === '/api/project/generate') {
      assert.equal(body.completed_sections.introduction_background, [0, 1, 2].map(partText).join('\n\n'));
      return new Response(JSON.stringify({ report: context.report, warnings: [] }));
    }
    return normalFetch(url, options);
  };
  await run('startProjectGeneration()');
  assert.equal(run('state.projectFile.sectionParts.introduction_background.length'), 1);
  assert.equal(run('state.projectFile.completedSections.introduction_background'), undefined);
  assert.equal(run('state.projectFile.generationStatus'), 'failed');
  run('state.projectFile=createEmptyProjectFileState(); restoreSession()');
  node('customModel').value = 'new-model';
  failPart = false;
  await run('startProjectGeneration()');
  assert.deepEqual(partCalls, [0, 1, 1, 2]);
  assert.equal(run('state.projectFile.generationStatus'), 'complete');
  context.fetch = normalFetch;

  run("markProjectContextStale('{\"changed\":true}')");
  assert.equal(run('state.projectFile.report'), null);
  assert.equal(run('Object.keys(state.projectFile.sectionParts).length'), 0);
  assert.equal(node('projectGenerateBtn').disabled, true);

  run('state.projectFile.normalized=normalized; state.projectFile.report=report; state.projectFile.validation.status="valid"; persistSession()');
  run('state.projectFile=createEmptyProjectFileState(); restoreSession()');
  assert.equal(run('state.config.mode'), 'project');
  assert.equal(run('state.projectFile.report.title'), normalized.project.title);
  assert.equal(run('state.projectFile.images.length'), 0);

  run("selectProjectSubmode('dbms')");
  assert.equal(run('state.config.projectSubmode'), 'dbms');
  assert.equal(run('state.projectFile.report'), null);

  // DBMS diagrams work for every report and never count as missing screenshots.
  const dbmsContext = {
    submode: 'dbms', project: { title: 'Library' },
    dbms: {
      entities: [{ name: 'Member <script>', attributes: [{ name: 'id', data_type: 'int', key: 'PK' }] }, { name: 'Book', attributes: [] }],
      relationships: [{ from_entity: 'Member <script>', to_entity: 'Book', cardinality: 'M:N', description: 'Borrowing <img onerror=alert(1)>' }],
      tables: [{ name: 'members' }, { name: 'books' }], use_cases: [{ screenshot_filename: 'query.png' }],
    },
    figures: [{ filename: 'erd.png', caption: 'Uploaded ERD', section: 'erd' }],
  };
  context.dbmsReport = {
    submode: 'dbms', title: 'Library', context: dbmsContext,
    report_sections: { introduction: 'Introduction', erd_description: 'A factual description.', schema_description: 'Schema', use_cases_introduction: 'Queries' },
    erd_diagrams: [{ caption: 'Generated E-R <diagram>', data_url: 'data:image/png;base64,YWJj' }],
  };
  run('state.config.mode="project"; state.projectFile.report=dbmsReport; state.projectFile.normalized=dbmsReport.context; state.projectFile.validation.status="valid"; state.projectFile.images=[]; renderProjectReview(); renderProjectExportImageStatus()');
  assert.deepEqual(Array.from(run('getMissingProjectImages()')), ['query.png']);
  assert.equal(node('downloadBtn').disabled, true);
  assert.ok(node('reviewAccordion').innerHTML.includes('class="project-erd-figure"'));
  assert.ok(node('reviewAccordion').innerHTML.includes('&lt;diagram&gt;'));
  assert.ok(node('reviewAccordion').innerHTML.includes('&lt;img onerror='));
  assert.ok(!node('reviewAccordion').innerHTML.includes('<img onerror='));
  assert.ok(!node('reviewAccordion').innerHTML.includes('Member <script>'));
  run('state.projectFile.images=[{filename:"query.png", bytes:3}]; renderProjectExportImageStatus()');
  assert.equal(node('downloadBtn').disabled, false);
  assert.ok(node('projectExportImageStatus').textContent.includes('included automatically'));
  run('state.projectFile.images.push({filename:"erd.png",media_type:"image/png",data_base64:"ZGVm",bytes:3}); renderProjectImages()');
  assert.ok(node('reviewAccordion').innerHTML.includes('Uploaded ERD'));
  assert.ok(!node('reviewAccordion').innerHTML.includes('Generated E-R &lt;diagram&gt;'));
  assert.ok(node('projectExportImageStatus').textContent.includes('1 of 1 required'));
  run('state.projectFile.images.pop(); renderProjectImages(); persistSession()');
  assert.ok(node('reviewAccordion').innerHTML.includes('Generated E-R &lt;diagram&gt;'));
  const savedDbms = JSON.parse(stored.get('practigen_session_v6'));
  assert.equal(savedDbms.projectFile.report.erd_diagrams, undefined);
  assert.equal(savedDbms.projectFile.images.length, 0);

  // Restored diagrams use a no-AI preview call, with no keys or large preview bytes.
  let finishPreview;
  let previewCalls = 0;
  context.fetch = async (url, options) => {
    assert.equal(url, '/api/project/preview');
    assert.equal(JSON.parse(options.body).report.erd_diagrams, undefined);
    assert.ok(!options.body.includes('api_key'));
    previewCalls++;
    return new Promise(resolve => { finishPreview = resolve; });
  };
  run('state.projectFile.report=projectReportWithoutPreviews(dbmsReport)');
  const pendingPreview = run('refreshProjectDiagrams()');
  await run('refreshProjectDiagrams()');
  assert.equal(previewCalls, 1);
  assert.ok(node('reviewAccordion').innerHTML.includes('Loading the E-R diagram'));
  finishPreview(new Response(JSON.stringify({ report: context.dbmsReport })));
  await pendingPreview;
  assert.ok(node('reviewAccordion').innerHTML.includes('Generated E-R &lt;diagram&gt;'));
  run('state.projectFile.report=projectReportWithoutPreviews(dbmsReport)');
  const stalePreview = run('refreshProjectDiagrams()');
  run('state.projectFile.report=null');
  finishPreview(new Response(JSON.stringify({ report: context.dbmsReport })));
  await stalePreview;
  assert.equal(run('state.projectFile.report'), null);
  context.fetch = normalFetch;

  // Lab calls run serially, retry transient errors with a delay, and never retry bad credentials.
  run('state.config.mode="coding"; state.aimsText="Sort a list\\n---\\nReverse a list";');
  let inFlight = 0;
  let peakInFlight = 0;
  let labCalls = 0;
  const labResult = { concept: 'Valid explanation', code: 'print(1)', output: '1' };
  context.fetch = async url => {
    assert.equal(url, '/api/generate');
    labCalls++;
    inFlight++;
    peakInFlight = Math.max(peakInFlight, inFlight);
    await Promise.resolve();
    inFlight--;
    return new Response(JSON.stringify(labResult));
  };
  await run('startGeneration()');
  assert.equal(labCalls, 2);
  assert.equal(peakInFlight, 1);

  const retryDelays = [];
  context.setTimeout = (fn, delay) => { retryDelays.push(delay); fn(); return 1; };
  labCalls = 0;
  context.fetch = async () => {
    labCalls++;
    if (labCalls < 3) return new Response(JSON.stringify({ error: 'Timed out', retryable: true }), { status: 504 });
    return new Response(JSON.stringify(labResult));
  };
  run('state.isGenerating=true; state.experiments=[{id:0,aim:"Sort",status:"queued"}]');
  await run('generateOne(0)');
  assert.equal(labCalls, 3);
  assert.ok(retryDelays.includes(2000));
  assert.ok(retryDelays.includes(4000));
  assert.equal(run('state.experiments[0].status'), 'complete');

  labCalls = 0;
  context.fetch = async () => { labCalls++; return new Response(JSON.stringify({ error: 'Invalid key' }), { status: 401 }); };
  run('state.isGenerating=true; state.experiments=[{id:0,aim:"Sort",status:"queued"}]');
  await run('generateOne(0)');
  assert.equal(labCalls, 1);
  assert.equal(run('state.experiments[0].status'), 'failed');

  for (const [code, status] of [['llm_timeout', 504], ['llm_truncated', 502]]) {
    labCalls = 0;
    context.fetch = async () => {
      labCalls++;
      return new Response(JSON.stringify({ error: 'Adjust model settings before retrying', code,
        retryable: true, auto_retry: false }), { status });
    };
    run('state.isGenerating=true; state.experiments=[{id:0,aim:"Sort",status:"queued"}]');
    await run('generateOne(0)');
    assert.equal(labCalls, 1);
    assert.equal(run('state.experiments[0].status'), 'failed');
  }

  // A non-JSON server timeout must be treated as a failed request, never as a success.
  const gatewayFailure = new Response('Gateway timeout', { status: 504 });
  context.gatewayFailure = gatewayFailure;
  await assert.rejects(run('readGenerationResponse(gatewayFailure)'), /HTTP 504/);
  context.emptySuccess = new Response('{}');
  await assert.rejects(run('readGenerationResponse(emptySuccess)'), /no usable response/);
  context.badAuth = new Response('Unauthorized', { status: 401 });
  await assert.rejects(run('readGenerationResponse(badAuth)'), error => error.retryable === false);

  // Late responses after Stop must not overwrite saved experiment state.
  let finishLab;
  context.fetch = async () => new Promise(resolve => { finishLab = resolve; });
  run('state.isGenerating=true; state.experiments=[{id:0,aim:"Sort",status:"queued"}]');
  const stoppedLab = run('generateOne(0)');
  run('stopGeneration()');
  finishLab(new Response(JSON.stringify(labResult)));
  await stoppedLab;
  assert.notEqual(run('state.experiments[0].status'), 'complete');
  console.log('Project File frontend contracts passed (mode, prompt, validation, XSS, stale state and restore).');
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
