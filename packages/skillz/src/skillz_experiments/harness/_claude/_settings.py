from __future__ import annotations

import sys
import tempfile
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from skillz_experiments._cases import CodedError, loads_untrusted, mapping
from skillz_experiments._contract import Surface
from skillz_experiments._frontmatter import top_fields
from skillz_experiments.harness._claude._constants import CONFIG_PREFIX
from skillz_experiments.harness._claude._options import unique


PERMISSION_DENY_ROOTS = ("/home", "/root", "/Users", "/mnt", "/media", "/opt", "/srv", "/workspaces", "/data")


RUNTIME_READ = ("/usr", "/bin", "/lib", "/lib32", "/lib64", "/libx32", "/proc/self", "/etc/ld.so.cache", "/etc/ld.so.conf",
                "/etc/ld.so.conf.d", "/etc/alternatives", "/etc/localtime", "/etc/passwd", "/etc/group", "/etc/nsswitch.conf",
                "/dev/null", "/dev/zero", "/dev/random", "/dev/urandom")


MACOS_READ = ("/System", "/Library")


def settings(workspace: Path, out: Path | None = None, *, overrides: Collection[str] = (),
             unix_sockets: bool = False) -> dict[str, object]:
    """Return the sandbox floor. A missing sandbox stops the run, and no command leaves the sandbox.

    Sandboxed commands cannot read the host from `/`. The narrower allow wins, so they read only the
    workspace and the runtime roots. The Read tool follows permission rules, not the sandbox, so a deny
    rule covers the host roots, `/proc`, and the run directory `out` for that tool. Nothing else confines that
    tool, so the live preflight proves it: the Read tool must fail to read a file outside the workspace. Commands cannot write
    `.agents` or `.claude`, so they cannot change the candidate or plant project settings.
    Claude Code's own sandbox enforces the `.agents` and `.claude` write deny rules. `overrides` names the skills that
    `skillOverrides` turns off. `unix_sockets` skips the Unix-socket filter, which cannot start where the host blocks
    a nested user namespace; the live preflight then probes a host socket. The `Bash` allow rule stops the approval
    prompt, which `-p` turns into a denial, so the rule lets probes reach the sandbox. The live preflight detects any leak.
    """
    runtime = [*RUNTIME_READ, *(MACOS_READ if sys.platform == "darwin" else ())]
    temporary = unique([tempfile.gettempdir(), str(Path(tempfile.gettempdir()).resolve())])
    hidden = ["/proc", *([] if out is None else [str(out), str(out.resolve())])]
    network: dict[str, object] = {"allowedDomains": [], "strictAllowlist": True}
    if unix_sockets:
        network["allowAllUnixSockets"] = True
    protected = [f"{root}/{name}" for root in unique([str(workspace), str(workspace.resolve())]) for name in (".agents", ".claude")]
    floor: dict[str, object] = {
        "sandbox": {"enabled": True, "failIfUnavailable": True, "allowUnsandboxedCommands": False, "network": network,
                    "filesystem": {"denyRead": ["/"], "denyWrite": protected,
                                   "allowRead": unique([str(workspace), str(workspace.resolve()), *runtime])}},
        "disableBundledSkills": True, "disableAllHooks": True,
        "permissions": {"allow": ["Skill", "Bash"],
                        "deny": [*[f"Read(/{root}/**)" for root in unique([*PERMISSION_DENY_ROOTS, str(Path.home()), *hidden])],
                                 *[f"Read(/{root}/{CONFIG_PREFIX}*/**)" for root in temporary]]}}
    if overrides:
        floor["skillOverrides"] = {name: "off" for name in sorted(overrides)}
    return floor


@dataclass(frozen=True)
class Declared:
    """The names that a contract surface lets Claude Code load: companion skills, agents, and MCP servers."""
    skills: frozenset[str] = frozenset()
    agents: frozenset[str] = frozenset()
    servers: frozenset[str] = frozenset()


def _frontmatter_name(text: str) -> str | None:
    """Return the `name:` value of the leading frontmatter block, or None when the block or the field is absent."""
    fields = top_fields(text)
    return (fields or {}).get("name") or None


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
