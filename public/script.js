/* ==========================================================================
   PractiGen V6 — Core Frontend Controller
   Wired to Flask backend APIs: /api/parse, /api/generate, /api/download
   ========================================================================== */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

/* ============================
   STATE
   ============================ */
const state = {
  currentView: 'dashboard',
  currentStep: 0,
  config: { mode: null, provider: null, apiKey: '', termUser: 'student', termHost: 'ubuntu', codeLang: 'c', jevQualityGate: false },
  aimsText: '',
  experiments: [],
  logs: [],
  isGenerating: false,
  generationComplete: false,
  expandedExps: new Set(),
  refiningExp: null,
  genTimeouts: [],
};

/* ============================
   DATA
   ============================ */
// Curated picker. Only first-party inference APIs — Groq and Cerebras.
// `tag` drives the pill on each card; 'preview' models are evaluation-only
// upstream and may be withdrawn at short notice.
const models = [
  {
    id: 'openai/gpt-oss-120b', name: 'GPT-OSS 120B', provider: 'groq', providerLabel: 'Groq',
    desc: 'Best all-round quality for code and theory', meta: '131K context', tag: 'Recommended',
  },
  {
    id: 'openai/gpt-oss-20b', name: 'GPT-OSS 20B', provider: 'groq', providerLabel: 'Groq',
    desc: 'Same family, far cheaper — good for big batches', meta: '131K context', tag: 'Fastest',
  },
  {
    id: 'llama-3.3-70b-versatile', name: 'Llama 3.3 70B', provider: 'groq', providerLabel: 'Groq',
    desc: 'Dependable general-purpose writer', meta: '131K context',
  },
  {
    id: 'llama-3.1-8b-instant', name: 'Llama 3.1 8B', provider: 'groq', providerLabel: 'Groq',
    desc: 'Lightest option; theory can come out thin', meta: '131K context',
  },
  {
    id: 'qwen/qwen3.8-27b', name: 'Qwen 3.8 27B', provider: 'groq', providerLabel: 'Groq',
    desc: 'Strong on structured code output', meta: '131K context', tag: 'Preview',
  },
  {
    id: 'minimaxai/minimax-m2.7', name: 'MiniMax M2.7', provider: 'groq', providerLabel: 'Groq',
    desc: 'Widest context — useful for very long labs', meta: '196K context', tag: 'Preview',
  },
  {
    id: 'gpt-oss-120b', name: 'GPT-OSS 120B', provider: 'cerebras', providerLabel: 'Cerebras',
    desc: 'Same model, roughly 3000 tokens/sec', meta: '131K context', tag: 'Fastest',
  },
  {
    id: 'qwen-3.8-27b', name: 'Qwen 3.8 27B', provider: 'cerebras', providerLabel: 'Cerebras',
    desc: 'Quick mid-size model for routine aims', meta: '128K context',
  },
];

const DEFAULT_MODEL_ID = 'openai/gpt-oss-120b';

const sampleAims = `Write a C program to implement FCFS CPU scheduling algorithm
---
Write a C program to implement SJF (Non-preemptive) CPU scheduling algorithm
---
Write a shell script to calculate factorial of a number
---
Write a C program to simulate Banker's Algorithm for deadlock avoidance
---
Write a shell script to check whether a given string is a palindrome
---
Write a C program to implement Round Robin CPU scheduling algorithm`;

/* ============================
   EXPORT/DOWNLOAD UTILITIES
   ============================ */
const MAX_RAW_EXPORT_BYTES = 2_750_000;
const MAX_COMPRESSED_EXPORT_BYTES = 3_500_000;
const EXPORT_PROFILES = [
  { lines: 28, chars: 120 },
  { lines: 14, chars: 100 },
  { lines: 8, chars: 90 }
];

function compactOutputForExport(output, profile) {
  const lines = String(output || '').split('\n');
  let shortened = lines.length > profile.lines;

  const compacted = lines.slice(0, profile.lines).map((line) => {
    if (line.length <= profile.chars) return line;
    shortened = true;
    return `${line.slice(0, profile.chars - 3)}...`;
  });

  if (shortened) {
    compacted.push(`[Output shortened for export. Showing first ${profile.lines} lines.]`);
  }

  return compacted.join('\n');
}

// An experiment renders as OS (step-by-step) when the global mode is OS, or when
// Auto-mode produced steps for it. Keeps mixed batches correct.
function expIsCn(exp) {
  return exp.detectedMode === 'cn' || Boolean(exp.cn);
}

function expIsOs(exp) {
  if (expIsCn(exp)) return false;
  return state.config.mode === 'os' || (Array.isArray(exp.steps) && exp.steps.length > 0);
}

function getDownloadExperiments(profile) {
  return state.experiments.filter(e => e.status === 'complete').map((exp) => {
    const base = {
      aim: exp.aim,
      concept: exp.theory,
      caption: exp.caption || 'Experiment Output'
    };

    if (expIsCn(exp)) return { mode: 'cn', aim: exp.aim, profile: exp.cn.profile };

    if (expIsOs(exp)) {
      return {
        ...base,
        steps: (exp.steps || []).map((step) => ({
          num: step.num,
          explanation: step.explanation,
          command: step.command,
          output: compactOutputForExport(step.output, profile)
        }))
      };
    }

    return {
      ...base,
      code: exp.code,
      output: compactOutputForExport(exp.output, profile)
    };
  });
}

function getExportUnitCount() {
  return state.experiments.filter(e => e.status === 'complete').reduce((count, exp) => {
    if (expIsCn(exp)) return count + exp.cn.figures.length;
    return count + (expIsOs(exp) ? Math.max((exp.steps || []).length, 1) : 1);
  }, 0);
}

function getDownloadPayload(settings) {
  let lastResult = null;
  const unitCount = getExportUnitCount();
  const startIndex = unitCount > 450 ? 2 : unitCount > 300 ? 1 : 0;

  for (const profile of EXPORT_PROFILES.slice(startIndex)) {
    const payload = {
      experiments: getDownloadExperiments(profile),
      settings,
      mode: state.config.mode
    };
    const body = JSON.stringify(payload);
    const bytes = new Blob([body]).size;
    lastResult = { payload, body, bytes, profile, unitCount };

    if (bytes <= MAX_RAW_EXPORT_BYTES) {
      return lastResult;
    }
  }

  return lastResult;
}

async function gzipText(text) {
  if (!('CompressionStream' in window)) {
    return null;
  }

  const compressedStream = new Blob([text])
    .stream()
    .pipeThrough(new CompressionStream('gzip'));

  return await new Response(compressedStream).arrayBuffer();
}

async function getDownloadRequest(downloadPayload) {
  const compressed = await gzipText(downloadPayload.body);

  if (!compressed) {
    if (downloadPayload.bytes > MAX_RAW_EXPORT_BYTES) {
      throw new Error('This browser cannot compress a large export payload. Update Chrome/Edge and try again.');
    }

    return {
      body: downloadPayload.body,
      headers: { 'Content-Type': 'application/json' },
      compressedBytes: null
    };
  }

  if (compressed.byteLength > MAX_COMPRESSED_EXPORT_BYTES) {
    throw new Error(`Compressed export is still too large (${Math.round(compressed.byteLength / 1024)} KB). Reduce generated output length and retry.`);
  }

  return {
    body: compressed,
    headers: {
      'Content-Type': 'application/json',
      'X-Content-Encoding': 'gzip'
    },
    compressedBytes: compressed.byteLength
  };
}

function getSettings() {
  const fmtImgW = parseFloat($('#fmtImgW').value) || 85;
  const fmtTermW = parseFloat($('#fmtTermW').value) || 90;
  
  // Convert percentage inputs to backend absolute units (inches and pixels)
  const imageWidthInches = ((fmtImgW / 100) * 6.0).toFixed(1);
  const terminalImgWidthPx = Math.round((fmtTermW / 100) * 650);

  return {
    fontName: $('#fmtFont').value,
    bodySize: $('#fmtBody').value,
    headingSize: $('#fmtHeading').value,
    codeSize: $('#fmtCode').value,
    captionSize: '10',
    imageWidth: imageWidthInches,
    terminalImgWidth: terminalImgWidthPx,
    student: state.config.student || {},
    outputFilename: state.config.mode === 'cn' ? 'Computer_Networks_Practical_File.docx' : 'Generated_Practical_File.docx'
  };
}

/* ============================
   NAVIGATION
   ============================ */
const viewStepMap = { setup: 1, aims: 2, generation: 3, review: 4, export: 5 };

function navigate(view) {
  // Stop generation if navigating away
  if (state.isGenerating && view !== 'generation') stopGeneration();

  state.currentView = view;
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  const el = document.getElementById('view-' + view);
  if (el) el.classList.add('active');

  // Update sidebar active link styling
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  if (view === 'dashboard') {
    document.querySelector('[data-view="dashboard"]').classList.add('active');
  } else {
    document.querySelector('[data-view="pipeline"]').classList.add('active');
  }

  // Update stepper state
  const step = viewStepMap[view] || 0;
  state.currentStep = step;
  const stepper = document.getElementById('phase-stepper');
  stepper.style.display = step > 0 ? 'flex' : 'none';
  updateStepper(step);

  // View-specific initializations
  if (view === 'review') renderReview();
  if (view === 'export') renderExport();
  if (view === 'generation') scrollTerminalToBottom('genTerminal');

  closeSidebar();
}

function navigateToPipeline() {
  if (state.currentView === 'dashboard') {
    navigate('setup');
  } else {
    navigate(state.currentView);
  }
}

function updateStepper(activeStep) {
  document.querySelectorAll('.step-item').forEach(el => {
    const s = parseInt(el.dataset.step);
    el.classList.remove('completed', 'active');
    if (s < activeStep) el.classList.add('completed');
    else if (s === activeStep) el.classList.add('active');
  });
  document.querySelectorAll('.step-line').forEach(el => {
    const l = parseInt(el.dataset.line);
    el.classList.toggle('completed', l < activeStep);
  });
}

/* ============================
   SIDEBAR MOBILE
   ============================ */
function openSidebar() {
  document.getElementById('sidebar').classList.add('open');
  document.getElementById('sidebar-overlay').classList.add('open');
}
function closeSidebar() {
  document.getElementById('sidebar').classList.remove('open');
  document.getElementById('sidebar-overlay').classList.remove('open');
}

/* ============================
   TOAST SYSTEM
   ============================ */
function showToast(type, message) {
  const container = document.getElementById('toast-container');
  const icons = { success: 'fa-circle-check', error: 'fa-circle-xmark', info: 'fa-circle-info', warn: 'fa-triangle-exclamation' };
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.innerHTML = `<i class="fas ${icons[type] || icons.info}"></i><span>${message}</span>`;
  container.appendChild(toast);
  setTimeout(() => {
    toast.classList.add('removing');
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

/* ============================
   DASHBOARD
   ============================ */
function initDashboard() {
  const h = new Date().getHours();
  let greet = 'Good evening';
  if (h < 12) greet = 'Good morning';
  else if (h < 17) greet = 'Good afternoon';
  document.getElementById('greeting').textContent = greet + '.';

  renderRecentSessions();
}

/* Render the most recent saved session (from sessionStorage), or an empty state. */
function renderRecentSessions() {
  const container = document.getElementById('recent-sessions');
  if (!container) return;

  let saved = null;
  try {
    saved = JSON.parse(sessionStorage.getItem(SESSION_KEY) || 'null');
  } catch {
    saved = null;
  }

  const exps = (saved && Array.isArray(saved.experiments)) ? saved.experiments : [];
  if (exps.length === 0) {
    container.innerHTML = `
      <div class="card" style="padding:1.25rem;text-align:center;color:var(--muted);font-size:0.85rem;">
        No recent sessions yet. Start a project — your progress is saved here until you close the tab.
      </div>`;
    return;
  }

  const done = exps.filter(e => e.status === 'complete').length;
  const mode = (saved.config && saved.config.mode) || 'coding';
  const isOs = mode === 'os';
  const title = exps[0] && exps[0].aim ? exps[0].aim : 'Practical session';
  const when = saved.savedAt
    ? new Date(saved.savedAt).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
    : 'recently';

  container.innerHTML = `
    <div class="card" style="padding:1rem 1.25rem;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:0.75rem;">
      <div style="display:flex;align-items:center;gap:0.85rem;min-width:0;">
        <div style="width:36px;height:36px;border-radius:8px;background:${isOs ? 'var(--accent-dim)' : 'var(--amber-dim)'};display:flex;align-items:center;justify-content:center;flex-shrink:0;">
          <i class="fas ${isOs ? 'fa-terminal' : 'fa-code'}" style="font-size:0.8rem;color:${isOs ? 'var(--accent)' : 'var(--amber)'};"></i>
        </div>
        <div style="min-width:0;">
          <p style="font-weight:600;font-size:0.9rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:240px;">${escapeHtml(title)}</p>
          <p style="font-size:0.75rem;color:var(--muted);">${done} experiment${done !== 1 ? 's' : ''} ready &middot; ${when}</p>
        </div>
      </div>
      <div style="display:flex;gap:0.5rem;">
        <button class="btn btn-secondary" style="padding:0.4rem 0.85rem;font-size:0.8rem;" onclick="restoreSession()">Resume</button>
        <button class="btn btn-ghost" style="padding:0.4rem 0.7rem;font-size:0.8rem;" onclick="clearSession();renderRecentSessions();">Clear</button>
      </div>
    </div>`;
}

function startNewProject() {
  state.config = { mode: null, provider: null, apiKey: '', termUser: 'student', termHost: 'ubuntu', codeLang: 'c', jevQualityGate: false };
  state.aimsText = '';
  state.experiments = [];
  state.logs = [];
  state.isGenerating = false;
  state.generationComplete = false;
  state.expandedExps.clear();
  
  document.getElementById('aimsEditor').value = '';
  updateEditorDisplay();

  document.querySelectorAll('.mode-card').forEach(c => c.classList.remove('selected'));
  document.querySelectorAll('.provider-card').forEach(c => c.classList.remove('selected'));
  document.getElementById('mode-options').style.display = 'none';
  selectMode(null);
  
  loadFromLocalStorage();
  navigate('setup');
}

/* ============================
   SETUP
   ============================ */
function initProviders() {
  const grid = document.getElementById('provider-grid');
  const tagClass = { Recommended: 'tag-rec', Fastest: 'tag-fast', Preview: 'tag-preview' };

  grid.innerHTML = models.map(m => `
    <div class="provider-card" data-model="${m.id}" data-provider="${m.provider}"
         role="radio" tabindex="0" aria-checked="false"
         onclick="selectModelConfig('${m.id}')"
         onkeydown="if(event.key===' '||event.key==='Enter'){event.preventDefault();selectModelConfig('${m.id}')}">
      <div class="pcard-top">
        <span class="pname">${m.name}</span>
        ${m.tag ? `<span class="ptag ${tagClass[m.tag] || ''}">${m.tag}</span>` : ''}
      </div>
      <p class="pdesc">${m.desc}</p>
      <div class="pcard-foot">
        <span class="pprovider" data-provider="${m.provider}">${m.providerLabel}</span>
        <span class="pmeta">${m.meta || ''}</span>
      </div>
    </div>
  `).join('');

  // A custom model ID overrides the card selection but keeps the chosen provider.
  document.getElementById('customModel').addEventListener('input', (e) => {
    const val = e.target.value.trim();
    if (val) {
      document.querySelectorAll('.provider-card').forEach(c => {
        c.classList.remove('selected');
        c.setAttribute('aria-checked', 'false');
      });
      state.config.model = val;
    } else {
      selectModelConfig(state.config.modelCardId || DEFAULT_MODEL_ID);
    }
  });
}

function selectMode(mode) {
  state.config.mode = mode;
  document.querySelectorAll('.mode-card').forEach(c => c.classList.toggle('selected', c.dataset.mode === mode));
  const opts = document.getElementById('mode-options');
  // Auto mode classifies each aim at generation time; OS + coding options both apply.
  opts.style.display = mode === 'auto' ? 'none' : 'block';
  document.getElementById('os-options').style.display = (mode === 'os' || mode === 'auto') ? 'block' : 'none';
  document.getElementById('coding-options').style.display = (mode === 'coding' || mode === 'auto') ? 'block' : 'none';
  if (mode === 'auto') {
    opts.style.display = 'block';
  }
  document.getElementById('cn-options').style.display = mode === 'cn' ? 'block' : 'none';
  for (const id of ['model-options', 'key-options']) document.getElementById(id).style.display = mode === 'cn' ? 'none' : 'block';
  document.getElementById('quality-options').style.display = mode === 'cn' ? 'none' : 'flex';
  if (mode === null) opts.style.display = 'none';
}

function selectModelConfig(id) {
  const m = models.find(x => x.id === id);
  if (!m) return;
  state.config.provider = m.provider;
  state.config.model = m.id;
  state.config.modelCardId = m.id;

  document.querySelectorAll('.provider-card').forEach(c => {
    const on = c.dataset.model === id;
    c.classList.toggle('selected', on);
    c.setAttribute('aria-checked', String(on));
  });
  document.getElementById('customModel').value = '';
}

function toggleApiKey() {
  const input = document.getElementById('apiKey');
  const eye = document.getElementById('apiKeyEye');
  if (input.type === 'password') { 
    input.type = 'text'; 
    eye.className = 'fas fa-eye-slash'; 
  } else { 
    input.type = 'password'; 
    eye.className = 'fas fa-eye'; 
  }
}

function toggleCollapse(id) {
  const el = document.getElementById(id);
  const chevron = document.getElementById('formatting-chevron');
  el.classList.toggle('open');
  chevron.style.transform = el.classList.contains('open') ? 'rotate(180deg)' : '';
}

function proceedToAims() {
  if (!state.config.mode) { 
    showToast('warn', 'Please select a generation mode.'); 
    return; 
  }
  if (state.config.mode === 'cn' && !document.getElementById('cnName').value.trim()) {
    showToast('warn', 'Enter your name to personalise the CN file.');
    return;
  }
  if (state.config.mode !== 'cn' && !state.config.model && !document.getElementById('customModel').value.trim()) {
    showToast('warn', 'Please select an LLM provider or enter a custom model ID.'); 
    return; 
  }
  
  saveToLocalStorage();
  navigate('aims');
}

/* ============================
   AIMS EDITOR
   ============================ */
const aimsEditor = document.getElementById('aimsEditor');
const lineNumbers = document.getElementById('lineNumbers');
const editorHighlight = document.getElementById('editorHighlight');
const editorBody = document.getElementById('editorBody');

function escapeHtml(str) {
  return String(str || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function updateEditorDisplay() {
  const text = aimsEditor.value;
  state.aimsText = text;

  // Highlight separator layer
  const lines = text.split('\n');
  const highlighted = lines.map(line => {
    if (line.trim() === '---') return `<span class="sep-line">${escapeHtml(line)}</span>`;
    return escapeHtml(line);
  }).join('\n');
  editorHighlight.innerHTML = highlighted + '\n';

  // Render line numbers
  const count = Math.max(lines.length, 1);
  lineNumbers.innerHTML = Array.from({ length: count }, (_, i) => i + 1).join('\n');

  // Aim count status badge
  const aims = parseAims(text);
  const badge = document.getElementById('aimCountBadge');
  badge.textContent = `${aims.length} experiment${aims.length !== 1 ? 's' : ''} detected`;
  badge.className = `badge ${aims.length > 0 ? 'badge-accent' : 'badge-muted'}`;
  document.getElementById('startGenBtn').disabled = aims.length === 0;
}

function parseAims(text) {
  if (state.config.mode === 'cn' && !text.includes('---')) {
    // Accept a pasted numbered index as well as one aim per line. Preserve wrapped aim lines.
    const numbered = text.split(/(?:^|\n)\s*(?:(?:Experiment|Exp\.?)\s*(?:No\.?\s*)?)?\d+[.)\-:]\s*/i).map(s => s.trim()).filter(Boolean);
    if (numbered.length > 1) return numbered;
    return text.split('\n').map(s => s.trim()).filter(Boolean);
  }
  return text.split('---').map(s => s.trim()).filter(s => s.length > 0);
}

if (aimsEditor) {
  aimsEditor.addEventListener('input', updateEditorDisplay);
}
if (editorBody) {
  editorBody.addEventListener('scroll', () => {
    editorHighlight.style.transform = `translateY(${-editorBody.scrollTop}px)`;
    lineNumbers.style.transform = `translateY(${-editorBody.scrollTop}px)`;
  });
}

// Drag & drop file imports
const editorWindow = document.getElementById('editorWindow');
if (editorWindow) {
  editorWindow.addEventListener('dragover', (e) => { 
    e.preventDefault(); 
    editorWindow.classList.add('drop-zone-active'); 
  });
  editorWindow.addEventListener('dragleave', () => editorWindow.classList.remove('drop-zone-active'));
  editorWindow.addEventListener('drop', (e) => {
    e.preventDefault();
    editorWindow.classList.remove('drop-zone-active');
    const file = e.dataTransfer.files[0];
    if (file && file.name.endsWith('.txt')) {
      const reader = new FileReader();
      reader.onload = (ev) => { 
        aimsEditor.value = ev.target.result; 
        updateEditorDisplay(); 
        showToast('success', `Imported ${file.name}`); 
      };
      reader.readAsText(file);
    } else {
      showToast('error', 'Please drop a .txt file.');
    }
  });
}

function importFile() { 
  document.getElementById('fileInput').click(); 
}
function handleFileImport(e) {
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = (ev) => { 
    aimsEditor.value = ev.target.result; 
    updateEditorDisplay(); 
    showToast('success', `Imported ${file.name}`); 
  };
  reader.readAsText(file);
  e.target.value = '';
}

async function loadSamples() {
  if (state.config.mode === 'cn') {
    try {
      const res = await fetch('/api/cn/catalog');
      if (!res.ok) throw new Error('Could not load CN sample aims.');
      const data = await res.json();
      aimsEditor.value = data.experiments.map(e => e.aim).join('\n---\n');
      updateEditorDisplay();
      showToast('success', 'Eight CN sample aims loaded');
    } catch (err) { showToast('error', err.message); }
    return;
  }
  aimsEditor.value = sampleAims;
  updateEditorDisplay();
  showToast('success', 'Sample aims loaded — 6 experiments detected');
}

/* ============================
   GENERATION
   ============================ */
async function startGeneration() {
  const aims = parseAims(state.aimsText);
  if (aims.length === 0) return;

  state.config.termUser = document.getElementById('termUser')?.value || 'student';
  state.config.termHost = document.getElementById('termHost')?.value || 'ubuntu';
  state.config.student = { name: $('#cnName').value.trim(), roll: $('#cnRoll').value.trim(), section: $('#cnSection').value.trim() };
  state.config.codeLang = document.getElementById('codeLang')?.value || 'c';
  state.config.jevQualityGate = Boolean(document.getElementById('jevQualityGate')?.checked);

  let provider = state.config.provider;
  let model = state.config.model;
  const customModel = document.getElementById('customModel').value.trim();
  if (customModel) {
    model = customModel;
    if (!provider) provider = 'groq';
  }

  const apiKey = document.getElementById('apiKey').value.trim();

  state.experiments = aims.map((aim, i) => ({
    id: i, aim, status: 'queued', number: String(i + 1).padStart(3, '0'),
    theory: '', code: '', output: '', caption: '', steps: []
  }));

  state.isGenerating = true;
  state.generationComplete = false;
  state.logs = [];
  
  document.getElementById('genTerminal').innerHTML = '';
  document.getElementById('reviewBtn').style.display = 'none';
  document.getElementById('genSpinner').style.display = 'block';
  document.getElementById('genSubtitle').textContent = state.config.mode === 'cn' ? 'Preparing personalised CN experiments...' : `Processing your experiments with ${model}...`;

  navigate('generation');
  renderExpGrid();

  addGenLog(`Starting generation for ${aims.length} experiment(s)`, 'info');
  addGenLog(state.config.mode === 'cn' ? 'CN templates: addressing, configurations and figures' : `Target: ${provider ? provider.toUpperCase() : 'CUSTOM'} / ${(model || '').toUpperCase()}`, 'info');
  addGenLog('Concurrency: 3 experiments in parallel', 'info');
  addGenLog('---', 'info');

  // Enhancement 1 — generate in parallel batches instead of one-at-a-time.
  const BATCH_SIZE = 3;
  for (let i = 0; i < state.experiments.length; i += BATCH_SIZE) {
    if (!state.isGenerating) break;
    const batch = state.experiments.slice(i, i + BATCH_SIZE);
    await Promise.all(batch.map((exp) => generateOne(exp.id)));
  }

  finishGeneration();
}

/**
 * Resolve the active provider/model/apiKey from config + custom override.
 * Shared by generation, retry, refine and classroom flows.
 */
function genContext() {
  let provider = state.config.provider;
  let model = state.config.model;
  const customModel = document.getElementById('customModel').value.trim();
  if (customModel) {
    model = customModel;
    if (!provider) provider = 'groq';
  }
  const apiKey = document.getElementById('apiKey').value.trim();
  return { provider, model, apiKey };
}

/**
 * Generate a single experiment (by id) with up to 3 attempts. Resolves
 * independently so cards update as each finishes. Enhancement 1 + Bug 1 retry.
 */
async function generateOne(idx) {
  const exp = state.experiments[idx];
  if (!exp || !state.isGenerating) return;

  const { provider, model, apiKey } = genContext();
  const autoDetect = state.config.mode === 'auto';

  exp.status = 'synthesizing';
  renderExpGrid();
  addGenLog(`Generating experiment ${idx + 1}/${state.experiments.length}...`, 'info');

  let success = false;
  let attempts = 0;
  const maxAttempts = 3;

  while (attempts < maxAttempts && !success && state.isGenerating) {
    attempts++;
    if (attempts > 1) {
      addGenLog(`Retrying experiment ${idx + 1} (attempt ${attempts}/${maxAttempts})...`, 'warn');
    }

    try {
      const seed = Math.random().toString(36).substring(2, 8);
      const response = await fetch('/api/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          aim: exp.aim,
          api_key: apiKey,
          provider,
          model,
          mode: autoDetect ? 'auto' : state.config.mode,
          auto_detect: autoDetect,
          code_language: state.config.codeLang,
          student: state.config.student || {},
          terminal_user: state.config.termUser,
          terminal_host: state.config.termHost,
          variation_seed: seed,
          jev_quality_gate: state.config.jevQualityGate
        })
      });

      const data = await response.json();
      if (data.error) {
        if (response.status === 400) attempts = maxAttempts;
        throw new Error(data.error);
      }

      exp.theory = data.concept || 'No concept description provided.';
      exp.code = data.code || '// No code provided.';
      exp.output = data.output || 'No output.';
      exp.caption = data.caption || 'Experiment Output';
      exp.steps = data.steps || [];
      exp.cn = data.mode === 'cn' ? data : null;
      if (data.code_language) exp.codeLanguage = data.code_language;
      if (data.mode) exp.detectedMode = data.mode;
      exp.qualityGate = data.quality_gate || null;
      exp.status = 'complete';
      success = true;

      addGenLog(`Experiment ${idx + 1} complete`, 'success');
      if (data.quality_gate?.status === 'complete') {
        addGenLog(`Jev quality gate: ${data.quality_gate.answers?.decision?.choice || 'review complete'}`, 'info');
      }
      persistSession();
    } catch (err) {
      addGenLog(`Experiment ${idx + 1} attempt ${attempts} failed: ${err.message}`, 'error');
      if (attempts >= maxAttempts) {
        exp.status = 'failed';
      }
    }
    renderExpGrid();
    updateGenProgress();
  }
}

function finishGeneration() {
  state.isGenerating = false;
  state.generationComplete = true;
  document.getElementById('genSpinner').style.display = 'none';
  document.getElementById('genSubtitle').textContent = 'All experiments processed.';
  
  const failed = state.experiments.filter(e => e.status === 'failed').length;
  const complete = state.experiments.filter(e => e.status === 'complete').length;

  persistSession();

  addGenLog('---', 'info');
  addGenLog(`Generation complete: ${complete} succeeded, ${failed} failed`, failed > 0 ? 'warn' : 'success');
  
  if (complete > 0) {
    const btn = document.getElementById('reviewBtn');
    btn.style.display = 'inline-flex';
    btn.style.animation = 'fadeSlideIn 0.4s ease';
  }
}

function stopGeneration() {
  state.isGenerating = false;
  state.genTimeouts.forEach(t => clearTimeout(t));
  state.genTimeouts = [];
  document.getElementById('genSpinner').style.display = 'none';
  navigate('aims');
}

async function retryExperiment(idx) {
  if (idx < 0 || idx >= state.experiments.length) return;
  const exp = state.experiments[idx];

  const { provider, model, apiKey } = genContext();
  const autoDetect = state.config.mode === 'auto';

  exp.status = 'synthesizing';
  renderExpGrid();
  addGenLog(`Retrying experiment ${idx + 1}...`, 'warn');

  try {
    const seed = Math.random().toString(36).substring(2, 8);
    const response = await fetch('/api/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        aim: exp.aim,
        api_key: apiKey,
        provider,
        model,
        mode: autoDetect ? 'auto' : state.config.mode,
        auto_detect: autoDetect,
        code_language: state.config.codeLang,
        student: state.config.student || {},
        terminal_user: state.config.termUser,
        terminal_host: state.config.termHost,
        variation_seed: seed,
        jev_quality_gate: state.config.jevQualityGate
      })
    });

    const data = await response.json();
    if (data.error) throw new Error(data.error);

    exp.theory = data.concept || 'No concept description provided.';
    exp.code = data.code || '// No code provided.';
    exp.output = data.output || 'No output.';
    exp.caption = data.caption || 'Experiment Output';
    exp.steps = data.steps || [];
    exp.cn = data.mode === 'cn' ? data : null;
    if (data.mode) exp.detectedMode = data.mode;
    if (data.code_language) exp.codeLanguage = data.code_language;
    exp.qualityGate = data.quality_gate || null;
    exp.status = 'complete';

    persistSession();
    addGenLog(`Experiment ${idx + 1} retry complete`, 'success');
  } catch (err) {
    exp.status = 'failed';
    addGenLog(`Experiment ${idx + 1} retry failed: ${err.message}`, 'error');
  }

  renderExpGrid();
  updateGenProgress();

  if (state.currentView === 'review') renderReview();
}

function renderExpGrid() {
  const grid = document.getElementById('expGrid');
  grid.innerHTML = state.experiments.map((exp, i) => {
    const statusIcons = { queued: 'fa-clock', synthesizing: 'fa-spinner fa-spin', complete: 'fa-circle-check', failed: 'fa-circle-xmark' };
    const statusColors = { queued: 'var(--muted)', synthesizing: 'var(--accent)', complete: 'var(--success)', failed: 'var(--error)' };
    const statusLabels = { queued: 'QUEUED', synthesizing: 'GENERATING', complete: 'COMPLETE', failed: 'FAILED' };
    return `
      <div class="exp-card ${exp.status}">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:0.5rem;">
          <span style="font-size:0.72rem;font-family:'JetBrains Mono',monospace;color:var(--muted);">EXP-${exp.number}</span>
          <i class="fas ${statusIcons[exp.status]}" style="color:${statusColors[exp.status]};font-size:0.85rem;"></i>
        </div>
        <p style="font-size:0.85rem;font-weight:600;line-height:1.3;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:0.3rem;">${escapeHtml(exp.aim)}</p>
        <p style="font-size:0.73rem;color:var(--muted);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;line-height:1.4;">${escapeHtml(exp.aim)}</p>
        <div style="margin-top:0.6rem;">
          <span class="badge" style="background:${exp.status === 'complete' ? 'rgba(34,197,94,0.1)' : exp.status === 'failed' ? 'var(--error-dim)' : exp.status === 'synthesizing' ? 'var(--accent-dim)' : 'var(--hover)'};color:${statusColors[exp.status]};font-size:0.65rem;">${statusLabels[exp.status]}</span>
          ${exp.status === 'failed' ? `<button class="btn btn-danger" style="margin-left:0.5rem;padding:0.2rem 0.6rem;font-size:0.72rem;" onclick="retryExperiment(${i})">RETRY</button>` : ''}
        </div>
      </div>`;
  }).join('');
}

function updateGenProgress() {
  const total = state.experiments.length;
  const done = state.experiments.filter(e => e.status === 'complete' || e.status === 'failed').length;
  const pct = Math.round((done / total) * 100);
  document.getElementById('genProgress').style.width = pct + '%';
  document.getElementById('genPercent').textContent = pct + '%';
  document.getElementById('genCount').textContent = `${done} of ${total}`;
}

function addGenLog(message, type) {
  const terminal = document.getElementById('genTerminal');
  const line = document.createElement('div');
  line.className = `log-line log-${type}`;
  const prefix = type === 'success' ? '✔' : type === 'error' ? '✖' : type === 'warn' ? '⚠' : '➜';
  if (message === '---') {
    line.className = 'log-line';
    line.textContent = '─────────────────────────';
  } else {
    line.textContent = `${prefix}  ${message}`;
  }
  terminal.appendChild(line);
  scrollTerminalToBottom('genTerminal');
}

function scrollTerminalToBottom(id) {
  const el = document.getElementById(id);
  if (el) {
    requestAnimationFrame(() => { el.scrollTop = el.scrollHeight; });
  }
}

/* ============================
   REVIEW
   ============================ */
function renderCnReview(exp, index) {
  const cn = exp.cn;
  const figures = cn.figures.map(fig => {
    const source = /^data:image\/png;base64,[A-Za-z0-9+/=]+$/.test(fig.data_url || '') ? fig.data_url : '';
    return `<figure class="cn-figure"><img src="${source}" alt="${escapeHtml(fig.caption)}" loading="lazy"><figcaption>${escapeHtml(fig.caption)}</figcaption></figure>`;
  }).join('');
  const configurations = cn.configurations.map(c => `<details><summary>${escapeHtml(c.device)} configuration</summary><pre class="code-block">${escapeHtml(c.commands.join('\n'))}</pre></details>`).join('');
  return `<div class="cn-review">
    <p class="helper">${escapeHtml([cn.profile.name, cn.profile.roll, cn.profile.section].filter(Boolean).join(' | '))}</p>
    <h4>Requirements</h4><ul>${cn.requirements.map(r => `<li>${escapeHtml(r)}</li>`).join('')}</ul>
    <h4>Procedure / Commands</h4><ol>${cn.procedure.map(s => `<li>${escapeHtml(s)}</li>`).join('')}</ol>
    ${configurations}<div class="cn-figures">${figures}</div>
    <p>${escapeHtml(cn.result)}</p>
    <button class="btn btn-secondary" onclick="downloadCnPlan(${index})">Download network plan</button>
  </div>`;
}

function downloadCnPlan(index) {
  const exp = state.experiments[index];
  const plan = { ...exp.cn, figures: exp.cn.figures.map(({ data_url, ...figure }) => figure) };
  const url = URL.createObjectURL(new Blob([JSON.stringify(plan, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a'); link.href = url; link.download = `CN_Experiment_${index+1}_Plan.json`; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function renderReview() {
  const complete = state.experiments.filter(e => e.status === 'complete');
  document.getElementById('reviewCountBadge').textContent = `${complete.length} experiment${complete.length !== 1 ? 's' : ''} ready`;

  const container = document.getElementById('reviewAccordion');
  if (complete.length === 0) {
    container.innerHTML = '<div class="card" style="text-align:center;color:var(--muted);padding:2rem;">No experiments were generated successfully.</div>';
    return;
  }

  container.innerHTML = complete.map((exp) => {
    const realIdx = state.experiments.indexOf(exp);
    const isOpen = state.expandedExps.has(realIdx);
    
    let stepsHtml = '';
    if (expIsCn(exp)) {
      stepsHtml = renderCnReview(exp, realIdx);
    } else if (expIsOs(exp) && exp.steps && exp.steps.length > 0) {
      stepsHtml = exp.steps.map(step => `
        <div style="margin-bottom:1.25rem;border-left:2px solid var(--accent-border);padding-left:1rem;">
          <p style="font-size:0.85rem;font-weight:600;color:var(--text);margin-bottom:0.4rem;">Step ${step.num}: ${escapeHtml(step.explanation)}</p>
          <div class="code-block" style="margin-bottom:0.5rem;white-space:pre-wrap;">${escapeHtml(step.command)}</div>
          ${step.output ? `<div class="code-block terminal-output" style="white-space:pre-wrap;">${escapeHtml(step.output)}</div>` : ''}
        </div>
      `).join('');
    } else {
      stepsHtml = `
        <div style="margin-bottom:1rem;">
          <h4 style="font-size:0.8rem;font-weight:600;color:var(--text-sec);margin-bottom:0.5rem;text-transform:uppercase;letter-spacing:0.05em;">
            <i class="fas fa-code" style="margin-right:0.4rem;color:var(--amber);"></i>Code
          </h4>
          <div class="code-block">${escapeHtml(exp.code)}</div>
        </div>
        <div>
          <h4 style="font-size:0.8rem;font-weight:600;color:var(--text-sec);margin-bottom:0.5rem;text-transform:uppercase;letter-spacing:0.05em;">
            <i class="fas fa-terminal" style="margin-right:0.4rem;color:var(--success);"></i>Output
          </h4>
          <div class="code-block terminal-output">${escapeHtml(exp.output)}</div>
        </div>
      `;
    }

    return `
      <div class="accordion-item" id="acc-${realIdx}">
        <div class="accordion-header" onclick="toggleAccordion(${realIdx})">
          <span style="font-size:0.75rem;font-family:'JetBrains Mono',monospace;color:var(--muted);flex-shrink:0;">EXP-${exp.number}</span>
          <span style="font-size:0.9rem;font-weight:600;flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding-left:0.5rem;padding-right:0.5rem;">${escapeHtml(exp.aim)}</span>
          <span class="badge badge-accent" style="flex-shrink:0;margin-right:0.5rem;">${expIsCn(exp) ? 'CN' : expIsOs(exp) ? 'Linux' : (exp.codeLanguage || state.config.codeLang || 'C').toUpperCase()}</span>
          <button class="btn btn-ghost" style="padding:0.3rem 0.65rem;font-size:0.78rem;flex-shrink:0;margin-right:0.5rem;" onclick="event.stopPropagation();${expIsCn(exp) ? `retryExperiment(${realIdx})` : `openRefineModal(${realIdx})`}">
            <i class="fas fa-wand-magic-sparkles"></i> ${expIsCn(exp) ? 'REGENERATE' : 'REFINE'}
          </button>
          <i class="fas fa-chevron-down accordion-chevron ${isOpen ? 'open' : ''}" id="chev-${realIdx}"></i>
        </div>
        <div class="accordion-body ${isOpen ? 'open' : ''}" id="body-${realIdx}">
          <div style="padding:0 1.25rem 1.25rem;">
            <div style="margin-bottom:1.25rem;">
              <h4 style="font-size:0.8rem;font-weight:600;color:var(--text-sec);margin-bottom:0.5rem;text-transform:uppercase;letter-spacing:0.05em;">
                <i class="fas fa-book-open" style="margin-right:0.4rem;color:var(--accent);"></i>Theory
              </h4>
              <p style="font-size:0.88rem;color:var(--text-sec);line-height:1.7;white-space:pre-line;">${escapeHtml(exp.theory)}</p>
            </div>
            ${stepsHtml}
          </div>
        </div>
      </div>`;
  }).join('');
}

function toggleAccordion(idx) {
  if (state.expandedExps.has(idx)) {
    state.expandedExps.delete(idx);
  } else {
    state.expandedExps.add(idx);
  }
  renderReview();
}

/* ============================
   REFINE MODAL
   ============================ */
function openRefineModal(idx) {
  state.refiningExp = idx;
  const exp = state.experiments[idx];
  document.getElementById('refineAim').textContent = exp.aim;
  document.getElementById('refineInput').value = '';
  document.getElementById('refine-modal').classList.add('open');
}

function closeRefineModal() {
  document.getElementById('refine-modal').classList.remove('open');
  state.refiningExp = null;
}

async function applyRefine() {
  const input = document.getElementById('refineInput').value.trim();
  if (!input) { 
    showToast('warn', 'Please describe what should change.'); 
    return; 
  }
  const idx = state.refiningExp;
  if (idx === null) return;

  closeRefineModal();
  showToast('info', `Refining EXP-${state.experiments[idx].number}...`);

  const exp = state.experiments[idx];
  
  let provider = state.config.provider;
  let model = state.config.model;
  const customModel = document.getElementById('customModel').value.trim();
  if (customModel) {
    model = customModel;
    if (!provider) provider = 'groq';
  }
  const apiKey = document.getElementById('apiKey').value.trim();

  try {
    // Bug 3 — call the dedicated /api/refine endpoint with the FULL existing
    // experiment so the LLM edits it instead of regenerating from scratch.
    const response = await fetch('/api/refine', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        aim: exp.aim,
        change_request: input,
        existing_concept: exp.theory,
        existing_code: exp.code,
        existing_output: exp.output,
        existing_steps: exp.steps || [],
        api_key: apiKey,
        provider,
        model,
        mode: state.config.mode,
        code_language: state.config.codeLang,
        student: state.config.student || {},
        terminal_user: state.config.termUser,
        terminal_host: state.config.termHost
      })
    });

    const data = await response.json();
    if (data.error) throw new Error(data.error);

    exp.theory = data.concept || 'No concept description provided.';
    exp.code = data.code || '// No code provided.';
    exp.output = data.output || 'No output.';
    exp.caption = data.caption || 'Experiment Output';
    exp.steps = data.steps || [];

    persistSession();
    showToast('success', `EXP-${exp.number} refined successfully.`);
    renderReview();
  } catch (err) {
    showToast('error', `Refinement failed: ${err.message}`);
  }
}

/* ============================
   EXPORT
   ============================ */
function renderExport() {
  document.getElementById('classroom-panel').style.display = state.experiments.some(expIsCn) ? 'none' : 'block';
  const complete = state.experiments.filter(e => e.status === 'complete');
  document.getElementById('artifactTotal').textContent = `${complete.length} experiment${complete.length !== 1 ? 's' : ''}`;
  const sizeKB = complete.length * 12 + 8;
  document.getElementById('artifactSize').textContent = `~${sizeKB} KB estimated`;

  const list = document.getElementById('artifactList');
  list.innerHTML = complete.map(exp => `
    <div style="display:flex;align-items:center;gap:0.75rem;padding:0.65rem 0.85rem;background:var(--bg);border:1px solid var(--border);border-radius:8px;">
      <i class="fas fa-circle-check" style="color:var(--success);font-size:0.85rem;flex-shrink:0;"></i>
      <span style="font-size:0.75rem;font-family:'JetBrains Mono',monospace;color:var(--muted);flex-shrink:0;">EXP-${exp.number}</span>
      <span style="font-size:0.85rem;flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${escapeHtml(exp.aim)}</span>
    </div>
  `).join('');

  document.getElementById('downloadBtn').style.display = 'flex';
  document.getElementById('downloadSuccess').style.display = 'none';

  const terminal = document.getElementById('exportTerminal');
  terminal.innerHTML = '';
  const logLines = [
    { msg: 'Preparing document export...', type: 'info' },
    { msg: `Including ${complete.length} experiments`, type: 'info' },
    { msg: 'Applying formatting settings', type: 'info' },
    { msg: 'Generating code blocks', type: 'info' },
    { msg: 'Rendering terminal outputs', type: 'info' },
    { msg: 'Building .docx structure', type: 'info' },
    { msg: 'Document ready for download.', type: 'success' },
  ];
  logLines.forEach((l, i) => {
    setTimeout(() => {
      const line = document.createElement('div');
      line.className = `log-line log-${l.type}`;
      const prefix = l.type === 'success' ? '✔' : '➜';
      line.textContent = `${prefix}  ${l.msg}`;
      terminal.appendChild(line);
      scrollTerminalToBottom('exportTerminal');
    }, i * 200);
  });
}

function addExportLog(message, type) {
  const terminal = document.getElementById('exportTerminal');
  if (!terminal) return;
  const line = document.createElement('div');
  line.className = `log-line log-${type}`;
  const prefix = type === 'success' ? '✔' : type === 'error' ? '✖' : '➜';
  line.textContent = `${prefix}  ${message}`;
  terminal.appendChild(line);
  scrollTerminalToBottom('exportTerminal');
}

async function downloadDocument() {
  const btn = document.getElementById('downloadBtn');

  // Bug 4 — warn before assembling a very large document.
  const completeCount = state.experiments.filter(e => e.status === 'complete').length;
  if (completeCount > 30) {
    showToast('warn', `Large export (${completeCount} experiments). This may take a while or be split into smaller batches.`);
  }

  btn.disabled = true;
  btn.innerHTML = '<div class="spinner"></div> Generating...';

  try {
    const settings = getSettings();
    const downloadPayload = getDownloadPayload(settings);
    const downloadRequest = await getDownloadRequest(downloadPayload);
    
    const compressedLog = downloadRequest.compressedBytes
      ? `, compressed to ${Math.round(downloadRequest.compressedBytes / 1024)} KB`
      : '';
    
    addExportLog(`Export payload prepared (${Math.round(downloadPayload.bytes / 1024)} KB${compressedLog}, ${downloadPayload.profile.lines}-line output profile).`, 'info');

    const res = await fetch('/api/download', {
      method: 'POST',
      headers: downloadRequest.headers,
      body: downloadRequest.body,
    });

    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.error || 'Server rejected bundle request.');
    }

    const blob = await res.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = settings.outputFilename || 'Generated_Practical_File.docx';
    document.body.appendChild(a);
    a.click();
    window.URL.revokeObjectURL(url);
    a.remove();

    btn.style.display = 'none';
    document.getElementById('downloadSuccess').style.display = 'block';
    document.getElementById('downloadSuccess').style.animation = 'fadeSlideIn 0.4s ease';
    launchConfetti();
    showToast('success', 'Document downloaded successfully.');
    addExportLog('Export successful. Project completed.', 'success');

    setTimeout(() => {
      btn.disabled = false;
      btn.innerHTML = '<i class="fas fa-cloud-arrow-down"></i> DOWNLOAD DOCUMENT';
      btn.style.display = 'flex';
      document.getElementById('downloadSuccess').style.display = 'none';
    }, 5000);

  } catch (err) {
    console.error(err);
    showToast('error', `Bundle export error: ${err.message}`);
    addExportLog(`CRITICAL EXPORT ERROR: ${err.message}`, 'error');
    btn.disabled = false;
    btn.innerHTML = '<i class="fas fa-cloud-arrow-down"></i> DOWNLOAD DOCUMENT';
  }
}

function toggleClassroom() {
  const toggle = document.getElementById('classroomToggle');
  const opts = document.getElementById('classroomOptions');
  const knob = document.getElementById('classroomKnob');
  if (toggle.checked) {
    opts.style.display = 'block';
    knob.style.transform = 'translateX(18px)';
    toggle.parentElement.querySelector('span:first-of-type').style.background = 'var(--accent)';
  } else {
    opts.style.display = 'none';
    knob.style.transform = 'translateX(0)';
    toggle.parentElement.querySelector('span:first-of-type').style.background = 'var(--border)';
  }
}

/* ============================
   PHASE 3 — AUTO-DETECT MODE + LANGUAGE
   ============================ */
async function autoDetectAims() {
  const aims = parseAims(state.aimsText);
  if (aims.length === 0) {
    showToast('warn', 'Write some aims first.');
    return;
  }
  showToast('info', 'Analyzing aims...');
  try {
    const res = await fetch('/api/detect', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ aims })
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);

    const dets = data.detections || [];
    const osCount = dets.filter(d => d.mode === 'os').length;
    const cnCount = dets.filter(d => d.mode === 'cn').length;
    const genCount = dets.length - osCount - cnCount;
    if (cnCount) {
      selectMode(cnCount === dets.length ? 'cn' : 'auto');
      showToast('success', `Detected ${cnCount} CN aim(s). ${cnCount === dets.length ? 'CN mode selected; add student details in Setup.' : 'Using Auto for the mixed batch.'}`);
      return;
    }
    const langs = [...new Set(dets.map(d => d.code_language).filter(Boolean))];

    // If the batch is mixed, recommend Auto mode (per-experiment detection at generate time).
    if (osCount > 0 && genCount > 0) {
      state.config.mode = 'auto';
      showToast('success', `Mixed batch detected (${osCount} OS, ${genCount} coding). Using Auto mode — each experiment is classified at generation.`);
    } else if (osCount > 0) {
      state.config.mode = 'os';
      showToast('success', `Detected: OS practical mode for all ${osCount} aims.`);
    } else {
      state.config.mode = 'coding';
      if (langs.length === 1) {
        state.config.codeLang = langs[0];
        const sel = document.getElementById('codeLang');
        if (sel) sel.value = langs[0];
      }
      showToast('success', `Detected: General coding${langs.length ? ' (' + langs.join(', ') + ')' : ''}.`);
    }
  } catch (err) {
    showToast('error', `Auto-detect failed: ${err.message}`);
  }
}

/* ============================
   PHASE 3 — SYLLABUS PDF IMPORT
   ============================ */
function importSyllabusPdf() {
  document.getElementById('pdfInput').click();
}

async function handlePdfImport(e) {
  const file = e.target.files[0];
  e.target.value = '';
  if (!file) return;
  if (!file.name.toLowerCase().endsWith('.pdf')) {
    showToast('error', 'Please choose a .pdf file.');
    return;
  }

  showToast('info', `Extracting aims from ${file.name}...`);
  const { provider, model, apiKey } = genContext();

  const form = new FormData();
  form.append('file', file);
  form.append('api_key', apiKey);
  form.append('provider', provider || 'groq');
  form.append('model', model || DEFAULT_MODEL_ID);

  try {
    const res = await fetch('/api/extract-aims', { method: 'POST', body: form });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    if (!data.aims || data.aims.length === 0) throw new Error('No aims found in the PDF.');

    aimsEditor.value = data.text || data.aims.join('\n---\n');
    updateEditorDisplay();
    showToast('success', `Extracted ${data.aims.length} aim(s) from syllabus.`);
  } catch (err) {
    showToast('error', `PDF import failed: ${err.message}`);
  }
}

/* ============================
   PHASE 3 — CLASSROOM BULK VARIATIONS
   ============================ */
const CLASSROOM_MAX_STUDENTS = 12;

async function downloadClassroomZip() {
  const completeExps = state.experiments.filter(e => e.status === 'complete');
  if (completeExps.length === 0) {
    showToast('warn', 'Generate experiments first.');
    return;
  }

  let count = parseInt(document.getElementById('studentCount').value, 10) || 0;
  if (count < 2) { showToast('warn', 'Enter at least 2 students.'); return; }
  if (count > CLASSROOM_MAX_STUDENTS) {
    count = CLASSROOM_MAX_STUDENTS;
    showToast('warn', `Capped at ${CLASSROOM_MAX_STUDENTS} students to stay within rate limits.`);
  }

  const btn = document.getElementById('classroomBtn');
  const original = btn.innerHTML;
  btn.disabled = true;

  const { provider, model, apiKey } = genContext();
  const profile = EXPORT_PROFILES[0];
  const students = [];

  try {
    for (let s = 0; s < count; s++) {
      btn.innerHTML = `<div class="spinner"></div> Student ${s + 1}/${count}...`;

      // Re-generate each experiment with a fresh seed → unique variation per student.
      const variations = await Promise.all(completeExps.map(async (exp) => {
        const seed = Math.random().toString(36).substring(2, 8);
        try {
          const res = await fetch('/api/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              aim: exp.aim,
              api_key: apiKey,
              provider,
              model,
              mode: state.config.mode === 'auto' ? 'auto' : state.config.mode,
              auto_detect: state.config.mode === 'auto',
              code_language: state.config.codeLang,
              student: state.config.student || {},
              terminal_user: state.config.termUser,
              terminal_host: state.config.termHost,
              variation_seed: seed
            })
          });
          const data = await res.json();
          if (data.error) throw new Error(data.error);
          return packExperimentForExport({
            aim: exp.aim,
            theory: data.concept,
            code: data.code,
            output: data.output,
            caption: data.caption,
            steps: data.steps || []
          }, profile);
        } catch {
          // Fall back to the original variation if a re-gen fails.
          return packExperimentForExport(exp, profile);
        }
      }));

      students.push({ name: `Student_${String(s + 1).padStart(2, '0')}`, experiments: variations });
    }

    btn.innerHTML = '<div class="spinner"></div> Zipping...';
    const payload = { students, settings: getSettings(), mode: state.config.mode === 'auto' ? 'general' : state.config.mode };
    const body = JSON.stringify(payload);
    const compressed = await gzipText(body);
    const headers = compressed ? { 'Content-Type': 'application/json', 'X-Content-Encoding': 'gzip' } : { 'Content-Type': 'application/json' };

    const res = await fetch('/api/classroom-zip', { method: 'POST', headers, body: compressed || body });
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.error || 'Classroom export rejected.');
    }
    const blob = await res.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'Classroom_Variations.zip';
    document.body.appendChild(a);
    a.click();
    window.URL.revokeObjectURL(url);
    a.remove();
    showToast('success', `Downloaded ${count} unique variations.`);
  } catch (err) {
    showToast('error', `Classroom export failed: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.innerHTML = original;
  }
}

/* Shape an in-memory experiment into the backend export schema (mirrors getDownloadExperiments). */
function packExperimentForExport(exp, profile) {
  const base = {
    aim: exp.aim,
    concept: exp.theory,
    caption: exp.caption || 'Experiment Output'
  };
  if (expIsCn(exp)) return { mode: 'cn', aim: exp.aim, profile: exp.cn.profile };
  if (expIsOs(exp)) {
    return {
      ...base,
      steps: (exp.steps || []).map((step) => ({
        num: step.num,
        explanation: step.explanation,
        command: step.command,
        output: compactOutputForExport(step.output, profile)
      }))
    };
  }
  return { ...base, code: exp.code, output: compactOutputForExport(exp.output, profile) };
}

/* ============================
   CONFETTI
   ============================ */
function launchConfetti() {
  const canvas = document.getElementById('confetti-canvas');
  const ctx = canvas.getContext('2d');
  canvas.width = window.innerWidth;
  canvas.height = window.innerHeight;
  canvas.style.display = 'block';

  const colors = ['#00e5a0', '#f0a030', '#22c55e', '#f59e0b', '#f4f4f5', '#2dd4bf'];
  const particles = [];
  for (let i = 0; i < 120; i++) {
    particles.push({
      x: canvas.width / 2 + (Math.random() - 0.5) * 100,
      y: canvas.height / 2,
      vx: (Math.random() - 0.5) * 18,
      vy: (Math.random() - 0.5) * 18 - 8,
      w: Math.random() * 10 + 4,
      h: Math.random() * 6 + 2,
      color: colors[Math.floor(Math.random() * colors.length)],
      rot: Math.random() * 360,
      rotV: (Math.random() - 0.5) * 12,
      opacity: 1,
      gravity: 0.25 + Math.random() * 0.15,
    });
  }

  function animate() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    let alive = false;
    particles.forEach(p => {
      p.x += p.vx;
      p.vy += p.gravity;
      p.y += p.vy;
      p.vx *= 0.99;
      p.rot += p.rotV;
      p.opacity -= 0.007;
      if (p.opacity <= 0) return;
      alive = true;
      ctx.save();
      ctx.translate(p.x, p.y);
      ctx.rotate(p.rot * Math.PI / 180);
      ctx.globalAlpha = Math.max(0, p.opacity);
      ctx.fillStyle = p.color;
      ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h);
      ctx.restore();
    });
    if (alive) requestAnimationFrame(animate);
    else canvas.style.display = 'none';
  }
  animate();
}

/* ============================
   MOUSE GLOW
   ============================ */
document.addEventListener('mousemove', (e) => {
  document.body.style.setProperty('--mouse-x', (e.clientX / window.innerWidth).toFixed(3));
  document.body.style.setProperty('--mouse-y', (e.clientY / window.innerHeight).toFixed(3));
});

/* ============================
   KEYBOARD NAV
   ============================ */
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    if (document.getElementById('refine-modal').classList.contains('open')) closeRefineModal();
    closeSidebar();
  }
});

/* ============================
   LOCAL STORAGE STORAGE HELPERS
   ============================ */
function saveToLocalStorage() {
  localStorage.setItem('practigen_api_key_v5', $('#apiKey').value);
  localStorage.setItem('practigen_code_language_v5', $('#codeLang').value);
  localStorage.setItem('practigen_term_user_v5', $('#termUser').value);
  localStorage.setItem('practigen_term_host_v5', $('#termHost').value);
  localStorage.setItem('practigen_custom_model_v5', $('#customModel').value.trim());
  localStorage.setItem('practigen_jev_quality_gate_v1', String($('#jevQualityGate').checked));
}

function loadFromLocalStorage() {
  $('#apiKey').value = localStorage.getItem('practigen_api_key_v5') || '';
  $('#codeLang').value = localStorage.getItem('practigen_code_language_v5') || 'c';
  $('#termUser').value = localStorage.getItem('practigen_term_user_v5') || 'student';
  $('#termHost').value = localStorage.getItem('practigen_term_host_v5') || 'ubuntu';
  $('#customModel').value = localStorage.getItem('practigen_custom_model_v5') || '';
  $('#jevQualityGate').checked = localStorage.getItem('practigen_jev_quality_gate_v1') === 'true';
}

/* ============================
   SESSION PERSISTENCE (Enhancement 3)
   sessionStorage auto-clears on browser close, avoiding stale data.
   ============================ */
const SESSION_KEY = 'practigen_session_v6';

function persistSession() {
  try {
    const payload = {
      experiments: state.experiments,
      config: state.config,
      aimsText: state.aimsText,
      savedAt: Date.now()
    };
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(payload));
  } catch (err) {
    console.warn('Could not persist session', err);
  }
}

function maybeOfferRestore() {
  let saved;
  try {
    saved = JSON.parse(sessionStorage.getItem(SESSION_KEY) || 'null');
  } catch {
    saved = null;
  }
  if (!saved || !Array.isArray(saved.experiments) || saved.experiments.length === 0) return;

  const done = saved.experiments.filter(e => e.status === 'complete').length;
  const banner = document.getElementById('restoreBanner');
  if (!banner) return;
  document.getElementById('restoreText').textContent =
    `Previous session found — ${done} experiment${done !== 1 ? 's' : ''} ready. Restore?`;
  banner.style.display = 'flex';
}

function restoreSession() {
  let saved;
  try {
    saved = JSON.parse(sessionStorage.getItem(SESSION_KEY) || 'null');
  } catch {
    saved = null;
  }
  if (!saved) return;

  state.experiments = saved.experiments || [];
  state.config = { ...state.config, ...(saved.config || {}) };
  state.aimsText = saved.aimsText || '';
  state.generationComplete = true;
  state.config.student ||= state.experiments.find(expIsCn)?.cn.profile || {};
  for (const [id, key] of [['cnName', 'name'], ['cnRoll', 'roll'], ['cnSection', 'section']]) $('#'+id).value = state.config.student?.[key] || '';
  selectMode(state.config.mode);

  const banner = document.getElementById('restoreBanner');
  if (banner) banner.style.display = 'none';

  showToast('success', 'Session restored.');
  navigate('review');
}

function clearSession() {
  sessionStorage.removeItem(SESSION_KEY);
  const banner = document.getElementById('restoreBanner');
  if (banner) banner.style.display = 'none';
  showToast('info', 'Saved session cleared.');
}

/* ============================
   INIT
   ============================ */
function init() {
  initDashboard();
  initProviders();
  loadFromLocalStorage();
  updateEditorDisplay();

  // Select the recommended model by default
  selectModelConfig(DEFAULT_MODEL_ID);

  // Enhancement 3 — offer to restore a previous session if one exists.
  maybeOfferRestore();
}

init();
