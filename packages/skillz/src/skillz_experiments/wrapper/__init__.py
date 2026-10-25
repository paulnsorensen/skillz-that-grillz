"""The reference wrapper slice. It imports the harness slice only through its barrel. Import only this barrel from outside it.

The CLI creates the `harness-wrapper` group and calls `register` with it. The slice adds one command per reference wrapper and names no CLI framework at runtime.
"""
from skillz_experiments.wrapper._commands import register

__all__ = ["register"]
