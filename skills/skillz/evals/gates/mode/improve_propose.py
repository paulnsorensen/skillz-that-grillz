import hashlib
import pathlib
import re

from _workspace import changed_files, read, report, staged_files, text_of


def blob(data: bytes) -> str:
    return hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()


def package(root: str) -> str:
    names = sorted(n for n in staged_files() if n.startswith(root))
    return blob(''.join(blob(pathlib.Path(n).read_bytes()) + '\n' for n in names).encode())


def check() -> bool:
    changed = changed_files()
    r = text_of(changed, 'report.md')
    current = package('skills/demo/')
    audit = read('audit.md')
    found = re.search('Content id: ([0-9a-f]{40})', audit) if audit else None
    reused = found is not None and found[1] == current
    rows = [row for row in r.splitlines() if row.startswith('|') and 'WebFetch' in row]
    return (list(changed) == ['report.md']
            and 'Content id: ' + current in r
            and len(re.findall('Audit: (?:fresh|reused)', r)) == 1
            and ('Audit: reused ' + current if reused else 'Audit: fresh') in r
            and 'Usage: included' in r
            and any('Usage' in row and re.search('remove', row, re.I)
                    and re.search('[|] *`?apply`? *[|] *pending *[|]', row, re.I) for row in rows)
            and len(re.findall('^Approval question:', r, re.M)) == 1)


if __name__ == '__main__':
    report(check)
