"""Deterministic stand-in for the `gemini` executable. It is not evidence of Gemini behavior."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import cast

here = Path(__file__).resolve()
mode = (here.with_name("gemini.mode").read_text() if here.with_name("gemini.mode").exists() else "normal")
args = sys.argv[1:]
home = Path(os.environ["HOME"])
stdin = "" if args[:1] == ["skills"] else sys.stdin.read()
entry = {
    "argv": args, "stdin": stdin, "cwd": os.getcwd(), "home_dir": str(home),
    "tmp_is_dir": os.path.isdir(os.environ.get("TMPDIR", "")),
    "env": {name: os.environ.get(name) for name in
            ("GEMINI_SANDBOX", "GEMINI_SANDBOX_IMAGE", "SANDBOX_FLAGS", "SANDBOX_MOUNTS", "SEATBELT_PROFILE",
             "GOOGLE_GENAI_USE_GCA", "DOCKER_CONFIG", "GOOGLE_GEMINI_BASE_URL", "GOOGLE_VERTEX_BASE_URL",
             "GEMINI_SANDBOX_PROXY_COMMAND", "SANDBOX_ENV", "SANDBOX_PORTS", "BUILD_SANDBOX", "SANDBOX_SET_UID_GID",
             "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "TMPDIR")},
    "home": sorted(f"{path.relative_to(home)}{'@' if path.is_symlink() else ''}" for path in home.rglob("*")),
}
with here.with_name("gemini.log").open("a") as log:
    _ = log.write(json.dumps(entry) + "\n")

if args[:2] == ["skills", "list"]:
    for skill in sorted(Path(".agents/skills").glob("*/SKILL.md")):
        print(f"{skill.parent.name} [Enabled]\n  Description: fixture skill\n  Location: {skill.parent.resolve()}")
    if mode == "foreign":
        print("other-skill [Enabled]\n  Description: user skill")
    raise SystemExit(0 if mode != "list-fails" else 4)
if mode == "hang":
    time.sleep(60)
schema = cast(dict[str, dict[str, object]], json.loads(stdin.rsplit("\n", 1)[-1]))
answer = json.dumps({key: "fake" for key in schema["properties"]})
if mode == "fails":
    print("quota exhausted", file=sys.stderr)
    raise SystemExit(1)
if mode == "bad-answer":
    answer = "I cannot produce JSON"
stats = {"total_tokens": 13, "input_tokens": 10, "output_tokens": 3, "cached": 2, "input": 8, "duration_ms": 5,
         "tool_calls": 3, "models": {"fake-pro": {"total_tokens": 13, "input_tokens": 10, "output_tokens": 3,
                                                    "cached": 2, "input": 8}}}
witness: list[dict[str, object]] = []
if mode in {"witness-real", "witness-forged"}:
    # Report the helper the way a model does. "witness-forged" prints the tool events without running the helper.
    skill = next(Path(".agents/skills").iterdir())
    helper = f".agents/skills/{skill.name}/scripts/x.py"
    answer = json.dumps({"load_marker": (skill / "EXPERIMENT_MARKER").read_text(), "result_json": '{"echo": "hello"}'})
    code = 0
    if mode == "witness-real":
        code = subprocess.run([sys.executable, "-I", helper, "input.txt"], capture_output=True).returncode
    witness = [
        {"type": "tool_use", "tool_name": "read_file", "tool_id": "w1", "parameters": {"file_path": f".agents/skills/{skill.name}/SKILL.md"}},
        {"type": "tool_result", "tool_id": "w1", "status": "success", "output": "body"},
        {"type": "tool_use", "tool_name": "run_shell_command", "tool_id": "w2", "parameters": {"command": f"python3 -I {helper} input.txt"}},
        {"type": "tool_result", "tool_id": "w2", "status": "success", "output": f"Exit Code: {code}"},
    ]
half = len(answer) // 2
tools = [
    {"type": "tool_use", "tool_name": "read_file", "tool_id": "t1", "parameters": {"file_path": ".agents/skills/demo/SKILL.md"}},
    {"type": "tool_result", "tool_id": "t1", "status": "success", "output": "body"},
    {"type": "tool_use", "tool_name": "run_shell_command", "tool_id": "t2", "parameters": {"command": "false"}},
    {"type": "tool_result", "tool_id": "t2", "status": "error", "error": {"type": "x", "message": "exit 1"}},
    {"type": "tool_use", "tool_name": "google_web_search", "tool_id": "t3", "parameters": {"query": "q"}},
    {"type": "tool_result", "tool_id": "t3", "status": "success"},
    {"type": "tool_use", "tool_name": "run_shell_command", "tool_id": "t4", "parameters": {"command": "never finished"}},
    {"type": "tool_result", "tool_id": "unknown", "status": "success"},
]
if witness:
    tools = witness
events: list[object] = [{"type": "init", "session_id": "s", "model": "fake-pro"}]
events += [] if mode in {"no-tools", "malformed"} else tools
events += [{"type": "message", "role": "assistant", "content": "```json\n" + answer[:half], "delta": True},
           {"type": "message", "role": "assistant", "content": answer[half:] + "\n```", "delta": True}]
events += ([{"type": "result", "status": "error", "error": {"type": "quota", "message": "limit"}}] if mode == "error-result"
           else [{"type": "result", "status": "success", "stats": stats}])
print("Loaded cached credentials.")
for event in events:
    print(json.dumps(event))
if mode == "malformed":
    print('{"type": "tool_use", "tool_id": 7}\n{broken\n[1]')
