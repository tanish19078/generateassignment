"""Explicit live diagnostic using the web app's project or experiment routes.

No key, prompt, generated prose or provider error body is written to the diagnostic output.
Run from the repository root; credentials are read only from the named local environment variable.
"""
import argparse
import json
import logging
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--context', type=Path, help='Context pack or app request JSON file')
    source.add_argument('--aim', help='Experiment aim to test through /api/generate')
    parser.add_argument('--mode', choices=['coding', 'os', 'auto'], default='coding')
    parser.add_argument('--language', default='python')
    parser.add_argument('--submode', choices=['frontend', 'dbms'])
    parser.add_argument('--endpoint', help='API base URL or full chat-completions URL')
    parser.add_argument('--model')
    parser.add_argument('--key-env', default='LLM_API_KEY')
    parser.add_argument('--section', default='introduction_background')
    parser.add_argument('--timeout', type=int, help='Local socket timeout, 1-600 seconds')
    parser.add_argument('--max-output-tokens', type=int, help='Output cap for this process only, 1-131072')
    parser.add_argument('--reasoning-budget', type=int, help='Optional endpoint-specific reasoning token budget, -1 to 32768; requires provider support')
    parser.add_argument('--whole-section', action='store_true', help='Compare the old single-request path')
    parser.add_argument('--live', action='store_true', help='Send real requests to the selected provider')
    args = parser.parse_args()
    if args.max_output_tokens is not None and not 1 <= args.max_output_tokens <= 131072:
        parser.error('--max-output-tokens must be between 1 and 131072')
    if args.reasoning_budget is not None and not -1 <= args.reasoning_budget <= 32768:
        parser.error('--reasoning-budget must be between -1 and 32768')

    import app
    app.app.logger.setLevel(logging.INFO)
    if args.timeout is not None:
        os.environ['LLM_TIMEOUT_SECONDS'] = str(args.timeout)
    if args.max_output_tokens is not None:
        os.environ['LLM_MAX_OUTPUT_TOKENS'] = str(args.max_output_tokens)

    raw = args.context.read_text(encoding='utf-8-sig') if args.context else '{}'
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {}  # Let the app's tolerant context importer report validation errors.
    if not isinstance(parsed, dict):
        parsed = {}
    context = parsed.get('context', raw)
    submode = args.submode or parsed.get('submode')
    if not submode and isinstance(context, dict):
        submode = context.get('submode')
    with app.app.test_client() as client:
        if args.context:
            result = client.post('/api/project/validate', json={'submode': submode, 'context': context}).get_json()
            if not result.get('valid'):
                print(json.dumps({'valid': False, 'errors': result.get('errors'),
                                  'missing_fields': result.get('missing_fields')}, indent=2))
                return 1
            if args.section not in result['generation_needed']:
                parser.error('The selected section is already supplied or is not valid for this submode.')
            payload = {'submode': submode, 'context': result['normalized'], 'section': args.section}
        else:
            if not args.aim.strip():
                parser.error('Provide a nonblank experiment aim.')
            payload = {'aim': args.aim, 'mode': args.mode, 'code_language': args.language,
                       'jev_quality_gate': False}
        route = '/api/project/section' if args.context else '/api/generate'
        print(json.dumps({'route': route, 'timeout_seconds': app.llm_timeout_seconds(),
                          'max_output_tokens': app.llm_max_output_tokens()}))
        if not args.live:
            print('Validation only. Add --live to send provider requests.')
            return 0
        key = os.getenv(args.key_env, '').strip()
        if not key:
            parser.error(f'Set {args.key_env} locally before running a live probe.')
        payload.update(base_url=args.endpoint or parsed.get('base_url'),
                       model=args.model or parsed.get('model'), api_key=key)
        if args.reasoning_budget is not None:
            payload['reasoning_budget'] = args.reasoning_budget
        parts = []
        while True:
            if args.context and not args.whole_section:
                payload.update(part_index=len(parts), previous_parts=parts)
            started = time.monotonic()
            response = client.post(route, json=payload)
            body = response.get_json() or {}
            diagnostic = {'http_status': response.status_code, 'part_index': len(parts),
                          'elapsed_seconds': round(time.monotonic() - started, 2),
                          'word_count': len(body.get('text', body.get('concept', '')).split())}
            for field in ('request_id', 'timeout_seconds', 'part_count', 'retryable', 'auto_retry',
                          'max_output_tokens', 'reasoning_budget', 'finish_reason',
                          'prompt_tokens', 'completion_tokens', 'total_tokens', 'reasoning_tokens',
                          'content_chars', 'reasoning_chars'):
                if field in body:
                    diagnostic[field] = body[field]
            # Report safe app-defined errors only; never dump the raw response or request.
            if response.status_code >= 400:
                # Successful lab responses use "code" for generated source, not an error ID.
                if 'code' in body:
                    diagnostic['code'] = body['code']
                diagnostic['error'] = body.get('error', 'Request failed').replace(key, '[REDACTED]')
            print(json.dumps(diagnostic), flush=True)
            if response.status_code != 200:
                return 1
            if not args.context:
                print(json.dumps({'complete': True, 'code_chars': len(body.get('code', ''))}))
                return 0
            parts.append(body['text'])
            if args.whole_section or len(parts) == body['part_count']:
                print(json.dumps({'complete': True, 'parts': len(parts),
                                  'total_words': sum(len(part.split()) for part in parts)}))
                return 0


if __name__ == '__main__':
    raise SystemExit(main())
