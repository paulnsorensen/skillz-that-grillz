from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path


class BudgetExhausted(RuntimeError):
    pass


@dataclass
class Budget:
    maximum: int
    seconds: float
    reserve: int = 6
    calls: int = 0
    started: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        if not 1 <= self.maximum <= 20 or not 0 < self.seconds <= 1200:
            raise ValueError("budget exceeds 20 calls or 1200 seconds")
        if not 0 <= self.reserve <= self.maximum:
            raise ValueError("invalid holdout reservation")

    def remaining(self) -> float:
        remaining = self.seconds - (time.monotonic() - self.started)
        if remaining <= 0:
            raise BudgetExhausted("global deadline exhausted")
        return remaining

    def claim(self, *, holdout: bool = False) -> None:
        _ = self.remaining()
        limit = self.maximum if holdout else self.maximum - self.reserve
        if self.calls >= limit:
            raise BudgetExhausted("global invocation budget exhausted")
        self.calls += 1


def process(command: list[str], *, cwd: Path, timeout: float,
            environment: dict[str, str], input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    with subprocess.Popen(command, cwd=cwd, env=environment, text=True, stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True) as child:
        try:
            stdout, stderr = child.communicate(input_text, timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            _ = child.communicate()
            raise BudgetExhausted("process group terminated at deadline") from None
        except BaseException:
            os.killpg(child.pid, signal.SIGKILL)
            _ = child.communicate()
            raise
        return subprocess.CompletedProcess(command, child.returncode, stdout, stderr)
