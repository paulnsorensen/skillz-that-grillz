"""The reference wrapper slice. It imports the harness slice only through its barrel. Import only this barrel from outside it.

The CLI calls `register` and names no wrapper. Each wrapper adds its own command to the `harness-wrapper` group.
"""
from skillz_experiments.wrapper._commands import register
from skillz_experiments.wrapper._gemini import IMAGE, Runtime, respond

__all__ = ["register", "respond", "Runtime", "IMAGE"]
