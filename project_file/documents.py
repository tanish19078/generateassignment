"""Convention-locked Word reports for Frontend and DBMS Project File modes."""

import base64
import binascii
import io

from PIL import Image, UnidentifiedImageError
from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from .reports import ProjectContextError


MAX_IMAGES = 16
MAX_IMAGE_BYTES = 1_000_000
MAX_TOTAL_IMAGE_BYTES = 2_500_000
MAX_IMAGE_PIXELS = 16_000_000
ALLOWED_IMAGE_TYPES = {'image/png': 'PNG', 'image/jpeg': 'JPEG'}


def _set_run_font(run, size=12, bold=False, italic=False):
    run.font.name = 'Times New Roman'
    run._element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'), 'Times New Roman')
    run.font.size = Pt(size)
    run.bold = bold
    run.italic = italic


def _body(doc, text='', bold=False, align=WD_ALIGN_PARAGRAPH.JUSTIFY, spacing=1.5):
    paragraphs = str(text or '').split('\n\n') or ['']
    created = None
    for block in paragraphs:
        block = block.strip()
        if not block:
            continue
        created = doc.add_paragraph()
        created.alignment = align
        created.paragraph_format.line_spacing = spacing
        created.paragraph_format.space_after = Pt(6)
        _set_run_font(created.add_run(block), 12, bold)
    return created


def _heading(doc, text, level=1):
    paragraph = doc.add_paragraph(style=f'Heading {level}')
    paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
    paragraph.paragraph_format.keep_with_next = True
    paragraph.paragraph_format.space_before = Pt(10 if level == 1 else 7)
    paragraph.paragraph_format.space_after = Pt(5)
    run = paragraph.add_run(text)
    _set_run_font(run, 16 if level == 1 else 14, True)
    return paragraph


def _bullet_list(doc, items):
    for item in items:
        paragraph = doc.add_paragraph(style='List Bullet')
        paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        paragraph.paragraph_format.line_spacing = 1.5
        paragraph.paragraph_format.space_after = Pt(3)
        _set_run_font(paragraph.add_run(str(item)), 12)


def _numbered_list(doc, items):
    for index, item in enumerate(items, 1):
        paragraph = doc.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        paragraph.paragraph_format.line_spacing = 1.5
        paragraph.paragraph_format.space_after = Pt(3)
        _set_run_font(paragraph.add_run(f'{index}. {item}'), 12)


def _code(doc, text):
    paragraph = doc.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
    paragraph.paragraph_format.line_spacing = 1
    paragraph.paragraph_format.left_indent = Cm(0.5)
    paragraph.paragraph_format.right_indent = Cm(0.5)
    paragraph.paragraph_format.space_after = Pt(7)
    shading = OxmlElement('w:shd')
    shading.set(qn('w:fill'), 'F2F2F2')
    paragraph._p.get_or_add_pPr().append(shading)
    _set_run_font(paragraph.add_run(str(text or '')), 12)
    return paragraph


def _caption(doc, text, size):
    paragraph = doc.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.paragraph_format.line_spacing = 1
    paragraph.paragraph_format.space_after = Pt(6)
    paragraph.paragraph_format.keep_with_next = True
    _set_run_font(paragraph.add_run(text), size)
    return paragraph


def _table(doc, headers, rows, caption, caption_size, counters):
    counters['table'] += 1
    _caption(doc, f"Table {counters['table']}: {caption}", caption_size)
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = 'Table Grid'
    table.autofit = True
    for cell, label in zip(table.rows[0].cells, headers):
        cell.text = str(label)
    for row in rows:
        cells = table.add_row().cells
        for cell, value in zip(cells, row):
            cell.text = str(value or '')
    for row_index, row in enumerate(table.rows):
        cant_split = OxmlElement('w:cantSplit')
        row._tr.get_or_add_trPr().append(cant_split)
        if row_index == 0:
            row._tr.get_or_add_trPr().append(OxmlElement('w:tblHeader'))
        for cell in row.cells:
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            for paragraph in cell.paragraphs:
                paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
                paragraph.paragraph_format.line_spacing = 1
                paragraph.paragraph_format.space_after = Pt(2)
                for run in paragraph.runs:
                    _set_run_font(run, 12, row_index == 0)
    doc.add_paragraph('')
    return table


def _decode_images(raw_images):
    if raw_images is None:
        return {}
    if not isinstance(raw_images, list):
        raise ProjectContextError('images must be an array.')
    if len(raw_images) > MAX_IMAGES:
        raise ProjectContextError(f'No more than {MAX_IMAGES} screenshots can be exported at once.')

    total = 0
    decoded = {}
    for index, item in enumerate(raw_images):
        if not isinstance(item, dict):
            raise ProjectContextError(f'images[{index}] must be an object.')
        filename = str(item.get('filename') or '').strip()
        media_type = str(item.get('media_type') or '').strip().lower()
        payload = item.get('data_base64')
        if not filename or len(filename) > 260:
            raise ProjectContextError(f'images[{index}].filename is invalid.')
        if media_type not in ALLOWED_IMAGE_TYPES:
            raise ProjectContextError(f'images[{index}] must be PNG or JPEG.')
        if not isinstance(payload, str) or not payload:
            raise ProjectContextError(f'images[{index}].data_base64 is required.')
        if payload.startswith('data:'):
            payload = payload.split(',', 1)[-1]
        try:
            raw = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError) as err:
            raise ProjectContextError(f'images[{index}] is not valid base64 image data.') from err
        if len(raw) > MAX_IMAGE_BYTES:
            raise ProjectContextError(f'{filename} exceeds the 1 MB image limit.')
        total += len(raw)
        if total > MAX_TOTAL_IMAGE_BYTES:
            raise ProjectContextError('Combined screenshots exceed the 2.5 MB export limit.')

        try:
            with Image.open(io.BytesIO(raw)) as probe:
                if probe.format != ALLOWED_IMAGE_TYPES[media_type]:
                    raise ProjectContextError(f'{filename} does not match its declared image type.')
                if probe.width * probe.height > MAX_IMAGE_PIXELS:
                    raise ProjectContextError(f'{filename} has too many pixels.')
                probe.verify()
            with Image.open(io.BytesIO(raw)) as image:
                image.load()
                image.thumbnail((1600, 1200))
                normalized_width, normalized_height = image.size
                output = io.BytesIO()
                if media_type == 'image/jpeg':
                    image.convert('RGB').save(output, format='JPEG', quality=88, optimize=True)
                else:
                    image.convert('RGBA' if image.mode == 'RGBA' else 'RGB').save(
                        output, format='PNG', optimize=True,
                    )
                normalized = output.getvalue()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as err:
            raise ProjectContextError(f'{filename} could not be verified as an image.') from err

        key = filename.replace('\\', '/').split('/')[-1].lower()
        if key in decoded:
            raise ProjectContextError(f'Duplicate screenshot filename: {filename}.')
        decoded[key] = {
            'bytes': normalized,
            'filename': filename,
            'caption': str(item.get('caption') or '').strip()[:1_000],
            'section': str(item.get('section') or '').strip().lower()[:100],
            'use_case_id': str(item.get('use_case_id') or '').strip()[:100],
            'width': normalized_width,
            'height': normalized_height,
        }
    return decoded


def _image_key(filename):
    return str(filename or '').replace('\\', '/').split('/')[-1].lower()


def _canonicalize_images(report, images):
    declarations = {}

    def declare(filename, caption, section, use_case_id=''):
        key = _image_key(filename)
        if not key:
            return
        current = declarations.get(key, {})
        declarations[key] = {
            'filename': filename,
            'caption': caption or current.get('caption', ''),
            'section': section or current.get('section', ''),
            'use_case_id': use_case_id or current.get('use_case_id', ''),
        }

    context = report['context']
    for figure in context.get('figures', []):
        declare(figure['filename'], figure['caption'], figure['section'], figure['use_case_id'])
    if report['submode'] == 'frontend':
        for result in context['project']['results']:
            declare(result['screenshot_filename'], result['title'], 'results', result['id'])
    else:
        for use_case in context['dbms']['use_cases']:
            declare(
                use_case['screenshot_filename'], use_case['title'], 'use_case', use_case['id'],
            )

    undeclared = sorted(image['filename'] for key, image in images.items() if key not in declarations)
    if undeclared:
        raise ProjectContextError(
            'Uploaded images were not declared by the context pack: ' + ', '.join(undeclared)
        )
    missing = sorted(item['filename'] for key, item in declarations.items() if key not in images)
    if missing:
        raise ProjectContextError('Missing uploaded images declared by the context pack: ' + ', '.join(missing))

    for key, image in images.items():
        declaration = declarations[key]
        image['caption'] = declaration['caption'] or image['filename']
        image['section'] = declaration['section']
        image['use_case_id'] = declaration['use_case_id']
    return images


def _add_image(doc, image, caption, caption_size, counters):
    counters['figure'] += 1
    paragraph = doc.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.paragraph_format.keep_together = True
    with io.BytesIO(image['bytes']) as buffer:
        scale = min(15.4 / image['width'], 18.5 / image['height'])
        paragraph.add_run().add_picture(
            buffer,
            width=Cm(image['width'] * scale),
            height=Cm(image['height'] * scale),
        )
    _caption(doc, f"Figure {counters['figure']}: {caption}", caption_size)


def _find_image(images, filename):
    key = _image_key(filename)
    return images.get(key) if key else None


def _add_unmatched_images(doc, images, used, sections, caption_size, counters):
    for key, image in images.items():
        if key in used or image['section'] not in sections:
            continue
        _add_image(doc, image, image['caption'] or image['filename'], caption_size, counters)
        used.add(key)


def _configure_document(doc):
    section = doc.sections[0]
    section.page_width = Cm(21)
    section.page_height = Cm(29.7)
    section.top_margin = section.bottom_margin = section.left_margin = section.right_margin = Cm(2.54)

    normal = doc.styles['Normal']
    normal.font.name = 'Times New Roman'
    normal._element.rPr.rFonts.set(qn('w:eastAsia'), 'Times New Roman')
    normal.font.size = Pt(12)
    normal.font.color.rgb = RGBColor(0, 0, 0)
    normal.paragraph_format.line_spacing = 1.5
    normal.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    for style_name, size in (('Heading 1', 16), ('Heading 2', 14)):
        style = doc.styles[style_name]
        style.font.name = 'Times New Roman'
        style._element.rPr.rFonts.set(qn('w:eastAsia'), 'Times New Roman')
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = RGBColor(0, 0, 0)

    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_run_font(footer.add_run('Page '), 10)
    field = OxmlElement('w:fldSimple')
    field.set(qn('w:instr'), 'PAGE')
    footer._p.append(field)

    settings = doc.settings._element
    update_fields = OxmlElement('w:updateFields')
    update_fields.set(qn('w:val'), 'true')
    settings.append(update_fields)


def _add_title_page(doc, report):
    student = report['student']
    for _ in range(3):
        doc.add_paragraph('')
    paragraph = doc.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_run_font(paragraph.add_run(report['title'].upper()), 20, True)
    paragraph = doc.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_run_font(paragraph.add_run('PROJECT REPORT'), 16, True)
    doc.add_paragraph('')

    details = [
        ('Submitted by', student.get('name')),
        ('Roll Number', student.get('roll_number')),
        ('Class / Section', student.get('class_section')),
        ('Course', student.get('course')),
        ('Department', student.get('department')),
        ('Institution', student.get('institution')),
        ('Academic Session', student.get('academic_session')),
    ]
    for label, value in details:
        if not value:
            continue
        paragraph = doc.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _set_run_font(paragraph.add_run(f'{label}: '), 12, True)
        _set_run_font(paragraph.add_run(value), 12)
    doc.add_page_break()


def _add_toc(doc):
    _heading(doc, 'Table of Contents', 1)
    paragraph = doc.add_paragraph()
    run = paragraph.add_run()
    begin = OxmlElement('w:fldChar')
    begin.set(qn('w:fldCharType'), 'begin')
    instruction = OxmlElement('w:instrText')
    instruction.set(qn('xml:space'), 'preserve')
    instruction.text = ' TOC \\o "1-2" \\h \\z \\u '
    separate = OxmlElement('w:fldChar')
    separate.set(qn('w:fldCharType'), 'separate')
    text = OxmlElement('w:t')
    text.text = 'Open in Word and update this field if the table of contents is not populated.'
    end = OxmlElement('w:fldChar')
    end.set(qn('w:fldCharType'), 'end')
    for element in (begin, instruction, separate, text, end):
        run._r.append(element)
    doc.add_page_break()


def _add_frontend(doc, report, images, counters):
    context = report['context']
    project = context['project']
    frontend = context['frontend']
    sections = report['report_sections']
    used = set()
    caption_size = 12

    _heading(doc, 'Abstract', 1)
    _body(doc, sections['abstract'])
    doc.add_page_break()
    _add_toc(doc)

    _heading(doc, '1. Introduction', 1)
    _heading(doc, '1.1 Background', 2)
    _body(doc, sections['introduction_background'])
    _heading(doc, '1.2 Objectives', 2)
    _numbered_list(doc, project['objectives'])
    _heading(doc, '1.3 Significance', 2)
    _body(doc, sections['introduction_significance'])

    doc.add_page_break()
    _heading(doc, '2. Problem Definition and Requirements', 1)
    _heading(doc, '2.1 Problem Statement', 2)
    _body(doc, sections['problem_definition'])
    requirement_number = 2
    _heading(doc, f'2.{requirement_number} Software Requirements', 2)
    _bullet_list(doc, project['software_requirements'])
    if project['hardware_requirements']:
        requirement_number += 1
        _heading(doc, f'2.{requirement_number} Hardware Requirements', 2)
        _bullet_list(doc, project['hardware_requirements'])
    if project['datasets']:
        requirement_number += 1
        _heading(doc, f'2.{requirement_number} Data Sets', 2)
        _bullet_list(doc, project['datasets'])

    doc.add_page_break()
    _heading(doc, '3. Proposed Design / Methodology', 1)
    _heading(doc, '3.1 Design and Methodology', 2)
    _body(doc, sections['design_methodology'])
    design_number = 1
    if project['methodology']:
        design_number += 1
        _heading(doc, f'3.{design_number} Implementation Methodology', 2)
        _body(doc, project['methodology'])
    if project['architecture']:
        design_number += 1
        _heading(doc, f'3.{design_number} System Architecture', 2)
        _body(doc, project['architecture'])
    if frontend['stack']:
        design_number += 1
        _heading(doc, f'3.{design_number} Technology Stack', 2)
        _table(
            doc, ['Technology', 'Version', 'Purpose'],
            [[item['name'], item['version'], item['purpose']] for item in frontend['stack']],
            'Frontend Technology Stack', caption_size, counters,
        )
    if project['file_structure']:
        design_number += 1
        _heading(doc, f'3.{design_number} File Structure', 2)
        _table(
            doc, ['Path', 'Purpose'],
            [[item['path'], item['purpose']] for item in project['file_structure']],
            'Project File Structure', caption_size, counters,
        )
    if frontend['pages']:
        design_number += 1
        _heading(doc, f'3.{design_number} Pages and Routes', 2)
        _table(
            doc, ['Page', 'Route', 'Purpose'],
            [[item['name'], item['route'], item['purpose']] for item in frontend['pages']],
            'Application Pages and Routes', caption_size, counters,
        )
    if frontend['components']:
        design_number += 1
        _heading(doc, f'3.{design_number} Components / Modules', 2)
        _table(
            doc, ['Component', 'Source', 'Purpose'],
            [[item['name'], item['source_path'], item['purpose']] for item in frontend['components']],
            'Major Frontend Components', caption_size, counters,
        )
    if frontend['state_management']:
        design_number += 1
        _heading(doc, f'3.{design_number} State Management', 2)
        _body(doc, frontend['state_management'])
    if frontend['api_integrations']:
        design_number += 1
        _heading(doc, f'3.{design_number} API Integrations', 2)
        _table(
            doc, ['Integration', 'Endpoint', 'Purpose'],
            [[item['name'], item['endpoint'], item['purpose']] for item in frontend['api_integrations']],
            'Frontend API Integrations', caption_size, counters,
        )
    for title, field in (
        ('Responsive Design', 'responsive_design'),
        ('Accessibility', 'accessibility'),
        ('Testing', 'testing'),
        ('Deployment', 'deployment'),
    ):
        if frontend[field]:
            design_number += 1
            _heading(doc, f'3.{design_number} {title}', 2)
            _body(doc, frontend[field])
    _add_unmatched_images(doc, images, used, {'design', 'architecture', 'methodology'}, caption_size, counters)

    doc.add_page_break()
    _heading(doc, '4. Results', 1)
    _body(doc, sections['results_summary'])
    for index, result in enumerate(project['results'], 1):
        if index > 1:
            doc.add_page_break()
        _heading(doc, f"4.{index} {result['title'] or 'Result'}", 2)
        _body(doc, result['description'])
        if result['evidence']:
            _bullet_list(doc, result['evidence'])
        image = _find_image(images, result['screenshot_filename'])
        if image:
            key = image['filename'].replace('\\', '/').split('/')[-1].lower()
            _add_image(doc, image, image['caption'] or result['title'] or image['filename'], caption_size, counters)
            used.add(key)
        elif result['screenshot_filename']:
            paragraph = _body(
                doc, f"Screenshot not supplied for export: {result['screenshot_filename']}",
                align=WD_ALIGN_PARAGRAPH.CENTER, spacing=1,
            )
            if paragraph:
                for run in paragraph.runs:
                    run.italic = True
    _add_unmatched_images(doc, images, used, {'results', 'result', ''}, caption_size, counters)

    if project['references']:
        doc.add_page_break()
        _heading(doc, 'References', 1)
        _numbered_list(doc, project['references'])


def _entity_rows(entities):
    rows = []
    for entity in entities:
        if not entity['attributes']:
            rows.append([entity['name'], '', '', '', entity['description']])
            continue
        for attribute in entity['attributes']:
            rows.append([
                entity['name'], attribute['name'], attribute['data_type'],
                ' | '.join(part for part in (attribute['key'], attribute['constraints']) if part),
                attribute['description'],
            ])
    return rows


def _add_dbms(doc, report, images, counters):
    context = report['context']
    project = context['project']
    dbms = context['dbms']
    sections = report['report_sections']
    used = set()
    caption_size = 10

    _add_toc(doc)
    _heading(doc, '1. Introduction', 1)
    _body(doc, sections['introduction'])
    if project['objectives']:
        _heading(doc, '1.1 Objectives', 2)
        _numbered_list(doc, project['objectives'])

    doc.add_page_break()
    _heading(doc, '2. E-R Diagram', 1)
    _body(doc, sections['erd_description'])
    _heading(doc, '2.1 Entities and Attributes', 2)
    _table(
        doc, ['Entity', 'Attribute', 'Data Type', 'Key / Constraints', 'Description'],
        _entity_rows(dbms['entities']), 'Entities and Attributes', caption_size, counters,
    )
    _heading(doc, '2.2 Relationships and Cardinality', 2)
    _table(
        doc, ['From Entity', 'To Entity', 'Cardinality', 'Description'],
        [[item['from_entity'], item['to_entity'], item['cardinality'], item['description']]
         for item in dbms['relationships']],
        'Entity Relationships', caption_size, counters,
    )
    _add_unmatched_images(doc, images, used, {'erd', 'e-r diagram'}, caption_size, counters)

    doc.add_page_break()
    _heading(doc, '3. Database Schema', 1)
    _body(doc, sections['schema_description'])
    schema_number = 0
    if dbms['database_engine']:
        schema_number += 1
        _heading(doc, f'3.{schema_number} Database Platform', 2)
        platform = dbms['database_engine']
        if dbms['database_version']:
            platform += f" {dbms['database_version']}"
        _body(doc, platform)
    if dbms['normalization']:
        schema_number += 1
        _heading(doc, f'3.{schema_number} Normalization', 2)
        _body(doc, dbms['normalization'])
    for table in dbms['tables']:
        schema_number += 1
        _heading(doc, f"3.{schema_number} {table['name']} Table", 2)
        _body(doc, table['purpose'])
        _table(
            doc, ['Column', 'Data Type', 'Constraints', 'References', 'Description'],
            [[column['name'], column['data_type'], column['constraints'],
              column['references'], column['description']] for column in table['columns']],
            f"Schema of {table['name']}", caption_size, counters,
        )
    schema_number += 1
    _heading(doc, f'3.{schema_number} Database Definition SQL', 2)
    _code(doc, dbms['schema_sql'])

    doc.add_page_break()
    _heading(doc, '4. Use Cases (Problem Statements)', 1)
    _body(doc, sections['use_cases_introduction'])
    for index, use_case in enumerate(dbms['use_cases'], 1):
        if index > 1:
            doc.add_page_break()
        _heading(doc, f"4.{index} {use_case['title']}", 2)
        _body(doc, use_case['problem_statement'])
        if use_case['concepts_covered']:
            paragraph = doc.add_paragraph()
            paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            _set_run_font(paragraph.add_run('Concepts Covered: '), 12, True)
            _set_run_font(paragraph.add_run(', '.join(use_case['concepts_covered'])), 12)
        _heading(doc, 'SQL Query', 2)
        _code(doc, use_case['sql'])
        _heading(doc, 'Output / Result', 2)
        _body(doc, use_case['output_description'])
        image = _find_image(images, use_case['screenshot_filename'])
        if image:
            key = image['filename'].replace('\\', '/').split('/')[-1].lower()
            _add_image(doc, image, image['caption'] or use_case['title'], caption_size, counters)
            used.add(key)
        elif use_case['screenshot_filename']:
            paragraph = _body(
                doc, f"Screenshot not supplied for export: {use_case['screenshot_filename']}",
                align=WD_ALIGN_PARAGRAPH.CENTER, spacing=1,
            )
            if paragraph:
                for run in paragraph.runs:
                    run.italic = True
    _add_unmatched_images(doc, images, used, {'use_case', 'use case', 'results', ''}, caption_size, counters)


def build_project_document(report, raw_images=None):
    images = _canonicalize_images(report, _decode_images(raw_images))
    doc = Document()
    _configure_document(doc)
    _add_title_page(doc, report)
    counters = {'table': 0, 'figure': 0}
    if report['submode'] == 'frontend':
        _add_frontend(doc, report, images, counters)
    elif report['submode'] == 'dbms':
        _add_dbms(doc, report, images, counters)
    else:
        raise ProjectContextError(f"Unsupported report sub-mode: {report['submode']}.")

    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer
