from __future__ import annotations

import json
import os
import select
import shlex
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import final
from skillz_experiments._budget import Budget, CallTimeout
from skillz_experiments._candidate import Candidate, make_workspace, snapshot_outputs, stage_task
from skillz_experiments._cases import Case, loads_untrusted, mapping, string
from skillz_experiments._redact import redact
from skillz_experiments.evaluation import Reply, Transcript, usage

@final
class AppServer:
    def __init__(self, child: subprocess.Popen[bytes]) -> None:
        self.child = child
        self.buffer = bytearray()
        self.pending: list[dict[str, object]] = []
        self.raw: list[dict[str, object]] = []

    def send(self, request: dict[str, object]) -> None:
        assert self.child.stdin is not None
        _ = self.child.stdin.write((json.dumps(request) + "\n").encode())
        self.child.stdin.flush()

    def read(self, deadline: float) -> dict[str, object]:
        assert self.child.stdout is not None
        while time.monotonic() < deadline:
            if b"\n" in self.buffer:
                end = self.buffer.index(b"\n")
                line = bytes(self.buffer[:end])
                del self.buffer[:end + 1]
                return mapping(loads_untrusted(line.decode()))
            ready, _, _ = select.select([self.child.stdout], [], [], max(0, deadline - time.monotonic()))
            if not ready:
                break
            chunk = os.read(self.child.stdout.fileno(), 65536)
            if not chunk:
                raise RuntimeError("Codex app-server exited before completing the turn")
            self.buffer.extend(chunk)
            if len(self.buffer) > 4_000_000:
                raise RuntimeError("Codex app-server sent an oversized protocol message")
        raise CallTimeout("Codex app-server call timed out")

    def reply(self, identifier: int, deadline: float) -> dict[str, object]:
        while True:
            message = self.read(deadline)
            if "method" in message and "id" in message:
                raise RuntimeError("Codex app-server requested an unsupported client action")
            if message.get("id") == identifier:
                if "error" in message:
                    raise RuntimeError(f"Codex app-server rejected request {identifier}: {message['error']!r}")
                return mapping(message.get("result"))
            if "method" not in message:
                raise RuntimeError("Codex app-server sent an unexpected response")
            self.pending.append(message)
            self.raw.append(message)

    def next_event(self, deadline: float) -> dict[str, object]:
        if self.pending:
            return self.pending.pop(0)
        message = self.read(deadline)
        if "method" not in message:
            raise RuntimeError("Codex app-server sent an unexpected response")
        if "id" in message:
            raise RuntimeError("Codex app-server requested an unsupported client action")
        self.raw.append(message)
        return message


def _item_event(item: dict[str, object]) -> dict[str, object] | None:
    if item.get("type") == "commandExecution":
        return {"type": "item.completed", "item": {"type": "command_execution", "command": item.get("command"),
                "exit_code": item.get("exitCode"), "output": item.get("aggregatedOutput")}}
    return None


_GLOBAL_NOTIFICATIONS = frozenset({
    "skills/changed", "project/changed", "command/exec/outputDelta", "process/outputDelta",
    "process/exited", "mcpServer/oauthLogin/completed", "mcpServer/startupStatus/updated",
    "mcpServer/event/stream/notification", "account/updated", "account/gatewayOAuth/changed",
    "account/rateLimits/updated", "app/list/updated", "remoteControl/status/changed",
    "externalAgentConfig/import/progress", "externalAgentConfig/import/completed", "fs/changed",
    "warning", "deprecationNotice", "configWarning", "fuzzyFileSearch/sessionUpdated",
    "fuzzyFileSearch/sessionCompleted", "windows/worldWritableWarning", "windowsSandbox/setupCompleted",
    "account/login/completed",
})


def _turn(server: AppServer, thread: str, identifier: int, prompt: str, deadline: float,
          previous_total: dict[str, int] | None) -> tuple[list[dict[str, object]], str, dict[str, int] | None]:
    server.send({"id": identifier, "method": "turn/start", "params": {
        "threadId": thread, "input": [{"type": "text", "text": prompt}]}})
    started = server.reply(identifier, deadline)
    turn = string(mapping(started.get("turn")).get("id"), "Codex turn id")
    events: list[dict[str, object]] = []
    answer = ""
    total = previous_total
    tokens: dict[str, int] | None = None
    while True:
        event = server.next_event(deadline)
        method = event.get("method")
        params = mapping(event.get("params"))
        if method == "thread/started":
            if mapping(params.get("thread")).get("id") != thread:
                raise RuntimeError("Codex app-server started another journey thread")
            continue
        if method in _GLOBAL_NOTIFICATIONS and "threadId" not in params:
            continue
        if params.get("threadId") != thread:
            detail = f"method={method!r}, threadId={params.get('threadId')!r}, expected={thread!r}"
            raise RuntimeError(f"Codex app-server changed the journey thread: {detail}")
        if method == "turn/completed":
            completed = mapping(params.get("turn"))
            if completed.get("id") != turn or completed.get("status") != "completed":
                raise RuntimeError(f"Codex app-server turn failed: {completed.get('status')!r}")
            if not answer:
                raise RuntimeError("Codex app-server turn has no assistant message")
            if tokens is not None:
                events.append({"type": "turn.completed", "usage": tokens})
            return events, answer, total
        if params.get("turnId") != turn:
            if method in {"item/completed", "thread/tokenUsage/updated"}:
                detail = f"method={method!r}, turnId={params.get('turnId')!r}, expected={turn!r}"
                raise RuntimeError(f"Codex app-server sent an event for another turn: {detail}")
            continue
        if method == "item/completed":
            item = mapping(params.get("item"))
            normalized = _item_event(item)
            if normalized is not None:
                events.append(normalized)
            if item.get("type") == "agentMessage":
                answer = string(item.get("text"), "Codex assistant text")
        elif method == "thread/tokenUsage/updated":
            snapshot = mapping(mapping(params.get("tokenUsage")).get("total"))
            fields = (("input_tokens", "inputTokens"), ("cached_input_tokens", "cachedInputTokens"),
                      ("output_tokens", "outputTokens"))
            current: dict[str, int] = {}
            for name, source in fields:
                count = snapshot.get(source)
                if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                    raise RuntimeError(f"Codex app-server sent invalid token usage: {source}")
                prior = (total or {}).get(name, 0)
                if count < prior:
                    raise RuntimeError(f"Codex app-server token usage decreased: {source}")
                current[name] = count
            tokens = {name: count - (previous_total or {}).get(name, 0) for name, count in current.items()}
            total = current


def _first_turn_loaded(events: list[dict[str, object]], workspace: Path, candidate: Candidate) -> bool:
    root = workspace / ".agents" / "skills" / candidate.skill
    read_paths = {str(root / "SKILL.md"), str((root / "SKILL.md").relative_to(workspace))}
    marker_paths = {str(root / "EXPERIMENT_MARKER"), str((root / "EXPERIMENT_MARKER").relative_to(workspace))}
    seen: set[str] = set()
    for event in events:
        if event.get("type") != "item.completed":
            continue
        item = mapping(event.get("item"))
        command = item.get("command")
        if item.get("type") != "command_execution" or item.get("exit_code") != 0 or not isinstance(command, str):
            continue
        if any(token in command for token in (";", "&&", "||", "|", ">", "<", "$", "`", "\n")):
            continue
        try:
            parts = shlex.split(command)
            if len(parts) == 3 and parts[0] in {"/bin/sh", "/bin/bash", "/bin/zsh", "/usr/bin/sh", "/usr/bin/bash", "/usr/bin/zsh"} and parts[1] in {"-c", "-lc"}:
                parts = shlex.split(parts[2])
        except ValueError:
            continue
        if len(parts) != 2 or parts[0] not in {"/bin/cat", "/usr/bin/cat"}:
            continue
        if parts[1] in read_paths:
            seen.add("read")
        if parts[1] in marker_paths and str(item.get("output", "")).strip() == candidate.identity:
            seen.add("marker")
    return seen == {"read", "marker"}


def converse(request: str, candidate: Candidate, case: Case, holdout: bool,
             simulate: Callable[[Transcript], Reply], *, budget: Budget, checkpoint: Callable[[], None],
             role: str, model: str, command: Callable[[Path], list[str]],
             environment: Callable[[Path], dict[str, str]]) -> dict[str, object]:
    if case.journey is None:
        raise ValueError("converse needs a journey case")
    with tempfile.TemporaryDirectory(prefix="skillz-journey-") as directory:
        workspace = make_workspace(Path(directory) / "workspace")
        stage_task(workspace, candidate, case)
        started = time.monotonic()
        with tempfile.TemporaryFile() as errors:
            child = subprocess.Popen(command(workspace), cwd=workspace, env=environment(workspace), stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=errors, start_new_session=True)
            try:
                server = AppServer(child)
                server.send({"id": 0, "method": "initialize", "params": {
                    "clientInfo": {"name": "skillz-experiment", "version": "1"},
                    "capabilities": {"experimentalApi": True}}})
                _ = server.reply(0, time.monotonic() + min(30, budget.remaining()))
                server.send({"method": "initialized", "params": {}})
                server.send({"id": 1, "method": "thread/start", "params": {
                    "cwd": str(workspace), "model": model, "ephemeral": True, "approvalPolicy": "never"}})
                thread = string(mapping(server.reply(1, time.monotonic() + min(30, budget.remaining()))
                                        .get("thread")).get("id"), "Codex thread id")
                transcript: Transcript = []
                events: list[dict[str, object]] = []
                message = request
                shown = case.request
                marker = ""
                total: dict[str, int] | None = None
                fresh: list[dict[str, object]] = []
                for turn in range(case.journey.max_turns):
                    budget.claim(holdout=holdout, role=role)
                    checkpoint()
                    with budget.timed(role):
                        fresh, answer, total = _turn(server, thread, turn + 2, message,
                                                      time.monotonic() + budget.call_timeout(len(message)), total)
                    events.extend(fresh)
                    transcript.extend(({"role": "user", "text": shown}, {"role": "assistant", "text": answer}))
                    if turn == 0:
                        if not _first_turn_loaded(fresh, workspace, candidate):
                            break
                        marker = candidate.identity
                    if turn + 1 >= case.journey.max_turns:
                        break
                    reply = simulate(transcript)
                    if reply.done:
                        break
                    message = shown = reply.reply
                return {"answer": {"result_json": transcript[-1]["text"], "load_marker": marker},
                        "events": events, "usage": usage(events) if any(event.get("type") == "turn.completed" for event in fresh)
                        else {"input_tokens": None, "cached_input_tokens": None, "output_tokens": None},
                        "workspace": str(workspace),
                        "latency_seconds": time.monotonic() - started, "output_files": snapshot_outputs(workspace),
                        "transcript": transcript, "raw_events": server.raw}
            except RuntimeError as error:
                # Stdout EOF can precede process exit. Read available stderr without waiting for poll().
                _ = errors.seek(0, os.SEEK_END)
                _ = errors.seek(max(0, errors.tell() - 4096))
                detail = redact(errors.read(4096).decode(errors="replace"))
                if not detail:
                    raise
                raise RuntimeError(f"{error}; Codex stderr: {detail}") from error
            finally:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                _ = child.wait()
