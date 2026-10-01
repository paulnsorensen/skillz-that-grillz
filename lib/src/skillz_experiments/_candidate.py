from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from skillz_experiments._cases import Case, digest, mapping, relative


def candidate_files(value: object) -> dict[str, str]:
    files: dict[str, str] = {}
    for name, content in mapping(value).items():
        if not isinstance(content, str):
            raise ValueError("candidate content must be text")
        files[relative(name)] = content
    return files


@dataclass(frozen=True)
class Candidate:
    files: dict[str, str]
    editable: tuple[str, ...]

    @classmethod
    def capture(cls, root: Path, editable: list[str]) -> Candidate:
        if root.is_symlink():
            raise ValueError("candidate must not contain symlinks")
        files: dict[str, str] = {}
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise ValueError("candidate must not contain symlinks")
            if path.is_file():
                name = relative(path.relative_to(root).as_posix())
                if name == "scripts/skillz-experiment.pyz":
                    continue
                if path.stat().st_size > 262144:
                    raise ValueError("candidate file exceeds size limit")
                files[name] = path.read_text(encoding="utf-8")
        if sum(len(text) for text in files.values()) > 1_000_000:
            raise ValueError("candidate package exceeds size limit")
        if "SKILL.md" not in files or not set(editable) <= files.keys():
            raise ValueError("candidate needs SKILL.md and existing editable components")
        return cls(files, tuple(editable))

    @property
    def identity(self) -> str:
        return digest(self.files)

    def changed(self, components: dict[str, str]) -> Candidate:
        if set(components) != set(self.editable):
            raise ValueError("proposal components differ from the frozen set")
        if any(len(text) > 262144 for text in components.values()):
            raise ValueError("proposal exceeds component size limit")
        files = self.files | components
        if sum(len(text) for text in files.values()) > 1_000_000:
            raise ValueError("candidate package exceeds size limit")
        return Candidate(files, self.editable)

    def materialize(self, root: Path) -> None:
        for name, content in self.files.items():
            path = root / relative(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            _ = path.write_text(content, encoding="utf-8")


def make_workspace(workspace: Path) -> Path:
    for directory in ("home", "tmp", ".agents/skills"):
        (workspace / directory).mkdir(parents=True, exist_ok=True)
    return workspace


def stage_task(workspace: Path, candidate: Candidate | None, case: Case | None) -> None:
    if candidate is not None:
        candidate.materialize(workspace / ".agents/skills/skillz")
        _ = (workspace / ".agents/skills/skillz/EXPERIMENT_MARKER").write_text(candidate.identity)
    if case is not None:
        for name, content in case.files.items():
            path = workspace / name
            path.parent.mkdir(parents=True, exist_ok=True)
            _ = path.write_text(content)
