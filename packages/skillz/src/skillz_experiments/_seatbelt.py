"""macOS Seatbelt profiles: one for a sandboxed command, and one for Gemini CLI `infer`.

`command_profile` confines one command to its workspace. The Claude adapter and the Gemini `sandbox` operation use it.

The `infer` profile is a copy of Gemini CLI's `strict-open` profile plus a deny for `.agents`.
Copied from upstream `packages/cli/src/utils/sandboxBuiltinProfiles.ts` (main branch, October 2026).
Not run on a live macOS host. Gemini CLI fills the `TARGET_DIR`, `TMP_DIR`, `HOME_DIR`, `CACHE_DIR` and
`INCLUDE_DIR_n` parameters. The last rule wins in Seatbelt, so the final deny overrides the workspace write rule.
"""
from __future__ import annotations

from pathlib import Path

SEATBELT_RUNTIME = ("/usr", "/bin", "/System", "/Library/Developer/CommandLineTools", "/private/var/db/dyld")
STRICT_OPEN = r"""(version 1)
(deny default)
(allow file-read*
    (literal "/")
    (subpath (param "TARGET_DIR"))
    (subpath (param "TMP_DIR"))
    (subpath (param "CACHE_DIR"))
    (literal (string-append (param "HOME_DIR") "/.gemini/settings.json"))
    (literal (string-append (param "HOME_DIR") "/.gemini/keybindings.json"))
    (subpath (string-append (param "HOME_DIR") "/.npm"))
    (subpath (string-append (param "HOME_DIR") "/.cache"))
    (literal (string-append (param "HOME_DIR") "/.gitconfig"))
    (subpath (string-append (param "HOME_DIR") "/.nvm"))
    (subpath (string-append (param "HOME_DIR") "/.fnm"))
    (subpath (string-append (param "HOME_DIR") "/.node"))
    (subpath (string-append (param "HOME_DIR") "/.config"))
    (subpath (param "INCLUDE_DIR_0"))
    (subpath (param "INCLUDE_DIR_1"))
    (subpath (param "INCLUDE_DIR_2"))
    (subpath (param "INCLUDE_DIR_3"))
    (subpath (param "INCLUDE_DIR_4"))
    (subpath "/usr")
    (subpath "/bin")
    (subpath "/sbin")
    (subpath "/Library")
    (subpath "/System")
    (subpath "/private")
    (subpath "/dev")
    (subpath "/etc")
    (subpath "/opt")
    (subpath "/Applications")
)
(allow file-read-metadata)
(allow process-exec)
(allow process-fork)
(allow signal (target self))
(allow sysctl-read
  (sysctl-name "hw.activecpu")
  (sysctl-name "hw.busfrequency_compat")
  (sysctl-name "hw.byteorder")
  (sysctl-name "hw.cacheconfig")
  (sysctl-name "hw.cachelinesize_compat")
  (sysctl-name "hw.cpufamily")
  (sysctl-name "hw.cpufrequency_compat")
  (sysctl-name "hw.cputype")
  (sysctl-name "hw.l1dcachesize_compat")
  (sysctl-name "hw.l1icachesize_compat")
  (sysctl-name "hw.l2cachesize_compat")
  (sysctl-name "hw.l3cachesize_compat")
  (sysctl-name "hw.logicalcpu_max")
  (sysctl-name "hw.machine")
  (sysctl-name "hw.ncpu")
  (sysctl-name "hw.nperflevels")
  (sysctl-name "hw.optional.arm.FEAT_BF16")
  (sysctl-name "hw.optional.arm.FEAT_DotProd")
  (sysctl-name "hw.optional.arm.FEAT_FCMA")
  (sysctl-name "hw.optional.arm.FEAT_FHM")
  (sysctl-name "hw.optional.arm.FEAT_FP16")
  (sysctl-name "hw.optional.arm.FEAT_I8MM")
  (sysctl-name "hw.optional.arm.FEAT_JSCVT")
  (sysctl-name "hw.optional.arm.FEAT_LSE")
  (sysctl-name "hw.optional.arm.FEAT_RDM")
  (sysctl-name "hw.optional.arm.FEAT_SHA512")
  (sysctl-name "hw.optional.armv8_2_sha512")
  (sysctl-name "hw.packages")
  (sysctl-name "hw.pagesize_compat")
  (sysctl-name "hw.physicalcpu_max")
  (sysctl-name "hw.tbfrequency_compat")
  (sysctl-name "hw.vectorunit")
  (sysctl-name "kern.hostname")
  (sysctl-name "kern.maxfilesperproc")
  (sysctl-name "kern.osproductversion")
  (sysctl-name "kern.osrelease")
  (sysctl-name "kern.ostype")
  (sysctl-name "kern.osvariant_status")
  (sysctl-name "kern.osversion")
  (sysctl-name "kern.secure_kernel")
  (sysctl-name "kern.usrstack64")
  (sysctl-name "kern.version")
  (sysctl-name "sysctl.proc_cputype")
  (sysctl-name-prefix "hw.perflevel")
)
(allow file-write*
    (subpath (param "TARGET_DIR"))
    (subpath (param "TMP_DIR"))
    (subpath (param "CACHE_DIR"))
    (subpath (string-append (param "HOME_DIR") "/.npm"))
    (subpath (string-append (param "HOME_DIR") "/.cache"))
    (subpath (param "INCLUDE_DIR_0"))
    (subpath (param "INCLUDE_DIR_1"))
    (subpath (param "INCLUDE_DIR_2"))
    (subpath (param "INCLUDE_DIR_3"))
    (subpath (param "INCLUDE_DIR_4"))
    (literal "/dev/stdout")
    (literal "/dev/stderr")
    (literal "/dev/null")
)
(allow mach-lookup (global-name "com.apple.sysmond"))
(allow file-ioctl (regex #"^/dev/tty.*"))
(allow network-inbound (local ip "localhost:9229"))
(allow network-outbound)
(deny file-read* file-write*
    (literal "/var/run/docker.sock")
    (literal "/var/run/docker.sock.raw")
    (literal "/private/var/run/docker.sock")
    (literal "/private/var/run/docker.sock.raw")
    (subpath "/var/run/docker")
    (subpath "/private/var/run/docker")
    (subpath "/Users/Shared/.docker")
    (subpath (string-append (param "HOME_DIR") "/.docker/run"))
    (subpath (string-append (param "HOME_DIR") "/.docker/desktop"))
    (subpath (string-append (param "HOME_DIR") "/.colima"))
    (subpath (string-append (param "HOME_DIR") "/.orbstack/run"))
    (subpath (string-append (param "HOME_DIR") "/.rd"))
)
(deny process-exec
    (literal "/usr/local/bin/docker")
    (literal "/usr/local/bin/dockerd")
    (literal "/usr/local/bin/docker-compose")
    (literal "/usr/bin/docker")
    (literal "/opt/homebrew/bin/docker")
    (literal "/opt/homebrew/bin/dockerd")
    (literal "/opt/homebrew/bin/docker-compose")
    (literal "/opt/homebrew/bin/podman")
    (literal "/usr/local/bin/podman")
    (literal "/opt/homebrew/bin/colima")
    (literal "/usr/local/bin/colima")
    (literal "/opt/homebrew/bin/orb")
    (literal "/Applications/Docker.app/Contents/Resources/bin/docker")
    (subpath "/Applications/Docker.app/Contents/MacOS/")
    (subpath "/Applications/OrbStack.app/Contents/MacOS/")
    (subpath "/Applications/Rancher Desktop.app/Contents/MacOS/")
)
(deny mach-lookup
    (xpc-service-name-prefix "com.docker.")
    (global-name-prefix "com.docker.")
    (global-name-prefix "dev.kdrag0n.OrbStack")
)
(deny ipc-posix-shm*
    (ipc-posix-name-prefix "docker")
    (ipc-posix-name-prefix "com.docker.")
)
(deny file-write*
    (subpath (string-append (param "HOME_DIR") "/.gemini"))
    (regex #"/oauth_creds\.json$")
    (regex #"/gemini-credentials\.json$")
    (regex #"/mcp-oauth-tokens\.json$")
    (regex #"/a2a-oauth-tokens\.json$")
    (regex #"/google_accounts\.json$")
    (regex #"/trusted_hooks\.json$")
    (regex #"/trustedFolders\.json$")
    (regex #"/policy_integrity\.json$")
    (regex #"/\.env($|\..*)")
)
(deny file-read*
    (literal (string-append (param "HOME_DIR") "/.gemini/oauth_creds.json"))
    (literal (string-append (param "HOME_DIR") "/.gemini/gemini-credentials.json"))
    (literal (string-append (param "HOME_DIR") "/.gemini/mcp-oauth-tokens.json"))
    (literal (string-append (param "HOME_DIR") "/.gemini/a2a-oauth-tokens.json"))
    (literal (string-append (param "HOME_DIR") "/.gemini/google_accounts.json"))
    (literal (string-append (param "HOME_DIR") "/.gemini/trusted_hooks.json"))
    (literal (string-append (param "HOME_DIR") "/.gemini/trustedFolders.json"))
    (literal (string-append (param "HOME_DIR") "/.gemini/policy_integrity.json"))
    (regex #"/oauth_creds\.json$")
    (regex #"/gemini-credentials\.json$")
    (regex #"/mcp-oauth-tokens\.json$")
    (regex #"/a2a-oauth-tokens\.json$")
    (regex #"/google_accounts\.json$")
    (regex #"/trusted_hooks\.json$")
    (regex #"/trustedFolders\.json$")
    (regex #"/policy_integrity\.json$")
    (regex #"/\.env($|\..*)")
)
"""


def profile(agents: str) -> str:
    """Return the profile text with `file-write*` denied below the given `.agents` path."""
    return STRICT_OPEN + f'(deny file-write* (subpath "{agents}"))\n'


def command_profile(workspace: Path, read_roots: tuple[str, ...] = ()) -> str:
    """Build the macOS `sandbox-exec` profile. Everything is denied unless this profile allows it.

    The profile allows no network. It allows reads of the system runtime, the extra read roots, and the
    workspace, and writes to the workspace except `.agents`. Later rules win, so the `.agents` deny follows
    the write allow.
    """
    path = str(workspace)
    rules = ["(version 1)", "(deny default)", "(allow process-fork)", "(allow process-exec)",
             "(allow signal (target self))", "(allow sysctl-read)", "(allow file-read-metadata)",
             *[f'(allow file-read* (subpath "{root}"))' for root in (*SEATBELT_RUNTIME, *read_roots)],
             '(allow file-read* (literal "/dev/null") (literal "/dev/urandom"))',
             f'(allow file-read* (subpath "{path}"))', f'(allow file-write* (subpath "{path}"))',
             f'(deny file-write* (subpath "{path}/.agents"))', '(allow file-write* (literal "/dev/null"))']
    return "\n".join(rules) + "\n"
