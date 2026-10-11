"""The transport contract of a harness adapter, and the four credential kinds that an adapter reports."""
from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, ClassVar, Protocol

from skillz_experiments._candidate import Candidate
from skillz_experiments._cases import Case
from skillz_experiments._runtime import Budget
from skillz_experiments.harness._check import Check

if TYPE_CHECKING:
    from skillz_experiments.harness._claude import ClaudeLogin


class Transport(Protocol):
    """One harness adapter. It owns its host checks, its credential kind, and the capabilities that a run asks about.

    `credential_kind` is one of the four credential constants above. The class variables answer a question about an
    adapter name before any instance exists, so the caller never compares the name.
    """

    # The adapter runs the Claude Code sandbox: it can run a contract that declares an extra tool surface,
    # and, as judge, it can show capture files to the judge.
    claude_sandbox: ClassVar[bool]
    # A run-level `--effort`, `--sandbox-read`, or `--sandbox-seconds` flag is an error for this adapter.
    refuses_claude_options: ClassVar[bool]

    budget: Budget
    rejection: str | None

    @property
    def credential_kind(self) -> str: ...

    @classmethod
    def host_checks(cls, executable: str | None = None) -> list[Check]:
        """Run the free host checks of this adapter, in report order. They make no model call."""
        ...

    def preflight(self) -> dict[str, object]: ...
    def close(self) -> None: ...
    def reuse_key(self, fingerprint: object) -> str | None:
        """Return the key under which a recorded live pass stays valid, or None when the adapter keeps no live pass."""
        ...

    def resume(self, kept: object) -> bool:
        """Return whether the recorded pass `kept` holds, so that the run skips the live preflight of this role."""
        ...

    def run_facts(self, evidence: Mapping[str, object]) -> dict[str, object]:
        """Return the facts that this adapter adds to the run record, from its preflight evidence."""
        ...

    def adopt_login(self) -> ClaudeLogin | None:
        """Return the Claude login that the run shares with later roles, or None when the adapter has none."""
        ...

    def check_name(self, candidate: Candidate) -> None: ...
    def check_candidate(self, candidate: Candidate) -> bool: ...
    def invoke(self, prompt: str, candidate: Candidate | None = None, case: Case | None = None,
               *, holdout: bool = False, schema: dict[str, object] | None = None) -> dict[str, object]: ...
