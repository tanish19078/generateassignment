"""Offline checks for the standalone provider diagnostic; no third-party dependencies."""
import importlib.util
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import urllib.error

spec = importlib.util.spec_from_file_location('diagnostic', Path(__file__).parents[1] / 'scripts/diagnose_nvidia.py')
diagnostic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostic)


class Response(io.BytesIO):
    status = 200
    headers = {}


class DiagnosticTests(unittest.TestCase):
    def test_controls_come_from_catalogue_when_old_llama_is_absent(self):
        models = {diagnostic.TARGET, 'qwen/qwen3-next-80b-a3b-instruct',
                  'microsoft/phi-4-mini-instruct', 'meta/llama-3.3-70b-instruct'}
        self.assertEqual(diagnostic.select_controls(models),
                         ['microsoft/phi-4-mini-instruct', 'meta/llama-3.3-70b-instruct'])

    def test_controls_exclude_non_chat_and_target_models(self):
        # These two real catalogue entries returned 404 in the user's comparison.
        # "instruct" and a small parameter count are not evidence of chat serving.
        models = {diagnostic.TARGET, 'ibm/granite-3.0-3b-a800m-instruct',
                  'nvidia/riva-translate-4b-instruct', 'example/text-nano-4b',
                  'nvidia/nemotron-3.5-bolt-e4b', 'microsoft/phi-4-mini-instruct'}
        self.assertEqual(diagnostic.select_controls(models), ['microsoft/phi-4-mini-instruct'])

    def test_controls_never_invent_missing_models(self):
        self.assertEqual(diagnostic.select_controls({diagnostic.TARGET}), [])

    def call_main(self, args, models, outcome='completed', status=200):
        events = []
        def probe(key, *, model=None, **kwargs):
            self.assertEqual(key, 'fake-secret')
            if model is None:
                return {'outcome': 'catalogue_received'}, models
            return {'outcome': outcome, 'http_status': status, 'model': model}, None
        with patch.object(sys, 'argv', ['diagnose_nvidia.py', *args]), \
                patch.dict(sys.modules, {'dotenv': SimpleNamespace(load_dotenv=Mock())}), \
                patch.dict(diagnostic.os.environ, {'NVIDIA_API_KEY': 'fake-secret'}, clear=True), \
                patch.object(diagnostic, 'emit', side_effect=events.append), \
                patch.object(diagnostic, 'probe', side_effect=probe) as mocked_probe:
            exit_code = diagnostic.main()
        self.assertNotIn('fake-secret', json.dumps(events))
        return exit_code, events, mocked_probe.call_args_list

    def test_controls_only_without_known_candidates_prints_catalogue_and_does_not_generate(self):
        models = {diagnostic.TARGET, 'nvidia/riva-translate-4b-instruct'}
        exit_code, events, calls = self.call_main(['--live', '--controls-only'], models)
        self.assertEqual(exit_code, 1)
        self.assertEqual(len(calls), 1)
        self.assertIn(sorted(models), [event.get('available_models') for event in events])

    def test_controls_only_404_is_inconclusive_and_does_not_retry_deepseek(self):
        model = 'microsoft/phi-4-mini-instruct'
        exit_code, events, calls = self.call_main(['--live', '--controls-only'],
                                                {diagnostic.TARGET, model}, 'http_error', 404)
        self.assertEqual(exit_code, 1)
        self.assertEqual([call.kwargs.get('model') for call in calls], [None, model])
        self.assertTrue(any(event.get('control_comparison') == 'inconclusive' for event in events))

    def test_explicit_listed_control_can_override_automatic_candidates(self):
        model = 'example/verified-chat-model'
        exit_code, events, calls = self.call_main(['--live', '--controls-only', '--control', model],
                                                {model, 'microsoft/phi-4-mini-instruct'})
        self.assertEqual(exit_code, 0)
        self.assertEqual([call.kwargs.get('model') for call in calls], [None, model])
        self.assertTrue(any(event.get('selection') == 'explicit' for event in events))

    def test_without_live_never_calls_provider(self):
        exit_code, _, calls = self.call_main(['--controls-only'], set())
        self.assertEqual(exit_code, 0)
        self.assertEqual(calls, [])

    def call(self, *, body=b'', model=diagnostic.TARGET, stream=False, failure=None):
        events = []
        def http(req, timeout):
            self.assertEqual(req.get_header('Authorization'), 'Bearer fake-secret')
            if failure:
                raise failure
            return Response(body)
        with patch.object(diagnostic.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = http
            result = diagnostic.probe('fake-secret', model=model, stream=stream, emit_event=events.append)
        self.assertNotIn('fake-secret', json.dumps(events))
        self.assertNotIn('PRIVATE CONTENT', json.dumps(events))
        return result

    def test_catalogue(self):
        result, models = self.call(model=None, body=json.dumps({'data': [{'id': diagnostic.TARGET}]}).encode())
        self.assertTrue(result['target_listed'])
        self.assertIn(diagnostic.TARGET, models)

    def test_non_streamed_response(self):
        result, _ = self.call(body=json.dumps({'choices': [{'message': {'content': 'PRIVATE CONTENT'}, 'finish_reason': 'stop'}]}).encode())
        self.assertEqual(result['outcome'], 'completed')

    def test_streaming_reasoning_then_content(self):
        body = b'data: {"choices":[{"delta":{"reasoning_content":"PRIVATE CONTENT"}}]}\n\n'
        body += b'data: {"choices":[{"delta":{"content":"PRIVATE CONTENT"}}]}\n\n'
        result, _ = self.call(body=body, stream=True)
        self.assertEqual(result['outcome'], 'first_content_received')
        self.assertIn('first_reasoning_seconds', result)

    def test_error_event_is_not_success(self):
        result, _ = self.call(body=b'data: {"error":{"message":"PRIVATE CONTENT"}}\n\n', stream=True)
        self.assertEqual(result['outcome'], 'provider_stream_error')

    def test_timeout_before_headers(self):
        result, _ = self.call(failure=TimeoutError())
        self.assertEqual(result['phase'], 'waiting_for_headers')
        self.assertEqual(result['outcome'], 'timeout')

    def test_auth_failure_never_logs_body(self):
        error = urllib.error.HTTPError('https://example.invalid', 401, 'PRIVATE CONTENT', {}, io.BytesIO(b'PRIVATE CONTENT'))
        result, _ = self.call(failure=error)
        self.assertEqual(result['http_status'], 401)
        self.assertEqual(result['outcome'], 'http_error')

    def test_404_records_received_headers_and_request_id_without_error_body(self):
        error = urllib.error.HTTPError('https://example.invalid', 404, 'PRIVATE CONTENT',
                                       {'NVCF-REQID': 'test-request-id'}, io.BytesIO(b'PRIVATE CONTENT'))
        result, _ = self.call(failure=error)
        self.assertEqual(result['http_status'], 404)
        self.assertEqual(result['phase'], 'headers_received')
        self.assertEqual(result['provider_request_id'], 'test-request-id')
        self.assertIn('headers_seconds', result)


if __name__ == '__main__':
    unittest.main()
