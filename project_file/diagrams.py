"""E-R figures drawn from validated facts, without a model, browser or executable."""

import base64
import io
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ERD_SECTIONS = frozenset(('erd', 'e-r diagram'))
WIDTH = 1400
ROW_HEIGHT = 268
CARD_WIDTH = 470
CARD_HEIGHT = 232
MAX_RELATIONSHIPS_PER_FIGURE = 4
INK = '#183047'
EDGE = '#475e74'


def strip_diagram_source(text):
    """Remove recognized diagram blocks, preserving prose around them.

    This deliberately does not interpret diagram code. The validated entities
    and relationships, rather than arbitrary generated syntax, define the figure.
    """
    marker = re.compile(r'^\s*(?:erDiagram\b|(?:flowchart|graph)\s+(?:TD|TB|LR|RL|BT)\b|'
                        r'@start(?:uml|erd)\b|(?:di)?graph\s+[^\n]*\{|Table\s+\S+\s*\{)', re.I | re.M)

    def fence(match):
        language, body = match.group(1).lower(), match.group(2)
        if language in ('mermaid', 'plantuml', 'puml', 'dot', 'graphviz', 'dbml') or marker.search(body):
            return ''
        return match.group(0)

    cleaned = re.sub(r'```([\w-]*)[^\S\n]*\n(.*?)(?:```|\Z)', fence, str(text or ''), flags=re.S)
    cleaned = re.sub(r'@start(?:uml|erd)\b.*?(?:@end(?:uml|erd)\b|\Z)', '', cleaned, flags=re.S | re.I)
    lines = []
    in_diagram = False
    depth = 0
    for line in cleaned.splitlines():
        if re.search(r'[|o}{][-.]+[|o}{]', line):
            in_diagram = True
            continue
        if marker.match(line):
            in_diagram = True
            depth = line.count('{') - line.count('}') if 'erDiagram' not in line else 0
            continue
        if in_diagram:
            if depth > 0:
                depth += line.count('{') - line.count('}')
                continue
            if not line.strip() or line.lstrip().startswith(('%%', '//', 'direction ', 'Ref:')):
                continue
            if re.match(r'^\s*(?:"[^"]+"|[\w.-]+)\s*\{', line):
                depth = line.count('{') - line.count('}')
                continue
            if re.search(r'[|o}{][-.]+[|o}{]|-->|---|\[[^\]]*\].*--', line):
                continue
            in_diagram = False
        lines.append(line)
    return '\n'.join(lines).strip()


def erd_prose(text, dbms):
    cleaned = strip_diagram_source(text)
    if cleaned or not str(text or '').strip():
        return cleaned
    return (
        f"The database model contains {len(dbms['entities'])} entities with their documented "
        f"attributes and keys. The E-R figures show the {len(dbms['relationships'])} supplied "
        'relationships and their cardinalities. Complete attribute details and relationship '
        'descriptions accompany the diagrams.'
    )


def plan_erd_figures(dbms):
    """Partition edges into small neighborhoods; never omit an edge or entity.

    Repeated entities denote the same entity across figures. Stable R identifiers
    map every connection to the complete relationship table in review and export.
    """
    relationships = [dict(item, id=f'R{index + 1}')
                     for index, item in enumerate(dbms['relationships'])]
    order = {entity['name']: index for index, entity in enumerate(dbms['entities'])}
    remaining = [item for item in relationships if item['from_entity'] != item['to_entity']]
    plans = []
    while remaining:
        degree = Counter(name for item in remaining for name in (item['from_entity'], item['to_entity']))
        hub = max(degree, key=lambda name: (degree[name], -order[name]))
        connected = [item for item in remaining if hub in (item['from_entity'], item['to_entity'])]
        for start in range(0, len(connected), MAX_RELATIONSHIPS_PER_FIGURE):
            subset = connected[start:start + MAX_RELATIONSHIPS_PER_FIGURE]
            names = list(dict.fromkeys([hub] + [item['to_entity'] if item['from_entity'] == hub
                                               else item['from_entity'] for item in subset]))
            plans.append({'kind': 'neighborhood', 'hub': hub, 'entities': names, 'relationships': subset})
        remaining = [item for item in remaining if hub not in (item['from_entity'], item['to_entity'])]

    loops = [item for item in relationships if item['from_entity'] == item['to_entity']]
    for start in range(0, len(loops), MAX_RELATIONSHIPS_PER_FIGURE):
        subset = loops[start:start + MAX_RELATIONSHIPS_PER_FIGURE]
        plans.append({'kind': 'self', 'entities': list(dict.fromkeys(item['from_entity'] for item in subset)),
                      'relationships': subset})
    connected_names = {name for item in relationships for name in (item['from_entity'], item['to_entity'])}
    isolated = [entity['name'] for entity in dbms['entities'] if entity['name'] not in connected_names]
    for start in range(0, len(isolated), 8):
        plans.append({'kind': 'entities', 'entities': isolated[start:start + 8], 'relationships': []})
    return plans


@lru_cache(maxsize=16)
def _font(size, bold=False):
    names = [
        Path('/usr/share/fonts/truetype/dejavu') / ('DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf'),
        Path('/usr/share/fonts/truetype/liberation2') / ('LiberationSans-Bold.ttf' if bold else 'LiberationSans-Regular.ttf'),
        Path('C:/Windows/Fonts') / ('arialbd.ttf' if bold else 'arial.ttf'),
    ]
    for path in names:
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size=size)


def _fit(draw, value, font, width):
    value = ' '.join(str(value).split())
    if draw.textlength(value, font=font) <= width:
        return value
    lo, hi = 0, len(value)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if draw.textlength(value[:mid] + '...', font=font) <= width:
            lo = mid
        else:
            hi = mid - 1
    return value[:lo].rstrip() + '...'


def _key_label(attribute):
    text = f"{attribute.get('key', '')} {attribute.get('constraints', '')}".upper()
    keys = []
    if re.search(r'\bPK\b|\bPRIMARY\s+KEY\b', text):
        keys.append('PK')
    if re.search(r'\bFK\b|\bFOREIGN\s+KEY\b', text):
        keys.append('FK')
    if not keys and attribute.get('key'):
        return attribute['key']
    return '/'.join(keys)


def _card(draw, entity, x, y):
    draw.rounded_rectangle((x, y, x + CARD_WIDTH, y + CARD_HEIGHT), radius=10,
                           fill='white', outline=INK, width=3)
    draw.rounded_rectangle((x, y, x + CARD_WIDTH, y + 74), radius=10, fill=INK)
    draw.rectangle((x, y + 50, x + CARD_WIDTH, y + 74), fill=INK)
    name = entity['name']
    header_font = _font(30, True)
    first = _fit(draw, name, header_font, CARD_WIDTH - 32)
    if first != name:
        cut = len(first.removesuffix('...'))
        draw.text((x + 16, y + 5), name[:cut], font=header_font, fill='white')
        draw.text((x + 16, y + 38), _fit(draw, name[cut:], header_font, CARD_WIDTH - 32),
                  font=header_font, fill='white')
    else:
        draw.text((x + 16, y + 21), name, font=header_font, fill='white')
    attributes = sorted(enumerate(entity['attributes']), key=lambda item: (not bool(_key_label(item[1])), item[0]))
    for index, (_, attribute) in enumerate(attributes[:3]):
        key = _key_label(attribute)
        label = f"{key + '  ' if key else ''}{attribute['name']} : {attribute['data_type']}"
        draw.text((x + 16, y + 84 + index * 34), _fit(draw, label, _font(25), CARD_WIDTH - 32),
                  font=_font(25), fill=INK)
    remaining = len(attributes) - 3
    note = f'+ {remaining} attributes; see entity table' if remaining > 0 else 'Full details in the entity table'
    draw.text((x + 16, y + 202), note, font=_font(21), fill=EDGE)


def _cardinalities(value):
    # No optionality or min/max is invented. Unknown notation stays in the key.
    token = r'(?:\d+(?:\.\.(?:\d+|\*))?|[MN*]|one|many)'
    match = re.match(rf'^\s*({token})\s*(?::|[- ]to[- ])\s*({token})(?=\s|\(|$)', value, re.I)
    if not match:
        return '', ''
    return tuple({'one': '1', 'many': 'N'}.get(part.lower(), part.upper()) for part in match.groups())


def _endpoint(draw, point, toward, cardinality):
    if not cardinality:
        return
    dx, dy = toward[0] - point[0], toward[1] - point[1]
    length = (dx * dx + dy * dy) ** 0.5 or 1
    ux, uy = dx / length, dy / length
    px, py = -uy, ux
    if cardinality in ('M', 'N', '*'):
        joint = (point[0] + ux * 24, point[1] + uy * 24)
        for side in (-1, 1):
            draw.line((joint, (point[0] + px * side * 12, point[1] + py * side * 12)), fill=EDGE, width=3)
    elif cardinality == '1':
        cx, cy = point[0] + ux * 14, point[1] + uy * 14
        draw.line(((cx - px * 11, cy - py * 11), (cx + px * 11, cy + py * 11)), fill=EDGE, width=3)
    x, y = point[0] + ux * 48, point[1] + uy * 48 - 20
    label_font = _font(23, True)
    bounds = draw.textbbox((x, y), cardinality, font=label_font, anchor='mm')
    draw.rectangle((bounds[0] - 4, bounds[1] - 3, bounds[2] + 4, bounds[3] + 3), fill='white')
    draw.text((x, y), cardinality, font=label_font, fill=INK, anchor='mm')


def _relationship_label(draw, x, y, relationship):
    label = relationship['id']
    draw.rounded_rectangle((x - 38, y - 20, x + 38, y + 20), radius=8,
                           fill='#edf3f7', outline='#bccbd6', width=1)
    draw.text((x, y), label, font=_font(25, True), fill=INK, anchor='mm')


def render_erd_figure(dbms, plan, number, count):
    entities = {entity['name']: entity for entity in dbms['entities']}
    rows = len(plan['relationships']) if plan['kind'] != 'entities' else (len(plan['entities']) + 1) // 2
    height = 170 + max(rows, 1) * ROW_HEIGHT
    with Image.new('RGB', (WIDTH, height), 'white') as image:
        draw = ImageDraw.Draw(image)
        draw.text((45, 20), f'E-R diagram {number} of {count}', font=_font(32, True), fill=INK)
        subtitle = 'Self relationships' if plan['kind'] == 'self' else 'Entities and documented relationships'
        draw.text((45, 64), subtitle, font=_font(25), fill=EDGE)
        if plan['kind'] == 'neighborhood':
            hub_y = 116 + (rows - 1) * ROW_HEIGHT // 2
            for index, relationship in enumerate(plan['relationships']):
                leaf_y = 116 + index * ROW_HEIGHT
                name = relationship['to_entity'] if relationship['from_entity'] == plan['hub'] else relationship['from_entity']
                a = (45 + CARD_WIDTH, hub_y + CARD_HEIGHT * (index + 1) / (rows + 1))
                b = (885, leaf_y + CARD_HEIGHT / 2)
                draw.line((a, b), fill=EDGE, width=3)
                left_card, right_card = _cardinalities(relationship['cardinality'])
                if relationship['from_entity'] != plan['hub']:
                    left_card, right_card = right_card, left_card
                _endpoint(draw, a, b, left_card)
                _endpoint(draw, b, a, right_card)
                _relationship_label(draw, a[0] + (b[0] - a[0]) * .6, a[1] + (b[1] - a[1]) * .6, relationship)
                _card(draw, entities[name], 885, leaf_y)
            _card(draw, entities[plan['hub']], 45, hub_y)
        elif plan['kind'] == 'self':
            for index, relationship in enumerate(plan['relationships']):
                y = 116 + index * ROW_HEIGHT
                a, b = (515, y + 90), (515, y + 185)
                points = [a, (1260, a[1]), (1260, b[1]), b]
                draw.line(points, fill=EDGE, width=3)
                first, second = _cardinalities(relationship['cardinality'])
                _endpoint(draw, a, points[1], first)
                _endpoint(draw, b, points[-2], second)
                _relationship_label(draw, 900, a[1], relationship)
                draw.text((700, y + 127), 'Both ends refer to the same entity', font=_font(24), fill=EDGE)
                _card(draw, entities[relationship['from_entity']], 45, y)
        else:
            for index, name in enumerate(plan['entities']):
                _card(draw, entities[name], 45 + (index % 2) * 840, 116 + (index // 2) * ROW_HEIGHT)
        draw.text((45, height - 39), 'R labels match the relationship table. Repeated names represent the same entity.',
                  font=_font(23), fill=EDGE)
        with io.BytesIO() as output:
            # A bounded palette keeps previews small enough for serverless responses.
            with image.convert('P', palette=Image.Palette.ADAPTIVE, colors=64) as compressed:
                compressed.save(output, format='PNG', optimize=True)
            return output.getvalue(), WIDTH, height


def build_erd_figures(dbms):
    plans = plan_erd_figures(dbms)
    figures = []
    for index, plan in enumerate(plans, 1):
        content, width, height = render_erd_figure(dbms, plan, index, len(plans))
        relation_ids = [item['id'] for item in plan['relationships']]
        detail = ', '.join(relation_ids) if relation_ids else 'entities without declared relationships'
        figures.append({
            'id': f'erd-{index}',
            'caption': f'E-R diagram {index} of {len(plans)}: {detail}',
            'entity_names': plan['entities'],
            'relationship_ids': relation_ids,
            'width': width, 'height': height,
            'data_url': 'data:image/png;base64,' + base64.b64encode(content).decode('ascii'),
        })
    return figures
