"""Offline probe workflow checks; fake routes, no dependencies or live credentials."""
from contextlib import redirect_stdout
import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


spec = importlib.util.spec_from_file_location('ai_probe', Path(__file__).parents[1] / 'scripts/probe_ai.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class Client:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, route, json):
        self.calls.append((route, copy.deepcopy(json)))
        status, body = next(self.responses)
        return SimpleNamespace(status_code=status, get_json=lambda: body)


class ProbeTests(unittest.TestCase):
    def run_probe(self, args, responses):
        client = Client(responses)
        app = SimpleNamespace(
            app=SimpleNamespace(test_client=lambda: client, logger=Mock()),
            llm_timeout_seconds=lambda: 120,
            llm_max_output_tokens=lambda: int(probe.os.getenv('LLM_MAX_OUTPUT_TOKENS', '4096')),
        )
        output = io.StringIO()
        with patch.dict(sys.modules, {'app': app}), \
                patch.dict(probe.os.environ, {'NVIDIA_API_KEY': 'fake-secret'}, clear=True), \
                patch.object(sys, 'argv', ['probe_ai.py', '--key-env', 'NVIDIA_API_KEY', *args]), \
                redirect_stdout(output):
            exit_code = probe.main()
        text = output.getvalue()
        self.assertNotIn('fake-secret', text)
        self.assertNotIn('PRIVATE', text)
        return exit_code, text, client.calls

    def test_lab_success_reports_counts_without_generated_source(self):
        code = 'print("PRIVATE source")'
        status, output, calls = self.run_probe(['--aim', 'Sort a list', '--live'], [
            (200, {'concept': 'PRIVATE explanation', 'code': code}),
        ])
        self.assertEqual(status, 0)
        events = [json.loads(line) for line in output.splitlines()]
        self.assertEqual(events[-1], {'complete': True, 'code_chars': len(code)})
        self.assertTrue(all('code' not in event for event in events))
        self.assertEqual([route for route, _ in calls], ['/api/generate'])

    def test_error_retains_timeout_code_and_redacts_key(self):
        status, output, calls = self.run_probe(['--aim', 'Sort a list', '--live'], [
            (504, {'code': 'llm_timeout', 'error': 'Failed for fake-secret', 'request_id': 'ref-123',
                   'retryable': True, 'auto_retry': False}),
        ])
        self.assertEqual(status, 1)
        event = json.loads(output.splitlines()[-1])
        self.assertEqual(event['code'], 'llm_timeout')
        self.assertEqual(event['request_id'], 'ref-123')
        self.assertFalse(event['auto_retry'])
        self.assertEqual(event['error'], 'Failed for [REDACTED]')
        self.assertEqual(len(calls), 1)

    def test_project_parts_carry_forward_in_order_without_printing_text(self):
        context = {'submode': 'frontend', 'project': {'summary': 'PRIVATE context'}}
        validation = {'valid': True, 'normalized': context, 'generation_needed': ['introduction_background']}
        parts = [f'PRIVATE generated part {index}' for index in range(3)]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'context.json'
            path.write_text(json.dumps(context), encoding='utf-8')
            responses = [(200, validation)] + [
                (200, {'text': text, 'part_count': 3, 'part_index': index})
                for index, text in enumerate(parts)
            ]
            status, output, calls = self.run_probe(['--context', str(path), '--live'], responses)
        self.assertEqual(status, 0)
        self.assertEqual(calls[0][0], '/api/project/validate')
        self.assertEqual(len(calls), 4)
        for index, (route, payload) in enumerate(calls[1:]):
            self.assertEqual(route, '/api/project/section')
            self.assertEqual(payload['part_index'], index)
            self.assertEqual(payload['previous_parts'], parts[:index])
            self.assertEqual(payload['section'], 'introduction_background')
        self.assertEqual(json.loads(output.splitlines()[-1]),
                         {'complete': True, 'parts': 3, 'total_words': 12})

    def test_validation_only_never_generates(self):
        status, output, calls = self.run_probe(['--aim', 'Sort a list'], [])
        self.assertEqual(status, 0)
        self.assertEqual(calls, [])
        self.assertIn('Validation only', output)

    def test_explicit_budgets_apply_to_probe_and_preserve_usage_errors(self):
        status, output, calls = self.run_probe([
            '--aim', 'Sort a list', '--live', '--max-output-tokens', '8192', '--reasoning-budget', '1024',
        ], [(502, {'code': 'llm_truncated', 'error': 'Output limit reached', 'max_output_tokens': 8192,
                  'finish_reason': 'length', 'completion_tokens': 8192, 'reasoning_tokens': 1024,
                  'auto_retry': False})])
        self.assertEqual(status, 1)
        self.assertEqual(json.loads(output.splitlines()[0])['max_output_tokens'], 8192)
        self.assertEqual(calls[0][1]['reasoning_budget'], 1024)
        event = json.loads(output.splitlines()[-1])
        self.assertEqual(event['reasoning_tokens'], 1024)
        self.assertEqual(event['code'], 'llm_truncated')
        self.assertEqual(len(calls), 1)

    def test_unset_reasoning_budget_is_not_sent(self):
        _, _, calls = self.run_probe(['--aim', 'Sort a list', '--live'], [(200, {'concept': '', 'code': ''})])
        self.assertNotIn('reasoning_budget', calls[0][1])


if __name__ == '__main__':
    unittest.main()
