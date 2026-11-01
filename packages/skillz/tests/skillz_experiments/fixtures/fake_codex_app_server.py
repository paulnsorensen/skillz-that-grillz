#!/usr/bin/python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import cast

workspace = None
thread = "thread-1"
turns = 0
mode = Path(__file__).with_suffix(".mode").read_text().strip()


def send(message: dict[str, object]) -> None:
    print(json.dumps(message), flush=True)


for line in sys.stdin:
    request = cast(dict[str, object], json.loads(line))
    method = request.get("method")
    if method == "initialize":
        if mode in {"startup_exit", "startup_stdout_closed"}:
            print("startup failed: key=private-value", file=sys.stderr, flush=True)
            if mode == "startup_stdout_closed":
                # Keep the process alive after stdout EOF, before the parent reads stderr.
                os.close(sys.stdout.fileno())
                _ = sys.stdin.read()
            sys.exit(2)
        send({"id": request["id"], "result": {"userAgent": "fake"}})
    elif method == "skills/list":
        params = cast(dict[str, object], request["params"])
        cwds = cast(list[str], params["cwds"])
        assert len(cwds) == 1 and params["forceReload"] is True
        skill_paths = list((Path(cwds[0]) / ".agents/skills").glob("*/SKILL.md"))
        assert len(skill_paths) == 1
        skill_path = skill_paths[0]
        send({"id": request["id"], "result": {"data": [{"cwd": cwds[0], "errors": [],
              "skills": [{"name": skill_path.parent.name, "enabled": True, "scope": "repo",
                          "pluginId": None, "path": str(skill_path.resolve())}]}]}})
    elif method == "thread/start":
        params = cast(dict[str, object], request["params"])
        workspace = Path(cast(str, params["cwd"]))
        assert params["ephemeral"] is True and params["approvalPolicy"] == "never"
        assert os.environ["HOME"] == str(workspace / "home")
        assert "--strict-config" in sys.argv
        assert any("network={enabled=false}" in part for part in sys.argv)
        if mode == "request_start":
            send({"id": 100, "method": "item/commandExecution/requestApproval", "params": {}})
            continue
        if mode == "error_start":
            send({"id": request["id"], "error": {"message": "denied"}})
            continue
        if mode == "unexpected_response_start":
            send({"id": 999, "result": {}})
            continue
        send({"method": "thread/started", "params": {"thread": {"id": "other" if mode == "wrong_started" else thread}}})
        send({"id": request["id"], "result": {"thread": {"id": thread}}})
    elif method == "turn/start":
        assert workspace is not None
        turns += 1
        turn = f"turn-{turns}"
        send({"id": request["id"], "result": {"turn": {"id": turn}}})
        if mode in {"global_warning", "global_notifications"}:
            send({"method": "configWarning", "params": {"summary": "configuration notice"}})
            send({"method": "remoteControl/status/changed", "params": {"status": "disabled"}})
        if mode == "global_notifications":
            notifications: tuple[tuple[str, dict[str, object]], ...] = (
                ("account/updated", {"authMode": "apiKey"}),
                ("account/rateLimits/updated", {"rateLimits": {}}),
                ("skills/changed", {}),
                ("app/list/updated", {"data": []}),
                ("project/changed", {"projectId": "other"}),
                ("fs/changed", {"watchId": "other", "changedPaths": []}),
            )
            for method, params in notifications:
                send({"method": method, "params": params})
        if mode == "global_wrong_thread":
            send({"method": "account/updated", "params": {"threadId": "other"}})
            continue
        if mode == "legacy_event":
            send({"method": "codex/event", "params": {"msg": {"thread_id": "other"}}})
            continue
        if mode == "unscoped_item":
            send({"method": "item/completed", "params": {"turnId": turn, "item": {}}})
            continue
        if mode == "unexpected_response":
            send({"id": 999, "result": {}})
            continue
        if mode == "wrong_thread":
            send({"method": "item/completed", "params": {"threadId": "other", "turnId": turn, "item": {}}})
            continue
        if mode == "wrong_turn":
            send({"method": "item/completed", "params": {"threadId": thread, "turnId": "other", "item": {}}})
            continue
        if mode == "timeout":
            import time
            time.sleep(10)
        if mode == "approval":
            send({"id": 100, "method": "item/commandExecution/requestApproval", "params": {"threadId": thread}})
            continue
        if mode == "failed":
            send({"method": "turn/completed", "params": {"threadId": thread, "turn": {"id": turn, "status": "failed", "items": []}}})
            continue
        params = cast(dict[str, object], request["params"])
        prompt = cast(str, cast(list[dict[str, object]], params["input"])[0]["text"])
        previous = (workspace / "previous.txt").read_text() if (workspace / "previous.txt").exists() else "none"
        _ = (workspace / "previous.txt").write_text(prompt)
        skill = workspace / ".agents/skills/echo-skill"
        if mode == "bad_quote":
            send({"method": "item/completed", "params": {"threadId": thread, "turnId": turn,
                    "item": {"type": "commandExecution", "command": "cat '", "exitCode": 0}}})
        if mode != "unloaded":
            for index, name in enumerate(("SKILL.md", "EXPERIMENT_MARKER")):
                item = {"id": f"command-{turns}-{index}", "type": "commandExecution",
                        "command": f"{'./cat' if mode == 'workspace_cat' else '/usr/bin/cat'} .agents/skills/echo-skill/{name}",
                        "exitCode": 1 if mode == "read_failed" and name == "SKILL.md" else 0,
                        "status": "completed", "aggregatedOutput": ("wrong" if mode == "marker_wrong"
                                                                     and name == "EXPERIMENT_MARKER" else (skill / name).read_text())}
                send({"method": "item/completed", "params": {"threadId": thread, "turnId": turn, "item": item,
                                                             "completedAtMs": 1}})
        if mode in {"gate_journey", "gate_journey_bad", "gate_journey_bad_out", "gate_journey_cwd_bad",
                    "gate_journey_cd_bad"}:
            case_hash = "ab" * 32
            cwd = str(workspace / "toys/a") if mode == "gate_journey_cwd_bad" else str(workspace)
            run = ("cd toys/a; python3 x/skillz-experiment.pyz run --target . --out result"
                   if mode == "gate_journey_cd_bad" else
                   f"python3 x/skillz-experiment.pyz run --target {workspace / 'toys/a'} --out result"
                   if mode == "gate_journey_cwd_bad" else
                   "python3 x/skillz-experiment.pyz run --target toys/a --out toys/a/result"
                   if mode == "gate_journey_bad_out" else
                   "python3 x/skillz-experiment.pyz run --target toys/a --out output")
            commands = ([(run, json.dumps({"stop": "cases-unapproved", "case_hash": case_hash})
                          if mode != "gate_journey_bad" else "no approval evidence"),
                         (run, json.dumps({"stop": "budget-unapproved", "estimate": {"calls": 2}}))]
                        if turns == 1 else [(f"{run} --approve-cases {case_hash} --approve-budget yes", "done")])
            for index, (command, output) in enumerate(commands):
                item = {"id": f"run-{turns}-{index}", "type": "commandExecution",
                        "command": f"/bin/bash -lc {json.dumps(command)}", "cwd": cwd,
                        "exitCode": 1 if turns == 1 else 0, "status": "completed", "aggregatedOutput": output}
                send({"method": "item/completed", "params": {"threadId": thread, "turnId": turn, "item": item}})
        message = {"id": f"message-{turns}", "type": "agentMessage", "text": (f"Approve {('ab' * 32)}?" if mode.startswith("gate_journey") and turns == 1
                                                                   else f"turn={turns} previous={previous}")}
        if mode != "no_answer":
            send({"method": "item/completed", "params": {"threadId": thread, "turnId": turn, "item": message,
                                                             "completedAtMs": 1}})
        totals = ({"inputTokens": 10 * turns, "cachedInputTokens": 2 * turns,
                   "outputTokens": 3 * turns},)
        if mode == "usage_snapshots":
            totals = (({"inputTokens": 4, "cachedInputTokens": 1, "outputTokens": 2},
                       {"inputTokens": 10, "cachedInputTokens": 2, "outputTokens": 3}) if turns == 1 else
                      ({"inputTokens": 14, "cachedInputTokens": 3, "outputTokens": 5},
                       {"inputTokens": 25, "cachedInputTokens": 5, "outputTokens": 9}))
        if mode == "invalid_usage":
            totals = ({"inputTokens": True, "cachedInputTokens": 2, "outputTokens": 3},)
        if mode == "decreasing_usage" and turns == 2:
            totals = ({"inputTokens": 9, "cachedInputTokens": 2, "outputTokens": 3},)
        if mode != "no_usage" and not (mode == "missing_final_usage" and turns == 2) and not (mode == "middle_gap_usage" and turns == 2):
            for total in (*totals, totals[-1]) if mode == "usage_snapshots" else totals:
                send({"method": "thread/tokenUsage/updated", "params": {"threadId": thread, "turnId": turn,
                        "tokenUsage": {"last": {"inputTokens": 1, "cachedInputTokens": 0, "outputTokens": 1},
                                       "total": total}}})
        send({"method": "turn/completed", "params": {"threadId": thread,
                "turn": {"id": turn, "status": "completed", "items": [message]}}})
