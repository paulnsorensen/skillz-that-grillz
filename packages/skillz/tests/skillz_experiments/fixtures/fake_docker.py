"""Deterministic stand-in for `docker`. It logs the wrapper's flags and emulates the denials the probe needs."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

args = sys.argv[1:]
here = Path(__file__).resolve()
with here.with_name("docker.log").open("a") as log:
    _ = log.write(json.dumps(args) + "\n")
if args[:1] == ["kill"]:
    raise SystemExit(0)
if args[:1] == ["ps"]:
    print("container-one")
    raise SystemExit(0)
if here.with_name("docker.mode").exists() and here.with_name("docker.mode").read_text() == "hang":
    time.sleep(60)
start = args.index("/usr/bin/env") + 2
command = args[start:]
while command and (command[0] == "-i" or command[0].split("=")[0] in {"PATH", "HOME", "TMPDIR"}):
    command = command[1:]
workspace = args[args.index("-w") + 1]
if "-c" in command:
    # Emulates the denials of a real container: no host reads, no network, a read-only .agents directory.
    prefix = ("import os,pathlib,socket\n"
              "def deny(*a,**k): raise PermissionError()\n"
              "pathlib.Path.read_bytes=deny\nsocket.socket=deny\nos.environ.clear()\n"
              "write=pathlib.Path.write_text\n"
              "pathlib.Path.write_text=lambda self,*a,**k: deny() if '.agents' in str(self) else write(self,*a,**k)\n")
    command = command[:-1] + [prefix + command[-1]]
result = subprocess.run(command, cwd=workspace, capture_output=True, text=True, check=False)
_ = sys.stdout.write(result.stdout)
_ = sys.stderr.write(result.stderr)
raise SystemExit(result.returncode)
