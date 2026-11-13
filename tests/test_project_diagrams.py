"""App-wide DBMS diagram contracts; no provider requests or private context files."""

import base64
import io
import json
import zipfile

import pytest
from docx import Document
from PIL import Image

import app as app_module
from project_file.diagrams import build_erd_figures, plan_erd_figures, strip_diagram_source
from project_file.reports import parse_generated_sections, validate_project_context
from test_project_file import dbms_context, generate_supplied, image_payload


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app_module, 'run_completion', lambda *a, **k: pytest.fail('Diagram rendering must not call AI'))
    app_module.app.config['TESTING'] = True
    with app_module.app.test_client() as client:
        yield client


def image_bytes(figure):
    return base64.b64decode(figure['data_url'].split(',', 1)[1], validate=True)


def test_missing_erd_upload_is_optional_but_result_screenshots_are_required(client):
    context = dbms_context()
    context['figures'] = []
    assert validate_project_context(context)['valid']
    context['dbms']['use_cases'][0]['screenshot_filename'] = 'query-result.png'
    context['figures'] = [{'filename': 'erd.png', 'caption': 'Optional E-R diagram', 'section': 'erd'}]
    report = generate_supplied(client, context).get_json()['report']
    response = client.post('/api/project/download', json={'submode': 'dbms', 'report': report})
    assert response.status_code == 400
    assert 'query-result.png' in response.get_json()['error']
    assert 'erd.png' not in response.get_json()['error']
    response = client.post('/api/project/download', json={
        'submode': 'dbms', 'report': report, 'images': [image_payload('query-result.png')],
    })
    assert response.status_code == 200
    assert len(Document(io.BytesIO(response.data)).inline_shapes) == len(report['erd_diagrams']) + 1


def test_export_rebuilds_preview_and_preserves_uploaded_erd_override(client):
    report = generate_supplied(client, dbms_context()).get_json()['report']
    expected = {image_bytes(figure) for figure in report['erd_diagrams']}
    report['erd_diagrams'][0]['data_url'] = 'data:image/png;base64,' + image_payload('fake.png')['data_base64']
    response = client.post('/api/project/download', json={'submode': 'dbms', 'report': report})
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.data)) as package:
        media = {package.read(name) for name in package.namelist() if name.startswith('word/media/')}
    assert media == expected
    document = Document(io.BytesIO(response.data))
    assert document.tables[1].rows[1].cells[0].text == 'R1'

    upload = image_payload('erd.png', section='erd')
    response = client.post('/api/project/download', json={'submode': 'dbms', 'report': report, 'images': [upload]})
    assert response.status_code == 200
    assert len(Document(io.BytesIO(response.data)).inline_shapes) == 1
    with zipfile.ZipFile(io.BytesIO(response.data)) as package:
        media = [package.read(name) for name in package.namelist() if name.startswith('word/media/')]
    assert len(media) == 1 and media[0] not in expected
    upload['data_base64'] = 'broken image'
    rejected = client.post('/api/project/download', json={'submode': 'dbms', 'report': report, 'images': [upload]})
    assert rejected.status_code == 400


@pytest.mark.parametrize('problem', ['duplicate', 'unknown'])
def test_ambiguous_or_dangling_relationships_are_validation_errors(client, problem):
    context = dbms_context()
    if problem == 'duplicate':
        context['dbms']['entities'].append({**context['dbms']['entities'][0], 'name': 'MEMBER'})
    else:
        context['dbms']['relationships'][0]['to_entity'] = 'MissingEntity'
    response = client.post('/api/project/generate', json={'submode': 'dbms', 'context': context})
    assert response.status_code == 400
    assert any(problem in error for error in response.get_json()['errors'])


def test_case_insensitive_references_and_self_relationships_use_canonical_names(client):
    context = dbms_context()
    context['dbms']['relationships'] = [{
        'from_entity': 'member', 'to_entity': 'MEMBER', 'cardinality': '1:N', 'description': 'Members can sponsor members.',
    }]
    report = generate_supplied(client, context).get_json()['report']
    assert report['context']['dbms']['relationships'][0]['from_entity'] == 'Member'
    assert report['context']['dbms']['relationships'][0]['to_entity'] == 'Member'
    assert set(name for fig in report['erd_diagrams'] for name in fig['entity_names']) == {'Member', 'Book'}
    assert [rid for fig in report['erd_diagrams'] for rid in fig['relationship_ids']] == ['R1']


@pytest.mark.parametrize('source', [
    '```mermaid\nerDiagram\n  Member ||--o{ Book : borrows\n```',
    '```\nerDiagram\n  Member ||--o{ Book : borrows\n```',
    'erDiagram\n  Member {\n    int member_id PK\n  }\n  Member ||--o{ Book : borrows',
    '@startuml\nentity Member {\n  member_id\n}\nMember -- Book\n@enduml',
    '```dbml\nTable Member {\n member_id int [pk]\n}\n```',
    'digraph model {\n Member -> Book;\n}',
    'Member ||--o{ Book : borrows',
])
def test_diagram_code_is_removed_without_discarding_surrounding_prose(source):
    before = 'Members borrow books through the documented library relationships.'
    after = 'The relationship key records the supplied cardinality.'
    cleaned = strip_diagram_source(before + '\n\n' + source + '\n\n' + after)
    assert before in cleaned and after in cleaned
    assert source not in cleaned
    assert 'member_id PK' not in cleaned
    assert strip_diagram_source(before) == before


@pytest.mark.parametrize('wrapped', [False, True])
def test_model_diagram_syntax_becomes_factual_prose_without_a_second_ai_call(client, monkeypatch, wrapped):
    context = dbms_context()
    context['report_sections']['erd_description'] = ''
    syntax = '```mermaid\nerDiagram\n  Member ||--o{ Book : borrows\n```'
    calls = []
    def completion(messages, *args, **kwargs):
        calls.append(messages)
        return json.dumps({'report_sections': {'erd_description': syntax}}) if wrapped else syntax
    monkeypatch.setattr(app_module, 'run_completion', completion)
    response = client.post('/api/project/section', json={
        'submode': 'dbms', 'context': context, 'section': 'erd_description', 'api_key': 'test-key',
    })
    assert response.status_code == 200, response.get_json()
    assert '2 entities' in response.get_json()['text']
    assert 'mermaid' not in response.get_json()['text']
    assert len(calls) == 1
    assert 'drawn separately' in calls[0][0]['content']


def test_saved_reports_are_upgraded_without_ai_or_trusting_old_images(client):
    report = generate_supplied(client, dbms_context()).get_json()['report']
    report['erd_diagrams'] = [{'data_url': 'https://untrusted.example/diagram.svg'}]
    report['report_sections']['erd_description'] = 'erDiagram\n  Member ||--o{ Book : borrows'
    response = client.post('/api/project/preview', json={'report': report})
    assert response.status_code == 200
    rebuilt = response.get_json()['report']
    assert rebuilt['erd_diagrams'][0]['data_url'].startswith('data:image/png;base64,')
    assert 'erDiagram' not in rebuilt['report_sections']['erd_description']
    assert rebuilt['report_sections']['introduction'] == report['report_sections']['introduction']
    assert client.post('/api/project/preview', json={'report': {}}).status_code == 400


def test_erd_cleanup_handles_unclosed_fences_and_json_with_literal_newlines():
    context = dbms_context()
    source = '```mermaid\nerDiagram\n Member ||--o{ Book : borrows'
    assert strip_diagram_source(source) == ''
    response = json.dumps({'report_sections': {'erd_description': source}}).replace('\\n', '\n')
    sections = parse_generated_sections(response, ['erd_description'], context=context)
    assert '2 entities' in sections['erd_description']


def graph(entities, pairs):
    return {'entities': [{'name': name, 'attributes': [
        {'name': f'{name}_id', 'data_type': 'INTEGER', 'key': 'PK', 'constraints': ''}
    ]} for name in entities], 'relationships': [
        {'from_entity': a, 'to_entity': b, 'cardinality': cardinality, 'description': 'Documented relationship'}
        for a, b, cardinality in pairs
    ]}


@pytest.mark.parametrize('dbms', [
    graph(['A', 'B', 'C', 'D'], [('A', 'B', '1:N'), ('B', 'C', 'M:N'), ('C', 'A', 'N:1'), ('A', 'A', '1:1')]),
    graph([f'E{i}' for i in range(16)], [('E0', f'E{i}', '1:N') for i in range(1, 16)]),
    graph(['A', 'B'], [('A', 'B', '1:1'), ('A', 'B', '0..*:1'), ('B', 'A', 'custom notation')]),
    graph(['Long_' * 35, 'Book'], [('Long_' * 35, 'Book', 'M:N through Loan')]),
])
def test_paginated_figures_cover_every_entity_and_relationship(dbms):
    plans = plan_erd_figures(dbms)
    assert {name for plan in plans for name in plan['entities']} == {entity['name'] for entity in dbms['entities']}
    ids = [item['id'] for plan in plans for item in plan['relationships']]
    assert len(ids) == len(set(ids)) == len(dbms['relationships'])
    assert all(len(plan['relationships']) <= 4 for plan in plans)
    figures = build_erd_figures(dbms)
    for figure in figures:
        with Image.open(io.BytesIO(image_bytes(figure))) as image:
            assert image.format == 'PNG'
            assert image.size == (figure['width'], figure['height'])
            assert image.width * image.height < 2_000_000
            assert len(image.getcolors() or []) > 2


def test_maximum_schema_diagrams_fit_a_serverless_response():
    names = [f'Entity_{index}' for index in range(100)]
    dbms = graph(names, [(names[i % 100], names[i % 100], '1:N') for i in range(200)])
    figures = build_erd_figures(dbms)
    assert len(figures) == 50
    assert len(json.dumps(figures).encode()) < 3_000_000
