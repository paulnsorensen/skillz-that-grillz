from typing import cast

from _workspace import changed_files, json_object, report, text_of


def check() -> bool:
    d = json_object(text_of(changed_files(), 'skills/demo/evals/autoimprove.json'))
    kinds = cast(dict[str, dict[str, object]], d['kinds'])
    return (d['status'] == 'draft' and d['schema_version'] == 1 and d['skill'] == 'demo' and bool(kinds)
            and all(set(v) == {'grader', 'rubric'} and v['grader'] == 'judge' and v['rubric'] for v in kinds.values()))


if __name__ == '__main__':
    report(check)
