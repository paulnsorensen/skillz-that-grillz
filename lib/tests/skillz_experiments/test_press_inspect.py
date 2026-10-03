"""Press attacks on `inspect_skill.py` long_sentences (AC-16) through the subprocess seam."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

SCRIPT = Path(__file__).parents[3] / "skills/skillz/scripts/inspect_skill.py"
LONG = " ".join(f"word{i}" for i in range(30)) + "."
TWENTY = " ".join(f"w{i}" for i in range(20)) + "."
TWENTY_ONE = " ".join(f"w{i}" for i in range(21)) + "."


def inspect(tmp_path: Path, body: str, raw: bytes | None = None, timeout: float | None = None) -> tuple[int, dict[str, object]]:
    path = tmp_path / "SKILL.md"
    _ = path.write_bytes(raw if raw is not None else f"---\nname: x\ndescription: y\n---\n{body}".encode())
    run = subprocess.run([sys.executable, "-I", str(SCRIPT), str(path)], capture_output=True, text=True, check=False,
                         timeout=timeout)
    return run.returncode, cast(dict[str, object], json.loads(run.stdout))


def found(tmp_path: Path, body: str) -> list[dict[str, int]]:
    code, report = inspect(tmp_path, body)
    assert code == 0 and report["schema_version"] == 2
    return cast(list[dict[str, int]], report["long_sentences"])


def test_prose_sentence_reports_its_line_and_word_count(tmp_path: Path) -> None:
    assert found(tmp_path, f"Short.\n\n{LONG}\n") == [{"line": 7, "words": 30}]


def test_the_limit_is_twenty_words_exclusive(tmp_path: Path) -> None:
    assert found(tmp_path, TWENTY + "\n") == []
    assert [item["words"] for item in found(tmp_path, TWENTY_ONE + "\n")] == [21]


@pytest.mark.parametrize("fence", ["```", "~~~"])
def test_fenced_code_is_excluded(tmp_path: Path, fence: str) -> None:
    assert found(tmp_path, f"{fence}\n{LONG}\n{fence}\n") == []


def test_tilde_fence_inside_a_backtick_fence_does_not_close_it(tmp_path: Path) -> None:
    assert found(tmp_path, f"```\n~~~\n{LONG}\n~~~\n```\n") == []


def test_backtick_fence_inside_a_tilde_fence_does_not_close_it(tmp_path: Path) -> None:
    assert found(tmp_path, f"~~~\n```\n{LONG}\n```\n~~~\n") == []


def test_longer_outer_fence_is_not_closed_by_a_shorter_inner_fence(tmp_path: Path) -> None:
    """CommonMark: a closing fence needs at least as many characters as the opener."""
    assert found(tmp_path, f"````\n```\n{LONG}\n````\n") == []


def test_prose_after_a_closed_fence_is_checked_again(tmp_path: Path) -> None:
    assert [item["words"] for item in found(tmp_path, f"```\nx\n```\n{LONG}\n")] == [30]


def test_inline_code_is_excluded_from_the_word_count(tmp_path: Path) -> None:
    code = " ".join(f"`c{i}`" for i in range(30))
    assert found(tmp_path, f"Use {code} now.\n") == []


def test_a_sentence_end_inside_inline_code_does_not_split_the_sentence(tmp_path: Path) -> None:
    words = " ".join(f"w{i}" for i in range(12))
    assert [item["words"] for item in found(tmp_path, f"{words} `a. b` {words}.\n")] == [24]


def test_quoted_text_that_spans_lines_is_excluded(tmp_path: Path) -> None:
    quoted = "\n".join(f"q{i} q{i}b q{i}c" for i in range(10))
    assert found(tmp_path, f'He said "{quoted}" and left.\n') == []


def test_curly_quoted_text_is_excluded(tmp_path: Path) -> None:
    assert found(tmp_path, f"Say “{LONG}” now.\n") == []


def test_table_rows_and_frontmatter_are_not_prose(tmp_path: Path) -> None:
    assert found(tmp_path, f"| a | {LONG} |\n|---|---|\n") == []
    code, report = inspect(tmp_path, "", raw=f"---\nname: x\ndescription: {LONG}\n---\nShort.\n".encode())
    assert code == 0 and report["long_sentences"] == []


def test_each_list_item_is_its_own_sentence(tmp_path: Path) -> None:
    items = "\n".join(f"- item {i} stays short" for i in range(30))
    assert found(tmp_path, items + "\n") == []


def test_a_long_sentence_wrapped_across_lines_reports_its_first_line(tmp_path: Path) -> None:
    half = " ".join(f"a{i}" for i in range(15))
    assert found(tmp_path, f"{half}\n{half}.\n") == [{"line": 5, "words": 30}]


def test_two_sentences_on_one_line_count_separately(tmp_path: Path) -> None:
    assert found(tmp_path, f"{TWENTY} {TWENTY}\n") == []


def test_prose_line_that_starts_with_inline_triple_backticks_is_not_a_fence(tmp_path: Path) -> None:
    """CommonMark: a backtick fence info string holds no backtick, so this line is prose."""
    assert len(found(tmp_path, f"```x``` {LONG}\n\n{LONG}\n")) == 2


def test_closing_fence_with_an_info_string_does_not_close(tmp_path: Path) -> None:
    assert found(tmp_path, f"```\n```js\n{LONG}\n```\n") == []


@pytest.mark.parametrize("raw", [b"", b"no frontmatter\n", b"---\nname: x\n", b"---\n\xff\xfe\n---\nbody\n"])
def test_hostile_input_exits_two_with_a_json_error(tmp_path: Path, raw: bytes) -> None:
    code, report = inspect(tmp_path, "", raw=raw)
    assert code == 2 and "error" in report


def test_oversized_input_exits_two(tmp_path: Path) -> None:
    code, report = inspect(tmp_path, "", raw=b"---\nname: x\n---\n" + b"a " * 140000)
    assert code == 2 and "error" in report


def test_symlink_input_exits_two(tmp_path: Path) -> None:
    target = tmp_path / "real.md"
    _ = target.write_text("---\nname: x\n---\nbody\n")
    link = tmp_path / "link.md"
    link.symlink_to(target)
    run = subprocess.run([sys.executable, "-I", str(SCRIPT), str(link)], capture_output=True, text=True, check=False)
    assert run.returncode == 2 and "error" in json.loads(run.stdout)


def test_pathological_markdown_finishes_quickly(tmp_path: Path) -> None:
    body = ("`" * 50 + " a ") * 1000 + "\n" + '"' * 20000 + "\n" + ("a. " * 20000) + "\n"
    code, _ = inspect(tmp_path, body, timeout=10)
    assert code == 0


def test_fence_in_a_nested_list_item_indented_four_spaces_is_a_fence(tmp_path: Path) -> None:
    body = f"- Step:\n  - Sub step:\n    ```bash\n    a=`\n    {LONG}\n    ```\n"
    assert found(tmp_path, body) == []


def test_indented_text_after_a_list_item_is_not_a_fence_without_the_item_indent(tmp_path: Path) -> None:
    assert len(found(tmp_path, f"Intro.\n\n    ```bash\n{LONG}\n")) == 1
