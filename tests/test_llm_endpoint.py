"""Endpoint routing and credential isolation; all HTTP is mocked."""
import io
import json
from types import SimpleNamespace

import pytest

import app


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch):
    for name in ('LLM_BASE_URL', 'LLM_API_KEY', 'LLM_MODEL', 'GROQ_API_KEY', 'VERCEL'):
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
    assert timeout < 60
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
