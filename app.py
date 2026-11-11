import json
import re
import io
import time
import os
import gzip
import zipfile
import traceback
import uuid
import urllib.error
import urllib.request
from flask import Flask, request, jsonify, send_from_directory, send_file, has_request_context
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()
from docx import Document
from docx.shared import Pt, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from PIL import Image, ImageDraw, ImageFont
from cn import CATALOG as CN_CATALOG, build_experiment as build_cn_experiment, identify_experiment as identify_cn_experiment
from cn.rendering import with_previews as cn_with_previews
from cn.documents import build_cn_document, add_experiment as add_cn_experiment, canonical_experiment as canonical_cn_experiment
from project_file import (
    ProjectContextError,
    ProjectGenerationError,
    build_collector_prompt,
    build_generation_messages as build_project_generation_messages,
    build_project_document,
    build_project_report,
    canonicalize_project_report,
    get_report_format as get_project_report_format,
    get_section_parts as get_project_section_parts,
    parse_generated_sections as parse_project_generated_sections,
    validate_project_context,
)


app = Flask(__name__, static_folder='public', static_url_path='')
CORS(app)


MAX_COMPRESSED_DOWNLOAD_BYTES = 3_500_000
MAX_DECOMPRESSED_DOWNLOAD_BYTES = 50_000_000


class DownloadRequestError(ValueError):
    pass


def read_download_payload():
    encoding = request.headers.get('X-Content-Encoding', '').strip().lower()
    body = request.get_data(cache=False)

    if encoding == 'gzip':
        if len(body) > MAX_COMPRESSED_DOWNLOAD_BYTES:
            raise DownloadRequestError('Compressed export payload is too large for this deployment.')

        try:
            with gzip.GzipFile(fileobj=io.BytesIO(body), mode='rb') as compressed:
                body = compressed.read(MAX_DECOMPRESSED_DOWNLOAD_BYTES + 1)
        except (OSError, EOFError) as err:
            raise DownloadRequestError('Compressed export payload could not be decoded.') from err
    elif encoding not in ('', 'identity'):
        raise DownloadRequestError(f'Unsupported export payload encoding: {encoding}')

    if not body:
        return None

    if len(body) > MAX_DECOMPRESSED_DOWNLOAD_BYTES:
        raise DownloadRequestError('Export payload is too large after decompression.')

    try:
        return json.loads(body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise DownloadRequestError('Export payload is not valid JSON.') from err


DEFAULT_MODEL = 'openai/gpt-oss-120b'
DEFAULT_BASE_URL = 'https://api.groq.com/openai/v1'
# Completion defaults belong to an exact endpoint/model pair, not a provider gallery.
LLM_MODEL_DEFAULTS = {
    ('https://integrate.api.nvidia.com/v1', 'nvidia/nemotron-3.5-lightning-30b-a3b'): {
        'max_output_tokens': 8192, 'reasoning_budget': 1024,
    },
}
LLM_PROVIDERS = {
    'groq': {'label': 'Default endpoint', 'base_url': DEFAULT_BASE_URL,
             'env_vars': ['LLM_API_KEY', 'GROQ_API_KEY'], 'requires_key': True},
}


class LLMConfigurationError(ValueError):
    pass


class LLMTimeoutError(RuntimeError):
    def __init__(self, timeout_seconds, elapsed_seconds, request_id):
        self.details = {'code': 'llm_timeout', 'retryable': True, 'auto_retry': False,
                        'timeout_seconds': timeout_seconds,
                        'elapsed_seconds': round(elapsed_seconds, 2), 'request_id': request_id}
        super().__init__(f'The AI endpoint did not finish within the {timeout_seconds:g}-second '
                         'read timeout. Saved work is preserved. Check model availability before retrying.')


def llm_timeout_response(error):
    return jsonify({'error': str(error), **error.details}), 504


class LLMTruncatedError(ValueError):
    def __init__(self, metrics):
        self.details = {**metrics, 'code': 'llm_truncated', 'retryable': True, 'auto_retry': False}
        super().__init__('The model response was cut off at the output limit. Increase max output tokens '
                         'or lower the model\'s reasoning budget before retrying. Saved work is preserved.')


def llm_truncated_response(error):
    return jsonify({'error': str(error), **error.details}), 502


def parse_reasoning_budget(value):
    if value is None or value == '':
        return None
    if isinstance(value, str):
        try:
            value = int(value)
        except ValueError:
            value = None
    if type(value) is not int or not -1 <= value <= 32768:
        raise LLMConfigurationError('reasoning_budget must be an integer from -1 to 32768.')
    return value


def llm_timeout_seconds():
    # Leave headroom inside vercel.json's 300-second Fluid function limit.
    default = 180 if os.getenv('VERCEL') else 300
    try:
        value = int(os.getenv('LLM_TIMEOUT_SECONDS', str(default)))
    except ValueError as err:
        raise LLMConfigurationError('LLM_TIMEOUT_SECONDS must be an integer from 1 to 600.') from err
    if not 1 <= value <= 600:
        raise LLMConfigurationError('LLM_TIMEOUT_SECONDS must be an integer from 1 to 600.')
    return min(value, 285) if os.getenv('VERCEL') else value


def llm_max_output_tokens(provider_config=None):
    default = (provider_config or {}).get('max_output_tokens', 4096)
    try:
        value = int(os.getenv('LLM_MAX_OUTPUT_TOKENS', str(default)))
    except ValueError as err:
        raise LLMConfigurationError('LLM_MAX_OUTPUT_TOKENS must be an integer from 1 to 131072.') from err
    if not 1 <= value <= 131072:
        raise LLMConfigurationError('LLM_MAX_OUTPUT_TOKENS must be an integer from 1 to 131072.')
    return value


@app.errorhandler(LLMConfigurationError)
def llm_configuration_error(error):
    return jsonify({'error': str(error)}), 400


def get_provider_keys(provider_config, submitted_key):
    # A submitted key is scoped to this request; never silently change credentials.
    if submitted_key:
        return [submitted_key]
    return list(dict.fromkeys(os.getenv(name, '').strip()
                for name in provider_config.get('env_vars', []) if os.getenv(name, '').strip()))


def normalize_endpoint(value):
    from urllib.parse import urlsplit
    value = str(value or '').strip().rstrip('/')
    parsed = urlsplit(value)
    local = parsed.hostname in ('localhost', '127.0.0.1', '::1') and not os.getenv('VERCEL')
    if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
            or (parsed.scheme != 'https' and not (parsed.scheme == 'http' and local))):
        raise LLMConfigurationError('Use an HTTPS API base URL (for example https://host/v1). '
                                    'HTTP localhost endpoints work in local development only.')
    if value.endswith('/chat/completions'):
        value = value[:-len('/chat/completions')]
    return value


def resolve_llm_config(data):
    configured_url = normalize_endpoint(os.getenv('LLM_BASE_URL') or DEFAULT_BASE_URL)
    endpoint = normalize_endpoint(data.get('base_url') or configured_url)
    custom = endpoint != DEFAULT_BASE_URL
    # Legacy provider IDs are rejected explicitly rather than sending their key to Groq.
    if data.get('provider') not in (None, '', 'groq', 'custom'):
        raise LLMConfigurationError('Provider presets were removed. Enter its API base URL and model ID.')
    server_model = os.getenv('LLM_MODEL') if endpoint == configured_url else ''
    model = str(data.get('model') or server_model or ('' if custom else DEFAULT_MODEL)).strip()
    if not model:
        raise LLMConfigurationError('Enter a model ID for the custom endpoint.')
    model_defaults = LLM_MODEL_DEFAULTS.get((endpoint, model), {})
    config = {'label': 'Custom endpoint' if custom else 'Default endpoint', 'base_url': endpoint,
              'requires_key': not (endpoint.startswith('http://')), 'env_vars': []}
    if 'max_output_tokens' in model_defaults:
        config['max_output_tokens'] = model_defaults['max_output_tokens']
    # This is an optional endpoint-specific parameter, not part of every chat API.
    # A server budget belongs to its configured model as well as its endpoint.
    server_budget = os.getenv('LLM_REASONING_BUDGET') if endpoint == configured_url and model == server_model else None
    default_budget = server_budget if server_budget is not None else model_defaults.get('reasoning_budget')
    budget = parse_reasoning_budget(data.get('reasoning_budget', default_budget))
    if budget is not None:
        config['reasoning_budget'] = budget
    if endpoint == configured_url:
        config['env_vars'] = ['LLM_API_KEY'] + (['GROQ_API_KEY'] if not custom else [])
    keys = get_provider_keys(config, str(data.get('api_key') or '').strip())
    if config['requires_key'] and not keys:
        raise LLMConfigurationError('Enter an API key for this endpoint or configure LLM_API_KEY on the server.')
    return ('custom' if custom else 'groq'), model, config, keys


def completion_metrics(parsed):
    """Only counts and known finish reasons may reach diagnostics; never text."""
    metrics = {}
    if not isinstance(parsed, dict):
        return metrics
    usage = parsed.get('usage')
    if isinstance(usage, dict):
        for field in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
            if type(usage.get(field)) is int and usage[field] >= 0:
                metrics[field] = usage[field]
        details = usage.get('completion_tokens_details')
        if isinstance(details, dict) and type(details.get('reasoning_tokens')) is int and details['reasoning_tokens'] >= 0:
            metrics['reasoning_tokens'] = details['reasoning_tokens']
    choices = parsed.get('choices')
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        reason = choices[0].get('finish_reason')
        if isinstance(reason, str):
            metrics['finish_reason'] = reason if reason in ('stop', 'length', 'tool_calls', 'content_filter', 'function_call') else 'other'
        message = choices[0].get('message')
        if isinstance(message, dict):
            content = message.get('content')
            metrics['content_chars'] = len(content) if isinstance(content, str) else 0
            for field in ('reasoning_content', 'reasoning'):
                if isinstance(message.get(field), str):
                    metrics['reasoning_chars'] = len(message[field])
                    break
    return metrics


def parse_openai_chat_response(provider_config, body, diagnostic=None):
    try:
        parsed = json.loads(body)
        metrics = completion_metrics(parsed)
        if diagnostic is not None:
            diagnostic.update(metrics)
        choice = parsed['choices'][0]
        if choice.get('finish_reason') == 'length':
            raise LLMTruncatedError(metrics)
        content = choice['message']['content']
        if not isinstance(content, str) or not content.strip():
            raise ValueError('The model returned no text. Retry with a text generation model.')
        return content
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as err:
        raise ValueError('The endpoint did not return an OpenAI-compatible chat completion.') from err


def create_chat_completion(provider_key, api_key, model, messages, temperature=0.7, top_p=0.9,
                           max_tokens=None, reasoning_budget=None):
    config = provider_key if isinstance(provider_key, dict) else LLM_PROVIDERS[provider_key]
    # The parser consumes a complete JSON response; some gateways default to SSE.
    payload = {'model': model, 'messages': messages, 'temperature': temperature, 'top_p': top_p,
               'stream': False}
    payload['max_tokens'] = max_tokens if max_tokens is not None else llm_max_output_tokens(config)
    if reasoning_budget is not None:
        payload['reasoning_budget'] = parse_reasoning_budget(reasoning_budget)
    headers = {'Accept': 'application/json', 'Content-Type': 'application/json', 'User-Agent': 'PractiGen/6'}
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'
    req = urllib.request.Request(config['base_url'] + '/chat/completions',
                                 data=json.dumps(payload).encode('utf-8'), headers=headers, method='POST')
    class NoEndpointRedirects(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            # Credentials must never follow a redirect to a different endpoint.
            return None

    timeout = llm_timeout_seconds()
    started = time.monotonic()
    request_id = uuid.uuid4().hex
    diagnostic = {'request_id': request_id, 'model': model,
                  'request_bytes': len(req.data), 'timeout_seconds': timeout,
                  'max_tokens': payload['max_tokens']}
    if 'reasoning_budget' in payload:
        diagnostic['reasoning_budget'] = payload['reasoning_budget']
    if has_request_context():
        request_data = request.get_json(silent=True)
        if isinstance(request_data, dict):
            diagnostic['section'] = request_data.get('section')
            diagnostic['part_index'] = request_data.get('part_index')
    app.logger.info('llm_request %s', json.dumps(diagnostic))
    try:
        opener = urllib.request.build_opener(NoEndpointRedirects())
        with opener.open(req, timeout=timeout) as response:
            diagnostic['http_status'] = getattr(response, 'status', 200)
            response_headers = getattr(response, 'headers', {})
            diagnostic['provider_request_id'] = (response_headers.get('X-Respan-Log-Id')
                                                 or response_headers.get('NVCF-REQID')
                                                 or response_headers.get('X-Request-Id'))
            body = response.read().decode('utf-8')
        return parse_openai_chat_response(config, body, diagnostic)
    except LLMTruncatedError as err:
        diagnostic['error'] = 'llm_truncated'
        err.details.update(request_id=request_id, max_output_tokens=payload['max_tokens'],
                           elapsed_seconds=round(time.monotonic() - started, 2))
        if 'reasoning_budget' in payload:
            err.details['reasoning_budget'] = payload['reasoning_budget']
        raise
    except ValueError:
        diagnostic['error'] = 'llm_invalid_response'
        raise
    except urllib.error.HTTPError as err:
        diagnostic['http_status'] = err.code
        # Do not echo provider bodies, which can contain credentials or private prompts.
        raise RuntimeError(f"{config['label']} API error {err.code}. Check the endpoint, model and key; retry if rate limited.") from err
    except urllib.error.URLError as err:
        if isinstance(err.reason, TimeoutError):
            diagnostic['error'] = 'llm_timeout'
            raise LLMTimeoutError(timeout, time.monotonic() - started, request_id) from err
        diagnostic['error'] = 'connection_failed'
        raise RuntimeError(f"{config['label']} connection failed. Check the endpoint and try again.") from err
    except TimeoutError as err:
        diagnostic['error'] = 'llm_timeout'
        raise LLMTimeoutError(timeout, time.monotonic() - started, request_id) from err
    finally:
        diagnostic['elapsed_seconds'] = round(time.monotonic() - started, 2)
        log = app.logger.warning if diagnostic.get('error') or diagnostic.get('http_status', 200) >= 400 else app.logger.info
        log('llm_result %s', json.dumps(diagnostic))


def run_completion(messages, provider, model, provider_config, api_keys, temperature=0.7, top_p=0.9,
                   max_tokens=None):
    # One provider call per attempt. The UI owns visible retries.
    # Keep request-specific endpoint configuration out of shared globals.
    target = provider_config if provider == 'custom' else provider
    options = {'temperature': temperature, 'top_p': top_p}
    if max_tokens is not None:
        options['max_tokens'] = max_tokens
    if provider_config.get('reasoning_budget') is not None:
        options['reasoning_budget'] = provider_config['reasoning_budget']
    return create_chat_completion(target, (api_keys or [''])[0], model, messages, **options)


def extract_section(tag, text):
    """Extract a [TAG] section from an LLM response. Returns None when the tag is absent."""
    text = text or ''
    pattern = rf"\[{tag}\](.*?)(?=\[(?:CONCEPT|CODE|PROCEDURE|OUTPUT|CAPTION)\]|$)"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)

    if match:
        return match.group(1).strip()

    pattern_fallback = rf"(?:\*\*|##\s*)?{tag}(?:\*\*|:)?\s*\n(.*?)(?=\n(?:\*\*|##\s*)?(?:CONCEPT|CODE|PROCEDURE|OUTPUT|CAPTION)(?:\*\*|:)?\s*\n|\Z)"
    match_fallback = re.search(pattern_fallback, text, re.DOTALL | re.IGNORECASE)
    return match_fallback.group(1).strip() if match_fallback else None


LANGUAGE_MARKERS = {
    'python': {'positive': ['def ', 'import ', 'print('], 'negative': ['#include', 'int main', 'System.out', 'console.log']},
    'c': {'positive': ['#include', 'int main', 'printf'], 'negative': ['def ', 'public class', 'console.log']},
    'cpp': {'positive': ['#include', 'std::', 'int main'], 'negative': ['def ', 'public class', 'console.log']},
    'c++': {'positive': ['#include', 'std::', 'int main'], 'negative': ['def ', 'public class', 'console.log']},
    'java': {'positive': ['public class', 'System.out', 'void main'], 'negative': ['#include', 'def ', 'print(']},
    'javascript': {'positive': ['console.log', 'function ', 'const ', 'let '], 'negative': ['#include', 'System.out', 'def ', 'printf']},
}


def validate_code_language(code, target_language):
    """Heuristic check that `code` appears to be written in `target_language`.

    Unknown languages skip validation (return True). For known languages, the code
    must contain at least one positive marker and no negative markers.
    """
    if not code:
        return False

    lang_key = (target_language or '').lower().strip()
    markers = LANGUAGE_MARKERS.get(lang_key)
    if not markers:
        return True  # Unknown language — cannot validate, accept.

    has_positive = any(m in code for m in markers['positive'])
    has_negative = any(m in code for m in markers['negative'])
    return has_positive and not has_negative


def validate_generation_result(mode, code, concept):
    """Raise ValueError when the parsed result is clearly empty/malformed.

    A malformed LLM response must NOT be silently accepted as a success — it
    should bubble up so the caller can retry instead of saving placeholder text.
    """
    if mode != 'os':
        if not code or len(code.strip()) < 20:
            raise ValueError("LLM returned empty or too-short code. Retry needed.")
    if not concept or not concept.strip():
        raise ValueError("LLM returned no concept/theory. Retry needed.")


# ==================== OPTIONAL JEV QUALITY GATE ====================
# Advisory only. This gate annotates a finished generation with typed review
# signals from TypeSafe Jev (a structured decision model). It NEVER blocks a
# response, edits generated content, or triggers a regeneration — and every
# failure path degrades to a plain 'unavailable' verdict so a Jev outage can
# never take down /api/generate.
JEV_ENDPOINT = 'https://openrouter.ai/api/alpha/decisions'
JEV_MODEL = 'typesafe/jev-1.13'
JEV_TIMEOUT_SECONDS = 8  # leave headroom for the main generation within the function budget

JEV_QUESTIONS = {
    'decision': {
        'type': 'choice',
        'instructions': 'Assess readiness for student review using only the supplied state.',
        'criteria': {
            'readyForReview': 'Required sections and requested-language checks are present.',
            'needsRegeneration': 'A required section or requested-language check is missing.',
            'needsStudentClarification': 'The aim is too underspecified for a useful practical.',
        },
    },
    'primaryConcern': {
        'type': 'choice',
        'instructions': 'Choose the main quality concern visible from the state.',
        'criteria': {
            'none': 'No concern is visible from the supplied state.',
            'missingSection': 'Theory, code, or output is missing.',
            'wrongLanguage': 'The code failed the requested-language check.',
            'thinContent': 'A required section is present but suspiciously short.',
            'underspecifiedAim': 'The aim lacks essential implementation detail.',
        },
    },
    'needsHumanReview': {
        'type': 'noul',
        'instructions': 'How likely is it that a human should inspect this before export?',
        'criteria': {
            'true': 'A material concern or ambiguity is present.',
            'false': 'The supplied checks show no material concern.',
        },
    },
    'submissionReadiness': {
        'type': 'score',
        'instructions': 'Rate how close this experiment is to being submission-ready.',
        'criteria': [
            '1 - Major problems; a regeneration is probably needed.',
            '3 - Usable, but a student should read it before submitting.',
            '5 - Complete and consistent; no visible concern.',
        ],
    },
}

# Short UI labels per decision. Wording stays advisory — nothing here is an order.
JEV_DECISION_LABELS = {
    'readyForReview': 'Looks complete',
    'needsRegeneration': 'Worth regenerating',
    'needsStudentClarification': 'Aim is vague',
}


def jev_is_configured():
    """True when a server-side OpenRouter key is available for the gate."""
    return bool(os.getenv('OPENROUTER_API_KEY', '').strip())


def build_jev_state(aim, mode, target_language, result):
    """Build the factual state Jev evaluates. Only cheap local checks — no prose."""
    code = result.get('code') or ''
    concept = result.get('concept') or ''
    output = result.get('output') or ''
    steps = result.get('steps') or []

    return {
        'aim': aim,
        'mode': mode,
        'targetLanguage': target_language or None,
        'checks': {
            'hasConcept': bool(concept.strip()),
            'hasCode': bool(code.strip()),
            'hasOutput': bool(output.strip()),
            'stepCount': len(steps),
            'languageValidationPassed': (
                True if not target_language else validate_code_language(code, target_language)
            ),
            'conceptCharacters': len(concept),
            'codeCharacters': len(code),
            'outputCharacters': len(output),
        },
    }


def _normalize_score(answer):
    """Map a Jev score answer onto 0..1 using its own legend size."""
    raw = answer.get('score')
    if not isinstance(raw, (int, float)):
        return None
    steps = len(answer.get('legend') or {}) or len(answer.get('probabilities') or {})
    if steps < 2:
        return None
    return max(0.0, min(1.0, raw / (steps - 1)))


def summarize_jev_answers(answers):
    """Flatten Jev's typed answers into the compact advisory shape the UI renders.

    Defensive by construction: anything missing or of an unexpected type is
    dropped rather than raised on, because this runs on a successful generation.
    """
    answers = answers if isinstance(answers, dict) else {}

    def block(key):
        value = answers.get(key)
        return value if isinstance(value, dict) else {}

    decision_block = block('decision')
    concern_block = block('primaryConcern')
    review_block = block('needsHumanReview')
    readiness_block = block('submissionReadiness')

    decision = decision_block.get('choice')
    review_probability = review_block.get('noul')
    if not isinstance(review_probability, (int, float)):
        review_probability = None

    summary = {
        'decision': decision,
        'decisionLabel': JEV_DECISION_LABELS.get(decision, 'Review suggested'),
        'decisionConfidence': decision_block.get('confidence'),
        'primaryConcern': concern_block.get('choice'),
        'reviewProbability': review_probability,
        'needsHumanReview': bool(review_probability is not None and review_probability >= 0.5),
        'readiness': _normalize_score(readiness_block),
    }

    # A clean bill of health only when Jev is positive on both axes.
    summary['clean'] = bool(decision == 'readyForReview' and not summary['needsHumanReview'])
    return summary


def call_jev_decisions(api_key, state, questions, timeout=JEV_TIMEOUT_SECONDS):
    """POST one Decisions request and return the decoded body. May raise."""
    payload = {'model': JEV_MODEL, 'state': state, 'questions': questions}
    request_obj = urllib.request.Request(
        JEV_ENDPOINT,
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
            'X-Title': 'PractiGen',
        },
        method='POST',
    )
    with urllib.request.urlopen(request_obj, timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8'))


def run_jev_quality_gate(aim, mode, target_language, result):
    """Return advisory typed quality signals. Never raises, never mutates `result`.

    Returns a dict that always carries 'enabled' and 'status'. On any failure the
    status is 'unavailable' and generation continues untouched.
    """
    api_key = os.getenv('OPENROUTER_API_KEY', '').strip()
    if not api_key:
        return {
            'enabled': False,
            'status': 'unavailable',
            'reason': 'OPENROUTER_API_KEY is not configured on the server.',
        }

    try:
        state = build_jev_state(aim, mode, target_language, result)
        body = call_jev_decisions(api_key, state, JEV_QUESTIONS)
        answers = body.get('answers') if isinstance(body, dict) else None
        summary = summarize_jev_answers(answers)
        return {'enabled': True, 'status': 'complete', 'summary': summary, 'answers': answers or {}}
    except Exception as err:  # noqa: BLE001 — advisory gate must never break generation
        # Deliberately broad: a bad payload, DNS failure, 5xx, timeout or an
        # unexpected response shape must all read as "no advice available".
        print(f"Jev quality gate unavailable: {type(err).__name__}: {err}")
        return {'enabled': True, 'status': 'unavailable', 'reason': f'{type(err).__name__}: {err}'}


# ==================== AUTO-DETECT (Phase 3) ====================
_OS_CUES = re.compile(
    r'\b(ls|cat|grep|chmod|chown|mkdir|touch|awk|sed|pwd|ps|kill|df|du|'
    r'shell script|bash|linux|unix|terminal|command[s]?|file permission|'
    r'system call|fork|exec|directory|kernel)\b',
    re.IGNORECASE,
)

_LANG_CUES = [
    ('cpp', re.compile(r'\bc\+\+\b', re.IGNORECASE)),
    ('python', re.compile(r'\bpython\b', re.IGNORECASE)),
    ('java', re.compile(r'\bjava\b(?!script)', re.IGNORECASE)),
    ('javascript', re.compile(r'\b(javascript|node\.?js|js)\b', re.IGNORECASE)),
    ('c', re.compile(r'\bc\s+(program|language|code)\b', re.IGNORECASE)),
]


def detect_mode_and_language(aim):
    """Heuristically infer {'mode', 'code_language'} from an aim string.

    Cheap regex-only classification. mode is 'os' or 'general'; code_language is a
    string or None when no clear language cue is present.
    """
    aim_text = aim or ''

    detected_lang = None
    for lang, pattern in _LANG_CUES:
        if pattern.search(aim_text):
            detected_lang = lang
            break

    if not detected_lang and identify_cn_experiment(aim_text):
        return {'mode': 'cn', 'code_language': None}

    is_shell = bool(re.search(r'\bshell\s+script\b', aim_text, re.IGNORECASE))
    has_os_cue = bool(_OS_CUES.search(aim_text))

    # A program in a specific high-level language => general coding mode.
    if detected_lang in ('python', 'java', 'javascript', 'cpp') and not is_shell:
        return {'mode': 'general', 'code_language': detected_lang}

    if is_shell or (has_os_cue and detected_lang != 'c'):
        return {'mode': 'os', 'code_language': None}

    if detected_lang:
        return {'mode': 'general', 'code_language': detected_lang}

    return {'mode': 'general', 'code_language': None}


# ==================== PROMPT / MESSAGE BUILDER ====================
def build_messages(mode, aim, target_language='', terminal_user='student', terminal_host='kali', variation_seed=''):
    """Construct a [system, user] message pair for generation.

    Hard rules (output format, language lock) live in the system message for
    maximum LLM compliance; the aim + variation seed live in the user message.
    """
    variation_note = ''
    if variation_seed:
        variation_note = (
            "\n\nIMPORTANT: Use creative and unique variable names, examples, and data values. "
            "Do not produce generic textbook examples. "
            f"Variation reference: {variation_seed}"
        )

    if mode == 'os':
        system = (
            "You are a professional Linux systems instructor preparing a practical lab file for a "
            "university Operating Systems course. You always respond using the exact section tags "
            "requested, with realistic terminal output and no placeholders."
        )
        user = f"""Your instructor has assigned this aim:

"{aim}"

Work through the aim methodically, step by step, like an experienced professional would demonstrate in a real lab. Before each command or code block, write a clear explanation (1-2 lines) of what it does and why. Then write the command. Then show what the terminal actually displayed.

If the aim asks you to explore a command with its options, use it normally first, then show a few useful options — just like you'd actually try them in a real lab session. Don't robotically list every flag. Use your judgement.

If the aim asks for a C program (system calls, algorithms, etc.), write a clean, complete program. Compile and run it.

If the aim has multiple parts or multiple commands, work through each one properly.

Keep it natural. No filler. No padding. Just a real, useful practical.

Respond in this exact format:

[CONCEPT]
4-5 lines explaining the core OS concepts behind this practical in academic language. Keep it concise and focused. If there is a specific term or technique that needs extra explanation (e.g., what a system call is, what a process control block does), add ONE short follow-up paragraph for it — but only if truly needed. Do not over-explain obvious things.

[PROCEDURE]
Plain text, no markdown fences. Write it as numbered steps. Each step MUST include its own output immediately after the command.

Format each step EXACTLY like this:

Step 1: <what you're doing and why>
$ <command or code>
Output:
{terminal_user}@{terminal_host}:~$ <the command typed>
<realistic terminal output for this specific command>

Step 2: <what you're doing and why>
$ <command or code>
Output:
{terminal_user}@{terminal_host}:~$ <the command typed>
<realistic terminal output for this specific command>

...and so on. Only what the aim needs.

For C programs, write the full source code in one step (no $ prefix for the code itself), then compile and run as separate steps with $ prefix and their own outputs.

CRITICAL — Terminal output rules:
- The Output section for each step MUST start with the prompt and the command being typed on the FIRST line, then show the result below it. Do NOT add an extra prompt line at the end.
- Use this prompt: {terminal_user}@{terminal_host}:~$
- For root: root@{terminal_host}:~#
- Real permissions, real file sizes, real dates (Feb-Mar 2026), real PIDs, real kernel (6.1.0-18-amd64)
- No placeholders, no "...", no skipped output
- Each step's output must be realistic and complete

[CAPTION]
3-5 word caption for the experiment.{variation_note}"""
        return [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user},
        ]

    if mode in ('general', 'language') and target_language:
        system = (
            f"You are a {target_language} programming expert. You MUST write ALL code exclusively in "
            f"{target_language}. Never use any other programming language under any circumstances, "
            f"regardless of what the aim text says. You always respond using the exact section tags requested."
        )
        user = f"""MANDATORY: Write ALL code in {target_language} ONLY.

Experiment aim:

"{aim}"

IMPORTANT GUIDELINES:
- Write the complete solution only in {target_language}.
- If the aim mentions any other programming language, implement the same practical in {target_language} instead.
- Do not include code, syntax, headers, libraries, build tools, or examples from any other language.
- Keep comments minimal and only where genuinely needed.
- Provide a brief academic explanation of the core concepts being targeted.
- Show a realistic text output of running this {target_language} code.
- Do NOT include shell prompts like student@kali. Just show raw console execution outputs.

Respond EXACTLY in this format (use these exact tags):

[CONCEPT]
Write 3-4 lines explaining the concepts used. Academic style. Mention that the implementation uses {target_language}.

[CODE]
Write the full {target_language} source code. Plain text only, no markdown fences.

[OUTPUT]
Show REALISTIC output from running the code.
Make it look like a real terminal or console output. Do not show generic placeholder output.

[CAPTION]
Write a very short (3-5 words) descriptive caption for the output.

FINAL REMINDER: The [CODE] section MUST contain {target_language} code. Do NOT write code in any other language regardless of what the aim says.{variation_note}"""
        return [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user},
        ]

    # Default generic coding mode (no language lock)
    system = (
        "You are an expert programming lab assistant. You always respond using the exact section "
        "tags requested, with realistic console output and no placeholders."
    )
    user = f"""Experiment aim:

"{aim}"

IMPORTANT GUIDELINES:
- Write clean and well-structured code.
- Provide a brief academic explanation of the core concepts being targeted.
- Show a realistic text output of running this code.
- If it's a programming language, provide the full source code.
- Do NOT include shell prompts like student@kali. Just show raw console execution outputs.

Respond EXACTLY in this format (use these exact tags):

[CONCEPT]
Write 3-4 lines explaining the concepts used. Academic style.

[CODE]
Write the code. Plain text only, no markdown fences.
Keep comments minimal — only where genuinely needed.

[OUTPUT]
Show REALISTIC output from running the code.
Make it look like a real terminal or console output. Do not show generic placeholder output.

[CAPTION]
Write a very short (3-5 words) descriptive caption for the output.{variation_note}"""
    return [
        {'role': 'system', 'content': system},
        {'role': 'user', 'content': user},
    ]


def parse_generation_text(text, mode, target_language=''):
    """Parse a raw LLM response into a result dict with concept/code/output/steps/caption."""
    concept = extract_section("CONCEPT", text)
    caption = extract_section("CAPTION", text)
    if not concept:
        concept = "No concept description provided by API."
    if not caption:
        caption = "Experiment Output"

    result = {
        'concept': concept,
        'caption': caption,
        'mode': mode,
    }
    if mode in ('general', 'language'):
        result['code_language'] = target_language

    if mode == 'os':
        procedure = extract_section("PROCEDURE", text) or "No procedure provided."
        procedure = re.sub(r'```[a-zA-Z]*', '', procedure).replace('```', '').strip()
        steps = parse_steps(procedure)
        result['steps'] = steps
        result['code'] = '\n\n'.join([f"Step {s['num']}: {s['explanation']}\n{s['command']}" for s in steps])
        result['output'] = '\n\n'.join([s['output'] for s in steps if s['output']])
    else:
        code = extract_section("CODE", text) or ""
        output_part = extract_section("OUTPUT", text) or "No output provided."

        code = re.sub(r'```[a-zA-Z]*', '', code).replace('```', '').strip()
        output_part = re.sub(r'```', '', output_part).strip()

        result['code'] = code
        result['output'] = output_part

    return result


@app.route('/')
def serve_index():
    return send_from_directory('public', 'index.html')

@app.route('/<path:path>')
def serve_static(path):
    return send_from_directory('public', path)


@app.route('/api/cn/catalog', methods=['GET'])
def api_cn_catalog():
    return jsonify({'experiments': [{'id': key, 'title': title, 'aim': aim} for key, title, aim in CN_CATALOG],
                    'execution': 'expected_output', 'runner_connected': False})


@app.route('/api/cn/plan', methods=['POST'])
def api_cn_plan():
    try:
        data = request.get_json() or {}
        result = build_cn_experiment(data.get('aim', ''), data.get('student'))
        return jsonify(result)
    except ValueError as err:
        return jsonify({'error': str(err)}), 400


# ==================== API: Project File ====================
@app.route('/api/project/prompt/<submode>', methods=['GET'])
def api_project_prompt(submode):
    try:
        submode = str(submode or '').strip().lower()
        return jsonify({
            'schema_version': 1,
            'submode': submode,
            'prompt': build_collector_prompt(submode),
            'report_format': get_project_report_format(submode),
        })
    except (ProjectContextError, LLMConfigurationError) as err:
        return jsonify({'error': str(err)}), 400


@app.route('/api/project/validate', methods=['POST'])
def api_project_validate():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'A JSON request object is required.'}), 400
    submode = str(data.get('submode') or '').strip().lower()
    result = validate_project_context(data.get('context'), submode)
    return jsonify(result)


@app.route('/api/project/section', methods=['POST'])
@app.route('/api/project/generate', methods=['POST'])
def api_project_generate():
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ProjectContextError('A JSON request object is required.')
        submode = str(data.get('submode') or '').strip().lower()
        validation = validate_project_context(data.get('context'), submode)
        if not validation['valid']:
            issues = validation['errors'] + validation['missing_fields']
            return jsonify({
                'error': 'The project context is incomplete.',
                **validation,
            }), 400

        context = validation['normalized']
        part_index = data.get('part_index')
        previous_parts = data.get('previous_parts', [])
        part_count = None
        if part_index is not None and not request.path.endswith('/section'):
            raise ProjectContextError('Part requests must use /api/project/section.')
        missing = validation['generation_needed']
        generated = {}
        completed = data.get('completed_sections', {})
        if not isinstance(completed, dict):
            raise ProjectContextError('completed_sections must be an object.')
        for field in missing:
            if field in completed:
                generated.update(parse_project_generated_sections(
                    json.dumps({'report_sections': {field: completed[field]}}), [field]))

        if request.path.endswith('/section'):
            field = data.get('section')
            if not isinstance(field, str) or field not in context['report_sections']:
                raise ProjectContextError('Choose a valid report section to generate.')
            if context['report_sections'][field]:
                return jsonify({'section': field, 'text': context['report_sections'][field]})
            if part_index is not None:
                part_count = len(get_project_section_parts(field))
                if type(part_index) is not int or not 0 <= part_index < part_count:
                    raise ProjectContextError('Choose a valid part_index for this section.')
                if not isinstance(previous_parts, list) or len(previous_parts) != part_index:
                    raise ProjectContextError('previous_parts must contain each completed part in order.')
                previous_parts = [parse_project_generated_sections(
                    json.dumps({'report_sections': {field: value}}), [field])[field]
                    for value in previous_parts]
                if sum(map(len, previous_parts)) > 45_000:
                    raise ProjectContextError('Saved section parts are too large.')
            requested = [field]
        else:
            requested = [field for field in missing if field not in generated]
            if len(requested) > 1:
                return jsonify({'error': 'Generate missing sections individually, then assemble the report.',
                                'generation_needed': requested}), 400
        if requested:
            provider, model, provider_config, keys = resolve_llm_config(data)
            messages = build_project_generation_messages(context, requested,
                                                         part_index=part_index, previous_parts=previous_parts)
            text = run_completion(messages, provider, model, provider_config, keys,
                                  temperature=0.25, top_p=0.9,
                                  max_tokens=llm_max_output_tokens(provider_config))
            generated.update(parse_project_generated_sections(text, requested))
        if request.path.endswith('/section'):
            if part_count is not None:
                return jsonify({'section': field, 'text': generated[field],
                                'part_index': part_index, 'part_count': part_count})
            return jsonify({'section': field, 'text': generated[field]})

        report = build_project_report(context, generated, missing)
        return jsonify({
            'report': report,
            'warnings': validation['warnings'],
            'generated_sections': missing,
        })
    except (ProjectContextError, LLMConfigurationError) as err:
        return jsonify({'error': str(err)}), 400
    except ProjectGenerationError as err:
        return jsonify({'error': str(err)}), 502
    except LLMTruncatedError as err:
        return llm_truncated_response(err)
    except LLMTimeoutError as err:
        return llm_timeout_response(err)
    except Exception as err:
        error_msg = str(err)
        status_code = 500
        if '401' in error_msg or 'authentication' in error_msg.lower():
            status_code = 401
        elif '403' in error_msg:
            status_code = 403
        elif '429' in error_msg or 'rate limit' in error_msg.lower():
            status_code = 429
        return jsonify({'error': error_msg}), status_code


def safe_project_filename(value, fallback):
    name = os.path.basename(str(value or '')).strip()
    if not name:
        name = fallback
    if name.lower().endswith('.docx'):
        name = name[:-5]
    name = re.sub(r'[^A-Za-z0-9 _.-]+', '', name).strip(' ._-')
    name = re.sub(r'[\s.]+', '_', name)[:100] or 'Project_Report'
    return name + '.docx'


@app.route('/api/project/download', methods=['POST'])
def api_project_download():
    try:
        data = read_download_payload()
        if not isinstance(data, dict):
            raise ProjectContextError('No project report data was received for export.')
        submode = str(data.get('submode') or '').strip().lower()
        report = canonicalize_project_report(data.get('report'), submode)
        output_filename = safe_project_filename(
            data.get('output_filename'),
            f"{report['title']}_{submode}_project_report.docx",
        )
        file_buf = build_project_document(report, data.get('images'))
        return send_file(
            file_buf,
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            as_attachment=True,
            download_name=output_filename,
        )
    except (DownloadRequestError, ProjectContextError, ValueError) as err:
        return jsonify({'error': str(err)}), 400
    except Exception as err:
        print(f"CRITICAL PROJECT EXPORT ERROR: {traceback.format_exc()}")
        return jsonify({'error': f'Project export failed: {str(err)}'}), 500


# ==================== API: Parse Aims ====================
@app.route('/api/parse', methods=['POST'])
def api_parse():
    try:
        data = request.get_json()
        text = data.get('text', '')
        separator = data.get('separator', '---')

        pattern = r'\n\s*' + re.escape(separator) + r'+\s*\n'
        aim_blocks = re.split(pattern, text)
        aims = [b.strip() for b in aim_blocks if b.strip()]

        return jsonify({'aims': aims})
    except LLMConfigurationError:
        raise
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==================== STEP PARSING (OS MODE) ====================
def parse_steps(procedure_text):
    """Parse the procedure text into individual steps with explanation, command, and output.
    Handles both 'Step N:' format and 'N.' numbered list format from the LLM."""
    steps = []

    has_step_format = bool(re.search(r'Step\s+\d+\s*[:.]\s', procedure_text, re.IGNORECASE))

    if has_step_format:
        split_pat = r'(?=Step\s+\d+\s*[:.]\s)'
        header_pat = r'Step\s+(\d+)\s*[:.]\s*(.*?)(?:\n)'
    else:
        split_pat = r'(?:^|\n)(?=\d+\.\s)'
        header_pat = r'(\d+)\.\s*(.*?)(?:\n)'

    step_blocks = re.split(split_pat, procedure_text, flags=re.IGNORECASE)
    step_blocks = [b.strip() for b in step_blocks if b.strip()]

    print(f"DEBUG parse_steps: {len(step_blocks)} blocks (format: {'Step N' if has_step_format else 'N.'})")

    for idx, block in enumerate(step_blocks):
        header_match = re.match(header_pat, block, re.IGNORECASE)
        if not header_match:
            print(f"DEBUG: Block {idx} no header: {block[:80]}...")
            continue

        step_num = int(header_match.group(1))
        explanation = header_match.group(2).strip()
        explanation = re.sub(r'\*\*([^*]*)\*\*', r'\1', explanation)
        explanation = explanation.rstrip(':').strip()

        rest = block[header_match.end():]

        output_part = ''
        command_part = rest.strip()

        for pattern in [
            r'\n\s*\*{0,2}Output\*{0,2}\s*:\s*\n',
            r'\n\s*\*{0,2}Output\*{0,2}\s*:\s*',
            r'\*{0,2}Output\*{0,2}\s*:\s*\n',
        ]:
            parts = re.split(pattern, rest, maxsplit=1, flags=re.IGNORECASE)
            if len(parts) == 2:
                command_part = parts[0].strip()
                output_part = parts[1].strip()
                break
        else:
            print(f"DEBUG: Step {step_num} no Output: delimiter")

        command_part = re.sub(r'^\$\s*', '', command_part, flags=re.MULTILINE)
        command_part = re.sub(r'\*\*([^*]*)\*\*', r'\1', command_part)

        print(f"DEBUG: Step {step_num} cmd:{len(command_part)} out:{len(output_part)}")

        steps.append({
            'num': step_num,
            'explanation': explanation,
            'command': command_part,
            'output': output_part,
        })

    if not steps:
        print(f"DEBUG: No steps! Text: {procedure_text[:200]}...")
        steps = [{'num': 1, 'explanation': 'Execute the procedure', 'command': procedure_text, 'output': ''}]

    return steps


# ==================== API: Generate Content ====================
@app.route('/api/generate', methods=['POST'])
def api_generate():
    try:
        data = request.get_json()
        aim = data.get('aim', '')
        mode = data.get('mode', 'general')
        if mode == 'coding':  # frontend label → backend language-locked mode
            mode = 'general'
        target_language = data.get('code_language', '').strip()
        variation_seed = str(data.get('variation_seed', '') or '').strip()
        auto_detect = bool(data.get('auto_detect', False)) or mode == 'auto'

        # Phase 3 — per-experiment auto-detect of mode + language from the aim text.
        if auto_detect:
            detected = detect_mode_and_language(aim)
            mode = detected['mode']
            if detected['code_language']:
                target_language = detected['code_language']

        if mode == 'cn':
            result = build_cn_experiment(aim, data.get('student'))
            return jsonify(cn_with_previews(result))

        if mode in ('general', 'language') and not target_language:
            raise ValueError("Code language is required for General Coding mode.")

        terminal_user = data.get('terminal_user', 'student')
        if not terminal_user.strip():
            terminal_user = 'student'

        terminal_host = data.get('terminal_host', 'kali')
        if not terminal_host.strip():
            terminal_host = 'kali'

        provider, model, selected_provider_config, selected_api_keys = resolve_llm_config(data)

        messages = build_messages(
            mode, aim, target_language, terminal_user, terminal_host, variation_seed
        )

        text = run_completion(
            messages, provider, model, selected_provider_config, selected_api_keys,
            temperature=0.7, top_p=0.9,
        )

        result = parse_generation_text(text, mode, target_language)
        validate_generation_result(mode, result.get('code'), result.get('concept'))

        # A correction must be a new frontend attempt, not a second AI call
        # inside the same serverless request.
        if mode in ('general', 'language') and target_language:
            if not validate_code_language(result.get('code', ''), target_language):
                return jsonify({'error': f'The model returned the wrong code language (expected {target_language}). Retry this experiment.',
                                'code': 'wrong_code_language', 'retryable': True}), 502

        # Advisory quality gate — on by default, opt out with jev_quality_gate:false.
        # `result` is already parsed and validated here, so the gate can only add
        # commentary. The extra try/except is belt-and-braces: even a bug inside
        # the gate itself must not fail an otherwise good generation.
        if data.get('jev_quality_gate', True):
            try:
                result['quality_gate'] = run_jev_quality_gate(aim, mode, target_language, result)
            except Exception as gate_error:  # noqa: BLE001 — advisory only
                print(f"Jev quality gate skipped: {type(gate_error).__name__}: {gate_error}")

        return jsonify(result)
    except LLMTruncatedError as err:
        return llm_truncated_response(err)
    except LLMTimeoutError as err:
        return llm_timeout_response(err)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except LLMConfigurationError:
        raise
    except Exception as e:
        error_msg = str(e)
        status_code = 500
        if "401" in error_msg or "Invalid API Key" in error_msg or "Authentication" in error_msg:
            status_code = 401
        elif "403" in error_msg:
            status_code = 403
        elif "429" in error_msg or "Rate limit" in error_msg:
            status_code = 429
        return jsonify({'error': error_msg}), status_code


# ==================== API: Refine Experiment (Bug 3) ====================
def build_refine_messages(mode, aim, change_request, existing_concept, existing_code,
                          existing_output, target_language='', terminal_user='student',
                          terminal_host='kali'):
    """Build a refine-specific [system, user] message pair that includes the full existing
    experiment as context so the LLM edits it instead of regenerating from scratch."""
    if mode == 'os':
        fmt = ("[CONCEPT] ... [PROCEDURE] (numbered Step N: with $ commands and Output: blocks) "
               "[CAPTION]")
        lang_line = ''
    elif mode in ('general', 'language') and target_language:
        fmt = "[CONCEPT] [CODE] [OUTPUT] [CAPTION]"
        lang_line = f"\nAll code MUST remain in {target_language}."
    else:
        fmt = "[CONCEPT] [CODE] [OUTPUT] [CAPTION]"
        lang_line = ''

    system = (
        "You are a precise lab-file editor. You make the SMALLEST change that satisfies the "
        "user's request and keep everything else identical. You always respond using the exact "
        "section tags requested."
    )
    user = f"""Here is an existing experiment that was already generated:

[AIM] {aim}
[CONCEPT] {existing_concept}
[CODE] {existing_code}
[OUTPUT] {existing_output}

The user wants this SPECIFIC modification: "{change_request}"

RULES:
- Modify ONLY what the user requested.
- Keep everything else as close to the original as possible.{lang_line}
- For OS terminal prompts use {terminal_user}@{terminal_host}:~$.
- Return the FULL updated experiment in this exact tag format: {fmt}
"""
    return [
        {'role': 'system', 'content': system},
        {'role': 'user', 'content': user},
    ]


@app.route('/api/refine', methods=['POST'])
def api_refine():
    try:
        data = request.get_json()
        aim = data.get('aim', '')
        change_request = data.get('change_request', '')
        if not change_request.strip():
            raise ValueError("A change request is required to refine an experiment.")

        if data.get('mode') == 'cn':
            return jsonify({'error': 'CN recipes use a consistent network plan. Change the aim or student details and regenerate.'}), 400

        existing_code = data.get('existing_code', '')
        existing_concept = data.get('existing_concept', '')
        existing_output = data.get('existing_output', '')

        mode = data.get('mode', 'general')
        if mode == 'coding':
            mode = 'general'
        target_language = data.get('code_language', '').strip()

        terminal_user = (data.get('terminal_user') or 'student').strip() or 'student'
        terminal_host = (data.get('terminal_host') or 'kali').strip() or 'kali'

        provider, model, selected_provider_config, selected_api_keys = resolve_llm_config(data)

        messages = build_refine_messages(
            mode, aim, change_request, existing_concept, existing_code, existing_output,
            target_language, terminal_user, terminal_host,
        )

        # Lower temperature keeps the edit minimal and targeted.
        text = run_completion(
            messages, provider, model, selected_provider_config, selected_api_keys,
            temperature=0.3, top_p=0.9,
        )

        result = parse_generation_text(text, mode, target_language)
        validate_generation_result(mode, result.get('code'), result.get('concept'))
        result['aim'] = aim  # preserve the original aim, not the change request
        return jsonify(result)
    except LLMTruncatedError as err:
        return llm_truncated_response(err)
    except LLMTimeoutError as err:
        return llm_timeout_response(err)
    except LLMConfigurationError:
        raise
    except Exception as e:
        error_msg = str(e)
        status_code = 500
        if "401" in error_msg or "Authentication" in error_msg:
            status_code = 401
        elif "429" in error_msg or "Rate limit" in error_msg:
            status_code = 429
        return jsonify({'error': error_msg}), status_code


# ==================== API: Auto-Detect Mode + Language (Phase 3) ====================
@app.route('/api/detect', methods=['POST'])
def api_detect():
    try:
        data = request.get_json() or {}
        aims = data.get('aims')
        if aims is None:
            text = data.get('text', '')
            separator = data.get('separator', '---')
            pattern = r'\n\s*' + re.escape(separator) + r'+\s*\n'
            aims = [b.strip() for b in re.split(pattern, text) if b.strip()]

        detections = [
            {'aim': aim, **detect_mode_and_language(aim)}
            for aim in aims
        ]
        return jsonify({'detections': detections})
    except LLMConfigurationError:
        raise
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==================== API: Extract Aims from Syllabus PDF (Phase 3) ====================
@app.route('/api/extract-aims', methods=['POST'])
def api_extract_aims():
    try:
        upload = request.files.get('file')
        if upload is None or not upload.filename:
            return jsonify({'error': 'No PDF file uploaded.'}), 400
        if not upload.filename.lower().endswith('.pdf'):
            return jsonify({'error': 'Only .pdf files are supported.'}), 400

        try:
            from pypdf import PdfReader
        except ImportError:
            return jsonify({'error': 'PDF support is unavailable on this server (pypdf not installed).'}), 400

        try:
            reader = PdfReader(upload.stream)
            raw_text = '\n'.join((page.extract_text() or '') for page in reader.pages)
        except Exception as pdf_err:
            return jsonify({'error': f'Could not read PDF: {pdf_err}'}), 400

        raw_text = raw_text.strip()
        if not raw_text:
            return jsonify({'error': 'No extractable text found in the PDF (it may be scanned images).'}), 400

        # Truncate to keep the LLM prompt within sane limits.
        raw_text = raw_text[:12000]

        provider, model, selected_provider_config, selected_api_keys = resolve_llm_config(request.form)

        messages = [
            {'role': 'system', 'content': (
                "You extract a clean list of practical/experiment aims from messy university "
                "syllabus text. Output ONLY the aims, one per line, each starting with no numbering "
                "or bullet, separated by a line containing exactly ---. No commentary."
            )},
            {'role': 'user', 'content': (
                "Extract every distinct experiment/practical aim from this syllabus text. "
                "Return them separated by lines containing only ---.\n\n" + raw_text
            )},
        ]

        text = run_completion(
            messages, provider, model, selected_provider_config, selected_api_keys,
            temperature=0.2, top_p=0.9,
        )

        aims = [a.strip(' \t\r\n-•.').strip() for a in re.split(r'\n\s*-{3,}\s*\n', text)]
        aims = [a for a in aims if a and len(a) > 4]
        if not aims:
            aims = [line.strip(' \t\r\n-•.') for line in text.splitlines() if len(line.strip()) > 8]

        return jsonify({'aims': aims, 'text': '\n---\n'.join(aims)})
    except LLMTruncatedError as err:
        return llm_truncated_response(err)
    except LLMTimeoutError as err:
        return llm_timeout_response(err)
    except LLMConfigurationError:
        raise
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==================== API: Download .docx ====================
def set_font(paragraph, font_name='Times New Roman', size=12, bold=False):
    for run in paragraph.runs:
        run.font.name = font_name
        run._element.rPr.rFonts.set(qn('w:eastAsia'), font_name)
        run.font.size = Pt(size)
        run.bold = bold


def add_bold_para(doc, text, font_name='Times New Roman', size=12, align=None):
    p = doc.add_paragraph()
    if align:
        p.alignment = align
    run = p.add_run(text)
    run.bold = True
    set_font(p, font_name=font_name, size=size, bold=True)
    return p


def add_labeled_para(doc, label, content, font_name='Times New Roman', size=12):
    p = doc.add_paragraph()
    if any(c in content for c in ['*', '•', '·']) or '  ' in content:
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    else:
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    run_label = p.add_run(f'{label} ')
    run_label.bold = True
    run_label.font.name = font_name
    run_label.font.size = Pt(size)
    run_content = p.add_run(content)
    run_content.font.name = font_name
    run_content.font.size = Pt(size)
    return p


def add_code_para(doc, code_text, font_name='Times New Roman', size=10):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    run = p.add_run(code_text)
    run.font.name = font_name
    run.font.size = Pt(size)


COMMAND_CAPTIONS = {
    'cal': 'Calendar Output',
    'cat': 'File Content Output',
    'cc': 'Compilation Output',
    'cd': 'Directory Change Output',
    'chmod': 'Permission Change Output',
    'chown': 'Ownership Change Output',
    'clear': 'Clear Screen Output',
    'cp': 'File Copy Output',
    'date': 'Date Command Output',
    'df': 'Disk Usage Output',
    'du': 'Storage Usage Output',
    'echo': 'Echo Command Output',
    'find': 'File Search Output',
    'free': 'Memory Usage Output',
    'gcc': 'Compilation Output',
    'grep': 'Search Command Output',
    'help': 'Shell Help Output',
    'history': 'Command History Output',
    'hostname': 'Hostname Output',
    'id': 'User Identity Output',
    'java': 'Program Execution Output',
    'javac': 'Compilation Output',
    'ls': 'Directory Listing Output',
    'make': 'Build Output',
    'man': 'Manual Page Output',
    'mkdir': 'Directory Creation Output',
    'mv': 'File Move Output',
    'ps': 'Process List Output',
    'pwd': 'Working Directory Output',
    'python': 'Program Execution Output',
    'python3': 'Program Execution Output',
    'reboot': 'Reboot Command Output',
    'rm': 'File Removal Output',
    'rmdir': 'Directory Removal Output',
    'sh': 'Script Execution Output',
    'stat': 'File Status Output',
    'su': 'User Switch Output',
    'sudo': 'Privilege Command Output',
    'top': 'Process Monitor Output',
    'touch': 'File Creation Output',
    'tty': 'Terminal Device Output',
    'uname': 'System Information Output',
    'uptime': 'System Uptime Output',
    'who': 'Logged-In Users Output',
    'whoami': 'Current User Output',
}


def make_caption_text(text, fallback='Terminal Output', max_words=8, max_chars=70):
    cleaned = re.sub(r'\s+', ' ', str(text or '')).strip()
    cleaned = re.sub(r'^Step\s*\d+\s*[:.)-]\s*', '', cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip(' .:-')

    if not cleaned:
        return fallback

    if len(cleaned.split()) > max_words or len(cleaned) > max_chars:
        return fallback

    return cleaned or fallback


def make_step_caption(command, fallback='Terminal Output'):
    lines = [line.strip() for line in str(command or '').splitlines() if line.strip()]
    if not lines:
        return fallback

    first_line = re.sub(r'^[\w.-]+@[\w.-]+:.*?[#$]\s*', '', lines[0]).strip()
    first_line = re.sub(r'^\$\s*', '', first_line).strip()
    first_line = re.split(r'\s*(?:&&|\|\||;|\|)\s*', first_line, maxsplit=1)[0].strip()

    if not first_line:
        return fallback

    command_match = re.match(r'([./\w+-]+)', first_line)
    if not command_match:
        return fallback

    command_name = command_match.group(1).strip()
    base_name = command_name.replace('\\', '/').split('/')[-1].lower()

    if command_name.startswith('./'):
        return 'Program Execution Output'

    return COMMAND_CAPTIONS.get(base_name, f'Output of {base_name} Command')


def add_caption_para(doc, text, experiment_no, step_no=None, font_name='Times New Roman', size=10):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    caption_text = make_caption_text(text)
    if step_no:
        label = f'Figure {experiment_no}.{step_no} - {caption_text}'
    else:
        label = f'Figure {experiment_no} - {caption_text}'
    run = p.add_run(label)
    run.font.name = font_name
    run.font.size = Pt(size)


def get_terminal_lines(output_text, max_lines=28, max_line_chars=120):
    raw_lines = str(output_text or '').split('\n')
    lines = []
    shortened = len(raw_lines) > max_lines

    for raw_line in raw_lines[:max_lines]:
        line = raw_line.replace('\r', '')
        if len(line) > max_line_chars:
            line = line[:max_line_chars - 3] + '...'
            shortened = True
        lines.append(line)

    if shortened:
        lines.append(f'[Output shortened for export. Showing first {max_lines} lines.]')

    return lines or ['']


def create_terminal_image(output_text, img_width=600):
    # Image settings
    width = img_width
    font_size = 16
    padding = 20
    
    # Check for font
    try:
        font = ImageFont.truetype("consola.ttf", font_size) # Windows console font
    except IOError:
        try:
            font = ImageFont.truetype("cour.ttf", font_size) # Courier
        except IOError:
            font = ImageFont.load_default()

    # Calculate height
    lines = get_terminal_lines(output_text)
    line_height = font_size + 9 
    height = (len(lines) * line_height) + (2 * padding)
    
    # Create Image
    img = Image.new('RGB', (width, height), color=(0, 0, 0)) # Pure black background
    d = ImageDraw.Draw(img)
    
    # Draw text
    y = padding
    for line in lines:
        try:
            text_line = line
            # Normal font weight, matching Picture1.png color
            d.text((padding, y), text_line, font=font, fill=(201, 219, 213))
        except Exception as draw_err:
            print(f"DEBUG: terminal image line draw failed: {draw_err}")
        y += line_height

    buf = io.BytesIO()
    img.save(buf, format='PNG')
    img.close()  # Free the PIL image immediately (Bug 4 — memory)
    buf.seek(0)
    return buf


def add_normal_para(doc, text, font_name='Times New Roman', size=12, align=None):
    if align is None:
        if any(c in text for c in ['*', '•', '·']) or '  ' in text:
            align = WD_ALIGN_PARAGRAPH.LEFT
        else:
            align = WD_ALIGN_PARAGRAPH.JUSTIFY
    p = doc.add_paragraph()
    p.alignment = align
    run = p.add_run(text)
    set_font(p, font_name=font_name, size=size)
    return p


def embed_output_image(doc, output_text, caption_text, exp_no, step_no,
                       terminal_img_width, image_width_inches, font_name, caption_size, code_size):
    """Render `output_text` as a terminal image, embed it, and ALWAYS free the buffer.

    Falls back to plain code text if image generation fails. Centralises the
    Bug 4 memory fix: every BytesIO buffer is closed after use.
    """
    img_buf = None
    try:
        img_buf = create_terminal_image(output_text, terminal_img_width)
        pic_para = doc.add_paragraph()
        pic_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = pic_para.add_run()
        run.add_picture(img_buf, width=Inches(image_width_inches))
        add_caption_para(doc, caption_text, exp_no, step_no, font_name, caption_size)
    except Exception as img_err:
        print(f"DEBUG: terminal image error (exp {exp_no}, step {step_no}): {img_err}")
        add_code_para(doc, output_text, font_name, code_size)
    finally:
        if img_buf is not None:
            img_buf.close()
            del img_buf


def count_output_units(experiments, mode=None):
    """Total number of terminal images a batch will produce (steps, min 1 per experiment)."""
    total = 0
    for exp in experiments:
        if mode == 'cn' or exp.get('mode') == 'cn':
            total += len(build_cn_experiment(exp.get('aim', ''), exp.get('profile'))['figures'])
        else:
            total += max(len(exp.get('steps', []) or []), 1)
    return total


def build_document(experiments, settings, mode):
    """Build a .docx from experiments and return a seeked BytesIO. Reused by
    /api/download and /api/classroom-zip."""
    settings = settings or {}
    if mode == 'cn' or all(exp.get('mode') == 'cn' for exp in experiments):
        return build_cn_document(experiments, settings)
    font_name = settings.get('fontName', 'Times New Roman')
    body_size = int(settings.get('bodySize', 12))
    heading_size = int(settings.get('headingSize', 14))
    code_size = int(settings.get('codeSize', 10))
    caption_size = int(settings.get('captionSize', 10))
    image_width_inches = float(settings.get('imageWidth', 5.0))
    terminal_img_width = int(settings.get('terminalImgWidth', 600))

    # Bug 4 — cap terminal image width on large batches to limit peak memory.
    if len(experiments) > 15:
        terminal_img_width = min(terminal_img_width, 500)

    doc = Document()

    for i, exp in enumerate(experiments, 1):
        if exp.get('mode') == 'cn':
            add_cn_experiment(doc, canonical_cn_experiment(exp, settings.get('student')), i, start_new_page=False)
            if i < len(experiments):
                doc.add_page_break()
            continue
        aim = exp.get('aim', 'N/A')
        concept = exp.get('concept', 'No concept description provided.')
        caption = exp.get('caption', 'Terminal Output Preview')
        steps = exp.get('steps', [])

        # Experiment heading
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(f'Experiment No. {i}')
        run.bold = True
        run.font.name = font_name
        run.font.size = Pt(heading_size)
        doc.add_paragraph('')

        add_labeled_para(doc, 'Aim:', aim, font_name, body_size)
        doc.add_paragraph('')

        # Per-experiment OS detection so Auto-mode mixed batches export correctly.
        exp_is_os = (mode == 'os') or bool(steps)

        if exp_is_os:
            add_bold_para(doc, 'Theory:', font_name, body_size)
            add_normal_para(doc, concept, font_name, body_size)
            doc.add_paragraph('')

            # Procedure with per-step output images
            add_bold_para(doc, 'Procedure:', font_name, body_size)

            if steps:
                for step in steps:
                    step_num = step.get('num', '')
                    explanation = step.get('explanation', '')
                    command = step.get('command', '')
                    output = step.get('output', '')

                    # Step explanation
                    add_normal_para(doc, f"Step {step_num}: {explanation}", font_name, body_size)

                    # For multi-line code (C programs etc.), show source code as text
                    is_multiline_code = command.count('\n') > 2
                    if is_multiline_code:
                        add_code_para(doc, command, font_name, code_size)

                    # Output as terminal image
                    if output.strip():
                        embed_output_image(
                            doc, output, make_step_caption(command), i, step_num,
                            terminal_img_width, image_width_inches, font_name, caption_size, code_size,
                        )

                    doc.add_paragraph('')  # spacing between steps
            else:
                # Fallback
                code = exp.get('code', '// No procedure available.')
                output = exp.get('output', 'No output.')
                add_code_para(doc, code, font_name, code_size)
                doc.add_paragraph('')
                add_bold_para(doc, 'Output:', font_name, body_size)
                embed_output_image(
                    doc, output, caption, i, None,
                    terminal_img_width, image_width_inches, font_name, caption_size, code_size,
                )

        else:
            code = exp.get('code', '// No code available.')
            output = exp.get('output', 'Program executed successfully.')

            add_labeled_para(doc, 'Concept Used:', concept, font_name, body_size)
            doc.add_paragraph('')
            add_bold_para(doc, 'Code:', font_name, body_size)
            add_code_para(doc, code, font_name, code_size)
            doc.add_paragraph('')
            add_bold_para(doc, 'Output:', font_name, body_size)
            embed_output_image(
                doc, output, caption, i, None,
                terminal_img_width, image_width_inches, font_name, caption_size, code_size,
            )

        if i < len(experiments):
            doc.add_page_break()

    file_buf = io.BytesIO()
    doc.save(file_buf)
    file_buf.seek(0)
    return file_buf


@app.route('/api/download', methods=['POST'])
def api_download():
    try:
        data = read_download_payload()
        if not data:
            return jsonify({'error': 'No data received for export.'}), 400

        experiments = data.get('experiments', [])
        if not experiments:
            return jsonify({'error': 'No experiment artifacts found to bundle.'}), 400

        # Bug 4 — reject batches that would build too many images at once.
        total_units = count_output_units(experiments, data.get('mode'))
        if total_units > 200:
            return jsonify({
                'error': f'Too many output units ({total_units}). Split into smaller batches.'
            }), 400

        settings = data.get('settings', {})
        mode = data.get('mode', 'general')
        output_filename = (settings or {}).get('outputFilename', 'Generated_Practical_File.docx')

        file_buf = build_document(experiments, settings, mode)

        return send_file(
            file_buf,
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            as_attachment=True,
            download_name=output_filename
        )
    except (DownloadRequestError, ValueError) as e:
        return jsonify({'error': str(e)}), 400
    except LLMConfigurationError:
        raise
    except Exception as e:
        print(f"CRITICAL EXPORT ERROR: {traceback.format_exc()}")
        return jsonify({'error': f'Export Pipeline Fault: {str(e)}'}), 500


# ==================== API: Classroom Bulk Variations ZIP (Phase 3) ====================
@app.route('/api/classroom-zip', methods=['POST'])
def api_classroom_zip():
    try:
        data = read_download_payload()
        if not data:
            return jsonify({'error': 'No data received for classroom export.'}), 400

        students = data.get('students', [])
        if not students:
            return jsonify({'error': 'No student variations found to bundle.'}), 400

        settings = data.get('settings', {})
        mode = data.get('mode', 'general')
        zip_name = (settings or {}).get('zipFilename', 'Classroom_Variations.zip')

        # Guard against runaway batches (per-student unit budget shared across the zip).
        total_units = sum(count_output_units(s.get('experiments', []) or [], mode) for s in students)
        if total_units > 600:
            return jsonify({
                'error': f'Too many total output units ({total_units}). Reduce students or experiments.'
            }), 400

        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for idx, student in enumerate(students, 1):
                experiments = student.get('experiments', []) or []
                if not experiments:
                    continue
                name = student.get('name') or f'Student_{idx:02d}'
                safe_name = re.sub(r'[^A-Za-z0-9_-]+', '_', str(name)).strip('_') or f'Student_{idx:02d}'
                doc_buf = build_document(experiments, settings, mode)
                zf.writestr(f'{safe_name}.docx', doc_buf.getvalue())
                doc_buf.close()

        zip_buf.seek(0)
        return send_file(
            zip_buf,
            mimetype='application/zip',
            as_attachment=True,
            download_name=zip_name,
        )
    except (DownloadRequestError, ValueError) as e:
        return jsonify({'error': str(e)}), 400
    except LLMConfigurationError:
        raise
    except Exception as e:
        print(f"CRITICAL CLASSROOM EXPORT ERROR: {traceback.format_exc()}")
        return jsonify({'error': f'Classroom Export Fault: {str(e)}'}), 500


if __name__ == '__main__':
    print('\n  [+] PractiGen running at http://localhost:5000\n')
    app.run(host='0.0.0.0', port=5000, debug=True)
