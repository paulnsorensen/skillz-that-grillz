import re
from typing import cast

from _workspace import changed_files, report, text_of

FIELDS = ['Inputs', 'Output', 'Ordering', 'Empty', 'Errors', 'Side effects', 'Caller line']


def check() -> bool:
    changed = changed_files()
    brief = text_of(changed, 'brief.md')
    parts = re.split('^### Candidate ', brief, flags=re.M)[1:]
    rows = cast(list[str], re.findall('^[|] [0-9]+ [|].*$', brief, re.M))
    return (list(changed) == ['brief.md'] and len(parts) >= 1 and len(rows) == len(parts)
            and all('**' + f + '**' in p for p in parts for f in FIELDS)
            and all(re.search('line [0-9]+|[(][0-9]+[)]|L[0-9]+', row) for row in rows)
            and re.search('^## skillz wedge: ', brief, re.M) is not None
            and '| Delivery |' in brief)


if __name__ == '__main__':
    report(check)
