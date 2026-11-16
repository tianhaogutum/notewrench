# SPDX-License-Identifier: AGPL-3.0-only
"""Conservative Markdown links with source-context protection and loss reporting."""

import re
from urllib.parse import quote

URI = re.compile(r":/([a-fA-F0-9]{32})(?![a-fA-F0-9])(?P<fragment>#[^\s)>\"']*)?")
# Inline links and reference destinations share escaped fragment closers.
# Pair backslashes before consuming an escaped fragment closer. The alternatives
# cannot split a pair on backtracking; even runs leave ) structural. Other
# fragment delimiters retain their existing meaning, including after a backslash.
LINK_FRAGMENT = r"#(?:\\[\\)]|\\(?![\\)])|[^\s)>\"'\\])*"
REFERENCE_URI = re.compile(r":/([a-fA-F0-9]{32})(?![a-fA-F0-9])(?P<fragment>" + LINK_FRAGMENT + r")?")
# An angle opener requires its closer; malformed wrappers stay in source context.
# Match escaped characters in pairs before considering title delimiters.
INLINE_TITLE = r'''(?:"(?:\\[^\n]|[^"\\\n])*"|'(?:\\[^\n]|[^'\\\n])*'|\((?:\\[^\n]|[^()\\\n])*\))'''
LINK = re.compile(r"(?P<label>!?\[(?:\\.|[^\[\]\\\n])*\])\((?P<leading>[ \t]*)(?P<inline_angle><)?:/"
                  r"(?P<id>[a-fA-F0-9]{32})(?![a-fA-F0-9])"
                  r"(?P<fragment>" + LINK_FRAGMENT + r")?(?(inline_angle)>)(?P<title>[ \t]+" + INLINE_TITLE + r")?"
                  r"(?P<padding>[ \t]*)\)")
FENCE = re.compile(r"^(?P<indent>[ \t]*(?:>[ \t]*)*)(?:(?:[-+*]|[0-9]+[.)])[ \t]+)?(?P<marks>`{3,}|~{3,})(?P<info>.*)$")
CHECKBOX = re.compile(r"^([ \t]*)(?:[-+*]) \[([ xX])\] ")

REFERENCE_START = re.compile(r'^[ \t]*\[(?:\\.|[^\[\]\\\n])+\]:')
# Consume backslash pairs before considering a closing delimiter: odd runs
# escape it, even runs leave it structural. Escapes never cross physical lines.
REFERENCE_TITLE = INLINE_TITLE
REFERENCE = re.compile(
    r'(?P<prefix> {0,3}\[(?:\\.|[^\[\]\\\n])+\]:[ \t]*(?:\n[ \t]*)?)'
    r'(?P<angle><)?(?P<uri>' + REFERENCE_URI.pattern + r')(?(angle)>)'
    # A same-line title needs horizontal whitespace; a next-line title may
    # start at column zero. Keep trailing whitespace without a title too.
    r'(?P<suffix>(?:(?:[ \t]+|[ \t]*\n[ \t]*)' + REFERENCE_TITLE + r')?[ \t]*)'
    r'(?=\n|$)')
REFERENCE_LINES = re.compile(REFERENCE_START.pattern + r'[^\n]*', re.MULTILINE)
INDENTED = re.compile(r'^(?: {4}| {0,3}\t)')
BLOCK = re.compile(FENCE.pattern + '|' + INDENTED.pattern + r'[^\n]*$', re.MULTILINE)
TOKEN = re.compile(
    r'(?P<block>' + BLOCK.pattern + r')|(?P<reference>' + REFERENCE_LINES.pattern + r')|'
    r'(?P<inline>' + LINK.pattern + r')|(?P<ticks>`+)|'
    r'(?P<html><!--|</?[A-Za-z][A-Za-z0-9-]*(?=[\s/>])|<![A-Z]|<\?)', re.MULTILINE)
FENCE_LINES = re.compile(FENCE.pattern, re.MULTILINE)
PARAGRAPH_END = re.compile(r'\n[ \t]*\n')


def escaped(text, position):
    start = position
    while start and text[start - 1] == '\\':
        start -= 1
    return (position - start) % 2 == 1


def protected_spans(body, stats):
    """Scan source contexts before any link rewrite; offsets always refer to source.

    HTML quotes/comments and paired code spans can cross lines. Block boundaries
    and blank lines stop inline-code pairing. Four-space/tab lines are protected
    conservatively even where full container parsing might interpret them as prose.
    """
    position = 0
    while token := TOKEN.search(body, position):
        start, end = token.span()
        if token['block'] is not None:
            if INDENTED.match(token.group()):
                yield start, end, 'code'
                position = end
                continue
            marks = token['marks']
            if not (marks[0] == '`' and '`' in token['info']):
                stop = len(body)
                kind = 'unclosed_fence'
                for close in FENCE_LINES.finditer(body, end):
                    if (close['marks'][0] == marks[0] and len(close['marks']) >= len(marks)
                            and not close['info'].strip()):
                        stop = close.end()
                        kind = 'code'
                        break
                stats['protected_fences'] += 1
                yield start, stop, kind
                position = stop
                continue
            # A backtick in the info string makes this an inline-code candidate.
            start = token.start('marks')
            end = token.end('marks')
        if token['reference'] is not None:
            # Recognize the whole definition before classifying continuation
            # indentation as code. Only its URI is rewritten; labels/titles are
            # data even when they contain backticks, HTML or other internal URIs.
            definition = REFERENCE.match(body, start)
            if definition:
                yield start, definition.start('uri'), 'definition_data'
                yield definition.start('uri'), definition.end('uri'), 'definition_uri'
                end = definition.end()
                yield definition.end('uri'), end, 'definition_data'
            else:
                # Keep malformed destination continuations out of bare-link
                # rewriting too, without crossing a blank line or swallowing prose.
                if re.search(r'\]:[ \t]*$', token.group()):
                    continuation = re.match(r'\n(?:[ \t]+|(?=<:/|:/))[^\n]*', body[end:])
                    if continuation and continuation.group().strip():
                        end += continuation.end()
                yield start, end, 'unsupported'
            position = end
            continue
        if token['inline'] is not None:
            # Recognize the whole link before interpreting backticks/HTML in its
            # label/title. Only its destination is syntax; the rest is data.
            image = token['label'].startswith('!')
            if image and escaped(body, start):
                # Leave the escaped marker in the literal prefix. The span now
                # starts at [, so rewriting sees a normal link, with no image !.
                start += 1
                image = False
            opening = start + int(image)
            yield start, end, 'code' if escaped(body, opening) else 'inline_link'
            position = end
            continue
        if escaped(body, start):
            position = end
            continue
        if token['html'] is not None:
            if body.startswith('<!--', start):
                close = body.find('-->', end)
                stop = len(body) if close < 0 else close + 3
            else:
                stop = end
                quote_char = None
                while stop < len(body):
                    char = body[stop]
                    stop += 1
                    if quote_char:
                        if char == quote_char:
                            quote_char = None
                    elif char in "\"'":
                        quote_char = char
                    elif char == '>':
                        break
            yield start, stop, 'html'
            position = stop
            continue
        boundary = len(body)
        for pattern in (PARAGRAPH_END, BLOCK, REFERENCE_LINES):
            next_block = pattern.search(body, end)
            if next_block:
                boundary = min(boundary, next_block.start())
        close = re.compile(r'(?<!`)' + re.escape(body[start:end]) + r'(?!`)').search(body, end, boundary)
        if close:
            yield start, close.end(), 'code'
            position = close.end()
        else:
            position = end


def render_body(body, note_id, pages, assets, issues, stats):
    def issue(code, target, line):
        issues.append({"code": code, "note_id": note_id, "target": target, "line": line})

    def unsupported(text, line_number):
        for match in URI.finditer(text):
            target = match[1].lower()
            issue('unsupported_link_context', target, line_number)
            if target not in pages and target not in assets:
                issue('broken_link', target, line_number)
        return text

    def definition_uri(text, line_number):
        uri = REFERENCE_URI.fullmatch(text)
        target = uri[1].lower()
        if target in assets:
            destination = '../' + quote(assets[target], safe='/')
            stats['resource_links'] += 1
        elif target in pages:
            destination = '../' + quote(pages[target]['path'], safe='/')
            stats['note_links'] += 1
            issue('note_link_uses_file_path', target, line_number)
        else:
            issue('broken_link', target, line_number)
            return text
        return destination + (uri['fragment'] or '')

    def rewrite(text, line_number):
        def link(match):
            target = match['id'].lower()
            fragment = match['fragment'] or ''
            suffix = (match['title'] or '') + match['padding']
            prefix = match['label'] + '(' + match['leading']
            if target in assets:
                stats['resource_links'] += 1
                return f"{prefix}../{quote(assets[target], safe='/')}{fragment}{suffix})"
            if target in pages:
                if fragment or match['label'].startswith('!') or match['title']:
                    issue('note_link_uses_file_path', target, line_number)
                    destination = '../' + quote(pages[target]['path'], safe='/') + fragment
                else:
                    destination = '[[' + pages[target]['page'] + ']]'
                stats['note_links'] += 1
                return f"{prefix}{destination}{suffix})"
            issue('broken_link', target, line_number)
            return match.group()

        # Do not run URI replacement on generated links or double-report broken links.
        output = []
        start = 0
        for match in LINK.finditer(text):
            output.append(bare(text[start:match.start()], line_number))
            if escaped(text, match.start() + (1 if match['label'].startswith('!') else 0)):
                output.append(match.group())
            else:
                output.append(link(match))
            start = match.end()
        output.append(bare(text[start:], line_number))
        return ''.join(output)

    def bare(text, line_number):
        def replace(match):
            target = match[1].lower()
            # Embedded bare URIs are kept verbatim and reported.
            before = text[match.start() - 1] if match.start() else ''
            after = text[match.end()] if match.end() < len(text) else ''
            # Horizontal padding cannot turn a malformed inline destination
            # into a standalone URI. Valid links were already consumed above.
            context = match.start()
            while context and text[context - 1] in ' \t':
                context -= 1
            inline_destination = text[max(0, context - 2):context] == ']('
            if (inline_destination or (before and not before.isspace())
                    or (after and after not in ' \t.,;!?')):
                issue('unsupported_link_context', target, line_number)
                if target not in pages and target not in assets:
                    issue('broken_link', target, line_number)
                return match.group()
            fragment = match['fragment'] or ''
            if target in pages:
                stats['note_links'] += 1
                if fragment:
                    issue('note_link_uses_file_path', target, line_number)
                    return '[note](../' + quote(pages[target]['path'], safe='/') + fragment + ')'
                return '[[' + pages[target]['page'] + ']]'
            if target in assets:
                stats['resource_links'] += 1
                return '[asset](../' + quote(assets[target], safe='/') + fragment + ')'
            issue('broken_link', target, line_number)
            return match.group()
        return URI.sub(replace, text)

    spans = iter(protected_spans(body, stats))
    active = next(spans, None)
    output = []
    offset = 0
    unclosed_fence = False
    for number, line in enumerate(body.split('\n'), 1):
        end = offset + len(line)
        position = offset
        parts = []
        checkbox = CHECKBOX.match(line)
        checkbox_protected = False
        while active and active[0] < end:
            start, stop, kind = active
            if stop <= position:
                active = next(spans, None)
                continue
            if start > position:
                parts.append(rewrite(body[position:start], number))
            left, right = max(position, start), min(end, stop)
            fragment = body[left:right]
            if kind == 'definition_uri':
                parts.append(definition_uri(fragment, number))
            elif kind == 'inline_link':
                parts.append(rewrite(fragment, number))
            elif kind in ('html', 'unsupported'):
                parts.append(unsupported(fragment, number))
            else:
                parts.append(fragment)
            unclosed_fence |= kind == 'unclosed_fence'
            if checkbox and left < offset + checkbox.end():
                checkbox_protected = True
            position = right
            if stop <= end:
                active = next(spans, None)
            else:
                break
        parts.append(rewrite(body[position:end], number))
        converted = ''.join(parts)
        if checkbox and not checkbox_protected:
            converted = CHECKBOX.sub(lambda m: m[1] + '- ' + ('TODO ' if m[2] == ' ' else 'DONE '), converted, count=1)
            stats['checkboxes'] += 1
        output.append(converted)
        offset = end + 1
    if unclosed_fence:
        issue('unclosed_code_fence', '', len(output))
    return '\n'.join(output)
