from __future__ import annotations

import sys
import tempfile
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from skillz_experiments._cases import CodedError, loads_untrusted, mapping
from skillz_experiments._contract import Surface
from skillz_experiments._frontmatter import read_frontmatter
from skillz_experiments.harness._claude._constants import CONFIG_PREFIX
from skillz_experiments.harness._claude._options import unique


PERMISSION_DENY_ROOTS = ("/home", "/root", "/Users", "/mnt", "/media", "/opt", "/srv", "/workspaces", "/data")


RUNTIME_READ = ("/usr", "/bin", "/lib", "/lib32", "/lib64", "/libx32", "/proc/self", "/etc/ld.so.cache", "/etc/ld.so.conf",
                "/etc/ld.so.conf.d", "/etc/alternatives", "/etc/localtime", "/etc/passwd", "/etc/group", "/etc/nsswitch.conf",
                "/dev/null", "/dev/zero", "/dev/random", "/dev/urandom")


MACOS_READ = ("/System", "/Library")


MACOS_DENY_READ = ("/Volumes", "/private/var/folders", "/var/folders")
"""The macOS roots that Bash may not read, besides the host roots. Claude Code 2.1.309 on macOS does not reopen a
narrower `allowRead` inside `denyRead: ["/"]`, and its own temp files then fail (#131). So macOS denies a root list."""


def settings(workspace: Path, out: Path | None = None, *, config: Path | None = None, overrides: Collection[str] = (),
             unix_sockets: bool = False) -> dict[str, object]:
    """Return the sandbox floor. A missing sandbox stops the run, and no command leaves the sandbox.

    On Linux, sandboxed commands cannot read the host from `/`. The narrower allow wins, so they read only the
    workspace and the runtime roots. On macOS, they cannot read the host roots, the user temp roots, the temporary
    directory, the home directory, the run directory, or the Claude config directory `config`, and the workspace allow
    reopens its own path. Other system paths stay readable. The Read tool follows permission rules, not the sandbox,
    so a deny rule covers the host roots, `/proc`, the run directory `out`, and `config` for that tool. The
    `blockReadsOutsideWorkingDirectories` permission (Claude Code 2.1.257 or later) makes the file tools refuse every
    path outside the working directory, the workspace, in every permission mode (#176). The live preflight proves it:
    the Read tool must fail to read the sealed file and `/etc/hosts`. Commands cannot write
    `.agents` or `.claude`, so they cannot change the candidate or plant project settings.
    Claude Code's own sandbox enforces the `.agents` and `.claude` write deny rules. `overrides` names the skills that
    `skillOverrides` turns off. `unix_sockets` skips the Unix-socket filter, which cannot start where the host blocks
    a nested user namespace; the live preflight then probes a host socket. The `Bash` allow rule stops the approval
    prompt, which `-p` turns into a denial, so the rule lets probes reach the sandbox. The live preflight detects any leak.
    """
    runtime = [*RUNTIME_READ, *(MACOS_READ if sys.platform == "darwin" else ())]
    temporary = unique([tempfile.gettempdir(), str(Path(tempfile.gettempdir()).resolve())])
    hidden = ["/proc", *(str(form) for path in (out, config) if path is not None for form in (path, path.resolve()))]
    network: dict[str, object] = {"allowedDomains": [], "strictAllowlist": True}
    if unix_sockets:
        network["allowAllUnixSockets"] = True
    protected = [f"{root}/{name}" for root in unique([str(workspace), str(workspace.resolve())]) for name in (".agents", ".claude")]
    floor: dict[str, object] = {
        "sandbox": {"enabled": True, "failIfUnavailable": True, "allowUnsandboxedCommands": False, "network": network,
                    "filesystem": {"denyRead": _deny_read([*hidden, *temporary]), "denyWrite": protected,
                                   "allowRead": unique([str(workspace), str(workspace.resolve()), *runtime])}},
        "disableBundledSkills": True, "disableAllHooks": True,
        "permissions": {"allow": ["Skill", "Bash"], "blockReadsOutsideWorkingDirectories": True,
                        "deny": [*[f"Read(/{root}/**)" for root in unique([*PERMISSION_DENY_ROOTS, str(Path.home()), *hidden])],
                                 *[f"Read(/{root}/{CONFIG_PREFIX}*/**)" for root in temporary]]}}
    if overrides:
        floor["skillOverrides"] = {name: "off" for name in sorted(overrides)}
    return floor


def _deny_read(hidden: list[str]) -> list[str]:
    """Return the `denyRead` list of the sandbox floor: `/` on Linux, and a root list on macOS.

    On macOS, `hidden` holds the temporary directory too, so a `TMPDIR` outside `/var/folders` stays denied.
    """
    if sys.platform != "darwin":
        return ["/"]
    return unique([*PERMISSION_DENY_ROOTS, *MACOS_DENY_READ, str(Path.home()), *hidden])


@dataclass(frozen=True)
class Declared:
    """The names that a contract surface lets Claude Code load: companion skills, agents, and MCP servers."""
    skills: frozenset[str] = frozenset()
    agents: frozenset[str] = frozenset()
    servers: frozenset[str] = frozenset()


def _frontmatter_name(text: str) -> str | None:
    """Return the `name:` value of the leading frontmatter block, or None when the block or the field is absent."""
    try:
        fields = read_frontmatter(text)
    except ValueError:
        return None
    return fields.get("name") or None


def declared_names(surface: Surface | None, files: Mapping[str, str] | None) -> Declared:
    """Name what `surface` declares. Stop with `contract-surface-missing` when a declared file is not in `files`.

    A companion is named by its directory, an agent by its file stem, and an MCP server by a key of `mcpServers`.
    An agent file must carry a frontmatter `name:` equal to its file stem, or the run stops with `contract-surface`.
    """
    if surface is None:
        return Declared()
    found = files or {}
    missing = [path for path in (*surface.agents, *([] if surface.mcp_config is None else [surface.mcp_config]))
               if path not in found]
    missing += [f"{path}/SKILL.md" for path in surface.companions if f"{path}/SKILL.md" not in found]
    if missing:
        raise CodedError("contract-surface-missing", f"the contract surface names files that the skill lacks: {', '.join(missing)}")
    for path in surface.agents:
        stem = PurePosixPath(path).stem
        if _frontmatter_name(found[path]) != stem:
            raise CodedError("contract-surface", f"contract surface agent {path} needs a frontmatter name equal to its file name {stem!r}")
    servers: frozenset[str] = frozenset()
    if surface.mcp_config is not None:
        try:
            servers = frozenset(mapping(mapping(loads_untrusted(found[surface.mcp_config])).get("mcpServers", {})))
        except ValueError:
            raise CodedError("contract-surface-missing", f"the MCP config {surface.mcp_config} is not a JSON object "
                             + "with an `mcpServers` object") from None
    return Declared(frozenset(PurePosixPath(path).name for path in surface.companions),
                    frozenset(PurePosixPath(path).stem for path in surface.agents), servers)


@dataclass(frozen=True)
class Inventory:
    """The command names that Claude Code lists to an SDK client.

    `builtin` holds Claude Code's own commands, which the docs keep hidden from the model when bundled skills are off.
    `foreign` holds every other name except the staged probe skill: account, user, or plugin skills.
    """
    builtin: frozenset[str]
    foreign: frozenset[str]
