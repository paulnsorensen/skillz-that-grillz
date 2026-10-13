"""The harness slice: how a run reaches a model. Import only this barrel from outside the slice."""
from skillz_experiments.harness._claude import ClaudeOptions, Effort, stop_kind
from skillz_experiments.harness._doctor import doctor, host_report
from skillz_experiments.harness._harness import Configuration, EnvironmentDiffers, Harness
from skillz_experiments.harness._roles import (BUILTIN_ADAPTERS, CONFIGURED_SELECTIONS, NONO_REMOVED, check_selection,
                                               configured_selection, has_claude_sandbox, refuses_claude_options)
from skillz_experiments.harness._seatbelt import command_profile

__all__ = [
    "Configuration", "Harness", "EnvironmentDiffers", "BUILTIN_ADAPTERS", "CONFIGURED_SELECTIONS", "doctor",
    "host_report", "ClaudeOptions", "Effort", "NONO_REMOVED", "check_selection", "command_profile",
    "configured_selection", "has_claude_sandbox", "refuses_claude_options", "stop_kind",
]
