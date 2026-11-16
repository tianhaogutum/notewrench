"""Original, deliberately restricted CommonMark 0.31.2 static renderer.

This is a subset recognizer, not a general Markdown parser. Unsupported blocks
or inline runs are kept literally and reported, never guessed or discarded.
All generated HTML goes through htmlclean before it can enter an EPUB.
"""
from html import escape, unescape
import re
from string import punctuation as ASCII_PUNCTUATION
import unicodedata

from .htmlclean import check_text, clean_body

HEADING = re.compile(r' {0,3}(#{1,6})(?:[ \t]+(.*)|[ \t]*)$')
ITEM = re.compile(r'([-+*]|[0-9]{1,6}[.)]) ([^ \t].*)$')
FENCE = re.compile(r'(`{3,}|~{3,})([^\n]*)$')
LINK_DESTINATION = re.compile(r'\]\((<[^<>\n]*>|[^\s()<>]*)\)')
LABEL_ESCAPE = re.compile(r'\\([' + re.escape(ASCII_PUNCTUATION) + r'])')
ENTITY = re.compile(r'&(?:#[xX][0-9a-fA-F]{1,6}|#[0-9]{1,7}|[A-Za-z][A-Za-z0-9]{1,31});')
PLAIN = re.compile(r'[^`\[\]*_\\<&~|!\n]+|!(?!\[)|\n')
DELIMITER = re.compile(r'\*+|_+')
# Constructs outside the block subset: preserve the entire block when encountered.
BLOCKISH = re.compile(r'^(?:[ \t]+|#{1,6}(?:\s|$)|>|[-+*](?:\s|$)|[0-9]+[.)](?:\s|$)|`{3}|~{3}|[=]+\s*$|[-_]{3,}\s*$|\[.*\]:|\|)')


def _entity(match):
    value = match.group()
    if value.startswith('&#'):
        digits = value[2:-1]
        number = int(digits[1:], 16) if digits[:1].lower() == 'x' else int(digits)
        if number <= 0x10FFFF:
            check_text(chr(number))
    decoded = unescape(value)
    check_text(decoded)
    return decoded


def _decode(text):
    return ENTITY.sub(_entity, text)


def _link(text, position):
    """Recognize a flat label without rescanning escaped text as syntax."""
    position += 1  # Opening bracket.
    start = position
    while position < len(text):
        char = text[position]
        if char == ']':
            destination = LINK_DESTINATION.match(text, position)
            if destination:
                return text[start:position], destination[1], destination.end()
            return None
        if char == '\\':
            following = text[position + 1:position + 2]
            if following == '\n':
                # An unpaired backslash makes a hard break; paired ones are text.
                return None
            if following and following in ASCII_PUNCTUATION:
                position += 2
                continue
        if char == '_':
            end = position + 1
            while end < len(text) and text[end] == '_':
                end += 1
            # Only immediate source letters/numbers qualify; escapes and entities
            # must not manufacture flanking characters. Consume the complete run.
            if position == start or not text[position - 1].isalnum() or not text[end:end + 1].isalnum():
                return None
            position = end
            continue
        if char in '[`*<>':
            return None
        position += 1
    return None


def _label(text):
    """Decode only recognized labels; escape tokens cannot start entities."""
    rendered, position = [], 0
    for token in LABEL_ESCAPE.finditer(text):
        rendered.extend((escape(_decode(text[position:token.start()])), escape(token[1])))
        position = token.end()
    rendered.append(escape(_decode(text[position:])))
    return ''.join(rendered)


def _flanking(text, start, end):
    """CommonMark 0.31.2 delimiter roles, using source characters, not entities."""
    before = text[start - 1] if start else ''
    after = text[end] if end < len(text) else ''

    def whitespace(char):
        return not char or char in '\t\n\f\r' or unicodedata.category(char) == 'Zs'

    def punctuation(char):
        return bool(char) and unicodedata.category(char)[0] in 'PS'

    left = not whitespace(after) and (not punctuation(after) or whitespace(before) or punctuation(before))
    right = not whitespace(before) and (not punctuation(before) or whitespace(after) or punctuation(after))
    if text[start] == '_':
        return left and (not right or punctuation(before)), right and (not left or punctuation(after))
    return left, right


def _inline(text, events, start, end):
    """Static inline tokens and flat emphasis; any unsupported token rolls back."""
    rendered, position = [], 0
    emphasis = None
    if not re.search(r' {2,}\n', text):
        while position < len(text):
            if text[position] == '\\':
                following = text[position + 1:position + 2]
                if following == '\n':
                    # Hard breaks remain outside this subset; roll back atomically.
                    break
                if following and following in ASCII_PUNCTUATION:
                    # Consume a text token, never rescan it as markup or an entity.
                    rendered.append(escape(following))
                    position += 2
                else:
                    rendered.append('\\')
                    position += 1
                continue
            delimiter = DELIMITER.match(text, position)
            if delimiter:
                marker = delimiter.group()
                # Outside emphasis, complete intraword underscore runs are text.
                # Inspect source neighbors before entity/escape decoding, as in
                # flat link labels; do not widen the emphasis interior subset.
                if (emphasis is None and marker[0] == '_' and position > 0
                        and text[position - 1].isalnum()
                        and text[delimiter.end():delimiter.end() + 1].isalnum()):
                    rendered.append(marker)
                    position = delimiter.end()
                    continue
                if len(marker) > 2:
                    break
                can_open, can_close = _flanking(text, position, delimiter.end())
                tag = 'em' if len(marker) == 1 else 'strong'
                if emphasis is None and can_open:
                    emphasis = marker
                    rendered.append(f'<{tag}>')
                elif emphasis == marker and can_close:
                    # Equal one/two-character runs cannot trigger the multiple-of-3
                    # exception. Mixed widths, nesting and unmatched runs fall back.
                    emphasis = None
                    rendered.append(f'</{tag}>')
                else:
                    break
                position = delimiter.end()
                continue
            if text.startswith('![', position):
                break
            if text[position] == '[':
                if emphasis is not None:
                    break
                link = _link(text, position)
                if link is None:
                    break
                label, href, position = link
                if href.startswith('<'):
                    href = href[1:-1]
                rendered.append(f'<a href="{escape(_decode(href), quote=True)}">{_label(label)}</a>')
                continue
            if text[position] == '`':
                if emphasis is not None:
                    break
                close = text.find('`', position + 1)
                if close == -1 or close == position + 1 or text[close:close + 2] == '``':
                    break
                code = text[position + 1:close].replace('\n', ' ')
                if code.startswith(' ') and code.endswith(' ') and code.strip(' '):
                    code = code[1:-1]
                rendered.append('<code>' + escape(code) + '</code>')
                position = close + 1
                continue
            entity = ENTITY.match(text, position)
            if entity:
                rendered.append(escape(_entity(entity)))
                position = entity.end()
                continue
            plain = PLAIN.match(text, position)
            if not plain:
                break
            rendered.append(escape(plain.group()))
            position = plain.end()
        else:
            if emphasis is None:
                return ''.join(rendered)
    events.append({'code': 'markdown_literal_inline', 'line_start': start, 'line_end': end,
                   'reason': 'inline_syntax_outside_static_subset_or_raw_html'})
    return escape(text)


def _fence(line):
    match = FENCE.fullmatch(line)
    if match and (match[1][0] != '`' or '`' not in match[2]):
        return match
    return None


def render_markdown(source, source_url=''):
    """Return cleaned XHTML and loss events, with 1-based original line ranges."""
    check_text(source)
    # Only CommonMark line endings are normalized; other Unicode separators are text.
    normalized = source.replace('\r\n', '\n').replace('\r', '\n')
    # split('\n') keeps every non-newline character, including Unicode separators.
    lines = normalized.split('\n')
    if normalized.endswith('\n'):
        lines.pop()
    output, events, index = [], [], 0
    while index < len(lines):
        if not lines[index].strip(' \t'):
            index += 1
            continue
        start = index
        fence = _fence(lines[index])
        if fence:
            marker, info = fence.groups()
            index += 1
            code_start = index
            closing = re.compile(r' {0,3}' + re.escape(marker[0]) + r'{' + str(len(marker)) + r',}[ \t]*$')
            while index < len(lines) and not closing.fullmatch(lines[index]):
                index += 1
            code = '\n'.join(lines[code_start:index])
            if index > code_start:
                code += '\n'
            output.append('<pre><code>' + escape(code) + '</code></pre>')
            if info.strip():
                events.append({'code': 'markdown_code_info', 'line_start': start + 1,
                               'line_end': start + 1, 'value': info.strip(),
                               'reason': 'code_info_retained_in_report_only'})
            if index < len(lines):
                index += 1
            continue
        # Blank-line-separated blocks keep ambiguous mixed/nested syntax literal.
        while index < len(lines) and lines[index].strip(' \t'):
            index += 1
        block = lines[start:index]
        text = '\n'.join(block)
        heading = HEADING.fullmatch(block[0]) if len(block) == 1 else None
        items = [ITEM.fullmatch(line) for line in block]
        if heading:
            content = heading[2] or ''
            content = re.sub(r'^(?:#+[ \t]*)$|[ \t]+#+[ \t]*$', '', content).strip(' \t')
            output.append(f'<h{len(heading[1])}>' + _inline(content, events, start + 1, index) + f'</h{len(heading[1])}>')
        elif all(items) and len({item[1][-1] for item in items}) == 1 and not any(BLOCKISH.match(item[2]) for item in items):
            ordered = items[0][1][0].isdigit()
            tag = 'ol' if ordered else 'ul'
            attrs = f' start="{int(items[0][1][:-1])}"' if ordered else ''
            rendered_items = [
                '<li>' + _inline(item[2], events, start + n + 1, start + n + 1) + '</li>'
                for n, item in enumerate(items)
            ]
            output.append(f'<{tag}{attrs}>' + ''.join(rendered_items) + f'</{tag}>')
        elif all(line.startswith('> ') and not BLOCKISH.match(line[2:]) for line in block):
            output.append('<blockquote><p>' + _inline('\n'.join(line[2:] for line in block), events, start + 1, index) + '</p></blockquote>')
        elif any(BLOCKISH.match(line) for line in block):
            output.append('<pre>' + escape(text) + '</pre>')
            events.append({'code': 'markdown_literal_block', 'line_start': start + 1,
                           'line_end': index, 'reason': 'block_syntax_outside_static_subset'})
        else:
            output.append('<p>' + _inline(text, events, start + 1, index) + '</p>')
    body, cleaning_events = clean_body(''.join(output), True, source_url)
    return body, events + cleaning_events
