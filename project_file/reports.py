"""Pure Project File schemas, collector prompts, and hybrid report assembly."""

import copy
import json
import re


SCHEMA_VERSION = 1
SUPPORTED_SUBMODES = ('frontend', 'dbms')
MAX_CONTEXT_CHARS = 300_000
MAX_SECTION_CHARS = 45_000
MIN_SECTION_CHARS = 40
MAX_PROJECT_IMAGES = 16
FRONTEND_RESULT_MIN = 10
FRONTEND_RESULT_MAX = 15

REPORT_SECTION_FIELDS = {
    'frontend': (
        'abstract',
        'introduction_background',
        'introduction_significance',
        'problem_definition',
        'design_methodology',
        'results_summary',
    ),
    'dbms': (
        'introduction',
        'erd_description',
        'schema_description',
        'use_cases_introduction',
    ),
}

REPORT_FORMATS = {
    'frontend': {
        'label': 'Frontend Project Report',
        'sections': [
            'Title Page (provisional until the institutional template is supplied)',
            'Abstract',
            'Table of Contents',
            '1. Introduction (3-5 pages)',
            '2. Problem Definition and Requirements (1-2 pages)',
            '3. Proposed Design / Methodology (3-5 pages)',
            '4. Results with screenshots (10-15 pages)',
            'References (optional; citation style intentionally unspecified)',
        ],
        'caption_size': 12,
    },
    'dbms': {
        'label': 'DBMS Project Report',
        'sections': [
            'Title Page (provisional until the institutional template is supplied)',
            'Table of Contents',
            '1. Introduction',
            '2. E-R Diagram',
            '3. Database Schema',
            '4. Use Cases with SQL and corresponding output/screenshots',
        ],
        'caption_size': 10,
    },
}

SECTION_WORD_GUIDANCE = {
    'frontend': {
        'abstract': 200,
        'introduction_background': 700,
        'introduction_significance': 250,
        'problem_definition': 400,
        'design_methodology': 800,
        'results_summary': 250,
    },
    'dbms': {
        'introduction': 500,
        'erd_description': 300,
        'schema_description': 300,
        'use_cases_introduction': 150,
    },
}

_INVALID_XML_CONTROLS = re.compile(r'[\x00-\x08\x0B\x0C\x0E-\x1F]')


class ProjectContextError(ValueError):
    """The submitted context pack or report is invalid."""


class ProjectGenerationError(ValueError):
    """The fallback LLM did not return the requested report sections."""


def _clean_text(value, path, errors, max_chars=20_000):
    if value is None:
        return ''
    if not isinstance(value, str):
        errors.append(f'{path} must be text.')
        return ''
    value = _INVALID_XML_CONTROLS.sub('', value).strip()
    if len(value) > max_chars:
        errors.append(f'{path} exceeds the {max_chars:,}-character limit.')
        return value[:max_chars]
    return value


def _minimum_section_words(field):
    for guidance in SECTION_WORD_GUIDANCE.values():
        if field in guidance:
            return guidance[field]
    return 1


def _section_is_complete(field, value):
    # Word counts are writing guidance, not a reason to discard supplied prose.
    return bool(str(value or '').strip())


def _created_table_names(sql):
    """Return table names from structurally complete CREATE TABLE definitions.

    This is intentionally a validator rather than a SQL parser: comments and string
    literals are removed, and a definition counts only when it has a non-empty,
    balanced parenthesized body.
    """
    text = re.sub(r'/\*.*?\*/', ' ', sql or '', flags=re.DOTALL)
    text = re.sub(r'--[^\n]*', ' ', text)
    text = re.sub(r"'(?:''|[^'])*'", ' ', text)
    pattern = re.compile(
        r'\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?'
        r'([A-Za-z0-9_`"\[\].$]+)\s*\(',
        re.IGNORECASE,
    )
    names = set()
    for match in pattern.finditer(text):
        depth = 1
        cursor = match.end()
        while cursor < len(text) and depth:
            if text[cursor] == '(':
                depth += 1
            elif text[cursor] == ')':
                depth -= 1
            cursor += 1
        if depth:
            continue
        body = text[match.end():cursor - 1].strip()
        if not body:
            continue
        names.add(match.group(1).split('.')[-1].strip('`"[]').lower())
    return names


def _object(value, path, errors):
    if value is None:
        return {}
    if not isinstance(value, dict):
        errors.append(f'{path} must be an object.')
        return {}
    return value


def _text_list(value, path, errors, max_items=100, item_max=2_000):
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append(f'{path} must be an array of text values.')
        return []
    if len(value) > max_items:
        errors.append(f'{path} cannot contain more than {max_items} items.')
    result = []
    for index, item in enumerate(value[:max_items]):
        text = _clean_text(item, f'{path}[{index}]', errors, item_max)
        if text:
            result.append(text)
    return result


def _record_list(value, path, fields, errors, max_items=100, field_max=6_000):
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append(f'{path} must be an array of objects.')
        return []
    if len(value) > max_items:
        errors.append(f'{path} cannot contain more than {max_items} items.')
    records = []
    for index, item in enumerate(value[:max_items]):
        item = _object(item, f'{path}[{index}]', errors)
        record = {
            field: _clean_text(item.get(field), f'{path}[{index}].{field}', errors, field_max)
            for field in fields
        }
        if any(record.values()):
            records.append(record)
    return records


def _extract_json(raw):
    if isinstance(raw, dict):
        return copy.deepcopy(raw)
    if not isinstance(raw, str):
        raise ProjectContextError('Context must be a JSON object or pasted JSON text.')
    if len(raw) > MAX_CONTEXT_CHARS:
        raise ProjectContextError(f'Context exceeds the {MAX_CONTEXT_CHARS:,}-character limit.')

    text = raw.lstrip('\ufeff').strip()
    marker_match = re.search(
        r'PROJECT_CONTEXT_START\s*(.*?)\s*PROJECT_CONTEXT_END', text, re.DOTALL | re.IGNORECASE,
    )
    if marker_match:
        text = marker_match.group(1).strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text, count=1, flags=re.IGNORECASE)
        text = re.sub(r'\s*```$', '', text, count=1)

    start = text.find('{')
    if start < 0:
        raise ProjectContextError('No JSON object was found in the pasted response.')
    text = text[start:]
    # Remove trailing commas outside strings only. SQL, quotes and backslashes
    # inside values must never be rewritten or guessed.
    text = re.sub(r'("(?:\\.|[^"\\])*"\s*)|,\s*(?=[}\]])',
                  lambda match: match.group(1) or '', text, flags=re.DOTALL)
    try:
        parsed, end = json.JSONDecoder(strict=False).raw_decode(text)
    except json.JSONDecodeError as err:
        raise ProjectContextError(
            f'Context JSON is invalid at line {err.lineno}, column {err.colno}: {err.msg}. '
            'The response may be cut off or contain an unescaped quote/backslash. '
            'Request the compact facts-only context again, or import the complete .json file. '
            'Do not paste a partial response.'
        ) from err
    if text[end:].strip().startswith(('{', ',')):
        raise ProjectContextError('Multiple JSON objects found. Import one complete context pack at a time.')
    if not isinstance(parsed, dict):
        raise ProjectContextError('The context JSON root must be an object.')
    return parsed


def _normalize_common(data, submode, errors):
    project = _object(data.get('project'), 'project', errors)
    student = _object(data.get('student'), 'student', errors)
    sections = _object(data.get('report_sections'), 'report_sections', errors)

    normalized_project = {
        'title': _clean_text(project.get('title'), 'project.title', errors, 200),
        'summary': _clean_text(project.get('summary'), 'project.summary', errors),
        'problem_statement': _clean_text(
            project.get('problem_statement'), 'project.problem_statement', errors,
        ),
        'objectives': _text_list(project.get('objectives'), 'project.objectives', errors, 20, 1_000),
        'significance': _clean_text(project.get('significance'), 'project.significance', errors),
        'software_requirements': _text_list(
            project.get('software_requirements'), 'project.software_requirements', errors, 40,
        ),
        'hardware_requirements': _text_list(
            project.get('hardware_requirements'), 'project.hardware_requirements', errors, 40,
        ),
        'datasets': _text_list(project.get('datasets'), 'project.datasets', errors, 40),
        'methodology': _clean_text(project.get('methodology'), 'project.methodology', errors),
        'architecture': _clean_text(project.get('architecture'), 'project.architecture', errors),
        'file_structure': _record_list(
            project.get('file_structure'), 'project.file_structure', ('path', 'purpose'), errors, 250,
        ),
        'results': [],
        'references': _text_list(project.get('references'), 'project.references', errors, 80, 4_000),
    }

    result_items = project.get('results')
    if result_items is None:
        result_items = []
    if not isinstance(result_items, list):
        errors.append('project.results must be an array of objects.')
        result_items = []
    if len(result_items) > 30:
        errors.append('project.results cannot contain more than 30 items.')
    for index, item in enumerate(result_items[:30]):
        item = _object(item, f'project.results[{index}]', errors)
        result = {
            'id': _clean_text(item.get('id'), f'project.results[{index}].id', errors, 100),
            'title': _clean_text(item.get('title'), f'project.results[{index}].title', errors, 300),
            'description': _clean_text(
                item.get('description'), f'project.results[{index}].description', errors, 12_000,
            ),
            'evidence': _text_list(
                item.get('evidence'), f'project.results[{index}].evidence', errors, 30, 2_000,
            ),
            'screenshot_filename': _clean_text(
                item.get('screenshot_filename'),
                f'project.results[{index}].screenshot_filename', errors, 260,
            ),
        }
        if any((result['id'], result['title'], result['description'], result['evidence'],
                result['screenshot_filename'])):
            normalized_project['results'].append(result)

    normalized_student = {
        key: _clean_text(student.get(key), f'student.{key}', errors, 200)
        for key in (
            'name', 'roll_number', 'class_section', 'institution', 'department', 'course',
            'academic_session',
        )
    }
    normalized_sections = {
        field: _clean_text(sections.get(field), f'report_sections.{field}', errors, MAX_SECTION_CHARS)
        for field in REPORT_SECTION_FIELDS[submode]
    }

    figures = _record_list(
        data.get('figures'), 'figures',
        ('filename', 'caption', 'section', 'use_case_id'), errors, 30, 1_000,
    )
    evidence = []
    raw_evidence = data.get('evidence')
    if raw_evidence is None:
        raw_evidence = []
    if not isinstance(raw_evidence, list):
        errors.append('evidence must be an array of objects.')
        raw_evidence = []
    if len(raw_evidence) > 200:
        errors.append('evidence cannot contain more than 200 items.')
    for index, item in enumerate(raw_evidence[:200]):
        item = _object(item, f'evidence[{index}]', errors)
        record = {
            'claim': _clean_text(item.get('claim'), f'evidence[{index}].claim', errors, 4_000),
            'paths': _text_list(item.get('paths'), f'evidence[{index}].paths', errors, 30, 1_000),
        }
        if record['claim'] or record['paths']:
            evidence.append(record)

    return {
        'schema_version': SCHEMA_VERSION,
        'submode': submode,
        'project': normalized_project,
        'student': normalized_student,
        'report_sections': normalized_sections,
        'figures': figures,
        'evidence': evidence,
        'missing_information': _text_list(
            data.get('missing_information'), 'missing_information', errors, 100, 2_000,
        ),
    }


def _normalize_frontend(data, errors):
    raw = _object(data.get('frontend'), 'frontend', errors)
    return {
        'stack': _record_list(raw.get('stack'), 'frontend.stack', ('name', 'version', 'purpose'), errors, 50),
        'pages': _record_list(raw.get('pages'), 'frontend.pages', ('name', 'route', 'purpose'), errors, 100),
        'components': _record_list(
            raw.get('components'), 'frontend.components', ('name', 'purpose', 'source_path'), errors, 200,
        ),
        'state_management': _clean_text(raw.get('state_management'), 'frontend.state_management', errors),
        'api_integrations': _record_list(
            raw.get('api_integrations'), 'frontend.api_integrations',
            ('name', 'purpose', 'endpoint'), errors, 100,
        ),
        'responsive_design': _clean_text(raw.get('responsive_design'), 'frontend.responsive_design', errors),
        'accessibility': _clean_text(raw.get('accessibility'), 'frontend.accessibility', errors),
        'testing': _clean_text(raw.get('testing'), 'frontend.testing', errors),
        'deployment': _clean_text(raw.get('deployment'), 'frontend.deployment', errors),
    }


def _normalize_dbms(data, errors):
    raw = _object(data.get('dbms'), 'dbms', errors)
    entities = []
    raw_entities = raw.get('entities') or []
    if not isinstance(raw_entities, list):
        errors.append('dbms.entities must be an array of objects.')
        raw_entities = []
    if len(raw_entities) > 100:
        errors.append('dbms.entities cannot contain more than 100 items.')
    for index, item in enumerate(raw_entities[:100]):
        item = _object(item, f'dbms.entities[{index}]', errors)
        entity = {
            'name': _clean_text(item.get('name'), f'dbms.entities[{index}].name', errors, 200),
            'description': _clean_text(
                item.get('description'), f'dbms.entities[{index}].description', errors, 4_000,
            ),
            'attributes': _record_list(
                item.get('attributes'), f'dbms.entities[{index}].attributes',
                ('name', 'data_type', 'key', 'constraints', 'description'), errors, 100, 2_000,
            ),
        }
        if entity['name'] or entity['description'] or entity['attributes']:
            entities.append(entity)

    tables = []
    raw_tables = raw.get('tables') or []
    if not isinstance(raw_tables, list):
        errors.append('dbms.tables must be an array of objects.')
        raw_tables = []
    if len(raw_tables) > 100:
        errors.append('dbms.tables cannot contain more than 100 items.')
    for index, item in enumerate(raw_tables[:100]):
        item = _object(item, f'dbms.tables[{index}]', errors)
        table = {
            'name': _clean_text(item.get('name'), f'dbms.tables[{index}].name', errors, 200),
            'purpose': _clean_text(item.get('purpose'), f'dbms.tables[{index}].purpose', errors, 4_000),
            'columns': _record_list(
                item.get('columns'), f'dbms.tables[{index}].columns',
                ('name', 'data_type', 'constraints', 'references', 'description'), errors, 150, 2_000,
            ),
        }
        if table['name'] or table['purpose'] or table['columns']:
            tables.append(table)

    use_cases = []
    raw_use_cases = raw.get('use_cases') or []
    if not isinstance(raw_use_cases, list):
        errors.append('dbms.use_cases must be an array of objects.')
        raw_use_cases = []
    if len(raw_use_cases) > 40:
        errors.append('dbms.use_cases cannot contain more than 40 items.')
    for index, item in enumerate(raw_use_cases[:40]):
        item = _object(item, f'dbms.use_cases[{index}]', errors)
        use_case = {
            'id': _clean_text(item.get('id'), f'dbms.use_cases[{index}].id', errors, 100),
            'title': _clean_text(item.get('title'), f'dbms.use_cases[{index}].title', errors, 300),
            'problem_statement': _clean_text(
                item.get('problem_statement'), f'dbms.use_cases[{index}].problem_statement', errors, 8_000,
            ),
            'sql': _clean_text(item.get('sql'), f'dbms.use_cases[{index}].sql', errors, 30_000),
            'output_description': _clean_text(
                item.get('output_description'), f'dbms.use_cases[{index}].output_description', errors, 8_000,
            ),
            'concepts_covered': _text_list(
                item.get('concepts_covered'), f'dbms.use_cases[{index}].concepts_covered', errors, 30,
            ),
            'screenshot_filename': _clean_text(
                item.get('screenshot_filename'),
                f'dbms.use_cases[{index}].screenshot_filename', errors, 260,
            ),
        }
        if any((use_case['id'], use_case['title'], use_case['problem_statement'], use_case['sql'],
                use_case['output_description'], use_case['concepts_covered'],
                use_case['screenshot_filename'])):
            use_cases.append(use_case)

    return {
        'database_engine': _clean_text(raw.get('database_engine'), 'dbms.database_engine', errors, 100),
        'database_version': _clean_text(raw.get('database_version'), 'dbms.database_version', errors, 100),
        'entities': entities,
        'relationships': _record_list(
            raw.get('relationships'), 'dbms.relationships',
            ('from_entity', 'to_entity', 'cardinality', 'description'), errors, 200, 4_000,
        ),
        'normalization': _clean_text(raw.get('normalization'), 'dbms.normalization', errors, 15_000),
        'tables': tables,
        'schema_sql': _clean_text(raw.get('schema_sql'), 'dbms.schema_sql', errors, 45_000),
        'use_cases': use_cases,
    }


def validate_project_context(raw, expected_submode=None):
    """Parse and normalize an external-agent context pack without inventing data."""
    response = {
        'valid': False,
        'normalized': None,
        'errors': [],
        'missing_fields': [],
        'warnings': [],
        'generation_needed': [],
    }
    try:
        data = _extract_json(raw)
    except ProjectContextError as err:
        response['errors'].append(str(err))
        return response

    try:
        serialized_size = len(json.dumps(data, ensure_ascii=False))
    except (TypeError, ValueError):
        response['errors'].append('Context contains values that cannot be represented as JSON.')
        return response
    if serialized_size > MAX_CONTEXT_CHARS:
        response['errors'].append(f'Context exceeds the {MAX_CONTEXT_CHARS:,}-character limit.')
        return response

    version = data.get('schema_version')
    if version != SCHEMA_VERSION:
        response['errors'].append(f'schema_version must be {SCHEMA_VERSION}.')

    submode = data.get('submode')
    if not isinstance(submode, str):
        submode = ''
    submode = submode.strip().lower()
    if expected_submode and expected_submode not in SUPPORTED_SUBMODES:
        response['errors'].append(f'Unsupported project sub-mode: {expected_submode}.')
        return response
    if submode not in SUPPORTED_SUBMODES:
        response['errors'].append('submode must be either "frontend" or "dbms".')
        return response
    if expected_submode and submode != expected_submode:
        response['errors'].append(
            f'This context pack is for {submode}, but {expected_submode} is selected.'
        )

    errors = response['errors']
    normalized = _normalize_common(data, submode, errors)
    if submode == 'frontend':
        normalized['frontend'] = _normalize_frontend(data, errors)
        normalized['dbms'] = None
    else:
        normalized['frontend'] = None
        normalized['dbms'] = _normalize_dbms(data, errors)

    project = normalized['project']
    required_text = (
        ('project.title', project['title']),
        ('project.summary', project['summary']),
        ('project.problem_statement', project['problem_statement']),
    )
    for path, value in required_text:
        if not value:
            response['missing_fields'].append(path)
    if not project['objectives']:
        response['missing_fields'].append('project.objectives')
    if not project['software_requirements']:
        response['missing_fields'].append('project.software_requirements')

    allowed_figure_sections = {
        'frontend': {'design', 'architecture', 'methodology', 'result', 'results'},
        'dbms': {'erd', 'e-r diagram', 'use_case', 'use case', 'result', 'results'},
    }[submode]
    for index, figure in enumerate(normalized['figures']):
        for field in ('filename', 'caption', 'section'):
            if not figure[field]:
                response['missing_fields'].append(f'figures[{index}].{field}')
        if figure['section'] and figure['section'].lower() not in allowed_figure_sections:
            response['errors'].append(
                f"figures[{index}].section must be one of: {', '.join(sorted(allowed_figure_sections))}."
            )

    if submode == 'frontend':
        frontend = normalized['frontend']
        if not frontend['stack']:
            response['missing_fields'].append('frontend.stack')
        if not frontend['pages']:
            response['missing_fields'].append('frontend.pages')
        if not project['results']:
            response['missing_fields'].append('project.results')
        for index, result in enumerate(project['results']):
            for field in ('title', 'description', 'screenshot_filename'):
                if not result[field]:
                    response['missing_fields'].append(f'project.results[{index}].{field}')
        for index, item in enumerate(frontend['stack']):
            if not item['name']:
                response['missing_fields'].append(f'frontend.stack[{index}].name')
        for index, item in enumerate(frontend['pages']):
            if not item['name']:
                response['missing_fields'].append(f'frontend.pages[{index}].name')
        screenshot_keys = [
            result['screenshot_filename'].replace('\\', '/').split('/')[-1].lower()
            for result in project['results'] if result['screenshot_filename']
        ]
        if len(screenshot_keys) != len(set(screenshot_keys)):
            response['errors'].append('Each Frontend result must declare a unique screenshot filename.')
        if not (FRONTEND_RESULT_MIN <= len(project['results']) <= FRONTEND_RESULT_MAX):
            response['missing_fields'].append(
                f'project.results ({FRONTEND_RESULT_MIN}-{FRONTEND_RESULT_MAX} screenshot-led entries required)'
            )
        if not frontend['components']:
            response['warnings'].append('No frontend components or modules were identified.')
    else:
        dbms = normalized['dbms']
        if not dbms['database_engine']:
            response['missing_fields'].append('dbms.database_engine')
        if len(dbms['entities']) < 2:
            response['missing_fields'].append('dbms.entities (at least 2)')
        for index, entity in enumerate(dbms['entities']):
            if not entity['name']:
                response['missing_fields'].append(f'dbms.entities[{index}].name')
            if not entity['attributes']:
                response['missing_fields'].append(f'dbms.entities[{index}].attributes')
            for attr_index, attribute in enumerate(entity['attributes']):
                for field in ('name', 'data_type'):
                    if not attribute[field]:
                        response['missing_fields'].append(
                            f'dbms.entities[{index}].attributes[{attr_index}].{field}'
                        )
        if not dbms['relationships']:
            response['missing_fields'].append('dbms.relationships')
        for index, relationship in enumerate(dbms['relationships']):
            for field in ('from_entity', 'to_entity', 'cardinality'):
                if not relationship[field]:
                    response['missing_fields'].append(f'dbms.relationships[{index}].{field}')
        if len(dbms['tables']) < 2:
            response['missing_fields'].append('dbms.tables (at least 2)')
        for index, table in enumerate(dbms['tables']):
            if not table['name']:
                response['missing_fields'].append(f'dbms.tables[{index}].name')
            if not table['columns']:
                response['missing_fields'].append(f'dbms.tables[{index}].columns')
            for column_index, column in enumerate(table['columns']):
                for field in ('name', 'data_type'):
                    if not column[field]:
                        response['missing_fields'].append(
                            f'dbms.tables[{index}].columns[{column_index}].{field}'
                        )
        created_tables = _created_table_names(dbms['schema_sql'])
        if not created_tables:
            response['missing_fields'].append('dbms.schema_sql (must include CREATE TABLE)')
        for index, table in enumerate(dbms['tables']):
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_$]*', table['name'] or ''):
                response['missing_fields'].append(f'dbms.tables[{index}].name (valid SQL identifier required)')
            elif table['name'].lower() not in created_tables:
                response['missing_fields'].append(
                    f"dbms.schema_sql (missing CREATE TABLE for {table['name']})"
                )
        if not dbms['use_cases']:
            response['missing_fields'].append('dbms.use_cases')
        for index, use_case in enumerate(dbms['use_cases']):
            for field in ('title', 'problem_statement', 'sql', 'output_description'):
                if not use_case[field]:
                    response['missing_fields'].append(f'dbms.use_cases[{index}].{field}')
        if len(dbms['use_cases']) < 5:
            response['warnings'].append(
                'Only a few DBMS use cases were supplied. Confirm the required count against the CHO/rubric.'
            )
        if not any(
            figure['filename'] and figure['section'].lower() in ('erd', 'e-r diagram')
            for figure in normalized['figures']
        ):
            response['missing_fields'].append('figures (an ERD image filename is required)')

    image_roles = {}

    def register_image(filename, role, path):
        key = filename.replace('\\', '/').split('/')[-1].lower()
        if not key:
            return
        existing = image_roles.get(key)
        if existing and existing[0] != role:
            response['errors'].append(
                f'{path} reuses {filename} for {role}; it is already assigned to {existing[0]}.'
            )
        else:
            image_roles[key] = (role, path)

    section_roles = {
        'design': 'design', 'architecture': 'design', 'methodology': 'design',
        'result': 'results', 'results': 'results',
        'erd': 'erd', 'e-r diagram': 'erd',
        'use_case': 'use_case', 'use case': 'use_case',
    }
    for index, figure in enumerate(normalized['figures']):
        role = section_roles.get(figure['section'].lower(), figure['section'].lower())
        if role == 'use_case' and figure['use_case_id']:
            role = f"use_case:{figure['use_case_id'].lower()}"
        register_image(figure['filename'], role, f'figures[{index}].filename')
    if submode == 'frontend':
        for index, result in enumerate(project['results']):
            register_image(
                result['screenshot_filename'], 'results',
                f'project.results[{index}].screenshot_filename',
            )
    else:
        for index, use_case in enumerate(normalized['dbms']['use_cases']):
            role_id = (use_case['id'] or str(index + 1)).lower()
            register_image(
                use_case['screenshot_filename'], f'use_case:{role_id}',
                f'dbms.use_cases[{index}].screenshot_filename',
            )
    if len(image_roles) > MAX_PROJECT_IMAGES:
        response['errors'].append(
            f'Context declares {len(image_roles)} unique images; the export limit is {MAX_PROJECT_IMAGES}.'
        )

    if not project['significance']:
        response['warnings'].append('Project significance is blank; the report fallback will use the supplied facts.')
    if not project['architecture']:
        response['warnings'].append('No architecture description was supplied.')
    if normalized['missing_information']:
        response['warnings'].append(
            f"The collector reported {len(normalized['missing_information'])} unresolved item(s)."
        )

    response['generation_needed'] = [
        field for field, value in normalized['report_sections'].items()
        if not _section_is_complete(field, value)
    ]
    if response['generation_needed']:
        response['warnings'].append(
            'Blank report prose will be completed by the selected PractiGen model.'
        )

    response['normalized'] = normalized
    response['valid'] = not response['errors'] and not response['missing_fields']
    return response


def _common_template(submode):
    return {
        'schema_version': SCHEMA_VERSION,
        'submode': submode,
        'project': {
            'title': '',
            'summary': '',
            'problem_statement': '',
            'objectives': [],
            'significance': '',
            'software_requirements': [],
            'hardware_requirements': [],
            'datasets': [],
            'methodology': '',
            'architecture': '',
            'file_structure': [{'path': '', 'purpose': ''}],
            'results': [],
            'references': [],
        },
        'student': {
            'name': '',
            'roll_number': '',
            'class_section': '',
            'institution': '',
            'department': '',
            'course': '',
            'academic_session': '',
        },
        'report_sections': {field: '' for field in REPORT_SECTION_FIELDS[submode]},
        'figures': [
            {'filename': '', 'caption': '', 'section': '', 'use_case_id': ''},
        ],
        'evidence': [{'claim': '', 'paths': []}],
        'missing_information': [],
    }


def _frontend_template():
    template = _common_template('frontend')
    template['frontend'] = {
        'stack': [{'name': '', 'version': '', 'purpose': ''}],
        'pages': [{'name': '', 'route': '', 'purpose': ''}],
        'components': [{'name': '', 'purpose': '', 'source_path': ''}],
        'state_management': '',
        'api_integrations': [{'name': '', 'purpose': '', 'endpoint': ''}],
        'responsive_design': '',
        'accessibility': '',
        'testing': '',
        'deployment': '',
    }
    template['dbms'] = None
    template['project']['results'] = [{
        'id': '', 'title': '', 'description': '', 'evidence': [], 'screenshot_filename': '',
    }]
    return template


def _dbms_template():
    template = _common_template('dbms')
    template['frontend'] = None
    template['dbms'] = {
        'database_engine': '',
        'database_version': '',
        'entities': [{
            'name': '',
            'description': '',
            'attributes': [{
                'name': '', 'data_type': '', 'key': '', 'constraints': '', 'description': '',
            }],
        }],
        'relationships': [{
            'from_entity': '', 'to_entity': '', 'cardinality': '', 'description': '',
        }],
        'normalization': '',
        'tables': [{
            'name': '',
            'purpose': '',
            'columns': [{
                'name': '', 'data_type': '', 'constraints': '', 'references': '', 'description': '',
            }],
        }],
        'schema_sql': '',
        'use_cases': [{
            'id': '',
            'title': '',
            'problem_statement': '',
            'sql': '',
            'output_description': '',
            'concepts_covered': [],
            'screenshot_filename': '',
        }],
    }
    return template


def build_collector_prompt(submode):
    """Return the copy/paste prompt used in an existing repository-aware AI session."""
    if submode not in SUPPORTED_SUBMODES:
        raise ProjectContextError(f'Unsupported project sub-mode: {submode}.')

    if submode == 'frontend':
        template = _frontend_template()
        mode_rules = """
FRONTEND-SPECIFIC WORK:
- Inspect package manifests, routes/pages, components, styles, state, API clients, tests, build and deployment files.
- Describe the actual architecture, user workflows, responsive behaviour, accessibility and testing. Do not claim tests were run unless this session ran them successfully.
- Create 10-15 evidence-based Results entries. Each entry must describe a distinct visible page, state, workflow or test outcome and must name its corresponding screenshot.
- Set every screenshot_filename to a unique, exact image filename the user will later upload. Do not fabricate image data or claim a screenshot exists.
- For extra figures, use only "design" or "results" as the figures[].section value.
- Leave report_sections empty. PractiGen writes the report one section at a time after import.
"""
    else:
        template = _dbms_template()
        mode_rules = """
DBMS-SPECIFIC WORK:
- Inspect SQL files, migrations, ORM models, seed data, queries, procedures, views, triggers, tests and documentation.
- Identify entities, attributes, primary/foreign keys, constraints, relationships and cardinalities. Explain normalization only to the level supported by evidence.
- Copy complete CREATE TABLE statements into schema_sql using the project's actual SQL dialect.
- Define meaningful use cases from implemented queries. Every use case needs a precise problem statement, SQL, concepts covered, an honest output description, and an optional screenshot filename.
- Never claim SQL was executed unless this session actually executed it successfully. Mark unexecuted outputs as expected.
- Declare an ERD image with figures[].section set to "erd". Use "use_case" for any additional SQL output figure.
- Leave report_sections empty. PractiGen writes the report one section at a time after import.
"""

    schema = json.dumps(template, indent=2, ensure_ascii=True)
    return f"""You are preparing a PractiGen {submode.upper()} Project Context Pack from the repository already open in this AI session.

OPERATING RULES:
1. Work read-only. Do not create, edit, delete, commit, install, deploy, or reconfigure anything.
2. Never reveal secrets, API keys, credentials, private user data, or values from .env files. Mention only environment variable names when relevant.
3. Base every factual claim on repository evidence. Do not invent features, outputs, screenshots, execution results, datasets, references, or student details.
4. Inspect relevant README/documentation, manifests, source files, configuration, tests, and deployment files. Ignore dependency/vendor/build-output directories.
5. Use empty strings or empty arrays for unknown optional values and explain unresolved facts in missing_information.
6. Add concise repository paths to evidence. Do not paste entire source files.
7. Return compact facts only: keep descriptions to 1-2 sentences, omit unused optional keys and empty template records, and leave report_sections as {{}}. Aim for under 12,000 characters for Frontend or 24,000 for DBMS; never truncate required facts or SQL to meet that target. Do not include report-length prose or repeat result images in figures.
8. Return exactly one valid JSON object matching schema_version {SCHEMA_VERSION}. Do not wrap it in Markdown fences and do not add commentary before or after it.
9. Escape newlines as \\n, quotes as \\" and backslashes as \\\\ inside JSON strings, especially SQL. Close every string, array and object. If the response is too large, reduce optional descriptions before returning it; never split the object across messages.
{mode_rules}
REPORT CONVENTIONS:
- Times New Roman; heading 16 pt bold left; subheading 14 pt bold left; body 12 pt justified with 1.5 spacing.
- Number and caption every table and figure in sequence.
- The institutional title-page format is pending, so collect available student/institution metadata without inventing it.
- Reference style is intentionally unspecified for now; preserve verified reference details as plain strings.

OUTPUT THIS EXACT SHAPE, replacing empty values with verified project information:
{schema}
""".strip()


def get_report_format(submode):
    if submode not in SUPPORTED_SUBMODES:
        raise ProjectContextError(f'Unsupported project sub-mode: {submode}.')
    return copy.deepcopy(REPORT_FORMATS[submode])


SECTION_PARTS = {
    'abstract': [('Summarize the purpose, approach and supported outcomes.', '200-300')],
    'introduction_background': [
        ('Introduce the project domain and the documented existing problem.', '250-350'),
        ('Explain the users, their needs and the relevant technical context.', '250-350'),
        ('Explain the project scope and how the documented approach addresses those needs.', '250-350')],
    'introduction_significance': [('Explain the project significance and supported benefits.', '250-400')],
    'problem_definition': [
        ('Define the documented problem, affected users and constraints.', '200-350'),
        ('Explain the objectives and requirements that address the problem.', '200-350')],
    'design_methodology': [
        ('Explain the documented development approach and design rationale.', '200-325'),
        ('Explain the architecture, components and division of responsibilities.', '200-325'),
        ('Explain the implemented data flow, interfaces and user interactions.', '200-325'),
        ('Explain documented validation, accessibility, deployment and limitations where supported.', '200-325')],
    'results_summary': [('Summarize the supplied results and distinguish observations from expectations.', '250-400')],
    'introduction': [
        ('Introduce the domain, problem and database project objectives.', '250-400'),
        ('Explain the database scope, approach and supported benefits.', '250-400')],
    'erd_description': [
        ('Explain the documented entities, attributes and keys.', '150-300'),
        ('Explain relationships, cardinalities and supported design rationale.', '150-300')],
    'schema_description': [
        ('Explain the table structure, keys and constraints.', '150-300'),
        ('Explain how the schema implements relationships and supported normalization.', '150-300')],
    'use_cases_introduction': [('Introduce the documented use cases and their execution status.', '150-300')],
}


def get_section_parts(field):
    return SECTION_PARTS[field]


def build_generation_messages(context, missing_fields, part_index=None, previous_parts=None):
    submode = context['submode']
    allowed = REPORT_SECTION_FIELDS[submode]
    missing_fields = [field for field in missing_fields if field in allowed]
    if not missing_fields:
        return []

    targets = {
        'abstract': '200-300 words',
        'introduction_background': '700-1100 words',
        'introduction_significance': '250-450 words',
        'problem_definition': '400-700 words',
        'design_methodology': '800-1300 words',
        'results_summary': '250-500 words',
        'introduction': '500-900 words',
        'erd_description': '300-600 words',
        'schema_description': '300-600 words',
        'use_cases_introduction': '150-300 words',
    }
    requested = '\n'.join(f'- {field}: {targets[field]}' for field in missing_fields)
    continuation = ''
    if part_index is not None:
        field = missing_fields[0]
        parts = get_section_parts(field)
        focus, words = parts[part_index]
        requested = f'- {field}, part {part_index + 1} of {len(parts)}: {words} words. {focus}'
        continuation = ('Write only this part, as complete paragraphs. Do not repeat earlier parts or '
                        'write a conclusion for the whole report. Other parts will be joined in order.\n'
                        'SECTION OUTLINE:\n' + '\n'.join(f'{i + 1}. {p[0]}' for i, p in enumerate(parts)))
        if previous_parts:
            continuation += ('\nEARLIER DRAFT PARTS (for continuity only, not additional factual evidence):\n'
                             + '\n\n'.join(previous_parts))
    facts_context = {key: value for key, value in context.items() if key != 'report_sections'}
    facts = json.dumps(facts_context, ensure_ascii=False, separators=(',', ':'))
    system = (
        'You complete missing academic report prose from a validated project context pack. '
        'Use only supplied facts. Never invent implementation details, screenshots, execution results, '
        'references, entities, SQL, test outcomes, student details, or measurements. Preserve an honest '
        'boundary between observed and expected behaviour. '
        + ('Return only the requested section as plain prose, without JSON or Markdown fences.'
           if len(missing_fields) == 1 else 'Return valid JSON only.')
    )
    output_shape = ('Return the section as plain prose. Do not wrap it in JSON.' if len(missing_fields) == 1
                    else 'Return exactly: ' + json.dumps({'report_sections': {field: '...' for field in missing_fields}}))
    user = f"""Complete only these blank report_sections fields:
{requested}

Use formal third-person academic prose. Avoid generic filler, repeated conclusions, Markdown headings, and unsupported claims. The structured objectives, requirements, entities, tables, use cases, and results will be rendered separately, so complement rather than duplicate them.

{output_shape}

{continuation}

VALIDATED CONTEXT:
{facts}
"""
    return [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]


def parse_generated_sections(text, requested_fields):
    if len(requested_fields) == 1 and isinstance(text, str) and not text.lstrip().startswith(('{', '```')):
        field = requested_fields[0]
        errors = []
        value = _clean_text(text, f'report_sections.{field}', errors, MAX_SECTION_CHARS)
        if errors or len(value) < MIN_SECTION_CHARS:
            raise ProjectGenerationError('The model returned an empty or incomplete section. Retry this section.')
        return {field: value}
    try:
        parsed = _extract_json(text)
    except ProjectContextError as err:
        raise ProjectGenerationError(f'Model output was not valid JSON: {err}') from err
    sections = parsed.get('report_sections', parsed)
    if not isinstance(sections, dict):
        raise ProjectGenerationError('Model output must contain a report_sections object.')

    errors = []
    result = {}
    for field in requested_fields:
        value = _clean_text(sections.get(field), f'report_sections.{field}', errors, MAX_SECTION_CHARS)
        minimum_words = _minimum_section_words(field)
        if len(value) < MIN_SECTION_CHARS:
            errors.append(
                f'report_sections.{field} is empty or incomplete. Retry this section.'
            )
        result[field] = value
    if errors:
        raise ProjectGenerationError(' '.join(errors))
    return result


def build_project_report(context, generated_sections=None, generated_fields=None):
    generated_sections = generated_sections or {}
    fields = REPORT_SECTION_FIELDS[context['submode']]
    merged = {}
    for field in fields:
        supplied = context['report_sections'].get(field, '')
        merged[field] = supplied if _section_is_complete(field, supplied) else generated_sections.get(field, '')
        if not _section_is_complete(field, merged[field]):
            raise ProjectGenerationError(f'Report section {field} is still missing.')

    generated_fields = generated_fields or list(generated_sections)
    if not generated_fields:
        source = 'supplied'
    elif len(generated_fields) == len(fields):
        source = 'generated'
    else:
        source = 'hybrid'
    return {
        'schema_version': SCHEMA_VERSION,
        'submode': context['submode'],
        'title': context['project']['title'],
        'student': copy.deepcopy(context['student']),
        'context': copy.deepcopy(context),
        'report_sections': merged,
        'generation': {
            'source': source,
            'generated_fields': list(generated_fields),
        },
    }


def canonicalize_project_report(raw_report, expected_submode=None):
    if not isinstance(raw_report, dict):
        raise ProjectContextError('report must be an object.')
    if raw_report.get('schema_version') != SCHEMA_VERSION:
        raise ProjectContextError(f'report.schema_version must be {SCHEMA_VERSION}.')
    submode = raw_report.get('submode')
    if submode not in SUPPORTED_SUBMODES:
        raise ProjectContextError('report.submode must be either "frontend" or "dbms".')
    if expected_submode and submode != expected_submode:
        raise ProjectContextError('Report sub-mode does not match the requested export sub-mode.')

    validation = validate_project_context(raw_report.get('context'), submode)
    if not validation['valid']:
        issues = validation['errors'] + validation['missing_fields']
        raise ProjectContextError('Invalid report context: ' + '; '.join(issues))

    raw_sections = raw_report.get('report_sections')
    if not isinstance(raw_sections, dict):
        raise ProjectContextError('report.report_sections must be an object.')
    section_errors = []
    sections = {
        field: _clean_text(
            raw_sections.get(field), f'report.report_sections.{field}', section_errors,
            MAX_SECTION_CHARS,
        )
        for field in REPORT_SECTION_FIELDS[submode]
    }
    for field, value in sections.items():
        if not _section_is_complete(field, value):
            section_errors.append(
                f'report.report_sections.{field} must contain at least {_minimum_section_words(field)} words.'
            )
    if section_errors:
        raise ProjectContextError(' '.join(section_errors))

    generation = raw_report.get('generation') if isinstance(raw_report.get('generation'), dict) else {}
    generated_fields = generation.get('generated_fields')
    if not isinstance(generated_fields, list):
        generated_fields = []
    generated_fields = [field for field in generated_fields if field in REPORT_SECTION_FIELDS[submode]]
    context = validation['normalized']
    context['report_sections'] = sections
    return build_project_report(context, generated_fields=generated_fields)
