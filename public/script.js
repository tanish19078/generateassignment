const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const sections = {
    dashboard: $('#section-dashboard'),
    workspace: $('#section-workspace')
};

const phases = {
    1: $('#phase-1'),
    2: $('#phase-2'),
    3: $('#phase-3'),
    4: $('#phase-4')
};

const navDashboard = $('#nav-dashboard');
const navWorkspace = $('#nav-workspace');
const breadcrumbMain = $('#breadcrumb-main');
const breadcrumbSub = $('#breadcrumb-sub');
const breadcrumbSep = $('#breadcrumb-sep-1');

const btnLaunch = $('#btn-launch');
const btnNextToP2 = $('#btn-p1-next');
const btnBackToP1 = $('#btn-p2-back');
const btnParse = $('#btn-p2-next');
const btnImport = $('#btn-import');
const btnBackToP2 = $('#btn-p3-back');
const btnNextToP4 = $('#btn-p3-next');
const btnHomeFromP4 = $('#btn-p4-home');
const btnDownload = $('#btn-download-all');
const importInput = $('#aims-import-input');

const apiKeyInput = $('#api-key');
const terminalUserInput = $('#terminal-user');
const terminalHostInput = $('#terminal-host');
const codeLanguageInput = $('#code-language');
const customModelInput = $('#custom-model-id');
const aimsTextarea = $('#aims-textarea');
const selectedModelInput = () => Array.from($$('input[name="model"]')).find(i => i.checked);
const modelInputs = () => customModelInput?.value.trim() || selectedModelInput().value;
const providerInputs = () => selectedModelInput().dataset.provider || 'groq';
const genModeInputs = () => Array.from($$('input[name="gen-mode"]')).find(i => i.checked).value;
const getCodeLanguage = () => codeLanguageInput.value.trim();

function updateModeFields() {
    const mode = genModeInputs();
    const osGroup = $('#os-credentials-group');
    const languageGroup = $('#general-language-group');

    if (mode === 'os') {
        osGroup.classList.remove('hidden', 'opacity-0');
        osGroup.classList.add('grid', 'opacity-100');
    } else {
        osGroup.classList.remove('grid', 'opacity-100');
        osGroup.classList.add('hidden', 'opacity-0');
    }

    if (mode === 'general') {
        languageGroup.classList.remove('hidden', 'opacity-0');
        languageGroup.classList.add('block', 'opacity-100');
    } else {
        languageGroup.classList.remove('block', 'opacity-100');
        languageGroup.classList.add('hidden', 'opacity-0');
    }
}

Array.from($$('input[name="gen-mode"]')).forEach(input => {
    input.addEventListener('change', updateModeFields);
});
const getSettings = () => ({
    fontName: $('#setting-font').value,
    bodySize: $('#setting-body-size').value,
    headingSize: $('#setting-heading-size').value,
    codeSize: $('#setting-code-size').value,
    captionSize: '10',
    imageWidth: $('#setting-img-width').value,
    terminalImgWidth: $('#setting-term-width').value,
    outputFilename: 'PractiGen_Artifact.docx'
});

const MAX_EXPORT_PAYLOAD_BYTES = 2_750_000;
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

function getDownloadExperiments(profile) {
    const mode = genModeInputs();

    return generatedExperiments.map((exp) => {
        const base = {
            aim: exp.aim,
            concept: exp.concept,
            caption: exp.caption
        };

        if (mode === 'os') {
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
    const mode = genModeInputs();
    if (mode !== 'os') return generatedExperiments.length;

    return generatedExperiments.reduce((count, exp) => {
        return count + Math.max((exp.steps || []).length, 1);
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
            mode: genModeInputs()
        };
        const body = JSON.stringify(payload);
        const bytes = new Blob([body]).size;
        lastResult = { payload, body, bytes, profile, unitCount };

        if (bytes <= MAX_EXPORT_PAYLOAD_BYTES) {
            return lastResult;
        }
    }

    return lastResult;
}

// Progress
const overallProgressBar = $('#overall-progress-bar');
const overallPercent = $('#overall-percent');
const experimentGrid = $('#experiment-grid');
const stepperProgress = $('#stepper-progress');
const stepItems = $$('.step-item');

// Advanced Tray
const toggleAdvanced = $('#toggle-advanced');
const advancedTray = $('#advanced-settings-tray');
const advancedArrow = $('#advanced-arrow');

// ========== State ==========
let currentStep = 1;
let parsedAims = [];
let generatedExperiments = [];

// ========== Core Logic ==========

function init() {
    loadFromLocalStorage();
    setupEventListeners();
    updateModeFields();
    showSection('dashboard');

    // UI Helper for Advanced Tray
    toggleAdvanced.addEventListener('click', () => {
        advancedTray.classList.toggle('hidden');
        advancedArrow.classList.toggle('rotate-180');
    });

    // Visibility toggle for API Key
    $('#toggle-api-visibility').addEventListener('click', () => {
        const type = apiKeyInput.type === 'password' ? 'text' : 'password';
        apiKeyInput.type = type;
        $('#toggle-api-visibility').querySelector('span').textContent = type === 'password' ? 'visibility' : 'visibility_off';
    });
}

function showSection(key) {
    Object.keys(sections).forEach(k => {
        sections[k].classList.add('hidden');
    });
    sections[key].classList.remove('hidden');

    // Update active nav
    if (key === 'dashboard') {
        navDashboard.classList.add('bg-primary/5', 'text-primary');
        navDashboard.classList.remove('text-slate-500');
        navWorkspace.classList.remove('bg-primary/5', 'text-primary');
        navWorkspace.classList.add('text-slate-500');
        breadcrumbMain.textContent = 'Overview';
        breadcrumbSub.classList.add('hidden');
        breadcrumbSep.classList.add('hidden');
    } else {
        navWorkspace.classList.add('bg-primary/5', 'text-primary');
        navWorkspace.classList.remove('text-slate-500');
        navDashboard.classList.remove('bg-primary/5', 'text-primary');
        navDashboard.classList.add('text-slate-500');
        breadcrumbMain.textContent = 'Pipeline';
        breadcrumbSub.classList.remove('hidden');
        breadcrumbSep.classList.remove('hidden');
        breadcrumbSub.textContent = 'Drafting Phase';
    }
}

function setPhase(phase) {
    currentStep = phase;
    Object.keys(phases).forEach(p => {
        phases[p].classList.add('hidden');
    });
    phases[phase].classList.remove('hidden');

    // Update Stepper
    const progressWidth = ((phase - 1) / (stepItems.length - 1)) * 100;
    stepperProgress.style.width = `${progressWidth}%`;

    stepItems.forEach((item, idx) => {
        const stepNum = idx + 1;
        const icon = item.querySelector('.icon-container');
        const label = item.querySelector('span');

        item.classList.remove('active', 'done');
        icon.classList.remove('bg-primary', 'text-white', 'bg-slate-100', 'text-slate-400', 'bg-emerald-50', 'text-emerald-600', 'border-emerald-100');
        label.classList.remove('text-slate-900', 'text-primary', 'text-emerald-600');

        if (stepNum < phase) {
            item.classList.add('done');
            icon.classList.add('bg-emerald-50', 'text-emerald-600', 'border-emerald-100');
            icon.innerHTML = '<span class="material-symbols-outlined text-[18px]">check</span>';
            label.classList.add('text-emerald-600');
        } else if (stepNum === phase) {
            item.classList.add('active');
            icon.classList.add('bg-primary', 'text-white');
            label.classList.add('text-primary', 'font-bold');
            const icons = ['settings', 'target', 'auto_awesome', 'ios_share'];
            icon.innerHTML = `<span class="material-symbols-outlined text-[18px]">${icons[idx]}</span>`;
        } else {
            icon.classList.add('bg-slate-100', 'text-slate-400');
            label.classList.add('text-slate-400');
            const icons = ['settings', 'target', 'auto_awesome', 'ios_share'];
            icon.innerHTML = `<span class="material-symbols-outlined text-[18px]">${icons[idx]}</span>`;
        }
    });

    // Update Breadcrumb
    const phaseNames = ['', 'Setup Context', 'Drafting Aims', 'Neural Synthesis', 'Artifact Export'];
    breadcrumbSub.textContent = phaseNames[phase];

    window.scrollTo({ top: 0, behavior: 'smooth' });
}

function setupEventListeners() {
    navDashboard.addEventListener('click', () => showSection('dashboard'));
    navWorkspace.addEventListener('click', () => {
        showSection('workspace');
        setPhase(currentStep);
    });

    btnLaunch.addEventListener('click', () => {
        showSection('workspace');
        setPhase(1);
    });

    btnNextToP2.addEventListener('click', () => {
        if (genModeInputs() === 'general' && !getCodeLanguage()) {
            showToast('Enter a code language for General Coding mode.', 'warning');
            codeLanguageInput.focus();
            return;
        }

        saveToLocalStorage();
        setPhase(2);
    });

    btnBackToP1.addEventListener('click', () => setPhase(1));

    btnParse.addEventListener('click', async () => {
        const text = aimsTextarea.value.trim();
        if (!text) {
            showToast('Logic drafting area is empty.', 'warning');
            return;
        }
        if (genModeInputs() === 'general' && !getCodeLanguage()) {
            showToast('Enter a code language before generation.', 'warning');
            setPhase(1);
            codeLanguageInput.focus();
            return;
        }

        btnParse.disabled = true;
        btnParse.innerHTML = '<span class="material-symbols-outlined animate-spin text-[18px]">refresh</span> Parsing Matrix...';

        try {
            const res = await fetch('/api/parse', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text }),
            });
            const data = await res.json();
            if (data.error) throw new Error(data.error);

            parsedAims = data.aims;
            renderExperimentWaitlist();
            setPhase(3);
            showToast(`Loaded ${parsedAims.length} experiments.`, 'success');

            // Auto-start generation
            startGeneration();
        } catch (err) {
            showToast(err.message, 'error');
        } finally {
            btnParse.disabled = false;
            btnParse.innerHTML = 'Start Generation <span class="material-symbols-outlined text-[18px]">bolt</span>';
        }
    });

    btnBackToP2.addEventListener('click', () => setPhase(2));
    btnNextToP4.addEventListener('click', () => setPhase(4));
    btnHomeFromP4.addEventListener('click', () => {
        showSection('dashboard');
        currentStep = 1;
    });

    $('#btn-sample').addEventListener('click', () => {
        aimsTextarea.value = "Aim 1: Implement a Doubly Linked List with basic CRUD operations in C++.\n---\nAim 2: Write a Python script to perform sentiment analysis on a batch of CSV comments.\n---\nAim 3: Develop a responsive landing page using pure HTML and modern CSS Gradients.";
        showToast("Simulation aims loaded.", "info");
    });

    btnImport.addEventListener('click', () => importInput.click());

    importInput.addEventListener('change', (e) => {
        const file = e.target.files[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = (re) => {
            aimsTextarea.value = re.target.result;
            showToast("Manifest imported successfully.", "success");
        };
        reader.readAsText(file);
    });

    btnDownload.addEventListener('click', async () => {
        btnDownload.disabled = true;
        btnDownload.innerHTML = '<span class="material-symbols-outlined animate-spin text-[18px]">refresh</span> PREPARING DOCUMENT...';

        try {
            const settings = getSettings();
            const downloadPayload = getDownloadPayload(settings);
            terminalLog(`➜ Export payload prepared (${Math.round(downloadPayload.bytes / 1024)} KB, ${downloadPayload.profile.lines}-line output profile).`);
            const res = await fetch('/api/download', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: downloadPayload.body,
            });

            if (!res.ok) {
                const errData = await res.json().catch(() => ({}));
                throw new Error(errData.error || 'Server rejected bundle request.');
            }

            const blob = await res.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = settings.outputFilename;
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
            showToast("Document bundle exported.", "success");
            terminalLog("➜ Export successful. Project completed.");
        } catch (err) {
            console.error(err);
            const message = err instanceof TypeError && err.message === 'Failed to fetch'
                ? 'Download service is unreachable or the request was too large. Try fewer experiments or smaller terminal output.'
                : err.message;
            showToast(`Bundle export error: ${message}`, "error");
            terminalLog(`➜ CRITICAL: ${message}`);
        } finally {
            btnDownload.disabled = false;
            btnDownload.innerHTML = '<span class="material-symbols-outlined text-[18px]">cloud_download</span> DOWNLOAD DOCUMENT';
        }
    });
}

function renderExperimentWaitlist() {
    experimentGrid.innerHTML = '';
    parsedAims.forEach((aim, i) => {
        const card = document.createElement('div');
        card.className = "glass-card p-6 rounded-2xl flex flex-col gap-4 border-l-4 border-l-slate-200 shadow-sm opacity-60 spring-transition";
        card.id = `exp-card-${i}`;
        card.innerHTML = `
            <div class="flex items-center justify-between">
                <div class="status-indicator px-2 py-1 rounded-md bg-slate-100 text-slate-400 text-[9px] font-black uppercase tracking-widest flex items-center gap-1">
                   <span class="size-1.5 rounded-full bg-slate-400"></span> QUEUED
                </div>
                <span class="text-[9px] font-mono text-slate-300">EXP-${(i + 1).toString().padStart(3, '0')}</span>
            </div>
            <h3 class="font-bold text-slate-800 line-clamp-1">${aim.split(':')[1]?.trim() || aim.slice(0, 30)}</h3>
            <p class="text-[11px] text-slate-500 line-clamp-2 italic leading-relaxed">${aim}</p>
        `;
        experimentGrid.appendChild(card);
    });

    // Reset progress
    overallProgressBar.style.width = '0%';
    overallPercent.textContent = '0%';
    btnNextToP4.classList.add('hidden');
    terminalLog("➜ Initializing neural stack...");
}

async function startGeneration() {
    generatedExperiments = [];
    const apiKey = apiKeyInput.value.trim();
    const model = modelInputs();
    const provider = providerInputs();
    const mode = genModeInputs();
    const codeLanguage = getCodeLanguage();
    const terminalUser = terminalUserInput.value.trim() || 'student';
    const terminalHost = terminalHostInput.value.trim() || 'kali';

    terminalLog(`➜ Target Provider: ${provider.toUpperCase()} / ${model.toUpperCase()}`);

    if (mode === 'general') {
        terminalLog(`Language Lock: ${codeLanguage}`);
    }

    for (let i = 0; i < parsedAims.length; i++) {
        const aim = parsedAims[i];
        const card = $(`#exp-card-${i}`);

        // Processing State
        card.classList.remove('opacity-60', 'border-l-slate-200');
        card.classList.add('border-l-primary', 'shadow-md');
        const indicator = card.querySelector('.status-indicator');
        indicator.className = "status-indicator px-2 py-1 rounded-md bg-primary/10 text-primary text-[9px] font-black uppercase tracking-widest flex items-center gap-1";
        indicator.innerHTML = '<span class="size-1.5 rounded-full bg-primary animate-ping"></span> SYNTHESIZING';

        terminalLog(`➜ Processing Seed ${i + 1}/${parsedAims.length}...`);

        try {
            const res = await fetch('/api/generate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ aim, api_key: apiKey, provider, model, mode, code_language: codeLanguage, terminal_user: terminalUser, terminal_host: terminalHost })
            });
            const data = await res.json();
            if (data.error) throw new Error(data.error);

            generatedExperiments.push({ aim, ...data });

            // Success State
            card.classList.replace('border-l-primary', 'border-l-emerald-500');
            indicator.className = "status-indicator px-2 py-1 rounded-md bg-emerald-50 text-emerald-600 text-[9px] font-black uppercase tracking-widest flex items-center gap-1";
            indicator.innerHTML = '<span class="material-symbols-outlined text-[10px]">check_circle</span> COMPLETE';
            terminalLog(`✔ Seed ${i + 1} synthesized successfully.`);
        } catch (err) {
            showToast(`Logic node ${i + 1} failed: ${err.message}`, 'error');
            card.classList.replace('border-l-primary', 'border-l-red-500');
            indicator.className = "status-indicator px-2 py-1 rounded-md bg-red-50 text-red-500 text-[9px] font-black uppercase tracking-widest flex items-center gap-1";
            indicator.innerHTML = '<span class="material-symbols-outlined text-[10px]">error</span> FAILED';
            terminalLog(`✖ Seed ${i + 1} synthesis error: ${err.message}`);
        }

        // Update Overall Progress
        const pct = Math.round(((i + 1) / parsedAims.length) * 100);
        overallProgressBar.style.width = `${pct}%`;
        overallPercent.textContent = `${pct}%`;
    }

    showToast("Global neural synthesis resolved.", "success");
    terminalLog("➜ All active pathways resolved. Synthesis session ended.");
    btnNextToP4.classList.remove('hidden');
    renderFinalArtifacts();
}

function renderFinalArtifacts() {
    const list = $('#artifacts-list');
    list.innerHTML = '';
    generatedExperiments.forEach((exp, i) => {
        const item = document.createElement('div');
        item.className = "glass-panel rounded-2xl p-1 overflow-hidden transition-all hover:shadow-lg border border-slate-100 bg-white shadow-sm";

        // Build steps HTML for preview
        let stepsHtml = '';
        if (exp.steps && exp.steps.length > 0) {
            exp.steps.forEach((step) => {
                stepsHtml += `
                    <div class="mb-4 border-l-2 border-primary/20 pl-4">
                        <p class="text-xs font-bold text-primary mb-1">Step ${step.num}: ${step.explanation || ''}</p>
                        <div class="bg-slate-950 rounded-lg p-3 font-mono text-[11px] text-slate-300 overflow-x-auto border border-slate-800 mb-2">
                            <pre><code>${step.command || ''}</code></pre>
                        </div>
                        ${step.output ? `
                        <div class="bg-slate-900 rounded-lg p-3 font-mono text-[10px] text-emerald-400 overflow-x-auto border border-slate-800">
                            <pre><code>${step.output}</code></pre>
                        </div>` : ''}
                    </div>`;
            });
        } else {
            stepsHtml = `
                <div class="bg-slate-950 rounded-xl p-6 font-mono text-[11px] text-slate-300 overflow-x-auto shadow-inner border border-slate-800">
                    <pre><code>${exp.code || '// No procedure provided.'}</code></pre>
                </div>`;
        }

        item.innerHTML = `
            <div class="flex items-center justify-between p-4 group">
                <button class="flex items-center gap-4 text-left flex-1" onclick="this.parentElement.nextElementSibling.classList.toggle('hidden')">
                    <div class="size-10 rounded-xl bg-slate-50 text-slate-400 flex items-center justify-center group-hover:text-primary transition-colors">
                        <span class="material-symbols-outlined">description</span>
                    </div>
                    <div>
                        <p class="font-bold text-slate-900 leading-none">Experiment ${(i + 1).toString().padStart(2, '0')}</p>
                        <p class="text-[10px] text-slate-400 mt-1 font-mono uppercase tracking-widest">${exp.steps ? 'Ready for Export' : 'In Progress'}</p>
                    </div>
                </button>
                <div class="flex gap-2">
                    <button onclick="promptRefine(${i})" class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[10px] font-bold text-primary hover:bg-primary/5 transition-all border border-transparent hover:border-primary/20">
                        <span class="material-symbols-outlined text-sm">edit_note</span> REFINE
                    </button>
                </div>
            </div>
            <div class="hidden px-6 pb-8 text-sm text-slate-600 leading-relaxed space-y-6 border-t border-slate-50 pt-6 bg-slate-50/30">
                <div class="space-y-2">
                    <h4 class="text-[10px] font-black text-slate-400 uppercase tracking-widest">Theory</h4>
                    <p class="text-slate-900 font-medium whitespace-pre-line">${exp.concept}</p>
                </div>
                
                <div class="space-y-2">
                    <h4 class="text-[10px] font-black text-slate-400 uppercase tracking-widest">Procedure</h4>
                    ${stepsHtml}
                </div>

                <div class="flex items-center justify-between pt-4 border-t border-slate-100 mt-4">
                    <div class="flex items-center gap-2">
                        <span class="size-2 rounded-full bg-emerald-500"></span>
                        <p class="text-[9px] font-bold text-slate-500 uppercase tracking-wider">${exp.caption}</p>
                    </div>
                    <span class="text-[9px] font-mono text-slate-300 uppercase tracking-tighter">Experiment Verified</span>
                </div>
            </div>
        `;
        list.appendChild(item);
    });
}

window.promptRefine = async (index) => {
    const change = prompt("What modifications would you like for this experiment?\n(e.g., 'Make it in Python', 'Add more comments', 'Explain focus on loops')");
    if (!change) return;

    const exp = generatedExperiments[index];
    const originalAim = exp.aim;
    const refinedAim = `Original Aim: ${originalAim}\nRequested Change: ${change}`;

    showToast(`Refining Experiment ${index + 1}...`, 'info');
    terminalLog(`➜ Updating Experiment ${index + 1} with requested changes...`);

    try {
        const apiKey = apiKeyInput.value.trim();
        const model = modelInputs();
        const provider = providerInputs();
        const mode = genModeInputs();
        const codeLanguage = getCodeLanguage();
        const terminalUser = terminalUserInput.value.trim() || 'student';
        const terminalHost = terminalHostInput.value.trim() || 'kali';

        const res = await fetch('/api/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ aim: refinedAim, api_key: apiKey, provider, model, mode, code_language: codeLanguage, terminal_user: terminalUser, terminal_host: terminalHost })
        });

        const data = await res.json();
        if (data.error) throw new Error(data.error);

        generatedExperiments[index] = { aim: originalAim, ...data };
        renderFinalArtifacts();
        showToast(`Node ${index + 1} refined successfully.`, 'success');
        terminalLog(`✔ Node ${index + 1} overrides applied.`);
    } catch (err) {
        showToast(`Refinement failed: ${err.message}`, 'error');
        terminalLog(`✖ Node ${index + 1} override error: ${err.message}`);
    }
};

function terminalLog(msg) {
    const log = $('#terminal-log');
    const p = document.createElement('p');
    p.className = "mt-1 animate-in fade-in slide-in-from-left-2 duration-300";
    p.textContent = msg;
    log.appendChild(p);
    log.scrollTop = log.scrollHeight;
}

function showToast(message, type = 'info') {
    const container = $('#toast-container');
    const toast = document.createElement('div');
    const colors = {
        success: 'bg-emerald-500',
        error: 'bg-red-500',
        info: 'bg-primary',
        warning: 'bg-amber-500'
    };
    const icons = {
        success: 'verified',
        error: 'report',
        info: 'info',
        warning: 'warning'
    };

    toast.className = `${colors[type]} text-white px-6 py-4 rounded-xl shadow-2xl flex items-center gap-3 font-bold animate-in slide-in-from-bottom-5 duration-500 border border-white/20`;
    toast.innerHTML = `<span class="material-symbols-outlined text-[20px]">${icons[type]}</span> <span class="text-sm">${message}</span>`;

    container.appendChild(toast);
    setTimeout(() => {
        toast.classList.add('animate-out', 'fade-out', 'slide-out-to-bottom-5');
        setTimeout(() => toast.remove(), 500);
    }, 5000);
}

function saveToLocalStorage() {
    localStorage.setItem('practigen_api_key_v5', apiKeyInput.value);
    localStorage.setItem('practigen_code_language_v5', getCodeLanguage());
    localStorage.setItem('practigen_custom_model_v5', customModelInput?.value.trim() || '');
}

function loadFromLocalStorage() {
    apiKeyInput.value = localStorage.getItem('practigen_api_key_v5') || '';
    codeLanguageInput.value = localStorage.getItem('practigen_code_language_v5') || '';
    if (customModelInput) {
        customModelInput.value = localStorage.getItem('practigen_custom_model_v5') || '';
    }
}

// Start
init();
