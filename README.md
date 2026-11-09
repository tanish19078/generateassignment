# PractiGen

Create formatted Word files from experiment aims or verified project facts.

## Three main modes

| Mode | What it creates |
| --- | --- |
| **Project files** | Frontend or DBMS project reports from repository facts, supplied prose and screenshots. |
| **Lab files** | Programming practicals with explanations, source code and sample output in C, C++, Python, Java or JavaScript. |
| **OS/CN/Custom files** | Choose Linux command steps, deterministic networking experiments, or custom mixed aims with per-aim language/type detection. |

Within these modes, aim imports, PDF syllabus extraction, refinement, formatting, session restore and classroom ZIP exports remain available where applicable. CN uses eight deterministic recipes and needs no AI key; generated expectations are not claimed simulator observations. See [CN files](docs/computer-networks.md).

## Project workflow

1. Choose Project files, then Frontend or DBMS.
2. Copy the compact collector prompt into the AI session that already knows your repository.
3. Paste its complete facts-only JSON or import a `.json` file, then validate it.
4. Build the report. Missing prose is drafted one section per request; supplied prose is preserved even when shorter than the writing guidance.
5. Review and upload the images named in your context. Export a `.docx`.

Each completed section is saved in the browser session. If a request fails, **Retry remaining sections** resumes from the last completed section, including after session restore. Images must be reattached after a refresh. Complete supplied reports need no AI call. The compact collector leaves prose blank and therefore needs a configured AI endpoint for drafting.

The importer accepts Markdown fences, context markers, a UTF-8 BOM, trailing commas and literal newlines in strings. It does not guess missing facts or repair truncated strings: errors identify a line/column and suggest importing a complete compact response. See [Project files](docs/project-files.md) for the schema and report conventions.

## Run locally

Python 3.9+ is required.

```sh
pip install -r requirements.txt
python app.py
```

Open http://localhost:5000 for the landing page, then choose **Open workspace** to start a file. The dashboard is also available directly at `/workspace.html`; shortcuts such as `/workspace.html?mode=project` select Project, Lab (`lab`) or OS/CN/Custom (`custom`) setup.

The landing page and all workflow screens share coral controls, navy outlines, light backgrounds and consistent typography. The mobile navigation and step bar support the same workflows as desktop. There is no pricing or account requirement. The frontend is vanilla HTML/CSS/JavaScript with no build step. Vercel deployment uses `vercel.json` to serve `public/` and route `/api/*` to Flask.

## AI configuration

There is one default model: `openai/gpt-oss-120b` at `https://api.groq.com/openai/v1`.

Use the **AI endpoint**, **Model ID**, and **API Key** fields to connect another OpenAI-compatible chat-completions endpoint. Enter its API base URL; a full `/chat/completions` URL is also accepted. HTTP localhost endpoints are supported when running locally. Deployed custom endpoints require HTTPS. Native Anthropic payloads and provider/model galleries have been removed.

Leave fields blank to use the server configuration in `.env` (see [.env.example](.env.example)):

| Variable | Purpose |
| --- | --- |
| `LLM_BASE_URL` | Server API base URL; defaults to Groq. |
| `LLM_MODEL` | Server model ID; defaults to GPT-OSS 120B only for Groq. |
| `LLM_API_KEY` | Server key for that endpoint. |
| `GROQ_API_KEY` | Compatibility fallback for the default Groq endpoint. |
| `OPENROUTER_API_KEY` | Optional existing advisory quality review for lab generation. |

A submitted key overrides server credentials. Server keys are never sent to a different user-selected endpoint; redirects are rejected, and providers are not silently switched. Each AI HTTP call has a 45-second timeout; visible retries belong to the frontend. Repeated `.env` key rotation and Cerebras/FreeModel presets are no longer supported. For an old preset, enter its endpoint, model and key again. Unscoped keys from older browser preferences are not reused automatically.

Endpoint settings and keys entered in the UI are stored in that browser's local storage; report progress uses session storage. Use a trusted browser profile.

## Focused checks

The backend and Node tests mock AI and HTTP calls. No live provider key is needed. Choose checks for the behavior changed instead of running every suite for visual or documentation edits.

```sh
pip install pytest
python -m pytest -q -p no:cacheprovider tests/test_project_file.py tests/test_llm_endpoint.py tests/test_api.py tests/test_cn.py
node tests/test_project_frontend.cjs
node tests/test_cn_frontend.cjs
```

The Project tests cover compact import, parsing recovery, section generation, supplied-prose preservation, image validation, and Frontend/DBMS Word exports. Frontend contracts cover three-mode navigation, endpoint persistence, failed-section resume, cancellation, review, download and session restore.

For changes across browser screens, an optional Playwright check exercises the running local app at desktop and mobile sizes. With the app running on port 5000:

```sh
pip install playwright pytest
python -m playwright install chromium
python tests/browser_smoke.py --url http://127.0.0.1:5000 --channel chromium
```

Use `--channel msedge` for an installed Microsoft Edge browser. This check intercepts AI generation and refinement, while context validation, report assembly, deterministic CN generation and Word downloads use the local backend. It covers navigation, lab refinement, failed Project section resume, image uploads, session restore and responsive overflow. Screenshots and downloaded artifacts go to ignored `.artifacts/browser/`. It does not verify live AI provider availability or execute generated programs.

## Structure

Contributors and coding agents: read [the agent and contributor guide](docs/AGENTS.md) before editing. It documents implementation contracts, current limits, and when verification is necessary.

- `app.py`: Flask API, shared endpoint configuration, OS/coding Word generation.
- `project_file/`: collector/schema, report assembly, image validation and project Word formatting.
- `cn/`: deterministic networking plans, figures and Word generation.
- `public/index.html`, `landing.css`, `landing.js`: landing page and interactive file preview.
- `public/workspace.html`, `workspace.css`, `script.js`: dashboard, setup, generation, review and export workflows.
- `public/design.css`: shared colors, typography, layout primitives and controls.
- `tests/`: offline backend/controller checks and optional local browser integration.

Java project reports and an institution-specific title page remain pending; the current project cover uses supplied metadata. Frontend reports retain the existing 10-15 distinct screenshot-result requirement. DBMS reports require declared ERD evidence. All declared images must be attached for final export.
