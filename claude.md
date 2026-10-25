# PractiGen — Engineering Guide (for Claude / contributors)

Context for working in this repo: what the code does, where things live, and the conventions to follow. Keep this file accurate when you change architecture.

## What it is

An AI tool that turns a list of experiment aims into a formatted `.docx` practical lab file. A student pastes aims (separated by `---`), each aim is sent to an LLM, and the app builds a Word document with theory, code/procedure, and terminal-output images per experiment. OS/coding lab styles: **OS** (numbered Linux steps with rendered terminal screenshots) and **coding** (concept + source + console output) in C/C++/Python/Java/JavaScript.

CN mode additionally generates eight supported network experiments from deterministic recipes, without an LLM. It derives addresses, CLI, figures and tables from one validated plan and exports the supplied lab-report style without a cover page. See `docs/computer-networks.md` for the execution boundary.

## Stack

| Layer | Tech |
|-------|------|
| Frontend | Vanilla ES6, HTML5, CSS3 — single-page app, **no build step** |
| Backend | Python 3.9+ / Flask — single `app.py` |
| Docs | `python-docx` + Pillow (terminal output rendered to PNG) |
| PDF | `pypdf` (lazy import; syllabus extraction) |
| Providers | Groq, Cerebras, FreeModel (OpenAI + Anthropic formats) |
| Deploy | Vercel serverless (`vercel.json`) |
| Tests | pytest + pytest-mock (LLM mocked; runs offline) |

## Layout

```
app.py              # Flask routes + OS/coding document generation
cn/
  experiments.py    # CN catalog, plan validation and deterministic recipes
  rendering.py      # Pillow topology, IP-setting and expected-output figures
  documents.py      # CN report formatting and canonical export rebuild
public/
  index.html        # SPA markup
  script.js         # State, API calls, rendering
  style.css         # Dark theme
tests/
  test_utils.py     # Pure helper unit tests
  test_api.py       # Endpoint tests (mocked LLM)
requirements.txt
vercel.json
```

## Backend map (`app.py`)

Order top to bottom:

- **Download payload I/O** — `read_download_payload()` handles gzip + size limits (`MAX_COMPRESSED_DOWNLOAD_BYTES`, `MAX_DECOMPRESSED_DOWNLOAD_BYTES`).
- **`LLM_PROVIDERS`** — provider config: base URL, env vars, API format (`openai`/`anthropic`), fallback model.
- **Provider plumbing** — `get_env_values`, `get_provider_keys` (UI key first, then env, deduped), `create_chat_completion` (one HTTP call; takes `temperature`/`top_p`), `build_anthropic_payload`, response parsers.
- **`run_completion(messages, provider, model, config, keys, temperature, top_p)`** — the shared completion runner. Builds provider attempts (incl. fallbacks via `build_provider_attempts`), rotates keys, backs off on 429, falls back across providers/models. Uses an explicit `result_text` sentinel — **do not** reintroduce the old `'text' in locals()` pattern.
- **Parsing/validation** — `extract_section` (tag parser with markdown fallback), `LANGUAGE_MARKERS` + `validate_code_language` (heuristic positive/negative markers; unknown language → pass), `validate_generation_result` (rejects empty/too-short responses so a malformed response retries instead of being saved).
- **`detect_mode_and_language(aim)`** — regex heuristics → `{mode, code_language}`; CN recognition precedes generic OS command cues, while explicit programming-language aims stay in coding mode.
- **`build_messages(mode, aim, lang, user, host, seed)`** — returns `[system, user]`. **All prompt construction goes through here.** Hard rules (output tags, language lock) live in the system message; aim + variation seed in the user message.
- **`parse_generation_text(text, mode, lang)`** — raw LLM text → result dict (concept/code/output/steps/caption).
- **`parse_steps(procedure)`** — OS procedure text → step list. Handles `Step N:` and `N.` formats with a single-step fallback.
- **Routes** — `/api/parse`, `/api/generate`, `/api/refine`, `/api/detect`, `/api/extract-aims`, `/api/download`, `/api/classroom-zip`.
- **Doc builders** — `set_font`, `add_*_para`, caption helpers, `create_terminal_image` (Pillow → PNG; closes the image), `embed_output_image` (embeds + **always frees the buffer**), `count_output_units`, **`build_document(experiments, settings, mode)` → BytesIO** (shared by download and classroom-zip).

### Endpoint notes

- **CN routes** — `/api/cn/catalog` lists recipes; `/api/cn/plan` returns a canonical plan without previews. `/api/generate` short-circuits for CN before provider/key resolution. Unsupported aims return 400. CN results contain `mode:"cn"` and `execution.status:"not_run"`.
- **`/api/generate`** — normalizes UI `mode:'coding'` → `'general'`; `mode:'auto'` (or `auto_detect:true`) routes through `detect_mode_and_language` per request. After parsing it validates the result, and for language-locked modes re-generates once if the code fails `validate_code_language`.
- **`/api/refine`** — takes the full existing experiment as context and edits it at low temperature (0.3). Preserves the original `aim` in the response.
- **`/api/classroom-zip`** — backend does **no LLM work**; the frontend generates per-student variations and posts them, the backend builds one `.docx` per student into a ZIP.

## Frontend map (`public/script.js`)

- One module-level `state` object holds everything (`config`, `experiments`, view, flags).
- **`genContext()`** — resolves provider/model/apiKey (custom model overrides preset). Reuse it; don't re-read the inputs inline.
- **`generateOne(idx)`** — generates a single experiment with a 3-attempt retry. `startGeneration()` runs these in **concurrent batches of 3** (`Promise.all`).
- **`expIsCn(exp)`** — recognises per-experiment CN records. The complete plan and PNG previews live on `exp.cn`; export sends only aim/profile/mode and the backend rebuilds the record. Session restore preserves student identity.
- **`expIsOs(exp)`** — an experiment renders OS-style when global mode is OS **or** it has steps (so Auto-mode mixed batches export correctly). Use this, not `state.config.mode === 'os'`, for per-experiment branching.
- **Session** — `persistSession()` writes to `sessionStorage` after each success and on finish; `maybeOfferRestore`/`restoreSession`/`clearSession` drive the restore banner.
- **Export** — `getDownloadExperiments`/`getExportUnitCount`/`packExperimentForExport` shape state into the backend schema; `compactOutputForExport` truncates per profile; large payloads are gzipped before POST.
- **Phase 3 UI** — `autoDetectAims`, `importSyllabusPdf`/`handlePdfImport`, `downloadClassroomZip` (capped at `CLASSROOM_MAX_STUDENTS = 12`).

## Conventions

- **Prompts:** only build them in `build_messages` / `build_refine_messages`. Keep hard constraints in the system message.
- **LLM calls:** always go through `run_completion`, never call `create_chat_completion` directly from a route.
- **CN conventions:** extend recipes in `cn/experiments.py`, preserve one source for all addresses, and add validator/endpoint/export tests. Never turn generated expectations into claimed simulator observations. No renderer may fetch arbitrary image URLs. CN edits regenerate the canonical recipe rather than entering the generic LLM refine path.
- **Documents:** build via `build_document`; embed OS/coding images via `embed_output_image` so buffers are freed (serverless memory is tight).
- **Per-experiment mode:** branch on `exp` having steps (`expIsOs` / `bool(steps)`), not the global mode, so Auto/mixed batches work.
- **Frontend:** no framework, no build — keep it vanilla. Tailwind is a CDN convenience, not required.
- **Tests:** mock the LLM (`monkeypatch app.create_chat_completion`). Tests must run with no network and no key. Add coverage when you add an endpoint or helper.

## Run & test

```bash
pip install -r requirements.txt
python app.py            # http://localhost:5000

pip install pytest pytest-mock
pytest -q
```

## Deploy

`vercel.json` routes `/api/*` → `app.py` (60s max duration) and serves `public/` statically. Batch guards exist because of the 60s limit and ~256MB serverless memory: `/api/download` rejects >200 output units; classroom mode is capped at 12 students.

## Env vars

`GROQ_API_KEY`, `CEREBRAS_API_KEY`, `FREEMODEL_API_KEY`, `FREEMODEL_OPENAI_API_KEY`, `FREEMODEL_ANTHROPIC_API_KEY`. Repeats of the same var in `.env` act as rotation backups. UI-entered key wins over env.
```
