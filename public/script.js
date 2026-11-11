/* ==========================================================================
   PractiGen V6 — Core Frontend Controller
   Wired to Flask backend APIs: /api/parse, /api/generate, /api/download
   ========================================================================== */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

function createEmptyProjectFileState() {
  return {
    rawContext: '',
    prompt: '',
    normalized: null,
    validation: {
      status: 'idle', errors: [], missingFields: [], warnings: [], generationNeeded: []
    },
    report: null,
    completedSections: {},
    sectionParts: {},
    images: [],
    generationStatus: 'idle'
  };
}

/* ============================
   STATE
   ============================ */
const state = {
  currentView: 'dashboard',
  currentStep: 0,
  config: { mode: null, mainMode: null, projectSubmode: null, baseUrl: '', model: '', apiKey: '', termUser: 'student', termHost: 'ubuntu', codeLang: 'c', jevQualityGate: false },
  aimsText: '',
  experiments: [],
  logs: [],
  isGenerating: false,
  generationComplete: false,
  expandedExps: new Set(),
  refiningExp: null,
  genTimeouts: [],
  projectFile: createEmptyProjectFileState(),
};

/* ============================
   DATA
   ============================ */
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
  if (!['dashboard', ...Object.keys(viewStepMap)].includes(view)) return;
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
  if (view === 'dashboard') initDashboard();
  if (view === 'aims') renderProjectWorkspace();
  if (view === 'review') renderReview();
  if (view === 'export') renderExport();
  if (view === 'generation') scrollTerminalToBottom('genTerminal');

  closeSidebar();
  document.title = `${view === 'dashboard' ? 'Workspace' : view.charAt(0).toUpperCase() + view.slice(1)} — PractiGen`;
  document.getElementById('content').focus?.({ preventScroll: true });
  window.scrollTo?.({ top: 0, behavior: 'instant' });
}

function navigateStep(view) {
  if (view === 'setup') return navigate('setup');
  if (view === 'aims') return proceedToAims();
  const ready = state.config.mode === 'project'
    ? Boolean(state.projectFile.report)
    : state.experiments.some(exp => exp.status === 'complete');
  if ((view === 'review' || view === 'export') && ready) return navigate(view);
  if (view === 'generation' && (state.isGenerating || ready || state.projectFile.generationStatus === 'failed' || state.experiments.length)) return navigate(view);
  showToast('info', 'Generate your content first. Your next step is available below.');
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
    if (s === activeStep) el.setAttribute('aria-current', 'step');
    else el.removeAttribute?.('aria-current');
  });
  document.querySelectorAll('.step-line').forEach(el => {
    const l = parseInt(el.dataset.line);
    el.classList.toggle('completed', l < activeStep);
  });
  revealActiveStep();
}

function revealActiveStep() {
  const stepper = document.getElementById('phase-stepper');
  const active = document.querySelector('.step-item.active');
  if (!active || stepper.scrollWidth <= stepper.clientWidth || !stepper.getBoundingClientRect) return;
  const item = active.getBoundingClientRect();
  const bounds = stepper.getBoundingClientRect();
  stepper.scrollLeft += item.left - bounds.left - (stepper.clientWidth - item.width) / 2;
}

/* ============================
   SIDEBAR MOBILE
   ============================ */
function openSidebar() {
  document.getElementById('sidebar').classList.add('open');
  document.getElementById('sidebar-overlay').classList.add('open');
  document.getElementById('workspace-menu').setAttribute('aria-expanded', 'true');
  document.getElementById('workspace-menu').setAttribute('aria-label', 'Close navigation');
}
function closeSidebar() {
  document.getElementById('sidebar').classList.remove('open');
  document.getElementById('sidebar-overlay').classList.remove('open');
  document.getElementById('workspace-menu').setAttribute('aria-expanded', 'false');
  document.getElementById('workspace-menu').setAttribute('aria-label', 'Open navigation');
}

function toggleWorkspaceMenu() {
  if (document.getElementById('sidebar').classList.contains('open')) closeSidebar();
  else openSidebar();
}

/* ============================
   TOAST SYSTEM
   ============================ */
function showToast(type, message) {
  const container = document.getElementById('toast-container');
  const icons = { success: 'fa-circle-check', error: 'fa-circle-xmark', info: 'fa-circle-info', warn: 'fa-triangle-exclamation' };
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  const icon = document.createElement('i');
  icon.className = `fas ${icons[type] || icons.info}`;
  const text = document.createElement('span');
  text.textContent = String(message || '');
  toast.appendChild(icon);
  toast.appendChild(text);
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
  const savedProject = saved && saved.config?.mode === 'project' ? saved.projectFile : null;
  const hasProject = Boolean(savedProject && (savedProject.report || savedProject.normalized || savedProject.rawContext));
  if (exps.length === 0 && !hasProject) {
    container.innerHTML = `
      <div class="empty-session"><i aria-hidden="true" class="far fa-folder-open"></i><div>
        <h4>A fresh start.</h4><p>Create your first file above. Your latest session will appear here.</p>
      </div></div>`;
    return;
  }

  const done = hasProject ? (savedProject.report ? 1 : 0) : exps.filter(e => e.status === 'complete').length;
  const mode = (saved.config && saved.config.mode) || 'coding';
  const isOs = mode === 'os';
  const isProject = mode === 'project';
  const title = isProject
    ? (savedProject.report?.title || savedProject.normalized?.project?.title || 'Project report')
    : (exps[0] && exps[0].aim ? exps[0].aim : 'Practical session');
  const when = saved.savedAt
    ? new Date(saved.savedAt).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
    : 'recently';

  const progress = isProject
    ? (done ? 'Report ready' : `${Object.keys(savedProject.completedSections || {}).length} sections saved`)
    : `${done} of ${exps.length} experiments ready`;
  container.innerHTML = `
    <div class="session-row">
      <span class="session-icon"><i aria-hidden="true" class="fas ${isProject ? 'fa-folder-open' : isOs ? 'fa-terminal' : 'fa-code'}"></i></span>
      <div class="session-info"><strong>${escapeHtml(title)}</strong><p>${escapeHtml(progress)} &middot; ${escapeHtml(when)}</p></div>
      <div class="action-row"><button class="btn btn-dark" onclick="restoreSession()">Resume <i aria-hidden="true" class="fas fa-arrow-right"></i></button><button class="btn btn-ghost" onclick="clearSession()">Clear</button></div>
    </div>`;
}

function startNewProject(mainMode = null) {
  cancelProjectGeneration();
  projectValidationSequence++;
  projectGenerationSequence++;
  projectImageSequence++;
  state.config = { mode: null, mainMode: null, projectSubmode: null, baseUrl: '', model: '', apiKey: '', termUser: 'student', termHost: 'ubuntu', codeLang: 'c', jevQualityGate: false };
  state.aimsText = '';
  state.experiments = [];
  state.logs = [];
  state.isGenerating = false;
  state.generationComplete = false;
  state.projectFile = createEmptyProjectFileState();
  state.expandedExps.clear();
  
  document.getElementById('aimsEditor').value = '';
  document.getElementById('projectContextInput').value = '';
  updateEditorDisplay();

  document.querySelectorAll('.mode-card').forEach(c => c.classList.remove('selected'));
  document.getElementById('mode-options').style.display = 'none';
  selectMode(null);
  
  loadFromLocalStorage();
  if (mainMode) selectMainMode(mainMode);
  navigate('setup');
}

/* ============================
   SETUP
   ============================ */
function selectMainMode(mainMode) {
  if (mainMode === 'project') selectMode('project');
  else if (mainMode === 'lab') selectMode('coding');
  else if (mainMode === 'custom') selectMode(state.config.customStyle || 'os');
}

function selectMode(mode) {
  if (state.config.mode === 'project' && mode !== 'project') cancelProjectGeneration();
  state.config.mode = mode;
  state.config.mainMode = mode === 'project' ? 'project' : mode === 'coding' ? 'lab' : mode ? 'custom' : null;
  if (['os', 'cn', 'auto'].includes(mode)) state.config.customStyle = mode;
  document.querySelectorAll('.mode-card').forEach(card => {
    const selected = card.dataset.mode === state.config.mainMode;
    card.classList.toggle('selected', selected);
    card.setAttribute('aria-pressed', String(selected));
  });
  document.getElementById('custom-file-options').style.display = state.config.mainMode === 'custom' ? 'block' : 'none';
  document.getElementById('fileStyle').value = state.config.customStyle || 'os';
  const labels = { project: 'PROJECT REPORT', coding: 'PROGRAMMING LAB', os: 'OS PRACTICAL', cn: 'COMPUTER NETWORKS', auto: 'CUSTOM / MIXED' };
  document.getElementById('setupModeSummary').textContent = labels[mode] || 'CHOOSE A MODE ABOVE';
  document.getElementById('setupFileSummary').textContent = { project: 'Your project, documented.', coding: 'Your practical, explained.', os: 'Commands with context.', cn: 'A consistent network plan.', auto: 'Every aim, in its format.' }[mode] || 'A clear, consistent format.';
  document.getElementById('setupEmptyHint').style.display = mode ? 'none' : 'block';
  document.getElementById('connection-card').style.display = mode === 'cn' ? 'none' : 'block';
  const opts = document.getElementById('mode-options');
  // Auto mode classifies each aim at generation time; OS + coding options both apply.
  opts.style.display = mode === 'auto' ? 'none' : 'block';
  document.getElementById('os-options').style.display = (mode === 'os' || mode === 'auto') ? 'block' : 'none';
  document.getElementById('coding-options').style.display = (mode === 'coding' || mode === 'auto') ? 'block' : 'none';
  if (mode === 'auto') {
    opts.style.display = 'block';
  }
  document.getElementById('cn-options').style.display = (mode === 'cn' || mode === 'auto') ? 'block' : 'none';
  document.getElementById('project-options').style.display = mode === 'project' ? 'block' : 'none';
  for (const id of ['model-options', 'key-options']) document.getElementById(id).style.display = mode === 'cn' ? 'none' : 'block';
  document.getElementById('quality-options').style.display = (mode === 'cn' || mode === 'project') ? 'none' : 'flex';
  document.getElementById('formatting-shell').style.display = mode === 'project' ? 'none' : 'block';
  const projectMode = mode === 'project';
  document.getElementById('setupStatusTitle').textContent = projectMode ? 'Context bridge' : 'Ready to go';
  document.getElementById('setupStatusText').textContent = projectMode
    ? 'Choose a report type, then collect verified context from the AI session already working on your project.'
    : 'Configure your settings, then proceed to write your experiment aims.';
  document.getElementById('setupProceedLabel').textContent = projectMode ? 'Open Context Bridge' : 'Proceed to Aims';
  document.getElementById('stepDataLabel').textContent = projectMode ? 'Project Data' : 'Aims';
  document.getElementById('stepGenerateLabel').textContent = projectMode ? 'Build Report' : 'Generate';
  document.getElementById('standard-aims-panel').style.display = projectMode ? 'none' : 'block';
  document.getElementById('project-file-panel').style.display = projectMode ? 'block' : 'none';
  if (mode === null) opts.style.display = 'none';
}

function selectProjectSubmode(submode, preserveContext = false) {
  if (!['frontend', 'dbms'].includes(submode)) return;
  const changed = state.config.projectSubmode && state.config.projectSubmode !== submode;
  if (changed) {
    cancelProjectGeneration();
    projectValidationSequence++;
    projectGenerationSequence++;
    projectImageSequence++;
  }
  state.config.projectSubmode = submode;
  document.querySelectorAll('.project-submode-card[data-project-submode]').forEach((card) => {
    const selected = card.dataset.projectSubmode === submode;
    card.classList.toggle('selected', selected);
    card.setAttribute('aria-checked', String(selected));
  });
  if (changed && !preserveContext) {
    state.projectFile = createEmptyProjectFileState();
    document.getElementById('projectContextInput').value = '';
  }
  document.getElementById('projectSubmodeBadge').textContent = submode.toUpperCase();
  renderProjectValidation();
  renderProjectImages();
  loadProjectPrompt();
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
  document.querySelector(`[aria-controls="${id}"]`)?.setAttribute('aria-expanded', String(el.classList.contains('open')));
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
  if (state.config.mode === 'project' && !state.config.projectSubmode) {
    showToast('warn', 'Choose Frontend or DBMS project report type.');
    return;
  }
  saveToLocalStorage();
  navigate('aims');
}

/* ============================
   PROJECT FILE CONTEXT BRIDGE
   ============================ */
const PROJECT_MAX_IMAGES = 16;
const PROJECT_MAX_IMAGE_BYTES = 1_000_000;
const PROJECT_MAX_TOTAL_IMAGE_BYTES = 2_500_000;
let projectValidationSequence = 0;
let projectGenerationSequence = 0;
let projectImageSequence = 0;
let projectAbortController = null;

function cancelProjectGeneration() {
  projectGenerationSequence++;
  projectAbortController?.abort();
  projectAbortController = null;
  if (state.projectFile.generationStatus === 'generating') {
    state.projectFile.generationStatus = 'idle';
    state.isGenerating = false;
    document.getElementById('genSpinner').style.display = 'none';
  }
}

function renderProjectWorkspace() {
  const projectMode = state.config.mode === 'project';
  document.getElementById('standard-aims-panel').style.display = projectMode ? 'none' : 'block';
  document.getElementById('project-file-panel').style.display = projectMode ? 'block' : 'none';
  if (!projectMode) return;

  const submode = state.config.projectSubmode;
  document.getElementById('projectSubmodeBadge').textContent = (submode || 'SELECT TYPE').toUpperCase();
  const input = document.getElementById('projectContextInput');
  if (input.value !== state.projectFile.rawContext) input.value = state.projectFile.rawContext || '';
  if (submode && !state.projectFile.prompt) loadProjectPrompt();
  renderProjectValidation();
  renderProjectImages();
}

async function loadProjectPrompt() {
  const submode = state.config.projectSubmode;
  const promptNode = document.getElementById('projectCollectorPrompt');
  if (!submode) {
    promptNode.textContent = 'Choose Frontend or DBMS in Setup to load its collector prompt.';
    return;
  }
  const requestedSubmode = submode;
  promptNode.textContent = 'Loading collector prompt...';
  try {
    const response = await fetch(`/api/project/prompt/${encodeURIComponent(submode)}`);
    const data = await response.json();
    if (!response.ok || data.error) throw new Error(data.error || 'Collector prompt could not be loaded.');
    if (state.config.projectSubmode !== requestedSubmode) return;
    state.projectFile.prompt = data.prompt || '';
    promptNode.textContent = state.projectFile.prompt;
  } catch (err) {
    if (state.config.projectSubmode !== requestedSubmode) return;
    promptNode.textContent = `Collector prompt unavailable: ${err.message}`;
    showToast('error', 'Could not load the collector prompt.');
  }
}

async function copyProjectPrompt() {
  const prompt = state.projectFile.prompt || document.getElementById('projectCollectorPrompt').textContent;
  if (!prompt || prompt.startsWith('Loading') || prompt.startsWith('Collector prompt unavailable')) {
    showToast('warn', 'Wait for the collector prompt to load.');
    return;
  }
  try {
    if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(prompt);
    } else {
      const textarea = document.createElement('textarea');
      textarea.value = prompt;
      textarea.setAttribute('readonly', '');
      textarea.style.position = 'fixed';
      textarea.style.opacity = '0';
      document.body.appendChild(textarea);
      textarea.select();
      if (!document.execCommand?.('copy')) throw new Error('Clipboard access is unavailable.');
      textarea.remove();
    }
    showToast('success', 'Collector prompt copied.');
  } catch {
    showToast('error', 'Clipboard access failed. Select the prompt text and copy it manually.');
  }
}

function markProjectContextStale(rawValue) {
  cancelProjectGeneration();
  projectValidationSequence++;
  projectGenerationSequence++;
  projectImageSequence++;
  state.projectFile.rawContext = String(rawValue ?? document.getElementById('projectContextInput').value);
  state.projectFile.normalized = null;
  state.projectFile.report = null;
  state.projectFile.completedSections = {};
  state.projectFile.sectionParts = {};
  state.projectFile.images = [];
  state.projectFile.generationStatus = 'idle';
  state.projectFile.validation = {
    status: 'idle', errors: [], missingFields: [], warnings: [], generationNeeded: []
  };
  state.generationComplete = false;
  renderProjectValidation();
  renderProjectImages();
  persistSession();
}

function handleProjectContextFile(event) {
  const file = event.target.files?.[0];
  event.target.value = '';
  if (!file) return;
  if (file.size > 1_200_000) {
    showToast('error', 'Context file is too large. Request compact facts only (maximum 300,000 characters).');
    return;
  }
  if (!file.name.toLowerCase().endsWith('.json')) {
    showToast('error', 'Choose a .json context file.');
    return;
  }
  const reader = new FileReader();
  reader.onload = () => {
    const value = String(reader.result || '');
    document.getElementById('projectContextInput').value = value;
    markProjectContextStale(value);
    showToast('success', `Imported ${file.name}`);
  };
  reader.onerror = () => showToast('error', 'The context file could not be read.');
  reader.readAsText(file);
}

function renderProjectValidation() {
  const size = (state.projectFile.rawContext || '').length;
  document.getElementById('projectContextSize').textContent = `${size.toLocaleString()} characters · limit 300,000`;
  const panel = document.getElementById('projectValidationPanel');
  const summary = document.getElementById('projectValidationSummary');
  const details = document.getElementById('projectValidationDetails');
  const button = document.getElementById('projectGenerateBtn');
  if (!panel || !summary || !details || !button) return;

  const validation = state.projectFile.validation;
  panel.className = 'project-validation';
  document.getElementById('validateProjectBtn').disabled = validation.status === 'validating';
  button.disabled = validation.status !== 'valid';
  document.getElementById('projectImageInput').disabled = validation.status !== 'valid';
  document.getElementById('projectImageButton').disabled = validation.status !== 'valid';
  document.getElementById('projectExportImageButton').disabled = validation.status !== 'valid';
  if (validation.status === 'validating') {
    summary.textContent = 'Validating the context schema and project evidence...';
    details.innerHTML = '';
    return;
  }
  if (validation.status === 'idle') {
    summary.textContent = state.projectFile.rawContext
      ? 'Context changed. Run validation again.'
      : 'Validation has not run yet.';
    details.innerHTML = '';
    return;
  }

  const errors = [...(validation.errors || []), ...(validation.missingFields || []).map(path => `Missing: ${path}`)];
  const warnings = validation.warnings || [];
  if (validation.status === 'valid') {
    panel.classList.add('valid');
    summary.innerHTML = '<span class="validation-ok"><i aria-hidden="true" class="fas fa-circle-check"></i> Context is valid and ready.</span>';
  } else {
    panel.classList.add('invalid');
    summary.innerHTML = '<span class="validation-error"><i aria-hidden="true" class="fas fa-circle-xmark"></i> Context needs attention.</span>';
  }
  details.innerHTML = [
    errors.length ? `<ul class="validation-error">${errors.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul>` : '',
    warnings.length ? `<ul class="validation-warning">${warnings.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul>` : '',
    validation.generationNeeded?.length
      ? `<p class="helper">PractiGen will complete: ${escapeHtml(validation.generationNeeded.join(', '))}</p>`
      : '<p class="helper">All report prose was supplied; no fallback LLM call is required.</p>'
  ].join('');
}

async function validateProjectContext() {
  const submode = state.config.projectSubmode;
  const rawContext = document.getElementById('projectContextInput').value.trim();
  if (!submode) {
    showToast('warn', 'Choose Frontend or DBMS first.');
    return;
  }
  if (!rawContext) {
    showToast('warn', 'Paste the JSON returned by your existing AI session.');
    return;
  }
  if (rawContext.length > 300_000) {
    showToast('error', 'Context exceeds 300,000 characters. Request compact facts without report prose.');
    return;
  }
  if (rawContext !== state.projectFile.rawContext.trim()) markProjectContextStale(rawContext);

  state.projectFile.rawContext = rawContext;
  const requestSequence = ++projectValidationSequence;
  const requestedSubmode = submode;
  state.projectFile.validation = {
    status: 'validating', errors: [], missingFields: [], warnings: [], generationNeeded: []
  };
  renderProjectValidation();
  const validateButton = document.getElementById('validateProjectBtn');
  validateButton.disabled = true;
  try {
    const response = await fetch('/api/project/validate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ submode, context: rawContext })
    });
    const data = await response.json().catch(() => ({}));
    if (requestSequence !== projectValidationSequence
        || state.config.projectSubmode !== requestedSubmode
        || document.getElementById('projectContextInput').value.trim() !== rawContext) return;
    if (!response.ok) throw new Error(data.error || 'Context validation failed.');
    state.projectFile.normalized = data.valid ? data.normalized : null;
    state.projectFile.report = null;
    state.projectFile.validation = {
      status: data.valid ? 'valid' : 'invalid',
      errors: data.errors || [],
      missingFields: data.missing_fields || [],
      warnings: data.warnings || [],
      generationNeeded: data.generation_needed || []
    };
    renderProjectValidation();
    if (data.valid) {
      persistSession();
      showToast('success', 'Project context validated.');
    } else {
      showToast('warn', 'The context pack is incomplete.');
    }
  } catch (err) {
    if (requestSequence !== projectValidationSequence) return;
    state.projectFile.normalized = null;
    state.projectFile.validation = {
      status: 'invalid', errors: [err.message], missingFields: [], warnings: [], generationNeeded: []
    };
    renderProjectValidation();
    showToast('error', 'Context validation failed.');
  } finally {
    if (requestSequence === projectValidationSequence) validateButton.disabled = false;
  }
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.onerror = () => reject(new Error(`Could not read ${file.name}.`));
    reader.readAsDataURL(file);
  });
}

function loadBrowserImage(dataUrl) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error('Image decoding failed.'));
    image.src = dataUrl;
  });
}

async function prepareProjectImage(file) {
  if (!['image/png', 'image/jpeg'].includes(file.type)) {
    throw new Error(`${file.name} is not a PNG or JPEG image.`);
  }
  if (file.size > 8_000_000) throw new Error(`${file.name} exceeds the 8 MB input limit.`);

  let dataUrl = await readFileAsDataUrl(file);
  let mediaType = file.type;
  let bytes = file.size;
  const image = await loadBrowserImage(dataUrl);
  const declaredCount = Math.max(getDeclaredProjectImages().size, 1);
  const targetBytes = Math.min(
    PROJECT_MAX_IMAGE_BYTES,
    Math.floor(PROJECT_MAX_TOTAL_IMAGE_BYTES / declaredCount)
  );
  if (file.size > targetBytes || image.width > 1600 || image.height > 1200) {
    const scale = Math.min(1600 / image.width, 1200 / image.height, 1);
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(image.width * scale));
    canvas.height = Math.max(1, Math.round(image.height * scale));
    const context = canvas.getContext('2d');
    context.fillStyle = '#ffffff';
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    let blob = null;
    for (const quality of [0.82, 0.7, 0.58]) {
      blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', quality));
      if (blob && blob.size <= targetBytes) break;
    }
    if (blob && blob.size > targetBytes) {
      const shrink = Math.min(Math.sqrt(targetBytes / blob.size) * 0.9, 0.9);
      const smaller = document.createElement('canvas');
      smaller.width = Math.max(1, Math.round(canvas.width * shrink));
      smaller.height = Math.max(1, Math.round(canvas.height * shrink));
      const smallerContext = smaller.getContext('2d');
      smallerContext.fillStyle = '#ffffff';
      smallerContext.fillRect(0, 0, smaller.width, smaller.height);
      smallerContext.drawImage(canvas, 0, 0, smaller.width, smaller.height);
      blob = await new Promise(resolve => smaller.toBlob(resolve, 'image/jpeg', 0.7));
    }
    if (!blob) throw new Error(`${file.name} could not be optimized.`);
    dataUrl = await readFileAsDataUrl(blob);
    mediaType = 'image/jpeg';
    bytes = blob.size;
  }
  if (bytes > targetBytes) {
    throw new Error(`${file.name} remains larger than its ${Math.round(targetBytes / 1024)} KB report budget after optimization.`);
  }

  const metadata = getProjectImageMetadata(file.name);
  if (!metadata) {
    throw new Error(`${file.name} is not declared in the validated context pack.`);
  }
  return {
    filename: file.name,
    media_type: mediaType,
    data_base64: dataUrl.split(',', 2)[1] || '',
    bytes,
    caption: metadata.caption || file.name.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' '),
    section: metadata.section || 'results',
    use_case_id: metadata.use_case_id || ''
  };
}

function getProjectImageMetadata(filename) {
  const normalized = state.projectFile.normalized;
  const cleanName = String(filename || '').replace(/\\/g, '/').split('/').pop().toLowerCase();
  if (!normalized) return null;
  const declared = (normalized.figures || []).find(item => {
    const itemName = String(item.filename || '').replace(/\\/g, '/').split('/').pop().toLowerCase();
    return itemName === cleanName;
  });
  if (declared) return declared;
  const result = (normalized.project?.results || []).find(item => {
    const itemName = String(item.screenshot_filename || '').replace(/\\/g, '/').split('/').pop().toLowerCase();
    return itemName === cleanName;
  });
  if (result) return { caption: result.title, section: 'results', use_case_id: result.id };
  const useCase = (normalized.dbms?.use_cases || []).find(item => {
    const itemName = String(item.screenshot_filename || '').replace(/\\/g, '/').split('/').pop().toLowerCase();
    return itemName === cleanName;
  });
  return useCase ? { caption: useCase.title, section: 'use_case', use_case_id: useCase.id } : null;
}

async function handleProjectImages(event) {
  const files = Array.from(event.target.files || []);
  event.target.value = '';
  if (!files.length) return;
  const requestSequence = ++projectImageSequence;
  const contextAtStart = state.projectFile.normalized;
  if (state.projectFile.images.length + files.length > PROJECT_MAX_IMAGES) {
    showToast('warn', `A report can contain at most ${PROJECT_MAX_IMAGES} uploaded images.`);
    return;
  }

  for (const file of files) {
    if (state.projectFile.images.some(image => image.filename.toLowerCase() === file.name.toLowerCase())) {
      showToast('warn', `${file.name} is already selected.`);
      continue;
    }
    try {
      const prepared = await prepareProjectImage(file);
      if (requestSequence !== projectImageSequence || state.projectFile.normalized !== contextAtStart) return;
      const currentBytes = state.projectFile.images.reduce((sum, image) => sum + image.bytes, 0);
      if (currentBytes + prepared.bytes > PROJECT_MAX_TOTAL_IMAGE_BYTES) {
        throw new Error('Combined images would exceed the 2.5 MB export limit.');
      }
      state.projectFile.images.push(prepared);
    } catch (err) {
      if (requestSequence !== projectImageSequence) return;
      showToast('error', err.message);
    }
  }
  if (requestSequence !== projectImageSequence) return;
  renderProjectImages();
}

function removeProjectImage(index) {
  projectImageSequence++;
  state.projectFile.images.splice(index, 1);
  renderProjectImages();
}

function getDeclaredProjectImages() {
  const normalized = state.projectFile.normalized || state.projectFile.report?.context;
  const declarations = new Map();
  const add = (filename) => {
    const value = String(filename || '').replace(/\\/g, '/').split('/').pop();
    if (value) declarations.set(value.toLowerCase(), value);
  };
  if (!normalized) return declarations;
  (normalized.figures || []).forEach(figure => add(figure.filename));
  if (normalized.submode === 'frontend') {
    (normalized.project?.results || []).forEach(result => add(result.screenshot_filename));
  } else {
    (normalized.dbms?.use_cases || []).forEach(useCase => add(useCase.screenshot_filename));
  }
  return declarations;
}

function getMissingProjectImages() {
  const uploaded = new Set(state.projectFile.images.map(image => image.filename.toLowerCase()));
  return [...getDeclaredProjectImages().entries()]
    .filter(([key]) => !uploaded.has(key))
    .map(([, filename]) => filename);
}

function renderProjectExportImageStatus() {
  const status = document.getElementById('projectExportImageStatus');
  if (!status) return;
  const declared = getDeclaredProjectImages();
  const missing = getMissingProjectImages();
  status.textContent = missing.length
    ? `${state.projectFile.images.length} of ${declared.size} uploaded. Missing: ${missing.join(', ')}`
    : `${state.projectFile.images.length} of ${declared.size} required images uploaded.`;
  document.getElementById('projectExportImageButton').disabled = state.projectFile.validation.status !== 'valid';
  if (state.config.mode === 'project') document.getElementById('downloadBtn').disabled = missing.length > 0;
}

function renderProjectImages() {
  const containers = [
    document.getElementById('projectImageList'),
    document.getElementById('projectExportImageList')
  ].filter(Boolean);
  const content = state.projectFile.images.length ? state.projectFile.images.map((image, index) => `
    <span class="project-image-chip" title="${escapeHtml(image.caption)}">
      <i aria-hidden="true" class="fas fa-image" style="color:var(--accent)"></i>
      <span>${escapeHtml(image.filename)} (${Math.round(image.bytes / 1024)} KB)</span>
      <button type="button" onclick="removeProjectImage(${index})" aria-label="Remove image"><i aria-hidden="true" class="fas fa-xmark"></i></button>
    </span>`).join('') : '<span class="helper">No images selected.</span>';
  containers.forEach(container => { container.innerHTML = content; });
  renderProjectExportImageStatus();
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
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
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
  aimsEditor.addEventListener('scroll', () => { lineNumbers.scrollTop = aimsEditor.scrollTop; });
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
  if (state.config.mode === 'project') return startProjectGeneration();
  const aims = parseAims(state.aimsText);
  if (aims.length === 0) return;

  document.getElementById('generationTitle').innerHTML = 'AI Generation <div class="spinner" id="genSpinner"></div>';
  document.getElementById('generationBackLabel').textContent = 'Modify Aims';

  state.config.termUser = document.getElementById('termUser')?.value || 'student';
  state.config.termHost = document.getElementById('termHost')?.value || 'ubuntu';
  state.config.student = { name: $('#cnName').value.trim(), roll: $('#cnRoll').value.trim(), section: $('#cnSection').value.trim() };
  state.config.codeLang = document.getElementById('codeLang')?.value || 'c';
  state.config.jevQualityGate = Boolean(document.getElementById('jevQualityGate')?.checked);

  const { provider, model, apiKey, baseUrl } = genContext();

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
  document.getElementById('genSubtitle').textContent = state.config.mode === 'cn' ? 'Preparing personalised CN experiments...' : `Processing your experiments with ${model || "the configured model"}...`;

  navigate('generation');
  renderExpGrid();

  addGenLog(`Starting generation for ${aims.length} experiment(s)`, 'info');
  addGenLog(state.config.mode === 'cn' ? 'CN templates: addressing, configurations and figures' : `Model: ${model || 'server default'}`, 'info');
  const batchSize = state.config.mode === 'cn' ? 3 : 1;
  const generationExperiments = state.experiments;
  addGenLog(`Concurrency: ${batchSize} experiment(s) at a time`, 'info');
  addGenLog('---', 'info');

  // Custom endpoints may queue or throttle concurrent calls; start with one.
  for (let i = 0; i < generationExperiments.length; i += batchSize) {
    if (!state.isGenerating || state.experiments !== generationExperiments) return;
    const batch = generationExperiments.slice(i, i + batchSize);
    await Promise.all(batch.map((exp) => generateOne(exp.id)));
  }

  if (state.isGenerating && state.experiments === generationExperiments) finishGeneration();
}

function renderProjectGenerationCard(status, message) {
  const statusClass = status === 'complete' ? 'complete' : status === 'failed' ? 'failed' : 'synthesizing';
  const icon = status === 'complete' ? 'fa-circle-check' : status === 'failed' ? 'fa-circle-xmark' : 'fa-spinner fa-spin';
  const label = status === 'complete' ? 'REPORT READY' : status === 'failed' ? 'BUILD FAILED' : 'ASSEMBLING REPORT';
  const title = state.projectFile.normalized?.project?.title || 'Project report';
  document.getElementById('expGrid').innerHTML = `
    <div class="exp-card project-generation-card ${statusClass}">
      <span class="project-gen-kicker"><i aria-hidden="true" class="fas ${icon}"></i> ${label}</span>
      <h3>${escapeHtml(title)}</h3>
      <p>${escapeHtml(message || '')}</p>
    </div>`;
}

async function startProjectGeneration() {
  if (state.isGenerating) return;
  if (state.projectFile.validation.status !== 'valid' || !state.projectFile.normalized) {
    showToast('warn', 'Validate the project context before building the report.');
    return;
  }
  const { provider, model, apiKey, baseUrl } = genContext();
  const requestSequence = ++projectGenerationSequence;
  projectAbortController = typeof AbortController === 'undefined' ? null : new AbortController();
  const signal = projectAbortController?.signal;
  const generatedFields = state.projectFile.validation.generationNeeded || [];
  state.projectFile.sectionParts ||= {};
  state.isGenerating = true;
  state.generationComplete = false;
  state.projectFile.generationStatus = 'generating';
  state.logs = [];

  document.getElementById('generationTitle').innerHTML = 'Project Report Build <div class="spinner" id="genSpinner"></div>';
  document.getElementById('generationBackLabel').textContent = 'Modify Context';
  document.getElementById('genTerminal').innerHTML = '';
  document.getElementById('reviewBtn').style.display = 'none';
  document.getElementById('genSpinner').style.display = 'block';
  document.getElementById('genSubtitle').textContent = generatedFields.length
    ? `Completing ${generatedFields.length} missing section(s), then assembling the report...`
    : 'All prose supplied. Assembling the validated report...';
  document.getElementById('genProgress').style.width = '15%';
  document.getElementById('genPercent').textContent = '15%';
  document.getElementById('generationProgressTrack').setAttribute('aria-valuenow', '15');
  document.getElementById('genCount').textContent = '0 of 1';
  navigate('generation');
  renderProjectGenerationCard('generating', 'Validating facts and applying the selected report convention.');
  addGenLog(`Project type: ${state.config.projectSubmode.toUpperCase()}`, 'info');
  addGenLog('Verified context accepted', 'success');
  if (generatedFields.length) {
    addGenLog(`Hybrid completion: ${generatedFields.join(', ')}`, 'info');
    addGenLog(`Model: ${model || 'server default'}`, 'info');
  } else {
    addGenLog('No fallback LLM call required', 'success');
  }

  try {
    for (let index = 0; index < generatedFields.length; index++) {
      const section = generatedFields[index];
      if (state.projectFile.completedSections[section]) continue;
      const label = section.replaceAll('_', ' ');
      const parts = state.projectFile.sectionParts[section] ||= [];
      while (!state.projectFile.completedSections[section]) {
        const partIndex = parts.length;
        renderProjectGenerationCard('generating', `Writing ${label}, part ${partIndex + 1} (${index + 1}/${generatedFields.length} sections)...`);
        const sectionResponse = await fetch('/api/project/section', {
          method: 'POST', signal,
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            submode: state.config.projectSubmode, context: state.projectFile.normalized,
            section, part_index: partIndex, previous_parts: parts,
            provider, model, base_url: baseUrl, api_key: apiKey
          })
        });
        const sectionData = await sectionResponse.json().catch(() => ({}));
        if (requestSequence !== projectGenerationSequence || !state.isGenerating) return;
        if (!sectionResponse.ok || sectionData.error || typeof sectionData.text !== 'string' || !sectionData.text.trim()) {
          const reference = sectionData.request_id ? ` Reference: ${sectionData.request_id}.` : '';
          throw new Error(`${label}, part ${partIndex + 1}: ${sectionData.error || 'No section returned. The server may have timed out.'}${reference} Completed sections and parts are saved; retry to continue.`);
        }
        // Older servers return a complete section without part metadata.
        const partCount = sectionData.part_count ?? 1;
        if (!Number.isInteger(partCount) || partCount <= partIndex || partCount > 12
            || (sectionData.part_index !== undefined && sectionData.part_index !== partIndex)) {
          throw new Error(`${label}: Invalid section progress returned. Saved work has been preserved.`);
        }
        parts.push(sectionData.text);
        if (parts.length === partCount) state.projectFile.completedSections[section] = parts.join('\n\n');
        persistSession();
        addGenLog(`Saved ${label}, part ${parts.length}/${partCount}`, 'success');
      }
      const progress = Math.round(10 + 80 * (index + 1) / generatedFields.length);
      document.getElementById('genProgress').style.width = `${progress}%`;
      document.getElementById('genPercent').textContent = `${progress}%`;
      document.getElementById('generationProgressTrack').setAttribute('aria-valuenow', String(progress));
      addGenLog(`Saved ${label}`, 'success');
    }
    const response = await fetch('/api/project/generate', {
      method: 'POST', signal,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        submode: state.config.projectSubmode,
        context: state.projectFile.normalized,
        completed_sections: state.projectFile.completedSections,
        provider: provider || 'groq',
        model,
        base_url: baseUrl,
        api_key: apiKey
      })
    });
    const data = await response.json().catch(() => ({}));
    if (requestSequence !== projectGenerationSequence) return;
    if (!response.ok || data.error) throw new Error(data.error || 'Project report generation failed.');
    if (!state.isGenerating) return;

    state.projectFile.report = data.report;
    state.projectFile.generationStatus = 'complete';
    state.generationComplete = true;
    document.getElementById('genProgress').style.width = '100%';
    document.getElementById('genPercent').textContent = '100%';
    document.getElementById('generationProgressTrack').setAttribute('aria-valuenow', '100');
    document.getElementById('genCount').textContent = '1 of 1';
    document.getElementById('genSubtitle').textContent = 'Project report assembled successfully.';
    renderProjectGenerationCard('complete', 'The structured report is ready for review and Word export.');
    addGenLog('Report sections assembled', 'success');
    for (const warning of data.warnings || []) addGenLog(warning, 'warn');
    addGenLog('Project report ready for review', 'success');
    const reviewButton = document.getElementById('reviewBtn');
    reviewButton.style.display = 'inline-flex';
    reviewButton.style.animation = 'fadeSlideIn 0.4s ease';
    persistSession();
  } catch (err) {
    if (requestSequence !== projectGenerationSequence || !state.isGenerating) return;
    state.projectFile.generationStatus = 'failed';
    renderProjectGenerationCard('failed', err.message);
    document.getElementById('expGrid').insertAdjacentHTML('beforeend', '<button class="btn btn-primary" onclick="startProjectGeneration()">Retry remaining sections</button>');
    document.getElementById('genSubtitle').textContent = 'The report could not be assembled.';
    addGenLog(`Project report failed: ${err.message}`, 'error');
    showToast('error', 'Project report generation failed.');
  } finally {
    if (requestSequence === projectGenerationSequence) {
      state.isGenerating = false;
      projectAbortController = null;
      document.getElementById('genSpinner').style.display = 'none';
    }
  }
}

/**
 * Resolve the active provider/model/apiKey from config + custom override.
 * Shared by generation, retry, refine and classroom flows.
 */
function genContext() {
  const baseUrl = document.getElementById('apiEndpoint').value.trim();
  const model = document.getElementById('customModel').value.trim();
  const apiKey = document.getElementById('apiKey').value.trim();
  Object.assign(state.config, { baseUrl, model, apiKey });
  return { provider: baseUrl ? 'custom' : 'groq', model, apiKey, baseUrl };
}

async function readGenerationResponse(response) {
  const data = await response.json().catch(() => null);
  const valid = data && typeof data === 'object' && !Array.isArray(data);
  const usable = valid && (data.mode === 'cn' || (typeof data.concept === 'string' && data.concept.trim()));
  if (!response.ok || !usable || data.error) {
    const reference = data?.request_id ? ` Reference: ${data.request_id}.` : '';
    const error = new Error((data?.error || `Server returned HTTP ${response.status}; no usable response received.`) + reference);
    error.retryable = data?.retryable !== false && data?.auto_retry !== false
      && ((response.ok && !usable) || response.status >= 500 || [408, 429].includes(response.status));
    throw error;
  }
  return data;
}

/**
 * Generate a single experiment (by id) with up to 3 attempts. Resolves
 * independently so cards update as each finishes. Enhancement 1 + Bug 1 retry.
 */
async function generateOne(idx) {
  const exp = state.experiments[idx];
  if (!exp || !state.isGenerating) return;
  const generationExperiments = state.experiments;
  const isActive = () => state.isGenerating && state.experiments === generationExperiments;

  const { provider, model, apiKey, baseUrl } = genContext();
  const autoDetect = state.config.mode === 'auto';

  exp.status = 'synthesizing';
  renderExpGrid();
  addGenLog(`Generating experiment ${idx + 1}/${state.experiments.length}...`, 'info');

  let success = false;
  let attempts = 0;
  const maxAttempts = 3;

  while (attempts < maxAttempts && !success && isActive()) {
    attempts++;
    if (attempts > 1) {
      const delayMs = 2000 * 2 ** (attempts - 2);
      addGenLog(`Waiting ${delayMs / 1000}s before retrying experiment ${idx + 1} (attempt ${attempts}/${maxAttempts})...`, 'warn');
      await new Promise(resolve => setTimeout(resolve, delayMs));
      if (!isActive()) return;
    }

    const started = Date.now();
    try {
      const seed = Math.random().toString(36).substring(2, 8);
      const response = await fetch('/api/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          aim: exp.aim,
          base_url: baseUrl,
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

      const data = await readGenerationResponse(response);
      if (!isActive()) return;

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
      if (!isActive()) return;
      addGenLog(`Experiment ${idx + 1} attempt ${attempts} failed after ${((Date.now() - started) / 1000).toFixed(1)}s: ${err.message}`, 'error');
      if (attempts >= maxAttempts || err.retryable === false) {
        exp.status = 'failed';
        attempts = maxAttempts;
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
  cancelProjectGeneration();
  if (state.config.mode === 'project') projectGenerationSequence++;
  state.isGenerating = false;
  state.genTimeouts.forEach(t => clearTimeout(t));
  state.genTimeouts = [];
  document.getElementById('genSpinner').style.display = 'none';
  navigate('aims');
}

async function retryExperiment(idx) {
  if (idx < 0 || idx >= state.experiments.length) return;
  const exp = state.experiments[idx];

  const { provider, model, apiKey, baseUrl } = genContext();
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
        base_url: baseUrl,
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

    const data = await readGenerationResponse(response);

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
          <i aria-hidden="true" class="fas ${statusIcons[exp.status]}" style="color:${statusColors[exp.status]};font-size:0.85rem;"></i>
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
  document.getElementById('generationProgressTrack').setAttribute('aria-valuenow', String(pct));
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

function renderProjectReview() {
  const report = state.projectFile.report;
  document.getElementById('reviewTitle').textContent = 'Review Project Report';
  const badge = document.getElementById('reviewCountBadge');
  const container = document.getElementById('reviewAccordion');
  if (!report) {
    badge.textContent = 'REPORT NOT READY';
    container.innerHTML = '<div class="card" style="text-align:center;color:var(--muted);padding:2rem;">Build the project report before reviewing it.</div>';
    return;
  }

  const frontendSections = [
    ['Abstract', 'abstract'],
    ['1. Introduction: Background', 'introduction_background'],
    ['1. Introduction: Significance', 'introduction_significance'],
    ['2. Problem Definition and Requirements', 'problem_definition'],
    ['3. Proposed Design / Methodology', 'design_methodology'],
    ['4. Results', 'results_summary']
  ];
  const dbmsSections = [
    ['1. Introduction', 'introduction'],
    ['2. E-R Diagram', 'erd_description'],
    ['3. Database Schema', 'schema_description'],
    ['4. Use Cases', 'use_cases_introduction']
  ];
  const definitions = report.submode === 'frontend' ? frontendSections : dbmsSections;
  const context = report.context;
  const source = report.generation?.source || 'supplied';
  const factSummary = report.submode === 'frontend'
    ? `${context.frontend.stack.length} technologies · ${context.frontend.pages.length} pages · ${context.project.results.length} results`
    : `${context.dbms.entities.length} entities · ${context.dbms.tables.length} tables · ${context.dbms.use_cases.length} use cases`;
  badge.textContent = `${definitions.length} SECTIONS READY`;

  container.innerHTML = `
    <div class="card" style="margin-bottom:1rem;padding:1rem 1.25rem;">
      <div style="display:flex;justify-content:space-between;gap:1rem;align-items:flex-start;flex-wrap:wrap;">
        <div><p style="font-weight:650;">${escapeHtml(report.title)}</p><p class="helper">${escapeHtml(factSummary)}</p></div>
        <div class="project-review-meta"><span class="badge badge-accent">${escapeHtml(report.submode)}</span><span class="badge badge-muted">${escapeHtml(source)}</span><span class="badge badge-muted">${state.projectFile.images.length} images</span></div>
      </div>
    </div>` + definitions.map(([label, key], index) => {
      const isOpen = state.expandedExps.has(index);
      return `
        <div class="accordion-item project-review-section">
          <button type="button" class="accordion-header project-accordion-button" onclick="toggleProjectSection(${index})" aria-expanded="${isOpen}">
            <span style="font:0.72rem 'JetBrains Mono',monospace;color:var(--accent);">SEC-${String(index + 1).padStart(2, '0')}</span>
            <span style="font-size:0.9rem;font-weight:600;flex:1;">${escapeHtml(label)}</span>
            ${report.generation?.generated_fields?.includes(key) ? '<span class="badge badge-amber">AI completed</span>' : '<span class="badge badge-muted">Supplied</span>'}
            <i aria-hidden="true" class="fas fa-chevron-down accordion-chevron ${isOpen ? 'open' : ''}"></i>
          </button>
          <div class="accordion-body ${isOpen ? 'open' : ''}">
            <div class="project-review-body"><p>${escapeHtml(report.report_sections[key])}</p></div>
          </div>
        </div>`;
    }).join('');
}

function toggleProjectSection(index) {
  if (state.expandedExps.has(index)) state.expandedExps.delete(index);
  else state.expandedExps.add(index);
  renderProjectReview();
}

function renderReview() {
  if (state.config.mode === 'project') {
    renderProjectReview();
    return;
  }
  document.getElementById('reviewTitle').textContent = 'Review Results';
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
            <i aria-hidden="true" class="fas fa-code" style="margin-right:0.4rem;color:var(--amber);"></i>Code
          </h4>
          <div class="code-block">${escapeHtml(exp.code)}</div>
        </div>
        <div>
          <h4 style="font-size:0.8rem;font-weight:600;color:var(--text-sec);margin-bottom:0.5rem;text-transform:uppercase;letter-spacing:0.05em;">
            <i aria-hidden="true" class="fas fa-terminal" style="margin-right:0.4rem;color:var(--success);"></i>Output
          </h4>
          <div class="code-block terminal-output">${escapeHtml(exp.output)}</div>
        </div>
      `;
    }

    return `
      <div class="accordion-item" id="acc-${realIdx}">
        <div class="accordion-header">
          <button class="accordion-toggle" onclick="toggleAccordion(${realIdx})" aria-expanded="${isOpen}" aria-controls="body-${realIdx}">
          <span style="font-size:0.75rem;font-family:'JetBrains Mono',monospace;color:var(--muted);flex-shrink:0;">EXP-${exp.number}</span>
          <span style="font-size:0.9rem;font-weight:600;flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding-left:0.5rem;padding-right:0.5rem;">${escapeHtml(exp.aim)}</span>
          <span class="badge badge-accent" style="flex-shrink:0;margin-right:0.5rem;">${expIsCn(exp) ? 'CN' : expIsOs(exp) ? 'Linux' : (exp.codeLanguage || state.config.codeLang || 'C').toUpperCase()}</span>
          <i aria-hidden="true" class="fas fa-chevron-down accordion-chevron ${isOpen ? 'open' : ''}" id="chev-${realIdx}"></i>
          </button>
          <button class="btn btn-ghost" style="padding:0.3rem 0.65rem;font-size:0.78rem;flex-shrink:0;margin-right:0.5rem;" onclick="event.stopPropagation();${expIsCn(exp) ? `retryExperiment(${realIdx})` : `openRefineModal(${realIdx})`}">
            <i aria-hidden="true" class="fas fa-wand-magic-sparkles"></i> ${expIsCn(exp) ? 'REGENERATE' : 'REFINE'}
          </button>
        </div>
        <div class="accordion-body ${isOpen ? 'open' : ''}" id="body-${realIdx}">
          <div style="padding:0 1.25rem 1.25rem;">
            <div style="margin-bottom:1.25rem;">
              <h4 style="font-size:0.8rem;font-weight:600;color:var(--text-sec);margin-bottom:0.5rem;text-transform:uppercase;letter-spacing:0.05em;">
                <i aria-hidden="true" class="fas fa-book-open" style="margin-right:0.4rem;color:var(--accent);"></i>Theory
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
  if (!state.experiments[idx]) return;
  state.refineReturnFocus = document.activeElement;
  state.refiningExp = idx;
  const exp = state.experiments[idx];
  document.getElementById('refineAim').textContent = exp.aim;
  document.getElementById('refineInput').value = '';
  document.getElementById('refine-modal').classList.add('open');
  document.body.style.overflow = 'hidden';
  document.getElementById('refineInput').focus?.();
}

function closeRefineModal() {
  document.getElementById('refine-modal').classList.remove('open');
  state.refiningExp = null;
  document.body.style.overflow = '';
  state.refineReturnFocus?.focus?.();
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
  
  const { provider, model, apiKey, baseUrl } = genContext();

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
        base_url: baseUrl,
        api_key: apiKey,
        provider,
        model,
        mode: exp.detectedMode || (expIsOs(exp) ? 'os' : 'coding'),
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
function projectOutputFilename() {
  const title = state.projectFile.report?.title || `${state.config.projectSubmode || 'Project'} Project Report`;
  const safe = String(title).replace(/[^A-Za-z0-9 _-]+/g, '').trim().replace(/\s+/g, '_').slice(0, 80);
  return `${safe || 'Project_Report'}.docx`;
}

function renderProjectExport() {
  const report = state.projectFile.report;
  document.getElementById('classroom-panel').style.display = 'none';
  document.getElementById('projectExportImagePanel').style.display = 'grid';
  document.getElementById('exportTitle').textContent = 'Project Report Export';
  document.getElementById('artifactListTitle').textContent = 'Report Package';
  document.getElementById('artifactTotal').textContent = report ? '1 project report' : 'Report unavailable';
  const imageKB = Math.round(state.projectFile.images.reduce((sum, image) => sum + image.bytes, 0) / 1024);
  document.getElementById('artifactSize').textContent = `~${Math.max(20, imageKB + 20)} KB source assets`;
  const list = document.getElementById('artifactList');
  if (!report) {
    list.innerHTML = '<div class="card" style="color:var(--muted);">Build the report before exporting it.</div>';
    document.getElementById('downloadBtn').style.display = 'none';
    return;
  }
  const context = report.context;
  const detail = report.submode === 'frontend'
    ? `${context.project.results.length} result entries · ${state.projectFile.images.length} screenshots`
    : `${context.dbms.use_cases.length} SQL use cases · ${state.projectFile.images.length} screenshots`;
  list.innerHTML = `
    <div style="display:flex;align-items:center;gap:0.75rem;padding:0.8rem 0.9rem;background:var(--bg);border:1px solid var(--border);border-radius:8px;">
      <i aria-hidden="true" class="fas fa-file-word" style="color:var(--accent);font-size:1rem;"></i>
      <div style="min-width:0;flex:1;"><p style="font-size:0.86rem;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${escapeHtml(projectOutputFilename())}</p><p class="helper" style="margin:0;">${escapeHtml(detail)}</p></div>
      <span class="badge badge-accent">${escapeHtml(report.submode)}</span>
    </div>`;

  document.getElementById('downloadBtn').style.display = 'flex';
  document.getElementById('downloadSuccess').style.display = 'none';
  renderProjectImages();
  const terminal = document.getElementById('exportTerminal');
  terminal.innerHTML = '';
  const messages = [
    ['Validated context locked', 'success'],
    ['Times New Roman report styles ready', 'info'],
    [`${state.projectFile.images.length} uploaded image(s) matched by filename`, 'info'],
    ['Table of contents and numbered captions enabled', 'info'],
    ['Word report ready for export', 'success']
  ];
  messages.forEach(([message, type], index) => {
    setTimeout(() => addExportLog(message, type), index * 140);
  });
}

function renderExport() {
  if (state.config.mode === 'project') {
    renderProjectExport();
    return;
  }
  document.getElementById('exportTitle').textContent = 'Final Document';
  document.getElementById('artifactListTitle').textContent = 'Experiments Included';
  document.getElementById('projectExportImagePanel').style.display = 'none';
  document.getElementById('downloadBtn').disabled = false;
  document.getElementById('classroom-panel').style.display = state.experiments.some(expIsCn) ? 'none' : 'block';
  const complete = state.experiments.filter(e => e.status === 'complete');
  document.getElementById('artifactTotal').textContent = `${complete.length} experiment${complete.length !== 1 ? 's' : ''}`;
  const sizeKB = complete.length * 12 + 8;
  document.getElementById('artifactSize').textContent = `~${sizeKB} KB estimated`;

  const list = document.getElementById('artifactList');
  list.innerHTML = complete.map(exp => `
    <div style="display:flex;align-items:center;gap:0.75rem;padding:0.65rem 0.85rem;background:var(--bg);border:1px solid var(--border);border-radius:8px;">
      <i aria-hidden="true" class="fas fa-circle-check" style="color:var(--success);font-size:0.85rem;flex-shrink:0;"></i>
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
  if (state.config.mode === 'project') return downloadProjectDocument();
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
      btn.innerHTML = '<i aria-hidden="true" class="fas fa-cloud-arrow-down"></i> DOWNLOAD DOCUMENT';
      btn.style.display = 'flex';
      document.getElementById('downloadSuccess').style.display = 'none';
    }, 5000);

  } catch (err) {
    console.error(err);
    showToast('error', `Bundle export error: ${err.message}`);
    addExportLog(`CRITICAL EXPORT ERROR: ${err.message}`, 'error');
    btn.disabled = false;
    btn.innerHTML = '<i aria-hidden="true" class="fas fa-cloud-arrow-down"></i> DOWNLOAD DOCUMENT';
  }
}

async function downloadProjectDocument() {
  const report = state.projectFile.report;
  if (!report) {
    showToast('warn', 'Build the project report before exporting it.');
    return;
  }
  const missingImages = getMissingProjectImages();
  if (missingImages.length) {
    showToast('warn', `Add all required screenshots before export (${missingImages.length} missing).`);
    return;
  }
  const button = document.getElementById('downloadBtn');
  button.disabled = true;
  button.innerHTML = '<div class="spinner"></div> Building report...';
  try {
    const images = state.projectFile.images.map(({ bytes, ...image }) => image);
    const payload = {
      submode: state.config.projectSubmode,
      report,
      images,
      output_filename: projectOutputFilename()
    };
    const body = JSON.stringify(payload);
    const downloadPayload = { body, bytes: new Blob([body]).size };
    const downloadRequest = await getDownloadRequest(downloadPayload);
    addExportLog(`Project payload prepared (${Math.round(downloadPayload.bytes / 1024)} KB).`, 'info');
    const response = await fetch('/api/project/download', {
      method: 'POST', headers: downloadRequest.headers, body: downloadRequest.body
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || 'Project report export was rejected.');
    }
    const blob = await response.blob();
    const url = window.URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = projectOutputFilename();
    document.body.appendChild(link);
    link.click();
    window.URL.revokeObjectURL(url);
    link.remove();

    button.style.display = 'none';
    document.getElementById('downloadSuccess').style.display = 'block';
    launchConfetti();
    addExportLog('Project report exported successfully.', 'success');
    showToast('success', 'Project report downloaded.');
    setTimeout(() => {
      button.disabled = false;
      button.innerHTML = '<i aria-hidden="true" class="fas fa-cloud-arrow-down"></i> DOWNLOAD DOCUMENT';
      button.style.display = 'flex';
      document.getElementById('downloadSuccess').style.display = 'none';
    }, 5000);
  } catch (err) {
    addExportLog(`PROJECT EXPORT ERROR: ${err.message}`, 'error');
    showToast('error', `Project export error: ${err.message}`);
    button.disabled = false;
    button.innerHTML = '<i aria-hidden="true" class="fas fa-cloud-arrow-down"></i> DOWNLOAD DOCUMENT';
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
      selectMode('auto');
      showToast('success', `Mixed batch detected (${osCount} OS, ${genCount} coding). Using Auto mode — each experiment is classified at generation.`);
    } else if (osCount > 0) {
      selectMode('os');
      showToast('success', `Detected: OS practical mode for all ${osCount} aims.`);
    } else {
      selectMode('coding');
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
  const { provider, model, apiKey, baseUrl } = genContext();

  const form = new FormData();
  form.append('file', file);
  form.append('api_key', apiKey);
  form.append('provider', provider || 'groq');
  form.append('model', model);
  form.append('base_url', baseUrl);

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

  const { provider, model, apiKey, baseUrl } = genContext();
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
              base_url: baseUrl,
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
  if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return;
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
   KEYBOARD NAV
   ============================ */
document.addEventListener('keydown', (e) => {
  if (e.key === 'Tab' && document.getElementById('refine-modal').classList.contains('open')) {
    const controls = [...document.querySelectorAll('#refine-modal button, #refine-modal textarea')];
    const first = controls[0];
    const last = controls[controls.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
  }
  if (e.key === 'Escape') {
    if (document.getElementById('refine-modal').classList.contains('open')) closeRefineModal();
    closeSidebar();
  }
});

/* ============================
   LOCAL STORAGE STORAGE HELPERS
   ============================ */
function saveToLocalStorage() {
  localStorage.setItem('practigen_api_key_v6', $('#apiKey').value);
  localStorage.setItem('practigen_code_language_v5', $('#codeLang').value);
  localStorage.setItem('practigen_term_user_v5', $('#termUser').value);
  localStorage.setItem('practigen_term_host_v5', $('#termHost').value);
  genContext();
  localStorage.setItem('practigen_endpoint_v1', $('#apiEndpoint').value.trim());
  localStorage.setItem('practigen_model_v6', $('#customModel').value.trim());
  localStorage.setItem('practigen_jev_quality_gate_v1', String($('#jevQualityGate').checked));
}

function loadFromLocalStorage() {
  $('#apiKey').value = localStorage.getItem('practigen_api_key_v6') || '';
  $('#codeLang').value = localStorage.getItem('practigen_code_language_v5') || 'c';
  $('#termUser').value = localStorage.getItem('practigen_term_user_v5') || 'student';
  $('#termHost').value = localStorage.getItem('practigen_term_host_v5') || 'ubuntu';
  $('#apiEndpoint').value = localStorage.getItem('practigen_endpoint_v1') || '';
  $('#customModel').value = localStorage.getItem('practigen_model_v6') || '';
  $('#jevQualityGate').checked = localStorage.getItem('practigen_jev_quality_gate_v1') === 'true';
}

/* ============================
   SESSION PERSISTENCE (Enhancement 3)
   sessionStorage auto-clears on browser close, avoiding stale data.
   ============================ */
const SESSION_KEY = 'practigen_session_v6';

function persistSession() {
  try {
    const projectFile = {
      ...state.projectFile,
      generationStatus: state.projectFile.report ? 'complete' : 'idle',
      prompt: '',
      images: []
    };
    const payload = {
      experiments: state.experiments,
      config: state.config,
      aimsText: state.aimsText,
      projectFile,
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
  const hasExperiments = Boolean(saved && Array.isArray(saved.experiments) && saved.experiments.length > 0);
  const hasProject = Boolean(
    saved?.config?.mode === 'project'
    && saved.projectFile
    && (saved.projectFile.report || saved.projectFile.normalized || saved.projectFile.rawContext)
  );
  if (!hasExperiments && !hasProject) return;

  const done = hasProject ? (saved.projectFile.report ? 1 : 0) : saved.experiments.filter(e => e.status === 'complete').length;
  const banner = document.getElementById('restoreBanner');
  if (!banner) return;
  document.getElementById('restoreText').textContent = hasProject
    ? `Previous project context found${done ? ' with a completed report' : ''}. Restore?`
    : `Previous session found — ${done} experiment${done !== 1 ? 's' : ''} ready. Restore?`;
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
  state.projectFile = {
    ...createEmptyProjectFileState(),
    ...(saved.projectFile || {}),
    images: []
  };
  state.generationComplete = state.config.mode === 'project'
    ? Boolean(state.projectFile.report)
    : state.experiments.some(exp => exp.status === 'complete');
  selectMode(state.config.mode);
  if (state.config.provider && !['groq', 'custom'].includes(state.config.provider) && !state.config.baseUrl) {
    state.config.apiKey = '';
    state.config.model = '';
    showToast('warn', 'This session used a removed provider preset. Set its endpoint, model and key in Setup before generating again.');
  }
  delete state.config.provider;
  delete state.config.modelCardId;
  $('#apiEndpoint').value = state.config.baseUrl || '';
  $('#customModel').value = state.config.model || '';
  $('#apiKey').value = state.config.apiKey || '';
  $('#codeLang').value = state.config.codeLang || 'c';
  $('#termUser').value = state.config.termUser || 'student';
  $('#termHost').value = state.config.termHost || 'ubuntu';

  if (state.config.mode === 'project') {
    if (state.config.projectSubmode) selectProjectSubmode(state.config.projectSubmode, true);
    document.getElementById('projectContextInput').value = state.projectFile.rawContext || '';
    const banner = document.getElementById('restoreBanner');
    if (banner) banner.style.display = 'none';
    showToast('success', state.projectFile.report ? 'Project report restored. Re-add images before export.' : 'Project context restored.');
    navigate(state.projectFile.report ? 'review' : 'aims');
    return;
  }

  state.config.student ||= state.experiments.find(expIsCn)?.cn.profile || {};
  for (const [id, key] of [['cnName', 'name'], ['cnRoll', 'roll'], ['cnSection', 'section']]) $('#'+id).value = state.config.student?.[key] || '';

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
  renderRecentSessions();
}

/* ============================
   INIT
   ============================ */
function init() {
  window.addEventListener?.('resize', revealActiveStep);
  initDashboard();
  loadFromLocalStorage();
  updateEditorDisplay();
  document.getElementById('projectContextInput').addEventListener('input', (event) => {
    markProjectContextStale(event.target.value);
  });


  // Enhancement 3 — offer to restore a previous session if one exists.
  maybeOfferRestore();
  // Landing-page shortcuts choose a file type without discarding a saved session.
  if (window.location?.search) {
    const mode = new URLSearchParams(window.location.search).get('mode');
    if (['project', 'lab', 'custom'].includes(mode)) {
      selectMainMode(mode);
      navigate('setup');
    }
  }
}

init();
