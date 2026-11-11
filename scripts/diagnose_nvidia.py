"""Short NVIDIA connectivity/model comparison; no application or document dependencies.

Only --live sends requests. Credentials come from NVIDIA_API_KEY or the project's .env.
Logs contain timings and counts, never keys, prompts, model output or raw error bodies.
"""
import argparse
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request

BASE_URL = 'https://integrate.api.nvidia.com/v1'
TARGET = 'deepseek-ai/deepseek-v4.1-flash'
# Deliberate chat-completion candidates, checked against NVIDIA's reference.
# Catalogue membership is still required and does not guarantee serving/access.
# https://docs.api.nvidia.com/nim/reference/microsoft-phi-4-mini-instruct-infer
# https://docs.api.nvidia.com/nim/reference/meta-llama-3_3-70b-instruct-infer
# https://docs.api.nvidia.com/nim/reference/qwen-qwen3-next-80b-a3b-instruct-infer
# https://docs.api.nvidia.com/nim/reference/nvidia-nemotron-3-5-lightning-30b-a3b-infer
CONTROL_CANDIDATES = (
    'microsoft/phi-4-mini-instruct',
    'meta/llama-3.3-70b-instruct',
    'qwen/qwen3-next-80b-a3b-instruct',
    'nvidia/nemotron-3.5-lightning-30b-a3b',
)


def select_controls(models, limit=2):
    """Intersect documented chat candidates with the live catalogue in priority order.

    Do not infer chat compatibility, gateway IDs or availability from model names.
    Explicit --control selection remains available for other verified chat models.
    """
    return [model for model in CONTROL_CANDIDATES if model in models][:limit]


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def emit(value):
    print(json.dumps(value), flush=True)


def probe(key, *, model=None, stream=False, timeout=30, emit_event=emit):
    label = 'catalogue' if model is None else ('streaming' if stream else 'non_streaming')
    result = {'test': label, 'model': model, 'phase': 'waiting_for_headers'}
    started = time.monotonic()
    headers = {'Authorization': f'Bearer {key}', 'Content-Type': 'application/json',
               'Accept': 'text/event-stream' if stream else 'application/json'}
    payload = None if model is None else json.dumps({
        'model': model, 'messages': [{'role': 'user', 'content': 'Reply only with OK.'}],
        'max_tokens': 512, 'temperature': 1.0, 'top_p': 0.95, 'stream': stream,
    }).encode('utf-8')
    req = urllib.request.Request(BASE_URL + ('/models' if model is None else '/chat/completions'),
                                 data=payload, headers=headers)
    emit_event({**result, 'event': 'started', 'read_timeout_seconds': timeout})
    models = None
    try:
        opener = urllib.request.build_opener(NoRedirects())
        with opener.open(req, timeout=timeout) as response:
            result.update(http_status=response.status, phase='reading_body',
                          headers_seconds=round(time.monotonic() - started, 2))
            result['provider_request_id'] = (response.headers.get('NVCF-REQID')
                                             or response.headers.get('X-Request-Id'))
            emit_event({**result, 'event': 'headers_received'})
            if response.status != 200:
                result['outcome'] = 'pending' if response.status == 202 else 'unexpected_http_status'
            elif stream:
                result['outcome'] = 'stream_ended_without_content'
                for _ in range(512):
                    if time.monotonic() - started > timeout * 2:
                        result['outcome'] = 'observation_limit'
                        break
                    line = response.readline(262145)
                    if len(line) > 262144:
                        result['outcome'] = 'event_too_large'
                        break
                    if not line:
                        break
                    if not line.startswith(b'data:'):
                        continue
                    data = line[5:].strip()
                    if data == b'[DONE]':
                        break
                    event = json.loads(data)
                    if event.get('error'):
                        result['outcome'] = 'provider_stream_error'
                        break
                    choices = event.get('choices') or []
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get('delta') or {}
                    if delta.get('reasoning_content') or delta.get('reasoning'):
                        if 'first_reasoning_seconds' not in result:
                            result['first_reasoning_seconds'] = round(time.monotonic() - started, 2)
                            emit_event({**result, 'event': 'reasoning_received'})
                    if delta.get('content'):
                        result.update(outcome='first_content_received',
                                      first_content_seconds=round(time.monotonic() - started, 2))
                        # This is an intentional early stop, not a completed generation.
                        break
                    if choice.get('finish_reason'):
                        result.update(outcome='finished_without_content', finish_reason=choice['finish_reason'])
                        break
            else:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ValueError('Response exceeds diagnostic size limit')
                body = json.loads(raw)
                if model is None:
                    models = {item['id'] for item in body['data'] if isinstance(item.get('id'), str)}
                    result.update(outcome='catalogue_received', model_count=len(models),
                                  target_listed=TARGET in models,
                                  documented_controls_listed=select_controls(models, limit=len(CONTROL_CANDIDATES)))
                else:
                    choice = body['choices'][0]
                    content = choice['message'].get('content') or ''
                    result.update(outcome='completed' if content and choice.get('finish_reason') != 'length'
                                  else 'truncated_or_empty', content_chars=len(content),
                                  finish_reason=choice.get('finish_reason'))
    except urllib.error.HTTPError as error:
        result.update(outcome='http_error', http_status=error.code, phase='headers_received',
                      headers_seconds=round(time.monotonic() - started, 2),
                      provider_request_id=(error.headers.get('NVCF-REQID')
                                           or error.headers.get('X-Request-Id')) if error.headers else None)
        error.close()
    except urllib.error.URLError as error:
        result.update(outcome='timeout' if isinstance(error.reason, TimeoutError) else 'connection_error',
                      cause_type=type(error.reason).__name__, os_error=getattr(error.reason, 'winerror', None))
    except TimeoutError:
        result['outcome'] = 'timeout'
    except (ValueError, KeyError, IndexError, TypeError):
        result['outcome'] = 'unexpected_response_format'
    except OSError as error:
        result.update(outcome='connection_error', cause_type=type(error).__name__)
    result['elapsed_seconds'] = round(time.monotonic() - started, 2)
    emit_event({**result, 'event': 'finished'})
    return result, models


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--timeout', type=int, default=30, help='Per-read timeout, 5-60 seconds')
    parser.add_argument('--controls-only', action='store_true', help='Compare available models without repeating the DeepSeek tests')
    parser.add_argument('--control', action='append', default=[], help='Explicit listed comparison model; may be supplied twice')
    parser.add_argument('--list-models', action='store_true', help='Print catalogue model IDs without generating')
    args = parser.parse_args()
    if not 5 <= args.timeout <= 60:
        parser.error('--timeout must be between 5 and 60 seconds')
    if len(args.control) > 2 or TARGET in args.control:
        parser.error('Choose at most two control models, different from the DeepSeek target.')
    if not args.live:
        emit({'live': False, 'endpoint': BASE_URL, 'target': TARGET,
              'documented_control_candidates': list(CONTROL_CANDIDATES)})
        return 0
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / '.env')
    key = os.getenv('NVIDIA_API_KEY', '').strip()
    if not key:
        parser.error('Save NVIDIA_API_KEY in the local .env first.')
    emit({'proxy_configured': bool(urllib.request.getproxies().get('https'))})
    _, models = probe(key, timeout=args.timeout)
    if models is None:
        emit({'next_step': 'Resolve catalogue connectivity/authentication before generating again.'})
        return 1
    if args.list_models:
        emit({'available_models': sorted(models)})
        return 0
    controls = list(dict.fromkeys(args.control)) or select_controls(models)
    if any(model not in models for model in controls):
        parser.error('A requested control is absent from this catalogue; use --live --list-models.')
    emit({'selected_controls': controls,
          'selection': 'explicit' if args.control else 'documented_chat_models_in_catalogue'})
    control_results = []
    for model in controls:
        result, _ = probe(key, model=model, timeout=args.timeout)
        control_results.append(result)
    if not controls:
        emit({'available_models': sorted(models),
              'next_step': 'No documented control candidates are listed. Verify a chat-completion model and its gateway ID in NVIDIA documentation, then use --control MODEL.'})
    elif not any(result.get('outcome') == 'completed' for result in control_results):
        emit({'control_comparison': 'inconclusive',
              'next_step': 'No control completed. A catalogue entry or HTTP 404 does not establish inference access or speed. Verify the selected models and key in NVIDIA Build before retrying the app.'})
    if args.controls_only:
        return 0 if any(result.get('outcome') == 'completed' for result in control_results) else 1
    if TARGET not in models:
        emit({'next_step': 'Target is absent from the returned catalogue; verify account/model availability.'})
        return 1
    non_stream, _ = probe(key, model=TARGET, timeout=args.timeout)
    probe(key, model=TARGET, stream=True, timeout=args.timeout)
    return 0 if non_stream.get('outcome') == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
