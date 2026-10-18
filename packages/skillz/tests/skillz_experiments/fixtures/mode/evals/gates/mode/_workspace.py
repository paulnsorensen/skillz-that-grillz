"""Read the grader workspace: the staged files, the task output, and what the task changed."""
import json
import pathlib
from collections.abc import Callable
from typing import cast


def read(name: str) -> str | None:
    try:
        return pathlib.Path(name).read_text()
    except OSError:
        return None


def outputs() -> dict[str, str]:
    return {p.relative_to('output').as_posix(): p.read_text() for p in pathlib.Path('output').rglob('*') if p.is_file()}


def staged_files() -> set[str]:
    return {p.as_posix() for p in pathlib.Path('.').rglob('*')
            if p.is_file() and p.parts[0] not in ('output', 'home', 'tmp') and not any(s.startswith('.') for s in p.parts)}


def changed_files() -> dict[str, str | None]:
    """Map each file that the task wrote or changed to its new text, and each staged file that it deleted to None."""
    out = outputs()
    changed: dict[str, str | None] = {name: text for name, text in out.items() if read(name) != text}
    changed.update({name: None for name in staged_files() - set(out)})
    return changed


def text_of(changed: dict[str, str | None], name: str) -> str:
    text = changed[name]
    if text is None:
        raise KeyError(name)
    return text


def json_object(text: str) -> dict[str, object]:
    return cast(dict[str, object], json.loads(text))


def objects(value: object) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], value)


def report(check: Callable[[], bool]) -> None:
    try:
        ok = check()
    except Exception:  # a missing or malformed output fails the check; it must not crash the grader
        ok = False
    print(json.dumps({'score': 1 if ok else 0}))
