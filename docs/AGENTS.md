# PractiGen: agent and contributor guide

Use this guide to locate the implementation, preserve its contracts, and choose checks appropriate to a change. Update it when capabilities, configuration, or architecture change. The source code remains the authority for current behavior.

This guide lives in `docs/`. Agents working elsewhere in the repository should read it explicitly; a nested `AGENTS.md` is not automatically a repository-wide instruction file. All code paths below are relative to the repository root.

## Working expectations

- Check Git status and applicable instructions before editing. Preserve unrelated changes and never stage them incidentally.
- Inspect the relevant implementation before choosing a solution. Ask follow-up questions when a missing requirement affects the result; continue independent work while waiting.
- Complete the authorized implementation and necessary verification. Report what changed, what was checked, and any remaining blockers or unverified behavior.
- Keep commits focused and reviewable when commits are requested. Push only when the current task or conversation authorizes it.
- Run checks because they validate changed behavior. Documentation-only changes normally need link/reference and whitespace checks, not application tests.

## Current capabilities

The UI must expose exactly three main modes, with understandable titles and short descriptions:

| Main mode | Supported work | Internal file types |
| --- | --- | --- |
| Project files | Frontend and DBMS reports from verified repository context, prose and screenshots | `project`, with `frontend` or `dbms` submode |
| Lab files | Programming practicals with explanations, code and sample output in C, C++, Python, Java and JavaScript | `coding` |
| OS/CN/Custom files | Linux command steps, eight deterministic networking experiments, or mixed aims classified individually | `os`, `cn`, `auto` |

`state.config.mainMode` groups the UI; `state.config.mode` retains the file type used by existing API and export contracts. The backend normalizes `coding` to `general`. Preserve useful choices inside the three main modes rather than adding top-level modes.

Lab workflows include aim/file import, PDF syllabus extraction, language detection and locking, targeted refinement, formatting, per-experiment retries, session restore, and classroom ZIP export. The optional Jev quality review is advisory: failure must not block or change a generated lab result.

CN generation needs no AI key. Complete supplied Project prose also needs no AI call. Compact Project collection deliberately leaves prose blank, so drafting those sections requires an AI endpoint.

## Implementation map

| Location | Responsibility |
| --- | --- |
| `app.py` | Flask routes, endpoint configuration, lab prompts/parsing, OS/coding Word generation |
| `public/index.html`, `public/landing.css`, `public/landing.js` | Landing page, responsive navigation and interactive illustrative preview |
| `public/workspace.html`, `public/workspace.css`, `public/script.js` | Dashboard, three-mode file builder, shared state, requests, review and export |
| `public/design.css` | Shared light theme, coral/navy colors, typography, responsive controls and layout primitives |
| `project_file/reports.py` | Project schema version 1, collectors, context validation, prose parsing and report assembly |
| `project_file/documents.py` | Project Word formatting, validated image decoding and embedding |
| `cn/experiments.py` | Supported CN catalog, validated plans and deterministic recipes |
| `cn/rendering.py`, `cn/documents.py` | CN figures, canonical export rebuild and Word formatting |
| `tests/` | Offline backend tests, Node controller contracts and optional Playwright integration |
| `.env.example`, `requirements.txt`, `vercel.json` | Configuration examples, Python dependencies and deployment routing |

The backend uses Flask, Flask-CORS and python-dotenv; documents use python-docx and Pillow. PDF aim extraction imports pypdf lazily. Start locally with `pip install -r requirements.txt` and `python app.py`, then open `http://localhost:5000`.

The homepage is the landing page; its calls to action open `/workspace.html`. Optional `?mode=project`, `?mode=lab` and `?mode=custom` shortcuts open setup without discarding a saved session. There is no frontend build step, pricing screen or account flow. Preserve the same design tokens across landing and workspace. Keep mobile navigation, visible active steps, keyboard-operated review controls and refinement-dialog focus behavior usable. The landing preview is illustrative; do not claim generated code has been executed.

## Project workflow and contracts

1. `/api/project/prompt/<submode>` returns the read-only collector prompt. Keep it facts-only: target under 12,000 characters for Frontend or 24,000 for DBMS without truncating required facts or SQL. Do not ask for a full report inside a single JSON response.
2. `/api/project/validate` parses and normalizes the context, returning errors, missing facts, warnings and `generation_needed`. The importer tolerates fences, context markers, BOMs, trailing commas and literal newlines in strings. It must not guess truncated strings or invent missing facts. Preserve useful line/column and recovery errors.
3. The browser requests each missing field through `/api/project/section`. The model returns one section as plain prose; the API returns `{section, text}`. Keep requests small enough for the deployment deadline.
4. `state.projectFile.completedSections` saves successful sections in session storage. Retry and restore must reuse them. Context changes clear cached prose and images; cancellation and stale responses must not overwrite newer work.
5. `/api/project/generate` validates `completed_sections` and assembles the report with supplied prose. It may draft at most one remaining section; callers with several missing sections must use the section endpoint first. Preserve every nonblank supplied section. Word counts are writing guidance, not a reason to replace user prose.
6. `/api/project/download` revalidates the report and declared images before creating Word output. Use `build_project_document`, not the lab parser or `/api/download`.

Facts, SQL, references, execution claims and screenshot declarations must come from validated context. AI completion fills prose only. Never execute submitted code/SQL or fetch image URLs. All declared images must be attached for export; images remain in memory and must be reattached after a page refresh.

See [Project files](project-files.md) for schema, report order, formatting and direct API usage.

## Shared AI configuration

- Keep one default model: `openai/gpt-oss-120b` at `https://api.groq.com/openai/v1`. Support a custom OpenAI-compatible base URL, model ID and API key without restoring the provider/model gallery.
- Frontend requests use `genContext()`. Backend generation, refinement, PDF extraction and Project drafting use `resolve_llm_config()` and `run_completion()`; do not duplicate endpoint resolution or call `create_chat_completion()` directly from routes.
- `LLM_BASE_URL`, `LLM_MODEL` and `LLM_API_KEY` define server defaults. `GROQ_API_KEY` is a compatibility fallback for the default endpoint. `OPENROUTER_API_KEY` belongs to the optional lab quality review.
- A submitted key overrides server credentials. Server keys and model defaults apply only to their configured endpoint. Keep request-specific configuration out of mutable globals and reject redirects so credentials cannot follow them.
- Require HTTPS for custom deployed endpoints. HTTP localhost endpoints are permitted locally, not on Vercel. Accept an API base URL or normalize a full `/chat/completions` URL.
- Each completion makes one HTTP request with a 45-second timeout. Reject empty text and responses marked truncated. Keep retries visible in the frontend; do not reintroduce cross-provider fallback or long server-side backoff.
- Native Anthropic payloads, Cerebras/FreeModel presets and repeated `.env` key rotation are removed. Old unscoped browser credentials must not be silently reused with a new endpoint.

UI endpoint/model/key preferences use local storage; report progress uses session storage. Never include credentials in logs, fixtures, exported documents, or commits. See [configuration](../README.md#ai-configuration) and [environment example](../.env.example).

## Lab, CN and export invariants

- Keep lab prompts in `build_messages` / `build_refine_messages`, and Project prompts in `project_file/reports.py`. Put output and language constraints in system messages.
- `generateOne()` has up to three attempts; lab generation runs in batches of three. Invalid client configuration should not cause repeated unnecessary calls.
- Refinement includes the full existing experiment, uses low temperature, and preserves the aim. Respect each experiment's detected type and language in mixed batches.
- Use `expIsCn()` and `expIsOs()` for per-experiment rendering/export. A global mode alone cannot describe a mixed batch.
- CN must branch before AI key resolution. Derive addresses, commands, figures and tables from one validated plan. Export rebuilds canonical recipes; edits regenerate the recipe instead of entering generic AI refinement.
- CN results have `execution.status: "not_run"`. Generated expected output is not simulator evidence. See [CN files](computer-networks.md).
- Lab documents use `build_document()` and `embed_output_image()`; close image buffers after use. Classroom ZIP assembly performs no AI work on the backend; the browser supplies student variations.
- Preserve bounded gzip decoding, output-unit guards and image validation. Vercel routes `/api/*` to Flask with a configured 60-second maximum duration and serves `public/` statically.

## Limits and unfinished capabilities

| Area | Current constraint |
| --- | --- |
| Project input | 300,000 characters per context; 45,000 per prose field; generated prose must contain at least 40 characters |
| Frontend report results | 10-15 distinct screenshot-led entries |
| DBMS report | Validated entities, relationships, tables, complete schema SQL, use cases and a declared ERD image |
| Project images | PNG/JPEG only; 16 images; 1,000,000 bytes each; 2,500,000 total decoded bytes; 16,000,000 pixels each |
| Export payload | 3,500,000 bytes compressed; 50,000,000 bytes after decompression |
| Lab export | At most 200 output units per document |
| Classroom export | At most 12 students |

Java project reports and an institution-specific project title page are pending; the current cover uses supplied metadata. Actual simulator captures are not connected for CN. Language checks are heuristic, and generated code/output is not executed by this app. Do not present automated or mocked verification as evidence of live provider availability, browser layout, or real execution.

## Choose necessary checks

Use pytest with its built-in `monkeypatch` fixture; pytest-mock is not required. All automated AI/HTTP tests must run without live keys or network calls.

| Changed area | Relevant checks |
| --- | --- |
| Project context, drafting or Word export | `python -m pytest -q -p no:cacheprovider tests/test_project_file.py` |
| AI configuration or request handling | `python -m pytest -q -p no:cacheprovider tests/test_llm_endpoint.py tests/test_api.py` |
| CN plans, rendering or export | `python -m pytest -q -p no:cacheprovider tests/test_cn.py` |
| Shared parsing, detection or prompt helpers | `python -m pytest -q -p no:cacheprovider tests/test_utils.py` |
| Project UI, mode selection or session recovery | `node tests/test_project_frontend.cjs` |
| CN or mixed-file browser behavior | `node tests/test_cn_frontend.cjs` |
| JavaScript syntax | `node --check public/script.js` and `node --check public/landing.js` as applicable |
| Full frontend redesign or cross-screen interactions | With a local server running: `python tests/browser_smoke.py --url http://127.0.0.1:5000 --channel chromium` |
| Documentation or final patch hygiene | Check local links and stale references; run `git diff --check` |

Install pytest in an isolated environment when needed. Add regression coverage for meaningful bugs and changed contracts, not tests that merely repeat implementation details. Inspect browser layout or rendered Word output when the change affects them; report tooling blockers honestly. Once appropriate checks pass, repeat them only for further changes, failures or unresolved concerns.

The optional browser check requires Playwright, pytest (for shared fixtures), project dependencies and a browser installed via `python -m playwright install chromium`, or use an installed Edge with `--channel msedge`. It mocks AI generation/refinement and uses the local backend for validation, report assembly, CN recipes and Word downloads. It captures desktop/mobile screenshots, checks overflow and exercises section failure/resume, image requirements and session restore. Outputs belong in ignored `.artifacts/browser/`; do not commit browser downloads or imported design archives. A passing run is not evidence of live provider availability. Review screenshots when appearance changes.
