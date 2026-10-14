from __future__ import annotations

import functools
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import cast

from skillz_experiments._cases import CodedError, mapping
from skillz_experiments.harness._claude._constants import INSTALL_BUBBLEWRAP, SANDBOX_STDERR
from skillz_experiments.harness._seatbelt import command_profile


# Curl exit codes that show a request attempt. Codes 5 and 6 are name-resolution failures, and 22 needs `-f`.
CURL_ATTEMPTED = frozenset({0, 7, 28, 52, 56, 97})


SANDBOX_HELPERS = ("socat",)


SANDBOX_CAPTURE = "/run/skillz-capture"


INSTALL_SOCAT = "install socat (for example `sudo apt install socat`)"


USERNS_FIX = ("allow unprivileged user namespaces with `sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0`, "
              "or add an AppArmor profile for bwrap")


LIVE_TOOLS = ("/usr/bin/python3", "/usr/bin/curl")


class NetworkIsolationFailed(CodedError):
    """The live Bash path reaches the network, or the network probe gives no usable result."""

    def __init__(self, reason: str) -> None:
        super().__init__("network-isolation-failed", f"Claude Code isolation preflight fails: {reason}; no unsafe fallback")


class SandboxUnavailable(CodedError):
    """The Bash sandbox cannot start. The message names the cause and the fix. The run never falls back."""

    def __init__(self, cause: str, fix: str) -> None:
        self.fix: str = fix
        super().__init__("sandbox-unavailable", f"Claude Code sandbox unavailable: {cause}; no unsafe fallback; fix: {fix}")


def host_tools() -> list[tuple[str, bool, str]]:
    """List each host tool that the sandbox and the live preflight need, as (name, found, fix).

    The doctor reports these rows, and the helper sandbox probe stops on the first missing one.
    """
    wanted = [] if sys.platform == "darwin" else [
        ("bwrap", INSTALL_BUBBLEWRAP), *((helper, INSTALL_SOCAT) for helper in SANDBOX_HELPERS)]
    return [*((name, shutil.which(name) is not None, fix) for name, fix in wanted),
            *((path, Path(path).is_file(), f"install {Path(path).name}") for path in LIVE_TOOLS)]


def sandbox_fix(detail: str) -> str:
    """Pick the fix that matches the sandbox failure text."""
    text = detail.lower()
    if "socat" in text:
        return INSTALL_SOCAT
    if any(word in text for word in ("uid map", "operation not permitted", "permission denied", "namespace", "apparmor")):
        return USERNS_FIX
    if "bubblewrap" in text or "bwrap" in text:
        return INSTALL_BUBBLEWRAP
    return f"{INSTALL_BUBBLEWRAP}; {INSTALL_SOCAT}; {USERNS_FIX}"


EXTERNAL_ADDRESS = ("1.1.1.1", 443)


LOOKUP_NAME = "example.com"


# Curl exit code for a name that does not resolve. Any attempt code instead means that the lookup worked.
CURL_UNRESOLVED = 6


def network_commands(port: int, token: str) -> list[str]:
    """Build the live Bash network attempts. Each command must run word for word.

    The first command is a direct loopback TCP attempt. It sends the token if it connects, and prints `denied-<token>`
    if not. The second is a direct TCP attempt on a non-loopback address; it sends nothing and prints the same words.
    The third is a direct name lookup (`--noproxy '*'` skips any proxy, so only the host resolver answers); it prints
    `exit-<code>-<token>`. A host that resolves names only through the sandbox proxy shows exit 6 here, which is the
    wanted denial. The last two send an HTTP request through any configured proxy. `--noproxy ''` clears `NO_PROXY`, so
    curl uses the sandbox proxy for loopback names too. They print `exit-<code>-<token>`. The listener judges the token.
    """
    curl = "/usr/bin/curl --noproxy '' --max-time 5 -sS -o /dev/null"
    host, external = EXTERNAL_ADDRESS
    return ["/usr/bin/python3 -c \"import socket;c=socket.socket();c.settimeout(5);"
            + f"r=c.connect_ex(('127.0.0.1',{port}));r or c.send(b'{token}');"
            + f"print(('open-' if r==0 else 'denied-')+'{token}')\"",
            "/usr/bin/python3 -c \"import socket;c=socket.socket();c.settimeout(5);"
            + f"r=c.connect_ex(('{host}',{external}));print(('open-' if r==0 else 'denied-')+'{token}')\"",
            f"/usr/bin/curl --noproxy '*' --max-time 5 -sS -o /dev/null http://{LOOKUP_NAME}/; echo exit-$?-{token}",
            *[f"{curl} http://{host}:{port}/{token}; echo exit-$?-{token}" for host in ("127.0.0.1", "localhost")]]


def _results(events: list[dict[str, object]], command: str) -> list[str]:
    """Return the Bash results that answer a tool call whose command equals `command`."""
    uses: set[str] = set()
    results: list[str] = []
    for event in events:
        message = event.get("message")
        content = cast(dict[str, object], message).get("content") if isinstance(message, dict) else None
        for block in cast(list[object], content) if isinstance(content, list) else []:
            item = mapping(block)
            if item.get("type") == "tool_use" and item.get("name") == "Bash" and isinstance(item.get("id"), str):
                if mapping(item.get("input", {})).get("command") == command:
                    uses.add(cast(str, item["id"]))
            elif item.get("type") == "tool_result" and item.get("tool_use_id") in uses:
                results.append(json.dumps(item))
    return results


def _exit_codes(events: list[dict[str, object]], command: str, token: str) -> list[int]:
    return [int(code) for text in _results(events, command)
            for code in cast(list[str], re.findall(rf"exit-(\d+)-{token}", text))]


def network_failure(events: list[dict[str, object]], port: int, token: str) -> str | None:
    """Judge the network probe. Each exact command needs output that shows it ran."""
    direct, external, lookup, *proxied = network_commands(port, token)
    outputs = _results(events, direct)
    if not any(f"denied-{token}" in text for text in outputs):
        return f"network probe has no evidence: the direct TCP command output is missing or malformed (output: {outputs!r:.200})"
    outputs = _results(events, external)
    if any(f"open-{token}" in text for text in outputs):
        return "network isolation failed: the Bash probe connected to a non-loopback address"
    if not any(f"denied-{token}" in text for text in outputs):
        return f"network probe has no evidence: the non-loopback TCP command output is missing or malformed (output: {outputs!r:.200})"
    codes = _exit_codes(events, lookup, token)
    if any(code in CURL_ATTEMPTED for code in codes):
        return "network isolation failed: the Bash probe resolved a public name"
    if not codes or any(code != CURL_UNRESOLVED for code in codes):
        return f"network probe has no evidence: the name lookup output is missing, or curl failed in another way (exit codes: {codes})"
    for command in proxied:
        codes = _exit_codes(events, command, token)
        if not codes or any(code not in CURL_ATTEMPTED for code in codes):
            return f"network probe has no evidence: the HTTP command output is missing, or curl did not attempt a request (exit codes: {codes})"
    return None


def unix_command(address: str, token: str) -> str:
    """Build the live Bash attempt on a host Unix socket. It sends the token if it connects and prints `denied-<token>` if not.

    `address` is a socket path, or a backslash, `0`, and a name for an abstract socket; Python reads that as the NUL byte.
    """
    return ("/usr/bin/python3 -c \"import socket;c=socket.socket(socket.AF_UNIX);c.settimeout(5);"
            + f"r=c.connect_ex('{address}');r or c.send(b'{token}');print(('open-' if r==0 else 'denied-')+'{token}')\"")


def unix_failure(events: list[dict[str, object]], address: str, token: str) -> str | None:
    """Judge the Unix-socket probe. The exact command needs output that shows it ran and was denied."""
    outputs = _results(events, unix_command(address, token))
    if not any(f"denied-{token}" in text for text in outputs):
        return f"network probe has no evidence: the Unix-socket command output is missing or malformed (output: {outputs!r:.200})"
    return None


NESTED_PROBE = ("import ctypes,os\n"
                "libc=ctypes.CDLL(None,use_errno=True)\n"
                "uid=os.getuid()\n"
                "if libc.unshare(0x10000000)!=0: raise SystemExit('unshare')\n"
                "try:\n"
                " open('/proc/self/setgroups','w').write('deny')\n"
                " open('/proc/self/uid_map','w').write(f'0 {uid} 1')\n"
                "except OSError: raise SystemExit('map')\n"
                "print('nested-ok')\n")


@functools.cache
def nested_userns_blocked() -> bool:
    """Check, with no model call, whether a process that `bwrap` starts can create its own user namespace.

    Claude Code's Unix-socket filter needs that nested namespace. On Ubuntu 24.04 and later, the AppArmor profile
    `bwrap-userns-restrict` strips the capabilities it needs, and the sysctl alone does not lift it.
    Return False on macOS, without `bwrap`, or when the first-level sandbox itself fails; the sandbox checks
    report those cases. The result holds for the process, so every role gets the same answer.
    """
    tool = shutil.which("bwrap")
    if sys.platform == "darwin" or tool is None:
        return False
    try:
        result = subprocess.run([tool, "--unshare-all", "--die-with-parent", "--ro-bind", "/", "/", "--dev", "/dev",
                                 "--proc", "/proc", "/usr/bin/python3", "-I", "-c", NESTED_PROBE],
                                capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode != 0 and result.stderr.strip() in {"unshare", "map"}


def sandbox_argv(system: str, tool: str, workspace: Path, argv: list[str], environment: dict[str, str],
                 read_roots: tuple[str, ...] = (), capture: Path | None = None) -> list[str]:
    """Wrap `argv` for the OS sandbox: `sandbox-exec` on macOS, bubblewrap elsewhere. No external network.
    Host access is read-only and limited to the system runtime and the extra read roots.

    Under bubblewrap, the command gets its own network namespace.
    It can reach a server that it starts on loopback, but not the host loopback.

    Under bubblewrap with `capture`, the host directory `capture` is bound at `/run/skillz-capture`, outside the
    workspace, and a shell sends the command's standard error to a file there before the command starts.
    `sandbox` reads it. Only bubblewrap can then write to the process standard error,
    so a message that starts with `bwrap:` is a setup failure that the command cannot forge.
    """
    assignments = [f"{name}={value}" for name, value in environment.items()]
    if system == "darwin":
        return [tool, "-p", command_profile(workspace, read_roots), "/usr/bin/env", "-i", *assignments, *argv]
    path = str(workspace)
    mount = ["--bind", str(capture), SANDBOX_CAPTURE] if capture is not None else []
    shell = ['/bin/sh', '-c', 'exec "$@" 2>"$0"', f"{SANDBOX_CAPTURE}/{SANDBOX_STDERR}"] if capture is not None else [
        '/bin/sh', '-c', 'exec "$@" 2>/dev/null', 'sh']
    return [tool, "--unshare-all", "--die-with-parent", "--clearenv",
            "--ro-bind", "/usr", "/usr", "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib",
            "--symlink", "usr/lib64", "/lib64", "--proc", "/proc", "--dev", "/dev",
            *[part for root in read_roots for part in ("--ro-bind", root, root)],
            "--bind", path, path, "--ro-bind", f"{path}/.agents", f"{path}/.agents", *mount, "--chdir", path,
            *shell, "/usr/bin/env", "-i", *assignments, *argv]


def remove_tree(path: Path) -> None:
    """Remove a directory tree, including a subdirectory that the helper made unreadable. Never raises."""
    try:
        path.chmod(0o700)
        with os.scandir(path) as entries:
            children = list(entries)
        for child in children:
            if child.is_dir(follow_symlinks=False):
                remove_tree(Path(child.path))
            else:
                os.unlink(child.path)
        path.rmdir()
    except OSError:
        pass
