"""Target-specific proposal and real archive execution for offline E2E fixtures."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import cast
from zipfile import ZipFile

NEW_MODULE = (
    "import json, sys\n"
    "from pathlib import Path\n"
    "def main() -> None:\n"
    "    text = Path(sys.argv[1]).read_text()\n"
    "    print(json.dumps({'text': text.upper()}))\n"
)
NEW_VALUE = f"name = {json.dumps('transform-cli')}\nmodule = {json.dumps(NEW_MODULE)}\n"


def propose_target(candidate: dict[str, str], fields: list[str], answer: dict[str, str]) -> None:
    if "@new-cli" in fields:
        answer["@new-cli"] = NEW_VALUE
        answer["SKILL.md"] += "\nUse scripts/transform-cli.pyz for fixture.md.\n"
    for name in fields:
        if name.startswith("@wedge/") or name == "scripts/transform.py":
            answer[name] = candidate[name].replace("text = text", "text = text.upper()")


def archive_digest(path: Path) -> str:
    digest = hashlib.sha256(b"wedge-content-v1\0")
    with ZipFile(path) as archive:
        for member in archive.infolist():
            name = member.filename.encode("utf-8")
            data = archive.read(member)
            digest.update(len(name).to_bytes(8, "big") + name)
            digest.update(len(data).to_bytes(8, "big") + data)
    return digest.hexdigest()


def task_target(
    workspace: Path, skill: Path, instructions: str,
) -> tuple[str, list[dict[str, object]]] | None:
    for name in ("transform-cli", "transform"):
        script = skill / "scripts" / f"{name}.pyz"
        if not script.is_file() or f"scripts/{name}.pyz" not in instructions:
            continue
        argv = ["/usr/bin/python3", "-I", str(script.relative_to(workspace)), "fixture.md"]
        result = subprocess.run(argv, cwd=workspace, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise ValueError(f"staged {name} failed: {result.stderr}")
        _ = (workspace / "archive.sha256").write_text(archive_digest(script))
        value = cast(dict[str, object], json.loads(result.stdout)).get("text")
        if not isinstance(value, str):
            raise ValueError(f"staged {name} returned no text")
        return value, [{"argv": argv, "exit_code": result.returncode,
                        "stdout": result.stdout, "stderr": result.stderr}]
    return None
