"""First-run surface: only headless `claude -p` or `codex exec` run (AC-5), and `run` plus `export` suffice (AC-17)."""
from __future__ import annotations

import json
import os
import shutil
import stat
import threading
from collections.abc import Callable
from pathlib import Path
from typing import cast, final

import pytest

from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case
from skillz_experiments._cli import main
from skillz_experiments._codex import Codex
from skillz_experiments._harness import Configuration
from skillz_experiments._records import read
from skillz_experiments._runtime import Budget

FIXTURES = Path(__file__).parent / "fixtures"

pytestmark = pytest.mark.usefixtures("host_login")


def _executable(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_run_accepts_only_claude_or_codex_and_launches_headless(tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """The CLI rejects other harness names. For claude and codex this checks adapter argv, not the run path."""
    for harness in ("command", "other"):
        code = main(["run", "--target", str(tmp_path), "--out", str(tmp_path / "out"), "--model", "m",
                     "--harness", harness])
        captured = capsys.readouterr()
        assert code == 2
        message = cast(str, json.loads(captured.err)["error"])
        assert f'Invalid value "{harness}" for --harness' in message
        assert not (tmp_path / "out").exists()

    claude = _executable(tmp_path / "claude-bin/claude", (FIXTURES / "fake_claude.py").read_text())
    config = tmp_path / "harness.json"
    _ = config.write_text(json.dumps({"schema_version": 1, "adapter": "claude", "command": [str(claude)]}))
    session = Configuration.load(config, "claude-test-model").create("claude-test-model", Budget(10, 120, 0), lambda: None)
    try:
        _ = session.transports["task"].invoke("hello")
    finally:
        session.close()
    logged = [cast(dict[str, object], json.loads(line))
              for line in claude.with_name("claude.log").read_text().splitlines()]
    claude_argv = cast(list[str], logged[0]["argv"])
    assert claude_argv[:2] == ["--restricted", "-p"]

    if Path("/etc/codex/skills").exists():
        pytest.skip("admin skill roots stop the Codex transport")
    host = tmp_path / "host-codex"
    host.mkdir()
    _ = (host / "auth.json").write_text("{}")
    log = tmp_path / "codex.log"
    codex = _executable(tmp_path / "codex-bin/codex", "#!/bin/sh\n"
                        + f"echo \"$@\" >> {log}\n"
                        + "while [ $# -gt 0 ]; do\n"
                        + "  if [ \"$1\" = --output-last-message ]; then echo '{}' > \"$2\"; fi\n"
                        + "  shift\n"
                        + "done\n")
    monkeypatch.setenv("PATH", f"{codex.parent}:{os.environ['PATH']}")
    monkeypatch.setenv("CODEX_HOME", str(host))
    adapter = Codex("codex-test-model", Budget(10, 120, 0), lambda: None)
    try:
        _ = adapter.invoke("hello")
    finally:
        adapter.close()
    assert log.read_text().split()[0] == "exec"


@final
class _Provider:
    """A fake provider: the seed scores 0 and any proposal that says `improved` scores 1."""

    def __init__(self, budget: Budget, checkpoint: Callable[[], None]) -> None:
        self.budget, self.checkpoint, self.lock = budget, checkpoint, threading.Lock()

    def preflight(self) -> dict[str, object]:
        return {"isolation": "test-provider"}

    def close(self) -> None:
        pass

    def evaluate(self, candidate: Candidate, case: Case, *, holdout: bool = False) -> dict[str, object]:
        self.budget.claim(holdout=holdout)
        self.checkpoint()
        return {"score": float("improved" in candidate.files["SKILL.md"]), "candidate_hash": candidate.identity,
                "case_hash": case.identifier, "usage": {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1}}

    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]:
        del prompt, candidate, case
        assert schema is not None
        self.budget.claim(holdout=holdout)
        self.checkpoint()
        return {"answer": {name: "improved" for name in cast(list[str], schema["required"])}}


def test_first_run_needs_only_run_and_export_without_contract_or_manifest(tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """Drive `main(["run", ...])` through its three stops to `complete`. Only the harness `create` step is faked."""
    def create(_self: Configuration, _model: str, budget: Budget, checkpoint: Callable[[], None]) -> _Provider:
        return _Provider(budget, checkpoint)

    monkeypatch.setattr(Configuration, "create", create)
    target = tmp_path / "echo-skill"
    _ = shutil.copytree(FIXTURES / "echo-skill", target)
    _ = (target / "SKILL.md.fixture").rename(target / "SKILL.md")
    shutil.rmtree(target / "evals")
    assert sorted(path.name for path in target.iterdir()) == ["SKILL.md"]
    out = tmp_path / "run"
    stops: list[str] = []

    def call(*approvals: str) -> tuple[int, dict[str, object]]:
        code = main(["run", "--target", str(target), "--out", str(out), "--model", "local-test", "--harness", "claude",
                     "--live", *approvals])
        captured = capsys.readouterr()
        if code:
            stops.append(cast(str, json.loads(captured.err)["code"]))
        return code, cast(dict[str, object], json.loads(captured.out))

    code, missing = call()
    assert code == 1 and cast(dict[str, object], missing["facts"])["name"] == "echo-skill"
    draft = [{"id": f"t{family}-{index}", "family": f"f{family}", "kind": "task", "request": f"request-{family}-{index}",
              "files": {"fixture.md": "hello\n"}, "expected": {}, "source": "skill"}
             for family in range(5) for index in range(2)]
    _ = Path(cast(str, missing["draft"])).write_text(json.dumps(draft))
    _, cases = call()
    case_hash = cast(str, cases["case_hash"])
    _, budget = call("--approve-cases", case_hash)
    calls = cast(dict[str, int], budget["estimate"])["calls"]
    code, result = call("--approve-cases", case_hash, "--approve-budget", str(calls))
    assert code == 0 and stops == ["cases-missing", "cases-unapproved", "budget-unapproved"]
    assert result["phase"] == "complete"
    assert read(out / "run.json")["contract_source"] == "intake"
    exported = main(["export", str(out), "--out", str(tmp_path / "export")])
    assert exported == 0
    assert (tmp_path / "export/candidate.patch").is_file()
    assert sorted(path.name for path in target.iterdir()) == ["SKILL.md"]
