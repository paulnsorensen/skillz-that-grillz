"""The `@new-cli` component: one new fromargs wedge target that a proposal adds to the skill.

The component value is TOML with the keys `name` and `module`. The host expands it into the skill file
`src/<package>/__init__.py` and a generated `[[target]]` in the candidate's `wedge.toml`. The host reads the
module with `ast` only. It never imports or runs it.
"""
from __future__ import annotations

import ast
import keyword
import re
import sys
import tomllib
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import cast

from wedge._config import TARGET_KEYS, ConfigError, WedgeConfig, parse_targets

from skillz_experiments._candidate import NEW_CLI, TEXT_FILE_LIMIT
from skillz_experiments._cases import CodedError

NAME_PATTERN = re.compile(r"[a-z][a-z0-9-]{1,39}")
_KEYS = frozenset({"name", "module"})
# The raw value holds the module plus the TOML wrapper: the two keys, the quotes, and the escapes.
VALUE_LIMIT = 2 * TEXT_FILE_LIMIT
# These keys of a target table or a single-target file belong to one target. The others, and `groups`, are shared defaults.
_PER_TARGET = TARGET_KEYS - {"groups"}
_HELPER = re.compile(r"scripts/[\w.-]+\.py(?!\w|\.\w)")
_ESCAPES = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f", "\r": "\\r"}


@dataclass(frozen=True)
class NewCli:
    """A checked `@new-cli` value."""

    name: str
    module: str

    @property
    def package(self) -> str:
        return self.name.replace("-", "_")

    @property
    def path(self) -> str:
        """Return the skill file that holds the module."""
        return f"src/{self.package}/__init__.py"


def parse_value(value: str) -> NewCli:
    """Check the TOML text of `@new-cli`. Raise a `CodedError` that names the first fault."""
    if len(value) > VALUE_LIMIT:
        raise CodedError("new-cli-module-size", f"@new-cli has {len(value)} characters; the limit is {VALUE_LIMIT}")
    try:
        table = tomllib.loads(value)
    except tomllib.TOMLDecodeError as error:
        raise CodedError("new-cli-malformed", f"@new-cli is not valid TOML: {error}") from None
    except (RecursionError, MemoryError):
        raise CodedError("new-cli-malformed", "@new-cli is nested too deeply to read as TOML") from None
    name = table.get("name")
    if "target" in table or isinstance(name, list):
        raise CodedError("new-cli-second-target", "@new-cli adds exactly one target; it holds a second one")
    extra = sorted(set(table) - _KEYS)
    if extra:
        raise CodedError("new-cli-extra-key", f"@new-cli has the key {extra[0]!r}; the only keys are name and module")
    missing = sorted(_KEYS - set(table))
    if missing:
        raise CodedError("new-cli-missing-key", f"@new-cli lacks the key {missing[0]!r}; it needs name and module")
    module = cast(object, table["module"])
    if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
        raise CodedError("new-cli-bad-name", f"@new-cli name {name!r} must match [a-z][a-z0-9-]{{1,39}}")
    package = name.replace("-", "_")
    if not package.isidentifier() or keyword.iskeyword(package):
        raise CodedError("new-cli-bad-name", f"@new-cli name {name!r} gives the package {package!r}, which is not an identifier")
    if not isinstance(module, str):
        raise CodedError("new-cli-malformed", f"@new-cli module must be text, not {type(module).__name__}")
    if not module.strip():
        raise CodedError("new-cli-module-size", "@new-cli module is empty; give Python source")
    if len(module) > TEXT_FILE_LIMIT:
        raise CodedError("new-cli-module-size", f"@new-cli module has {len(module)} characters; the limit is {TEXT_FILE_LIMIT}")
    return NewCli(name, module)


def prompt_rule() -> str:
    """Return the reflection-prompt sentences that describe the `@new-cli` component."""
    return (f"The {NEW_CLI} component adds one new fromargs wedge target as TOML with the keys name and module. "
            + f"The name matches {NAME_PATTERN.pattern} and is new. "
            + "The package is the name with each hyphen changed to an underscore. "
            + "The module imports only the standard library, its own package, fromargs, and the site layer. "
            + "The module defines a top-level main, and the entry is <package>:main. "
            + "The host builds the target to scripts/<name>.pyz. "
            + "SKILL.md calls it with python3 scripts/<name>.pyz. "
            + "The host rejects a second target. ")


def fromargs_include(configs: Sequence[WedgeConfig]) -> tuple[WedgeConfig, str] | None:
    """Return the first target that includes the fromargs package, and that include entry."""
    for config in configs:
        for entry in config.include:
            if PurePosixPath(entry).name == "fromargs":
                return config, entry
    return None


def _source_package(source: str) -> str:
    base = PurePosixPath(source)
    return base.stem if base.suffix == ".py" else base.name


def _collision(cli: NewCli, configs: Sequence[WedgeConfig], files: Collection[str],
               layer_modules: Collection[str] | None = None) -> None:
    package = cli.package
    if cli.name in {config.name for config in configs}:
        raise CodedError("new-cli-name-collision", f"@new-cli name {cli.name!r} matches an existing wedge target")
    taken = {_source_package(config.source) for config in configs}
    taken |= {PurePosixPath(entry).name for config in configs for entry in config.include}
    if package in taken or package in sys.stdlib_module_names:
        raise CodedError("new-cli-package-collision",
                         f"@new-cli package {package!r} matches a source package, an include package, or a standard library module")
    if layer_modules is not None and package in layer_modules:
        raise CodedError("new-cli-package-collision", f"@new-cli package {package!r} matches a top-level module of the site layer")
    if any(name in (f"src/{package}", f"src/{package}.py") or name.startswith(f"src/{package}/") for name in files):
        raise CodedError("new-cli-package-collision", f"@new-cli package {package!r} matches the skill path src/{package}")


def _has_main(tree: ast.Module) -> bool:
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "main":
            return True
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if any(isinstance(target, ast.Name) and target.id == "main" for target in targets):
            return True
    return False


def _imports(cli: NewCli, layer_modules: Collection[str]) -> None:
    """Reject a module with no top-level `main` or an import outside the standard library, itself, fromargs, and the site layer.

    Parse only. Dynamic imports (`__import__`, `importlib`) are outside this check; they fail inside the sandbox.
    """
    try:
        tree = ast.parse(cli.module)
    except (SyntaxError, ValueError) as error:
        raise CodedError("new-cli-syntax", f"@new-cli module does not parse: {error}") from None
    except (RecursionError, MemoryError):
        raise CodedError("new-cli-syntax", "@new-cli module is nested too deeply to parse") from None
    allowed = set(sys.stdlib_module_names) | {cli.package, "fromargs"} | set(layer_modules)
    for node in ast.walk(tree):
        roots: list[str] = []
        if isinstance(node, ast.Import):
            roots = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            roots = ["" if node.level else (node.module or "").split(".")[0]]
        for root in roots:
            if root not in allowed:
                raise CodedError("new-cli-undeclared-import", f"@new-cli module imports {root or 'a relative module'!r}, "
                                 + "which is not standard library, the package itself, fromargs, or in the site layer")
    if not _has_main(tree):
        raise CodedError("new-cli-no-main", "@new-cli module has no top-level `main`; the entry is <package>:main")


def _text(value: object) -> str:
    if isinstance(value, str):
        return '"' + "".join(_ESCAPES.get(char) or (f"\\u{ord(char):04x}" if ord(char) < 0x20 or ord(char) == 0x7F else char)
                             for char in value) + '"'
    if isinstance(value, list):
        return "[" + ", ".join(_text(item) for item in cast(list[object], value)) + "]"
    raise ConfigError(f"wedge.toml holds a value that the runner cannot write: {value!r}")


def _table(name: str, entry: str, source: str, include: Sequence[str], source_paths: Sequence[str] = (),
           *, no_groups: bool = False) -> str:
    lines = ["[[target]]", f"name = {_text(name)}", f"entry = {_text(entry)}", f"source = {_text(source)}"]
    if source_paths:
        lines.append(f"source_paths = {_text(list(source_paths))}")
    if include:
        lines.append(f"include = {_text(list(include))}")
    if no_groups:
        lines.append("groups = []")
    return "\n".join(lines) + "\n"


def generated_toml(text: str, configs: Sequence[WedgeConfig], include: str, cli: NewCli) -> str:
    """Return `text`, the candidate's `wedge.toml`, with the generated `[[target]]` appended.

    A multi-target file keeps its text and gains the new table at the end. A single-target file turns into the
    multi-target form, because wedge forbids a mix. The conversion is deterministic: the shared keys of the file
    (those besides name, entry, source, source_paths, and include) stay at the top in file order, and the old
    target moves into a table of its resolved values. The conversion drops the comments of a single-target file.
    The new table sets `groups = []`: the base dependencies already provide fromargs, so it never inherits a shared group.
    """
    new = _table(cli.name, f"{cli.package}:main", f"src/{cli.package}", [include], no_groups=True)
    if configs[0].multi:
        return text.rstrip("\n") + "\n\n" + new
    own = cast(Mapping[str, object], tomllib.loads(text))
    shared = "".join(f"{key} = {_text(value)}\n" for key, value in own.items() if key not in _PER_TARGET)
    old = configs[0]
    return shared + "\n" + _table(old.name, old.entry, old.source, old.include, old.source_paths) + "\n" + new


def _inherited_source_paths(skill_root: Path, text: str) -> tuple[str, ...]:
    """Return the `source_paths` that the last target of the `wedge.toml` content `text` has. Raise `ConfigError` if it does not load."""
    return parse_targets(skill_root, text)[-1].source_paths


def inherits_source_paths(skill_root: Path, text: str) -> bool:
    """Return whether a generated target for the `wedge.toml` content `text` would carry `source_paths`.

    A shared top-level `source_paths` reaches every table of a multi-target file, so a new table would inherit it.
    Return True when the file cannot take a target.
    """
    try:
        configs = parse_targets(skill_root, text)
        found = fromargs_include(configs)
        if found is None:
            return True
        taken = {config.name for config in configs}
        probe = next(name for name in (f"probe-cli{index}" for index in range(len(taken) + 1)) if name not in taken)
        return bool(_inherited_source_paths(skill_root, generated_toml(text, configs, found[1], NewCli(probe, ""))))
    except ConfigError:
        return True


def expand_files(skill_root: Path, files: Mapping[str, str], value: str | NewCli,
                 layer_modules: Collection[str] | None = None) -> dict[str, str]:
    """Return the skill files that `value` adds or changes: the module file and the new `wedge.toml`.

    `files` is the seed. `value` is the component text, or the `NewCli` that `parse_value` returned.
    Pass `layer_modules` to check the imports of the module against the site layer.
    Raise a `CodedError` for the first fault.
    """
    cli = value if isinstance(value, NewCli) else parse_value(value)
    try:
        configs = parse_targets(skill_root, files["wedge.toml"])
    except (ConfigError, KeyError) as error:
        raise CodedError("new-cli-unavailable", f"the seed wedge.toml cannot take a new target: {error}") from None
    found = fromargs_include(configs)
    if found is None:
        raise CodedError("new-cli-unavailable", "no target of wedge.toml includes the fromargs package")
    _collision(cli, configs, files, layer_modules)
    if layer_modules is not None:
        _imports(cli, layer_modules)
    text = generated_toml(files["wedge.toml"], configs, found[1], cli)
    try:
        inherited = _inherited_source_paths(skill_root, text)
    except ConfigError as error:
        raise CodedError("new-cli-unavailable", f"the generated wedge.toml does not load: {error}") from None
    if inherited:
        raise CodedError("new-cli-unavailable", "the wedge.toml shares source_paths, which the new target would inherit")
    return {"wedge.toml": text, cli.path: cli.module}


def unused_helpers(seed: Mapping[str, str], winner: Mapping[str, str]) -> list[str]:
    """Return the `scripts/*.py` helpers that the seed SKILL.md names and the winner SKILL.md no longer names.

    A new target replaces a helper when the winner guide stops naming it. The helper file stays in the skill.
    """
    before = set(_HELPER.findall(seed.get("SKILL.md", "")))
    after = set(_HELPER.findall(winner.get("SKILL.md", "")))
    return sorted(before - after)
