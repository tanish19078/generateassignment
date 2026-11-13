import base64
import gzip
import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm

import app as app_module


def prose(topic, words):
    vocabulary = (f'{topic} implementation architecture evidence workflow analysis design '.split())
    return ' '.join(vocabulary[index % len(vocabulary)] for index in range(words)) + '.'


def test_project_socket_timeout_returns_retryable_504(client, monkeypatch):
    monkeypatch.delenv('VERCEL', raising=False)
    monkeypatch.setenv('LLM_TIMEOUT_SECONDS', '90')
    def http(*args, **kwargs):
        raise TimeoutError('The read operation timed out')
    monkeypatch.setattr(app_module.urllib.request, 'build_opener',
                        lambda *args: SimpleNamespace(open=http))
    response = client.post('/api/project/section', json={
        'submode': 'frontend', 'context': frontend_context(False),
        'section': 'introduction_background', 'part_index': 0, 'api_key': 'test-key',
    })
    assert response.status_code == 504
    result = response.get_json()
    assert result['code'] == 'llm_timeout'
    assert result['timeout_seconds'] == 90
    assert result['retryable'] is True
    assert result['request_id']
    assert 'test-key' not in response.get_data(as_text=True)


def test_long_section_parts_preserve_continuity_and_assemble(client, monkeypatch):
    context = frontend_context()
    context['report_sections']['introduction_background'] = ''
    calls = []
    def completion(messages, *args, **kwargs):
        calls.append(messages)
        assert kwargs['max_tokens'] == 4096
        return prose(f'part-{len(calls)}', 260)
    monkeypatch.setattr(app_module, 'run_completion', completion)
    parts = []
    for index in range(3):
        response = client.post('/api/project/section', json={
            'submode': 'frontend', 'context': context, 'section': 'introduction_background',
            'part_index': index, 'previous_parts': parts, 'api_key': 'test-key',
        })
        assert response.status_code == 200
        result = response.get_json()
        assert (result['part_index'], result['part_count']) == (index, 3)
        assert f'part {index + 1} of 3: 250-350 words' in calls[-1][1]['content']
        if parts:
            assert parts[-1] in calls[-1][1]['content']
        parts.append(result['text'])
    monkeypatch.setattr(app_module, 'run_completion', lambda *a, **k: pytest.fail('No regeneration'))
    response = client.post('/api/project/generate', json={
        'submode': 'frontend', 'context': context,
        'completed_sections': {'introduction_background': '\n\n'.join(parts)},
    })
    assert response.status_code == 200
    sections = response.get_json()['report']['report_sections']
    assert sections['introduction_background'] == '\n\n'.join(parts)
    assert sections['abstract'] == context['report_sections']['abstract']


def test_project_truncation_is_502_and_never_returns_partial_prose(client, monkeypatch):
    def http(*args, **kwargs):
        return io.BytesIO(json.dumps({'choices': [{'finish_reason': 'length',
            'message': {'content': 'PRIVATE partial section'}}],
            'usage': {'completion_tokens': 4096}}).encode())
    monkeypatch.setattr(app_module.urllib.request, 'build_opener', lambda *args: SimpleNamespace(open=http))
    response = client.post('/api/project/section', json={
        'submode': 'frontend', 'context': frontend_context(False),
        'section': 'introduction_background', 'part_index': 0, 'api_key': 'fake-key',
    })
    assert response.status_code == 502
    body = response.get_json()
    assert body['code'] == 'llm_truncated'
    assert body['auto_retry'] is False
    assert body['request_id']
    assert body['completion_tokens'] == 4096
    assert 'text' not in body
    assert 'PRIVATE' not in response.get_data(as_text=True)


def test_project_browser_request_uses_lightning_budgets_without_server_model_settings(client, monkeypatch):
    for name in ('LLM_BASE_URL', 'LLM_MODEL', 'LLM_MAX_OUTPUT_TOKENS', 'LLM_REASONING_BUDGET', 'LLM_TIMEOUT_SECONDS'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('VERCEL', '1')
    calls = []
    def http(request, timeout):
        payload = json.loads(request.data)
        calls.append(payload)
        assert payload['max_tokens'] == 32768
        assert payload['reasoning_budget'] == 1024
        assert timeout == 180
        return io.BytesIO(json.dumps({'choices': [{'finish_reason': 'stop',
            'message': {'content': prose('background', 260)}}]}).encode())
    monkeypatch.setattr(app_module.urllib.request, 'build_opener', lambda *args: SimpleNamespace(open=http))
    response = client.post('/api/project/section', json={
        'submode': 'frontend', 'context': frontend_context(False),
        'section': 'introduction_background', 'part_index': 0,
        'provider': 'custom', 'base_url': 'https://integrate.api.nvidia.com/v1',
        'model': 'nvidia/nemotron-3.5-lightning-30b-a3b', 'api_key': 'fake-key',
    })
    assert response.status_code == 200
    assert response.get_json()['part_count'] == 3
    assert len(calls) == 1


@pytest.mark.parametrize('part_index,previous_parts', [(True, []), (-1, []), (3, []), ('0', []), (1, []), (0, ['x'])])
def test_invalid_part_progress_never_calls_model(client, monkeypatch, part_index, previous_parts):
    monkeypatch.setattr(app_module, 'run_completion', lambda *a, **k: pytest.fail('Invalid request'))
    response = client.post('/api/project/section', json={
        'submode': 'frontend', 'context': frontend_context(False), 'section': 'introduction_background',
        'part_index': part_index, 'previous_parts': previous_parts,
    })
    assert response.status_code == 400


def frontend_context(with_sections=True):
    sections = {
        'abstract': prose('abstract', 210),
        'introduction_background': prose('background', 720),
        'introduction_significance': prose('significance', 260),
        'problem_definition': prose('problem', 420),
        'design_methodology': prose('methodology', 820),
        'results_summary': prose('results', 270),
    }
    if not with_sections:
        sections = {key: '' for key in sections}
    results = [{
        'id': f'result-{index}',
        'title': f'Portal workflow {index}',
        'description': f'The repository implements workflow {index} using the documented page and component structure.',
        'evidence': [f'src/page-{index}.js'],
        'screenshot_filename': f'result-{index}.png',
    } for index in range(1, 11)]
    return {
        'schema_version': 1,
        'submode': 'frontend',
        'project': {
            'title': 'Student Portal',
            'summary': 'A responsive portal for student academic workflows.',
            'problem_statement': 'Academic information is distributed across disconnected interfaces.',
            'objectives': ['Provide a responsive dashboard', 'Present records clearly'],
            'significance': 'The portal reduces navigation effort.',
            'software_requirements': ['Modern web browser', 'Node.js 20'],
            'hardware_requirements': ['2 GB RAM'],
            'datasets': [],
            'methodology': 'Component-oriented iterative implementation.',
            'architecture': 'A browser client calls a JSON API and renders reusable page components.',
            'file_structure': [{'path': 'src/pages', 'purpose': 'Route-level pages'}],
            'results': results,
            'references': ['MDN Web Docs, Web accessibility guidance.'],
        },
        'student': {
            'name': 'Student Name', 'roll_number': '42', 'class_section': 'CSE 3A',
            'institution': 'Example University', 'department': 'Computer Science',
            'course': 'Frontend Development', 'academic_session': '2026-27',
        },
        'report_sections': sections,
        'frontend': {
            'stack': [{'name': 'JavaScript', 'version': 'ES2022', 'purpose': 'Client behaviour'}],
            'pages': [{'name': 'Dashboard', 'route': '/', 'purpose': 'Overview'}],
            'components': [{'name': 'SummaryCard', 'purpose': 'Metric display', 'source_path': 'src/SummaryCard.js'}],
            'state_management': 'Local state coordinates the selected view.',
            'api_integrations': [{'name': 'Student API', 'purpose': 'Load records', 'endpoint': '/api/students'}],
            'responsive_design': 'Layouts collapse at mobile breakpoints.',
            'accessibility': 'Controls use labels and keyboard focus styles.',
            'testing': 'Unit tests cover view state.',
            'deployment': 'Static assets are deployed with the API.',
        },
        'dbms': None,
        'figures': [{'filename': 'result-1.png', 'caption': 'Student dashboard', 'section': 'results', 'use_case_id': 'result-1'}],
        'evidence': [{'claim': 'Dashboard route exists', 'paths': ['src/pages/Dashboard.js']}],
        'missing_information': [],
    }


def dbms_context(with_sections=True):
    sections = {
        'introduction': prose('database introduction', 520),
        'erd_description': prose('entity relationship', 320),
        'schema_description': prose('database schema', 320),
        'use_cases_introduction': prose('use cases', 170),
    }
    if not with_sections:
        sections = {key: '' for key in sections}
    return {
        'schema_version': 1,
        'submode': 'dbms',
        'project': {
            'title': 'Library Database',
            'summary': 'A relational database for library circulation.',
            'problem_statement': 'Manual circulation records are inconsistent and difficult to query.',
            'objectives': ['Track books and members', 'Record borrowing transactions'],
            'significance': 'The system centralizes circulation data.',
            'software_requirements': ['PostgreSQL 16', 'psql client'],
            'hardware_requirements': ['2 GB RAM'],
            'datasets': ['Seed records in seed.sql'],
            'methodology': 'ER modelling followed by normalization and SQL implementation.',
            'architecture': 'A normalized relational schema with foreign-key constraints.',
            'file_structure': [{'path': 'schema.sql', 'purpose': 'Database definition'}],
            'results': [],
            'references': [],
        },
        'student': {
            'name': 'Database Student', 'roll_number': '77', 'class_section': 'CSE 3B',
            'institution': '', 'department': '', 'course': 'DBMS', 'academic_session': '',
        },
        'report_sections': sections,
        'frontend': None,
        'dbms': {
            'database_engine': 'PostgreSQL',
            'database_version': '16',
            'entities': [
                {'name': 'Member', 'description': 'A library patron.', 'attributes': [
                    {'name': 'member_id', 'data_type': 'INTEGER', 'key': 'PK', 'constraints': 'NOT NULL', 'description': 'Member identifier'}
                ]},
                {'name': 'Book', 'description': 'A catalogued book.', 'attributes': [
                    {'name': 'book_id', 'data_type': 'INTEGER', 'key': 'PK', 'constraints': 'NOT NULL', 'description': 'Book identifier'}
                ]},
            ],
            'relationships': [{
                'from_entity': 'Member', 'to_entity': 'Book', 'cardinality': 'M:N through Loan',
                'description': 'Members borrow books through loan records.',
            }],
            'normalization': 'Loan data is separated from member and book data to remove repeating groups.',
            'tables': [
                {'name': 'members', 'purpose': 'Stores patrons.', 'columns': [
                    {'name': 'member_id', 'data_type': 'INTEGER', 'constraints': 'PRIMARY KEY', 'references': '', 'description': 'Identifier'}
                ]},
                {'name': 'books', 'purpose': 'Stores books.', 'columns': [
                    {'name': 'book_id', 'data_type': 'INTEGER', 'constraints': 'PRIMARY KEY', 'references': '', 'description': 'Identifier'}
                ]},
            ],
            'schema_sql': 'CREATE TABLE members (member_id INTEGER PRIMARY KEY);\nCREATE TABLE books (book_id INTEGER PRIMARY KEY);',
            'use_cases': [{
                'id': 'uc-1', 'title': 'List available books',
                'problem_statement': 'Display books currently available for issue.',
                'sql': 'SELECT book_id FROM books ORDER BY book_id;',
                'output_description': 'Expected output lists each available book identifier; execution was not independently verified.',
                'concepts_covered': ['SELECT', 'ORDER BY'],
                'screenshot_filename': '',
            }],
        },
        'figures': [{'filename': 'erd.png', 'caption': 'Library E-R diagram', 'section': 'erd', 'use_case_id': ''}],
        'evidence': [{'claim': 'Schema creates members and books', 'paths': ['schema.sql']}],
        'missing_information': [],
    }


@pytest.fixture
def client():
    app_module.app.config['TESTING'] = True
    with app_module.app.test_client() as test_client:
        yield test_client


def generate_supplied(client, context):
    return client.post('/api/project/generate', json={
        'submode': context['submode'],
        'context': context,
        'api_key': 'test-key',
    })


def image_payload(filename, size=(320, 180), caption='Project figure', section='results'):
    buffer = io.BytesIO()
    Image.new('RGB', size, 'navy').save(buffer, format='PNG')
    return {
        'filename': filename,
        'media_type': 'image/png',
        'data_base64': base64.b64encode(buffer.getvalue()).decode('ascii'),
        'caption': caption,
        'section': section,
        'use_case_id': '',
    }


def frontend_images(context, first_size=(320, 180)):
    return [
        image_payload(
            result['screenshot_filename'],
            size=first_size if index == 0 else (320, 180),
            caption=result['title'],
        )
        for index, result in enumerate(context['project']['results'])
    ]


def test_collector_prompts_are_mode_specific_and_read_only(client):
    frontend = client.get('/api/project/prompt/frontend')
    assert frontend.status_code == 200
    prompt = frontend.get_json()['prompt']
    assert 'Work read-only' in prompt
    assert 'Never reveal secrets' in prompt
    assert '10-15 evidence-based Results entries' in prompt
    assert '"schema_version": 1' in prompt

    dbms = client.get('/api/project/prompt/dbms').get_json()['prompt']
    assert 'CREATE TABLE' in dbms
    assert 'relationships and cardinalities' in dbms
    assert client.get('/api/project/prompt/java').status_code == 400


def test_validate_accepts_fenced_json_and_reports_generation_need(client):
    context = frontend_context(with_sections=False)
    response = client.post('/api/project/validate', json={
        'submode': 'frontend',
        'context': f"```json\n{json.dumps(context)}\n```",
    })
    data = response.get_json()
    assert response.status_code == 200
    assert data['valid'] is True
    assert set(data['generation_needed']) == set(context['report_sections'])
    assert data['normalized']['project']['title'] == 'Student Portal'


def test_validate_rejects_mode_mismatch_and_missing_facts(client):
    context = frontend_context()
    mismatch = client.post('/api/project/validate', json={'submode': 'dbms', 'context': context}).get_json()
    assert mismatch['valid'] is False
    assert any('but dbms is selected' in error for error in mismatch['errors'])

    context['project']['objectives'] = []
    missing = client.post('/api/project/validate', json={'submode': 'frontend', 'context': context}).get_json()
    assert missing['valid'] is False
    assert 'project.objectives' in missing['missing_fields']

    short = frontend_context()
    short['report_sections']['abstract'] = 'A short abstract.'
    short['project']['results'].pop()
    data = client.post('/api/project/validate', json={'submode': 'frontend', 'context': short}).get_json()
    assert 'abstract' not in data['generation_needed']
    assert any('10-15' in field for field in data['missing_fields'])


def test_dbms_validation_rejects_hollow_records_and_commented_schema(client):
    context = dbms_context()
    context['dbms']['entities'][0]['attributes'][0]['data_type'] = ''
    context['dbms']['relationships'][0]['cardinality'] = ''
    context['dbms']['tables'][0]['columns'][0]['name'] = ''
    context['dbms']['schema_sql'] = '-- TODO: add CREATE TABLE statements'
    data = client.post('/api/project/validate', json={'submode': 'dbms', 'context': context}).get_json()
    assert data['valid'] is False
    assert 'dbms.entities[0].attributes[0].data_type' in data['missing_fields']
    assert 'dbms.relationships[0].cardinality' in data['missing_fields']
    assert 'dbms.tables[0].columns[0].name' in data['missing_fields']
    assert any('CREATE TABLE' in field for field in data['missing_fields'])

    incomplete_ddl = dbms_context()
    incomplete_ddl['dbms']['schema_sql'] = 'CREATE TABLE members (member_id INTEGER PRIMARY KEY);'
    data = client.post('/api/project/validate', json={
        'submode': 'dbms', 'context': incomplete_ddl,
    }).get_json()
    assert any('missing CREATE TABLE for books' in field for field in data['missing_fields'])

    token_only = dbms_context()
    token_only['dbms']['schema_sql'] = (
        "SELECT 'CREATE TABLE books (book_id INTEGER)' AS note;\n"
        'CREATE TABLE members\nCREATE TABLE books'
    )
    data = client.post('/api/project/validate', json={
        'submode': 'dbms', 'context': token_only,
    }).get_json()
    assert data['valid'] is False
    assert any('must include CREATE TABLE' in field for field in data['missing_fields'])

    collision = dbms_context()
    collision['dbms']['use_cases'][0]['screenshot_filename'] = 'erd.png'
    data = client.post('/api/project/validate', json={
        'submode': 'dbms', 'context': collision,
    }).get_json()
    assert data['valid'] is False
    assert any('already assigned to erd' in error for error in data['errors'])


def test_context_rejects_unknown_figure_sections_and_more_than_image_limit(client):
    context = frontend_context()
    context['figures'].append({
        'filename': 'testing.png', 'caption': 'Testing', 'section': 'testing', 'use_case_id': '',
    })
    data = client.post('/api/project/validate', json={'submode': 'frontend', 'context': context}).get_json()
    assert data['valid'] is False
    assert any('figures[1].section' in error for error in data['errors'])

    context = frontend_context()
    context['figures'].extend({
        'filename': f'design-{index}.png', 'caption': f'Design {index}',
        'section': 'design', 'use_case_id': '',
    } for index in range(1, 8))
    data = client.post('/api/project/validate', json={'submode': 'frontend', 'context': context}).get_json()
    assert data['valid'] is False
    assert any('export limit is 16' in error for error in data['errors'])


def test_complete_context_skips_fallback_llm(client, monkeypatch):
    monkeypatch.setattr(app_module, 'run_completion', lambda *args, **kwargs: pytest.fail('LLM must not run'))
    response = generate_supplied(client, frontend_context())
    assert response.status_code == 200
    data = response.get_json()
    assert data['generated_sections'] == []
    assert data['report']['generation']['source'] == 'supplied'


def test_hybrid_generation_only_completes_blank_sections(client, monkeypatch):
    context = frontend_context()
    supplied_abstract = context['report_sections']['abstract']
    context['report_sections']['design_methodology'] = ''
    calls = []

    def fake_completion(messages, *args, **kwargs):
        calls.append(messages)
        return json.dumps({'report_sections': {
            'design_methodology': prose('completed methodology', 820)
        }})

    monkeypatch.setattr(app_module, 'run_completion', fake_completion)
    response = generate_supplied(client, context)
    assert response.status_code == 200
    report = response.get_json()['report']
    assert report['report_sections']['abstract'] == supplied_abstract
    assert report['generation']['source'] == 'hybrid'
    assert report['generation']['generated_fields'] == ['design_methodology']
    assert 'screenshots' in calls[0][0]['content'].lower()


def test_malformed_hybrid_output_is_a_gateway_error(client, monkeypatch):
    context = dbms_context()
    context['report_sections']['erd_description'] = ''
    monkeypatch.setattr(app_module, 'run_completion', lambda *args, **kwargs: '{invalid json')
    response = generate_supplied(client, context)
    assert response.status_code == 502
    assert 'valid JSON' in response.get_json()['error']


def test_frontend_download_uses_required_order_and_styles(client):
    context = frontend_context()
    report = generate_supplied(client, context).get_json()['report']
    response = client.post('/api/project/download', json={
        'submode': 'frontend', 'report': report,
        'images': frontend_images(context),
        'output_filename': '../Student Portal?.docx',
    })
    assert response.status_code == 200
    assert response.data[:2] == b'PK'
    assert 'Student_Portal.docx' in response.headers['Content-Disposition']

    document = Document(io.BytesIO(response.data))
    texts = [paragraph.text for paragraph in document.paragraphs]
    assert texts.index('Abstract') < texts.index('Table of Contents') < texts.index('1. Introduction')
    assert texts.index('1. Introduction') < texts.index('2. Problem Definition and Requirements')
    assert texts.index('2. Problem Definition and Requirements') < texts.index('3. Proposed Design / Methodology') < texts.index('4. Results')
    heading = next(paragraph for paragraph in document.paragraphs if paragraph.text == '1. Introduction')
    assert heading.runs[-1].font.size.pt == 16
    assert heading.runs[-1].bold is True
    body = next(
        paragraph for paragraph in document.paragraphs
        if paragraph.text == context['report_sections']['introduction_background']
    )
    assert body.alignment == WD_ALIGN_PARAGRAPH.JUSTIFY
    assert body.paragraph_format.line_spacing == 1.5
    table_caption = next(paragraph for paragraph in document.paragraphs if paragraph.text.startswith('Table 1:'))
    assert table_caption.runs[0].font.size.pt == 12
    assert all(run.font.size.pt == 12 for run in document.tables[0].rows[1].cells[0].paragraphs[0].runs)
    assert any('State Management' in text for text in texts)
    assert any('Responsive Design' in text for text in texts)
    assert any('Accessibility' in text for text in texts)


def test_dbms_download_contains_erd_schema_sql_and_use_cases(client):
    report = generate_supplied(client, dbms_context()).get_json()['report']
    response = client.post('/api/project/download', json={
        'submode': 'dbms', 'report': report,
        'images': [image_payload('erd.png', caption='Library E-R diagram', section='erd')],
    })
    assert response.status_code == 200
    document = Document(io.BytesIO(response.data))
    text = '\n'.join(paragraph.text for paragraph in document.paragraphs)
    assert '2. E-R Diagram' in text
    assert '3. Database Schema' in text
    assert 'CREATE TABLE members' in text
    assert '4.1 List available books' in text
    assert 'SELECT book_id FROM books' in text
    assert '3.1 Database Platform' in text
    assert '3.5 Database Definition SQL' in text
    caption = next(paragraph for paragraph in document.paragraphs if paragraph.text.startswith('Table 1:'))
    assert caption.runs[0].font.size.pt == 10
    assert all(run.font.size.pt == 12 for run in document.tables[0].rows[1].cells[0].paragraphs[0].runs)


def test_verified_image_is_embedded_and_invalid_data_is_rejected(client):
    context = frontend_context()
    report = generate_supplied(client, context).get_json()['report']
    images = frontend_images(context, first_size=(300, 1200))
    response = client.post('/api/project/download', json={
        'submode': 'frontend', 'report': report, 'images': images,
    })
    assert response.status_code == 200
    document = Document(io.BytesIO(response.data))
    assert len(document.inline_shapes) == 10
    assert document.inline_shapes[0].height <= Cm(18.5)

    missing = client.post('/api/project/download', json={
        'submode': 'frontend', 'report': report, 'images': [],
    })
    assert missing.status_code == 400
    assert 'Missing uploaded images' in missing.get_json()['error']

    images[0]['data_base64'] = 'https://untrusted.example/image.png'
    rejected = client.post('/api/project/download', json={
        'submode': 'frontend', 'report': report, 'images': images,
    })
    assert rejected.status_code == 400
    assert 'base64' in rejected.get_json()['error']

    unrelated = image_payload('unrelated.png')
    rejected = client.post('/api/project/download', json={
        'submode': 'frontend', 'report': report, 'images': [unrelated],
    })
    assert rejected.status_code == 400
    assert 'not declared' in rejected.get_json()['error']


def test_malformed_report_submode_and_bounded_gzip_are_client_errors(client, monkeypatch):
    response = client.post('/api/project/download', json={
        'report': {'schema_version': 1, 'context': {}, 'report_sections': {}},
    })
    assert response.status_code == 400
    assert 'submode' in response.get_json()['error']

    monkeypatch.setattr(app_module, 'MAX_DECOMPRESSED_DOWNLOAD_BYTES', 100)
    response = client.post(
        '/api/project/download',
        data=gzip.compress(b'x' * 101),
        headers={'Content-Type': 'application/json', 'X-Content-Encoding': 'gzip'},
    )
    assert response.status_code == 400
    assert 'too large after decompression' in response.get_json()['error'].lower()


@pytest.mark.parametrize('submode,factory', [('frontend', frontend_context), ('dbms', dbms_context)])
def test_compact_context_section_workflow_through_download(client, monkeypatch, submode, factory):
    context = factory(with_sections=False)
    context['report_sections'] = {}
    calls = []
    def completion(messages, *args, **kwargs):
        calls.append(messages)
        assert 'plain prose' in messages[0]['content']
        assert '"report_sections"' not in messages[1]['content']
        return prose('Verified project facts explain the implementation', 160)
    monkeypatch.setattr(app_module, 'run_completion', completion)
    validation = client.post('/api/project/validate', json={'submode': submode, 'context': context}).get_json()
    assert validation['valid']
    completed = {}
    for field in validation['generation_needed']:
        response = client.post('/api/project/section', json={
            'submode': submode, 'context': validation['normalized'], 'section': field, 'api_key': 'test-key',
        })
        assert response.status_code == 200, response.get_json()
        completed[field] = response.get_json()['text']
    monkeypatch.setattr(app_module, 'run_completion', lambda *a, **k: pytest.fail('Assembly must not call an LLM'))
    response = client.post('/api/project/generate', json={
        'submode': submode, 'context': context, 'completed_sections': completed,
    })
    assert response.status_code == 200, response.get_json()
    report = response.get_json()['report']
    assert set(report['generation']['generated_fields']) == set(completed)
    images = frontend_images(context) if submode == 'frontend' else [image_payload('erd.png', section='erd')]
    download = client.post('/api/project/download', json={'submode': submode, 'report': report, 'images': images})
    assert download.status_code == 200
    assert len(Document(io.BytesIO(download.data)).inline_shapes) == len(images)


def test_context_repairs_wrappers_trailing_commas_and_literal_newlines(client):
    context = dbms_context(with_sections=False)
    context['project']['summary'] = 'A quoted "title", } and a backslash \\ stay intact.'
    raw = json.dumps(context, ensure_ascii=False, indent=2)
    raw = raw.replace('PRIMARY KEY);\\nCREATE', 'PRIMARY KEY);\nCREATE')
    raw = raw.rstrip()[:-1] + ',}'
    response = client.post('/api/project/validate', json={'submode': 'dbms', 'context': '\ufeff```json\n' + raw + '\n```'})
    result = response.get_json()
    assert result['valid'], result['errors']
    assert result['normalized']['project']['summary'] == context['project']['summary']
    assert result['normalized']['dbms']['schema_sql'] == context['dbms']['schema_sql']


def test_truncated_context_gives_recovery_instructions(client):
    raw = json.dumps(frontend_context())[:-70]
    result = client.post('/api/project/validate', json={'submode': 'frontend', 'context': raw}).get_json()
    assert not result['valid']
    assert 'line ' in result['errors'][0]
    assert 'compact facts-only' in result['errors'][0]


def test_supplied_short_section_is_not_replaced(client, monkeypatch):
    context = frontend_context()
    context['report_sections']['abstract'] = 'A concise supplied abstract.'
    monkeypatch.setattr(app_module, 'run_completion', lambda *a, **k: pytest.fail('Supplied prose must be preserved'))
    response = generate_supplied(client, context)
    assert response.status_code == 200
    assert response.get_json()['report']['report_sections']['abstract'] == 'A concise supplied abstract.'


def test_section_requires_valid_field_and_rejects_incomplete_output(client, monkeypatch):
    context = dbms_context(with_sections=False)
    response = client.post('/api/project/section', json={'submode': 'dbms', 'context': context, 'section': 'sql'})
    assert response.status_code == 400
    monkeypatch.setattr(app_module, 'run_completion', lambda *a, **k: 'Too short.')
    response = client.post('/api/project/section', json={'submode': 'dbms', 'context': context, 'section': 'introduction', 'api_key': 'test-key'})
    assert response.status_code == 502
