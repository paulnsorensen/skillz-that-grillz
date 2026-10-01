from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HELPER = Path(__file__).resolve().parents[3] / "skills/skillz/scripts/inspect_skill.py"


def test_helper_reports_facts_and_rejects_escape(tmp_path: Path) -> None:
    target = tmp_path / "SKILL.md"
    _ = target.write_text("---\nname: example\ndescription: Example\n---\n# Body\n[Guide](references/guide.md)\n")
    run = subprocess.run([sys.executable, str(HELPER), str(target)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == {
        "schema_version": 1, "frontmatter_keys": ["description", "name"],
        "body_line_count": 2, "local_link_targets": ["references/guide.md"],
    }
    _ = target.write_text("---\nname: example\n---\n[Private](../secret)\n")
    run = subprocess.run([sys.executable, str(HELPER), str(target)], capture_output=True, text=True)
    assert run.returncode == 2
    assert json.loads(run.stdout)["error"] == "link escapes package"


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
