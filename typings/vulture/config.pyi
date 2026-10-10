from typing import TypedDict

class InputError(Exception): ...

class Config(TypedDict):
    paths: list[str]
    exclude: list[str]
    ignore_names: list[str]
    ignore_decorators: list[str]
    min_confidence: int
    sort_by_size: bool
    make_whitelist: bool
    verbose: bool

def make_config(argv: list[str] | None = None) -> Config: ...
