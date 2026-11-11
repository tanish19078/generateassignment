"""Endpoint routing and credential isolation; all HTTP is mocked."""
import io
import json
import logging
import urllib.error
from types import SimpleNamespace

import pytest

import app


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch):
    for name in ('LLM_BASE_URL', 'LLM_API_KEY', 'LLM_MODEL', 'GROQ_API_KEY', 'VERCEL',
                 'LLM_TIMEOUT_SECONDS', 'LLM_MAX_OUTPUT_TOKENS', 'LLM_REASONING_BUDGET'):
        monkeypatch.delenv(name, raising=False)


def test_custom_endpoint_receives_only_its_submitted_key(monkeypatch):
    monkeypatch.setenv('GROQ_API_KEY', 'server-secret')
    calls = []
    def http(request, timeout):
        calls.append((request, timeout))
        return io.BytesIO(json.dumps({'choices': [{'message': {'content': 'A valid reply'}}]}).encode())
    def opener(handler):
        assert handler.redirect_request(None, None, 302, '', {}, 'https://another.example') is None
        return SimpleNamespace(open=http)
    monkeypatch.setattr(app.urllib.request, 'build_opener', opener)
    provider, model, config, keys = app.resolve_llm_config({
        'base_url': 'https://custom.example/v1/chat/completions/',
        'model': 'my-model', 'api_key': 'user-key',
    })
    assert app.run_completion([], provider, model, config, keys) == 'A valid reply'
    request, timeout = calls[0]
    assert request.full_url == 'https://custom.example/v1/chat/completions'
    assert request.get_header('Authorization') == 'Bearer user-key'
    assert json.loads(request.data)['model'] == 'my-model'
    assert json.loads(request.data)['max_tokens'] == 4096
    assert 'reasoning_budget' not in json.loads(request.data)
    assert timeout == 300
    assert keys == ['user-key']
    with pytest.raises(app.LLMConfigurationError, match='API key'):
        app.resolve_llm_config({'base_url': 'https://another.example/v1', 'model': 'other'})


def test_server_configuration_and_default_model(monkeypatch):
    monkeypatch.setenv('GROQ_API_KEY', 'groq-key')
    assert app.resolve_llm_config({})[1] == app.DEFAULT_MODEL
    monkeypatch.setenv('LLM_BASE_URL', 'https://server.example/v1')
    monkeypatch.setenv('LLM_MODEL', 'server-model')
    monkeypatch.setenv('LLM_API_KEY', 'server-key')
    provider, model, config, keys = app.resolve_llm_config({})
    assert (provider, model, keys) == ('custom', 'server-model', ['server-key'])
    assert config['base_url'] == 'https://server.example/v1'


def test_chat_endpoint_that_defaults_to_streaming(monkeypatch):
    def http(request, timeout):
        payload = json.loads(request.data)
        if payload.get('stream', True):
            return io.BytesIO(b'data: {"choices":[{"delta":{"content":"A valid reply"}}]}\n\n')
        return io.BytesIO(json.dumps({'choices': [
            {'message': {'content': 'A valid reply'}, 'finish_reason': 'stop'},
        ]}).encode())

    monkeypatch.setattr(app.urllib.request, 'build_opener', lambda *args: SimpleNamespace(open=http))
    config = {'base_url': 'https://custom.example/v1', 'label': 'Custom endpoint'}
    assert app.run_completion([], 'custom', 'test-model', config, ['fake-key']) == 'A valid reply'


@pytest.mark.parametrize('endpoint', ['file:///tmp/key', 'https://user:pass@example.com/v1',
                                    'http://example.com/v1', 'https://example.com/v1?key=secret'])
def test_invalid_endpoint_is_a_client_error(endpoint):
    with app.app.test_client() as client:
        response = client.post('/api/generate', json={
            'aim': 'List files', 'mode': 'os', 'base_url': endpoint,
            'model': 'test', 'api_key': 'test-key',
        })
    assert response.status_code == 400


def test_local_endpoint_without_key_and_missing_model(monkeypatch):
    assert app.resolve_llm_config({'base_url': 'http://localhost:11434/v1', 'model': 'local'})[3] == []
    with pytest.raises(app.LLMConfigurationError, match='model ID'):
        app.resolve_llm_config({'base_url': 'https://custom.example/v1', 'api_key': 'key'})
    monkeypatch.setenv('VERCEL', '1')
    with pytest.raises(app.LLMConfigurationError):
        app.resolve_llm_config({'base_url': 'http://localhost:11434/v1', 'model': 'local'})


def test_cut_off_model_response_is_reported():
    body = json.dumps({'choices': [{'finish_reason': 'length', 'message': {'content': 'Partial'}}]})
    with pytest.raises(ValueError, match='cut off'):
        app.parse_openai_chat_response({}, body)


def test_reasoning_budget_is_opt_in_and_scoped_to_server_model(monkeypatch):
    monkeypatch.setenv('LLM_BASE_URL', 'https://server.example/v1')
    monkeypatch.setenv('LLM_MODEL', 'configured-model')
    monkeypatch.setenv('LLM_API_KEY', 'fake-key')
    monkeypatch.setenv('LLM_REASONING_BUDGET', '1024')
    assert app.resolve_llm_config({})[2]['reasoning_budget'] == 1024
    assert 'reasoning_budget' not in app.resolve_llm_config({'model': 'different-model'})[2]
    assert 'reasoning_budget' not in app.resolve_llm_config({
        'base_url': 'https://another.example/v1', 'model': 'configured-model', 'api_key': 'other-key',
    })[2]
    assert app.resolve_llm_config({'reasoning_budget': 0})[2]['reasoning_budget'] == 0
    assert 'reasoning_budget' not in app.resolve_llm_config({'reasoning_budget': None})[2]


@pytest.mark.parametrize('value', [True, 1.5, -2, 32769, 'invalid'])
def test_invalid_reasoning_budgets_are_rejected(value):
    with pytest.raises(app.LLMConfigurationError, match='reasoning_budget'):
        app.resolve_llm_config({'api_key': 'fake-key', 'reasoning_budget': value})


def test_configured_budgets_reach_provider_and_usage_logs_are_safe(monkeypatch, caplog):
    monkeypatch.setenv('LLM_MAX_OUTPUT_TOKENS', '8192')
    def http(request, timeout):
        payload = json.loads(request.data)
        assert payload['max_tokens'] == 8192
        assert payload['reasoning_budget'] == 1024
        assert payload['stream'] is False
        response = io.BytesIO(json.dumps({
            'choices': [{'finish_reason': 'stop', 'message': {
                'content': 'PRIVATE report', 'reasoning_content': 'PRIVATE reasoning',
            }}],
            'usage': {'prompt_tokens': 9000, 'completion_tokens': 1500, 'total_tokens': 10500,
                      'completion_tokens_details': {'reasoning_tokens': 1000}, 'unknown': 'PRIVATE usage'},
        }).encode())
        response.headers = {'NVCF-REQID': 'provider-ref'}
        return response
    monkeypatch.setattr(app.urllib.request, 'build_opener', lambda *args: SimpleNamespace(open=http))
    provider, model, config, keys = app.resolve_llm_config({
        'base_url': 'https://custom.example/v1', 'model': 'my-model', 'api_key': 'fake-key', 'reasoning_budget': 1024,
    })
    with caplog.at_level(logging.INFO):
        assert app.run_completion([], provider, model, config, keys) == 'PRIVATE report'
    log = next(record.message for record in caplog.records if record.message.startswith('llm_result '))
    metrics = json.loads(log.removeprefix('llm_result '))
    assert metrics['reasoning_tokens'] == 1000
    assert metrics['completion_tokens'] == 1500
    assert metrics['finish_reason'] == 'stop'
    assert metrics['provider_request_id'] == 'provider-ref'
    assert metrics['content_chars'] == len('PRIVATE report')
    assert 'PRIVATE' not in caplog.text
    assert 'fake-key' not in caplog.text


def test_truncation_is_a_structured_error_with_usage_not_partial_text(monkeypatch, caplog):
    body = json.dumps({
        'choices': [{'finish_reason': 'length', 'message': {
            'content': 'PRIVATE partial report', 'reasoning_content': 'PRIVATE reasoning',
        }}],
        'usage': {'completion_tokens': 4096, 'completion_tokens_details': {'reasoning_tokens': 3900}},
    }).encode()
    calls = []
    def http(request, timeout):
        calls.append(request)
        return io.BytesIO(body)
    monkeypatch.setattr(app.urllib.request, 'build_opener', lambda *args: SimpleNamespace(open=http))
    with caplog.at_level(logging.INFO), pytest.raises(app.LLMTruncatedError) as failure:
        app.create_chat_completion('groq', 'fake-key', 'my-model', [])
    details = failure.value.details
    assert details['code'] == 'llm_truncated'
    assert details['finish_reason'] == 'length'
    assert details['max_output_tokens'] == 4096
    assert details['reasoning_tokens'] == 3900
    assert details['request_id']
    assert details['auto_retry'] is False
    assert len(calls) == 1
    assert 'PRIVATE' not in json.dumps(details) + caplog.text
    assert 'fake-key' not in caplog.text
    assert any(record.levelno == logging.WARNING and 'llm_truncated' in record.message for record in caplog.records)


def test_timeout_configuration_respects_deployment_budget(monkeypatch):
    assert app.llm_timeout_seconds() == 300
    monkeypatch.setenv('LLM_TIMEOUT_SECONDS', '120')
    assert app.llm_timeout_seconds() == 120
    monkeypatch.setenv('VERCEL', '1')
    assert app.llm_timeout_seconds() == 120
    monkeypatch.delenv('LLM_TIMEOUT_SECONDS')
    assert app.llm_timeout_seconds() == 180
    monkeypatch.setenv('LLM_TIMEOUT_SECONDS', '600')
    assert app.llm_timeout_seconds() == 285
    monkeypatch.setenv('LLM_TIMEOUT_SECONDS', '20')
    assert app.llm_timeout_seconds() == 20


def test_output_budget_is_explicit_and_configurable(monkeypatch):
    monkeypatch.setenv('LLM_MAX_OUTPUT_TOKENS', '8192')
    assert app.llm_max_output_tokens() == 8192
    monkeypatch.setenv('LLM_MAX_OUTPUT_TOKENS', '0')
    with pytest.raises(app.LLMConfigurationError):
        app.llm_max_output_tokens()


@pytest.mark.parametrize('value', ['0', '-1', '601', 'nan', 'infinity', 'abc'])
def test_invalid_timeouts_fail_before_http(monkeypatch, value):
    monkeypatch.setenv('LLM_TIMEOUT_SECONDS', value)
    with pytest.raises(app.LLMConfigurationError, match='LLM_TIMEOUT_SECONDS'):
        app.llm_timeout_seconds()


@pytest.mark.parametrize('stage', ['open', 'read', 'wrapped'])
def test_socket_timeouts_have_safe_diagnostics(monkeypatch, caplog, stage):
    def http(request, timeout):
        assert timeout == 300
        assert json.loads(request.data)['max_tokens'] == 4096
        if stage == 'wrapped':
            raise urllib.error.URLError(TimeoutError('socket timeout'))
        if stage == 'open':
            raise TimeoutError('The read operation timed out')
        class SlowBody(io.BytesIO):
            def read(self):
                raise TimeoutError('The read operation timed out')
        return SlowBody()

    monkeypatch.setattr(app.urllib.request, 'build_opener', lambda *args: SimpleNamespace(open=http))
    with caplog.at_level(logging.INFO), pytest.raises(app.LLMTimeoutError) as failure:
        app.create_chat_completion('groq', 'secret-key', 'test-model',
                                   [{'role': 'user', 'content': 'private project context'}], max_tokens=4096)
    assert failure.value.details['retryable'] is True
    assert failure.value.details['auto_retry'] is False
    assert failure.value.details['elapsed_seconds'] >= 0
    assert failure.value.details['request_id'] in caplog.text
    assert 'secret-key' not in caplog.text
    assert 'private project context' not in caplog.text
