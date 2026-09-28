from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from skillz_experiments._cases import digest, relative


@dataclass(frozen=True)
class Candidate:
    files: dict[str, str]
    editable: tuple[str, ...]

    @classmethod
    def capture(cls, root: Path, editable: list[str]) -> Candidate:
        if any(path.is_symlink() for path in (root, *root.parents)):
            raise ValueError("candidate symlinks are forbidden")
        files: dict[str, str] = {}
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise ValueError("candidate symlinks are forbidden")
            if path.is_file():
                name = relative(path.relative_to(root).as_posix())
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
            raise ValueError("proposal changed frozen components")
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
