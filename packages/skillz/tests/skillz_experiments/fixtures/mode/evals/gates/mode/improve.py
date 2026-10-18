import re

from _markdown import longest
from _workspace import changed_files, report, text_of


def check() -> bool:
    changed = changed_files()
    s = text_of(changed, 'skills/demo/SKILL.md')
    side = text_of(changed, 'skills/demo/agents/openai.yaml')
    r = text_of(changed, 'report.md')
    return (longest(s) <= 25
            and 'allow_implicit_invocation: false' in side
            and 'scripts/count.py' in s
            and re.search('before: ~?[0-9]+ tok', r, re.I) is not None
            and re.search('after: ~?[0-9]+ tok', r, re.I) is not None
            and re.search('Usage: omitted [(].+[)]', r) is not None and 'Audit: fresh' in r
            and len(re.findall('^Approval question:', r, re.M)) == 1)


if __name__ == '__main__':
    report(check)
