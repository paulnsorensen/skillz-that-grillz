"""Deterministic journey roles that act on staged files and a live transcript."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import cast

CAT = shutil.which("cat", path="/usr/bin:/bin") or "/usr/bin/cat"  # macOS keeps cat in /bin


def _command(workspace: Path, argv: list[str], name: str) -> tuple[str, list[dict[str, object]]]:
    result = subprocess.run(argv, cwd=workspace, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(f"{name} failed: {result.stderr}")
    command = " ".join(argv)
    events: list[dict[str, object]] = [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": name, "name": "Bash", "input": {"command": command}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": name, "is_error": False, "content": result.stdout}]}}
    ]
    return result.stdout, events


def handle(workspace: Path, skill_name: str, message: str, turn: int) -> tuple[str, list[dict[str, object]]]:
    root = f".agents/skills/{skill_name}"
    if turn == 1:
        instructions, read_events = _command(workspace, [CAT, f"{root}/SKILL.md"], "read-skill")
        _, marker_events = _command(workspace, [CAT, f"{root}/EXPERIMENT_MARKER"], "read-marker")
        topic, topic_events = _command(workspace, [CAT, "question.txt"], "read-question")
        if "Transform" not in instructions:
            raise ValueError("the staged skill has no transform instruction")
        return f"Which {topic.strip()} should I transform?", [*read_events, *marker_events, *topic_events]
    if not message.startswith("Value: "):
        raise ValueError("the simulator did not provide a value")
    value = message.removeprefix("Value: ").strip() + "\n"
    _ = (workspace / "fixture.md").write_text(value)
    instructions, read_events = _command(workspace, [CAT, f"{root}/SKILL.md"], f"read-skill-{turn}")
    helper = workspace / root / "scripts/transform.py"
    if helper.is_file():
        result, helper_events = _command(
            workspace, ["/usr/bin/python3", "-I", f"{root}/scripts/transform.py", "fixture.md"], f"helper-{turn}")
        text = cast(str, json.loads(result)["text"])
    else:
        text = value.upper() if "Uppercase fixture.md." in instructions else value
        helper_events = []
    output = workspace / "output"
    output.mkdir(exist_ok=True)
    _ = (output / "result.txt").write_text(text)
    return json.dumps({"text": text}), [*read_events, *helper_events]


def simulate(prompt: str, fields: list[str]) -> dict[str, object]:
    from vendor_common import untrusted_payload
    if fields != ["reply", "done"]:
        raise ValueError("unexpected simulator schema")
    data = untrusted_payload(prompt)
    brief = cast(str, data["brief"])
    transcript = cast(list[dict[str, str]], data["transcript"])
    if not transcript or transcript[-1]["role"] != "assistant":
        raise ValueError("the simulator needs an assistant turn")
    if not transcript[-1]["text"].startswith("Which ") or not transcript[-1]["text"].endswith(" should I transform?"):
        return {"reply": "", "done": True}
    label = brief.split("Value: ", 1)[1].split(".", 1)[0]
    return {"reply": f"Value: {label}", "done": False}


def judge(prompt: str, fields: list[str]) -> dict[str, int]:
    from vendor_common import untrusted_payload
    if fields != ["score_percent"]:
        raise ValueError("unexpected judge schema")
    data = untrusted_payload(prompt)
    reference = cast(dict[str, list[str]], data["reference"])
    files = cast(dict[str, str], data["output_files"])
    transcript = cast(list[dict[str, str]], data["transcript"])
    topic = cast(dict[str, str], data["files"])["question.txt"].strip()
    asked = any(row["role"] == "assistant" and row["text"] == f"Which {topic} should I transform?"
                for row in transcript)
    answered = any(row["role"] == "user" and row["text"].startswith("Value: ") for row in transcript)
    return {"score_percent": 100 if asked and answered and files.get("output/result.txt") == reference["must"][0] else 0}


def grade() -> None:
    output = Path("output/output/result.txt")
    trace = Path("trace/events.jsonl")
    events = [cast(dict[str, object], json.loads(line)) for line in trace.read_text().splitlines()]
    skill_read = any(
        event.get("type") == "assistant"
        and "SKILL.md" in json.dumps(event)
        and "Bash" in json.dumps(event)
        for event in events
    )
    topic = Path("question.txt").read_text().strip()
    asked = any(event.get("type") == "assistant" and f"Which {topic} should I transform?" in json.dumps(event)
                for event in events)
    score = int(output.is_file() and skill_read and asked)
    print(json.dumps({"score": score}))


if __name__ == "__main__":
    if sys.argv[1:] == ["fail"]:
        print(json.dumps({"score": 0}))
    elif sys.argv[1:] == ["grade"]:
        grade()
    else:
        raise SystemExit("expected grade or fail")
