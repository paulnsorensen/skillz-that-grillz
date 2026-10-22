"""The turn loop of a journey on one live Claude Code process.

`TurnRunner` is a thin friend of one `ClaudeCode` instance. It reads `budget`, `checkpoint`, `role`, `declared`,
`_inventory`, and `_late`, and it ends with exactly one `Exit` value.
"""
# pyright: reportPrivateUsage=false, reportImportCycles=false
from __future__ import annotations

from collections.abc import Callable, Collection
from enum import Enum
from typing import cast, final, TYPE_CHECKING

from skillz_experiments._cases import CodedError
from skillz_experiments._runtime import Live
from skillz_experiments.evaluation import Reply, Transcript
from skillz_experiments.harness._claude._events import (
    assistant_text,
    init_inventory,
    is_result,
    loaded_skills,
    parse_events,
    user_line,
)

if TYPE_CHECKING:
    from skillz_experiments.harness._claude._transport import ClaudeCode


NO_INIT = "has no init event, so the skill list is unknown; the invocation counts against the budget"


class Exit(Enum):
    DONE = "done"
    MAX_TURNS = "max-turns"
    NOT_LOADED = "not-loaded"
    PROCESS_DIED = "process-died"


@final
class TurnRunner:
    def __init__(self, claude: ClaudeCode, child: Live, *, expected: list[str], builtin: Collection[str], request: str,
                 shown: str, max_turns: int, holdout: bool, simulate: Callable[[Transcript], Reply]) -> None:
        self.claude = claude
        self.child = child
        self.expected = expected
        self.builtin = builtin
        self.message = request
        self.shown = shown
        self.max_turns = max_turns
        self.holdout = holdout
        self.simulate = simulate
        self.first: tuple[list[str], ...] | None = None
        self.events: list[dict[str, object]] = []
        self.transcript: Transcript = []

    def run(self) -> Exit:
        """Run turns until one ends the journey, and return why it ended. A raise inside a turn stays an exception."""
        for turn in range(self.max_turns):
            outcome = self.step(turn)
            if outcome is not None:
                return outcome
        return Exit.MAX_TURNS

    def step(self, turn: int) -> Exit | None:
        """Run one turn. Return the exit that it reached, or None when the journey goes on."""
        claude = self.claude
        claude.budget.claim(holdout=self.holdout, role=claude.role)
        claude.checkpoint()
        with claude.budget.timed(claude.role):
            lines, ended = self.child.turn(user_line(self.message), claude.budget.remaining(), is_result)
        fresh = parse_events("".join(lines))
        self.events += fresh
        if self.first is None:
            claude._inventory = claude._late.adopt(fresh, self.expected, claude.inventory(), claude.declared)
            self.builtin = claude._inventory.builtin
        for init in (event for event in fresh if event.get("type") == "system" and event.get("subtype") == "init"):
            if self.first is None:
                self.first = init_inventory(init)
            elif init_inventory(init) != self.first:
                raise CodedError("isolation-failed", "Claude Code isolation fails: the skill, tool, agent, MCP server, or plugin inventory "
                                 + f"changed in turn {turn + 1}; the invocation counts against the budget")
        if any(event.get("type") == "result" and event.get("is_error") is True for event in fresh):
            raise RuntimeError(f"Claude Code fails: error-result (turn {turn + 1} reports an error result); "
                               + "the invocation counts against the budget")
        if not ended:
            return Exit.PROCESS_DIED
        if self.first is None:
            raise CodedError("isolation-failed", "Claude Code isolation fails: turn 1 " + NO_INIT)
        self.transcript += [{"role": "user", "text": self.shown}, {"role": "assistant", "text": assistant_text(fresh)}]
        if turn == 0 and not [name for name in cast(list[str], loaded_skills(fresh, self.builtin))
                              if name not in claude.declared.skills]:
            return Exit.NOT_LOADED
        if turn + 1 >= self.max_turns:
            return Exit.MAX_TURNS
        reply = self.simulate(self.transcript)
        if reply.done:
            return Exit.DONE
        self.message = self.shown = reply.reply
        return None
