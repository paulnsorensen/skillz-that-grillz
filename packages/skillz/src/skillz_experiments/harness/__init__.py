"""The harness slice: how a run reaches a model. Import only this barrel from outside the slice."""
from skillz_experiments.harness._claude import ClaudeOptions, Effort, stop_kind
from skillz_experiments.harness._doctor import doctor, host_report
from skillz_experiments.harness._harness import Configuration, EnvironmentDiffers, Harness
from skillz_experiments.harness._roles import BUILTIN_ADAPTERS, NONO_REMOVED
from skillz_experiments.harness._seatbelt import command_profile
from skillz_experiments.harness._transport import Transport

__all__ = [
    "Configuration", "Harness", "EnvironmentDiffers", "BUILTIN_ADAPTERS", "Transport", "doctor", "host_report",
    "ClaudeOptions", "Effort", "NONO_REMOVED", "command_profile", "stop_kind",
]
