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
  console, Blob, URL, TextEncoder, Response,
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
  window: { innerWidth: 1200, innerHeight: 800 },
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
  run("selectMode('project')");
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

  run("markProjectContextStale('{\"changed\":true}')");
  assert.equal(run('state.projectFile.report'), null);
  assert.equal(node('projectGenerateBtn').disabled, true);

  run('state.projectFile.normalized=normalized; state.projectFile.report=report; state.projectFile.validation.status="valid"; persistSession()');
  run('state.projectFile=createEmptyProjectFileState(); restoreSession()');
  assert.equal(run('state.config.mode'), 'project');
  assert.equal(run('state.projectFile.report.title'), normalized.project.title);
  assert.equal(run('state.projectFile.images.length'), 0);

  run("selectProjectSubmode('dbms')");
  assert.equal(run('state.config.projectSubmode'), 'dbms');
  assert.equal(run('state.projectFile.report'), null);
  console.log('Project File frontend contracts passed (mode, prompt, validation, XSS, stale state and restore).');
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
