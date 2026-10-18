import re
from typing import cast

from _markdown import longest
from _workspace import changed_files, json_object, objects, outputs, report, text_of


def check() -> bool:
    out = outputs()
    changed = changed_files()
    s = text_of(changed, 'skills/release-notes/SKILL.md')
    side = text_of(changed, 'skills/release-notes/agents/openai.yaml')
    section = re.search('^## Skills$(.*?)(?=^## |\\Z)', text_of(changed, 'README.md'), re.M | re.S)
    if section is None:
        return False
    rows = [row for row in section[1].splitlines() if row.startswith('|') and 'skills/release-notes/SKILL.md' in row and '/release-notes' in row]
    phrases = ('write release notes', 'draft the changelog', 'summarize this release', 'prepare release notes', 'list what shipped')
    evals = json_object(text_of(changed, 'skills/release-notes/evals/evals.json'))
    cases = objects(evals['evals'])
    tasks = [c for c in cases if c.get('assertions')]
    triggers = [c for c in cases if c.get('expected_skill') == 'release-notes' and not c.get('assertions')]
    return (re.search('^name: release-notes$', s, re.M) is not None
            and re.search('^disable-model-invocation: true$', s, re.M) is not None
            and 'allow_implicit_invocation: false' in side
            and len(rows) == 1 and longest(s) <= 25
            and evals['skill_name'] == 'release-notes' and len({c['id'] for c in cases}) == len(cases)
            and all(type(c['id']) is int and c['name'] and c['prompt'] and c['expected_output'] and type(c['files']) is list
                    for c in cases)
            and all(any(p in cast(str, c['prompt']).lower() for c in triggers) for p in phrases)
            and any('expected_skill' in c and c['expected_skill'] is None and 'commit message' in cast(str, c['prompt']).lower() for c in cases)
            and len(tasks) >= 3 and all(c.get('expected_skill') == 'release-notes' for c in tasks)
            and all(type(c.get('assertions', [])) is list for c in cases)
            and all(isinstance(a, str) and a.strip() for c in tasks for a in cast(list[object], c['assertions']))
            and all('skills/release-notes/' + f in out for c in cases for f in cast(list[str], c['files'])))


if __name__ == '__main__':
    report(check)
