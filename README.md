# PractiGen

Turn a list of experiment aims into a formatted, submission-ready `.docx` practical file. Paste your aims, pick a model, and PractiGen writes the theory, code, and realistic terminal/console output for each one — then bundles everything into a single Word document.

Built for students who keep a practical lab file: OS labs, general programming labs (C, C++, Python, Java, JavaScript), and Computer Networks labs with personalised topologies, configurations and expected-output figures.

---

## Features

- **Aim → document in one flow.** Paste aims separated by `---`, generate, review, download.
- **Three lab styles.** OS mode produces Linux steps, coding mode produces source and console output, and CN mode generates eight supported network experiments without an API key.
- **CN personalisation.** Enter name, roll number and optional section. Paste your index or load sample aims. Generate consistent IP plans, device configurations, diagrams, verification panels and a sample-style Word file without a cover page. See [CN mode](docs/computer-networks.md) for supported aims and execution limits.
- **Auto-detect.** Let PractiGen infer the right mode and language per aim — a mixed batch of "explore `grep`" and "binary search in Java" is classified per experiment.
- **Language lock.** Choose a language and the output stays in it, even if an aim says otherwise. Generated code is heuristically validated and regenerated once if it comes back in the wrong language.
- **Plagiarism-safe variation.** Every generation uses a random variation seed, so the same aim produces different variable names, examples, and sample data each time.
- **Targeted refine.** Ask for a specific change ("add comments", "use different variable names") and only that changes — the rest of the experiment is preserved.
- **Classroom mode.** Generate N unique variations of the same aims (one `.docx` per student) and download them as a single ZIP.
- **Syllabus import.** Upload a course syllabus PDF and PractiGen extracts the experiment aims for you.
- **Resilient generation.** Concurrent batches, per-experiment retry, multi-provider fallback, and key rotation. A failed experiment can be retried on its own without re-running the batch.
- **Session restore.** Work survives an accidental tab refresh via `sessionStorage`.
- **Multi-provider.** Groq, Cerebras, and FreeModel (OpenAI- and Anthropic-compatible) with automatic fallback.

---

## Architecture

```
Browser (public/)                    Flask API (app.py)              LLM providers
─────────────────                    ──────────────────              ─────────────
index.html  — SPA markup             /api/parse        split aims    Groq        (OpenAI fmt)
script.js   — state + API calls      /api/generate     write lab     Cerebras    (OpenAI fmt)
style.css   — dark UI                /api/refine       targeted edit FreeModel   (OpenAI fmt)
                                     /api/detect       auto-classify FreeModel   (Anthropic fmt)
                                     /api/extract-aims syllabus PDF
                                     /api/download     build .docx
                                     /api/classroom-zip per-student ZIP
```

- **Frontend** is a dependency-free single-page app (vanilla ES6, no build step). All UI state lives in one `state` object in `script.js`.
- **Backend** is a single Flask module. LLM requests go through one shared runner (`run_completion`) that handles retries, exponential backoff on rate limits, provider/model fallback, and API-key rotation.
- **Documents** are assembled with `python-docx`. Terminal/console output is rendered to a PNG with Pillow and embedded as an image, mirroring how a real practical file looks.
- **Deployment** targets Vercel serverless (`vercel.json` routes `/api/*` → `app.py`, everything else → `public/`).

### Request flow

1. Configure mode, model/provider, API key, and formatting.
2. Paste aims separated by `---`.
3. Generate — experiments run in concurrent batches of 3, each with its own retry loop. Cards update independently as they finish.
4. Review the theory/code/output; refine any experiment in place.
5. Download a single `.docx`, or enable Classroom mode for a ZIP of per-student variations.

---

## Getting started

### Prerequisites

- Python 3.9+
- For OS/coding generation, an inference provider API key or a matching server environment variable. **CN mode does not require an API key.**

### Local development

```bash
git clone https://github.com/tanish19078/generateassignment
cd generateassignment

pip install -r requirements.txt
python app.py            # serves on http://localhost:5000
```

Or run it the way Vercel does:

```bash
vercel dev
```

### Configuration

Enter an API key in the UI, or set provider keys in a `.env` file at the project root:

| Variable | Provider |
|----------|----------|
| `GROQ_API_KEY` | Groq |
| `CEREBRAS_API_KEY` | Cerebras |
| `FREEMODEL_API_KEY` | FreeModel (shared fallback for both routes) |
| `FREEMODEL_OPENAI_API_KEY` | FreeModel (OpenAI-compatible) |
| `FREEMODEL_ANTHROPIC_API_KEY` | FreeModel (Anthropic-compatible) |

Listing the same variable multiple times in `.env` registers backup keys that are rotated through on rate-limit or auth failures. A key entered in the UI always takes priority over environment keys.

The **Custom Model ID** field overrides the selected preset — useful when your account exposes a model name that isn't in the dropdown.

---

## Testing

The suite mocks all LLM calls, so it runs offline with no API key.

```bash
pip install pytest pytest-mock
pytest -q                       # all tests
pytest tests/test_utils.py -v   # pure helpers (parsing, validation, detection)
pytest tests/test_api.py -v     # endpoints via the Flask test client
pytest tests/test_cn.py -v      # CN plans, figures and Word exports
node tests/test_cn_frontend.cjs # offline frontend controller contracts
```

- `tests/test_utils.py` — section parsing, language validation, response validation, OS step parsing, caption helpers, output truncation, auto-detect, and the prompt builder.
- `tests/test_api.py` — `/api/parse`, `/api/generate` (temperature, variation seed, system/user split, empty-response rejection, wrong-language retry, OS steps, auto-detect), `/api/refine` (existing-context, low temperature, aim preserved), `/api/detect`, `/api/download` (valid `.docx`, batch-size guard), and `/api/extract-aims`.

---

## Project structure

```
.
├── app.py              # Flask backend — all API routes and document generation
├── cn/                 # CN plans, Pillow figures and sample-style Word reports
├── public/
│   ├── index.html      # Single-page UI
│   ├── script.js       # Frontend state, API calls, rendering
│   └── style.css       # Dark theme, layout, animations
├── tests/
│   ├── test_utils.py   # Unit tests for backend helpers
│   └── test_api.py     # API integration tests (mocked LLM)
├── requirements.txt
├── vercel.json         # Serverless routing + function limits
└── README.md
```

---

## API reference

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/cn/catalog` | GET | List eight supported CN experiment aims |
| `/api/cn/plan` | POST | Generate a network plan without preview image bytes |
| `/api/parse` | POST | Split raw aim text into a list by separator |
| `/api/generate` | POST | Generate one experiment (concept, code/procedure, output, caption) |
| `/api/refine` | POST | Edit an existing experiment with a targeted change |
| `/api/detect` | POST | Infer mode + language for each aim |
| `/api/extract-aims` | POST | Extract aims from an uploaded syllabus PDF (multipart) |
| `/api/download` | POST | Build and return a `.docx` from generated experiments |
| `/api/classroom-zip` | POST | Build one `.docx` per student and return a ZIP |

Generation and download accept gzip-compressed request bodies (`X-Content-Encoding: gzip`) so large batches stay within serverless payload limits.

---

## Notes & limits

- CN figures use normal captions for topology/IP settings and **Expected output** captions for unexecuted verification panels. Actual Packet Tracer execution and screenshots require a future runner; this release does not fabricate successful runs.

- Serverless functions cap at 60s (`vercel.json`), so very large batches are guarded server-side: `/api/download` rejects more than 200 output units, and Classroom mode is capped at 12 students per run.
- Terminal output in the document is an image rendered with Pillow; it falls back to plain text if image generation fails.
- Language validation uses lightweight heuristics, not a full parser — it reliably catches gross mismatches (e.g. C returned for a Python request) but isn't a linter.
```
