from pathlib import Path

from vulture.utils import ExitCode

class Item:
    name: str
    typ: str
    filename: Path
    first_lineno: int
    def get_report(self, add_size: bool = False) -> str: ...
    def get_whitelist_string(self) -> str: ...

class Vulture:
    exit_code: ExitCode
    def __init__(self, verbose: bool = False, ignore_names: list[str] | None = None, ignore_decorators: list[str] | None = None) -> None: ...
    def scavenge(self, paths: list[str], exclude: list[str] | None = None) -> None: ...
    def get_unused_code(self, min_confidence: int = 0, sort_by_size: bool = False) -> list[Item]: ...
