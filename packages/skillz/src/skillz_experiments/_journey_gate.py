"""Generic gate of the `journey` grader. The scaffold command inlines this file into a contract argv.

The grader runs it as `python3 -I -c SOURCE` in the journey workspace. It reads `trace/events.jsonl`
and prints `{"score": 0 or 1, "failed": [...]}`. Add checks for your skill below `main`.
Standard library only.
"""
import json
import pathlib
from typing import cast


def agent_turn(line: str) -> bool:
    try:
        event = cast(object, json.loads(line))
    except json.JSONDecodeError:
        return False
    if not isinstance(event, dict):
        return False
    data = cast(dict[str, object], event)
    if data.get("type") == "assistant":
        return True
    if data.get("method") != "item/completed":
        return False
    params = data.get("params")
    if not isinstance(params, dict):
        return False
    item = cast(dict[str, object], params).get("item")
    return isinstance(item, dict) and cast(dict[str, object], item).get("type") == "agentMessage"


def main() -> None:
    trace = pathlib.Path("trace/events.jsonl")
    failed: list[str] = []
    if not trace.is_file():
        failed.append("no-trace")
    elif not any(agent_turn(line) for line in trace.read_text(encoding="utf-8").splitlines()):
        failed.append("no-agent-turn")
    print(json.dumps({"score": 0 if failed else 1, "failed": failed}))


if __name__ == "__main__":
    main()
