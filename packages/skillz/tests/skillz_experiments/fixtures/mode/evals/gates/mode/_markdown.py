"""Longest sentence of a skill body. The rules follow the prose rules of the skill inspector."""
import re

MARKER = re.compile(r'^\s*(?:#+\s+|[-*+]\s+|\d+[.)]\s+|>\s*)')
BREAK = re.compile(r'^\s*(?:#|[-*+]\s|\d+[.)]\s)')
MASK = re.compile(r'`[^`]*`|"[^"]*"|“[^”]*”|(?<=\])\([^)]*\)')
SENTENCE = re.compile(r'\S.*?(?:[.!?]+(?=\s|$)|$)', re.S)
OPEN = re.compile(r'^ {0,3}(`{3,}|~{3,})(.*)$')
CLOSE = re.compile(r'^ {0,3}(`{3,}|~{3,})[ \t]*$')
ITEM = re.compile(r'^ *(?:[-*+]|\d+[.)])[ \t]+(?=\S)')


def unindent(line: str, column: int) -> str:
    return line[min(column, len(line) - len(line.lstrip(' '))):]


def longest(text: str) -> int:
    lines = text.splitlines()
    paragraphs: list[str] = []
    current = ''
    fence = ''
    column = fence_column = 0
    for line in lines[lines.index('---', 1) + 1:]:
        line = re.sub(r'^(?: {0,3}> ?)+', '', line)
        blank = not line.strip()
        if fence:
            closer = CLOSE.match(unindent(line, fence_column))
            if closer and closer[1][0] == fence[0] and len(closer[1]) >= len(fence):
                fence = ''
            continue
        item = ITEM.match(line)
        if item:
            column = item.end()
        elif not blank and len(line) - len(line.lstrip(' ')) < column:
            paragraphs.append(current)
            current = ''
            column = 0
        opener = OPEN.match(unindent(line, column))
        if opener and not (opener[1][0] == '`' and '`' in opener[2]):
            paragraphs.append(current)
            current = ''
            fence = opener[1]
            fence_column = column
            continue
        if blank or line.strip().startswith('|'):
            paragraphs.append(current)
            current = ''
            continue
        if BREAK.match(line):
            paragraphs.append(current)
            current = ''
        current += (' ' if current else '') + MARKER.sub('', line).strip()
    paragraphs.append(current)
    best = 0
    for paragraph in paragraphs:
        for hit in SENTENCE.finditer(MASK.sub(lambda m: ' ' * len(m[0]), paragraph)):
            best = max(best, sum(1 for t in hit[0].split() if any(c.isalnum() for c in t)))
    return best
