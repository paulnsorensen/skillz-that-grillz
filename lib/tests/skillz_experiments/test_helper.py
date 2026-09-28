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
