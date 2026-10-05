from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import cast

HELPER = Path(__file__).resolve().parents[3] / "skills/skillz/scripts/inspect_skill.py"


def test_helper_reports_facts_and_rejects_escape(tmp_path: Path) -> None:
    target = tmp_path / "SKILL.md"
    _ = target.write_text("---\nname: example\ndescription: Example\n---\n# Body\n[Guide](references/guide.md)\n")
    run = subprocess.run([sys.executable, str(HELPER), str(target)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == {
        "schema_version": 3, "frontmatter_keys": ["description", "name"], "advisory_sentences": [],
        "body_line_count": 2, "local_link_targets": ["references/guide.md"], "long_sentences": [],
    }
    _ = target.write_text("---\nname: example\n---\n[Private](../secret)\n")
    run = subprocess.run([sys.executable, str(HELPER), str(target)], capture_output=True, text=True)
    assert run.returncode == 2
    assert json.loads(run.stdout)["error"] == "link escapes package"


def test_helper_reports_two_sentence_tiers(tmp_path: Path) -> None:
    long = " ".join(["word"] * 30) + "."
    barely = " ".join(["word"] * 20) + "."
    lines = ["---", "name: example", "description: Example", "---", "# Heading", "", "Short one. " + long, barely,
             "", "A sentence that wraps", "across " + " ".join(["word"] * 17) + " lines.", "",
             "```text", long, "```", "", "Inline `" + long + "` code and 'ok'.",
             "Quoted \"" + long + "\" text.", "- " + " ".join(["item"] * 21) + ".", "", "| " + long + " |"]
    target = tmp_path / "SKILL.md"
    _ = target.write_text("\n".join(lines) + "\n")
    run = subprocess.run([sys.executable, str(HELPER), str(target)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    result = cast(dict[str, object], json.loads(run.stdout))
    assert result["schema_version"] == 3
    assert result["long_sentences"] == [{"line": 7, "words": 30}]
    assert result["advisory_sentences"] == [{"line": 10, "words": 23}, {"line": 19, "words": 21}]
    _ = target.write_text("---\nname: x\n---\n[Private](../secret)\n")
    run = subprocess.run([sys.executable, str(HELPER), str(target)], capture_output=True, text=True)
    assert json.loads(run.stdout) == {"schema_version": 3, "error": "link escapes package"}


def test_helper_tiers_split_at_twenty_and_twenty_five_words(tmp_path: Path) -> None:
    lines = ["---", "name: example", "---"]
    for count in (20, 23, 25, 26):
        lines += [" ".join(["word"] * count) + ".", ""]
    target = tmp_path / "SKILL.md"
    _ = target.write_text("\n".join(lines) + "\n")
    run = subprocess.run([sys.executable, str(HELPER), str(target)], capture_output=True, text=True)
    result = cast(dict[str, object], json.loads(run.stdout))
    assert result["advisory_sentences"] == [{"line": 6, "words": 23}, {"line": 8, "words": 25}]
    assert result["long_sentences"] == [{"line": 10, "words": 26}]


def test_helper_accepts_host_alias_but_rejects_package_symlinks(tmp_path: Path) -> None:
    host = tmp_path / "physical"
    package = host / "skill"
    package.mkdir(parents=True)
    target = package / "SKILL.md"
    _ = target.write_text("---\nname: example\n---\n[Guide](references/guide.md)\n")
    references = package / "references"
    references.mkdir()
    guide = references / "guide.md"
    _ = guide.write_text("Guide")
    alias = tmp_path / "host-alias"
    alias.symlink_to(host, target_is_directory=True)
    supplied = alias / "skill/SKILL.md"
    run = subprocess.run([sys.executable, str(HELPER), str(supplied)], capture_output=True, text=True)
    assert run.returncode == 0, run.stdout
    guide.unlink()
    guide.symlink_to(target)
    run = subprocess.run([sys.executable, str(HELPER), str(supplied)], capture_output=True, text=True)
    assert run.returncode == 2
    assert json.loads(run.stdout)["error"] == "package must not contain symlinks"
    guide.unlink()
    references.rmdir()
    internal = package / "internal-guides"
    internal.mkdir()
    _ = (internal / "guide.md").write_text("Guide")
    references.symlink_to(internal, target_is_directory=True)
    run = subprocess.run([sys.executable, str(HELPER), str(supplied)], capture_output=True, text=True)
    assert run.returncode == 2
    assert json.loads(run.stdout)["error"] == "package must not contain symlinks"
    direct = package / "linked.md"
    direct.symlink_to(target)
    run = subprocess.run([sys.executable, str(HELPER), str(direct)], capture_output=True, text=True)
    assert run.returncode == 2
