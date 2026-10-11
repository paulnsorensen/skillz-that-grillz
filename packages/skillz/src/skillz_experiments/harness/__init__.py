"""The harness slice: how a run reaches a model. Import only this barrel from outside the slice."""
from skillz_experiments.harness._check import (API_KEY, MISSING_CREDENTIAL, SUBSCRIPTION_LOGIN, UNVERIFIED_CREDENTIAL,
                                               Check)
from skillz_experiments.harness._claude import NOTICE_CODES, TERMINAL_CODES, ClaudeOptions, Effort
from skillz_experiments.harness._doctor import doctor, host_report
from skillz_experiments.harness._harness import Configuration, EnvironmentDiffers, Harness
from skillz_experiments.harness._roles import BUILTIN_ADAPTERS, NONO_REMOVED, HarnessName
from skillz_experiments.harness._seatbelt import command_profile
from skillz_experiments.harness._transport import Transport

__all__ = [
    "Configuration", "Harness", "EnvironmentDiffers", "BUILTIN_ADAPTERS", "HarnessName", "Transport",
    "SUBSCRIPTION_LOGIN", "API_KEY", "UNVERIFIED_CREDENTIAL", "MISSING_CREDENTIAL", "doctor", "host_report", "Check",
    "ClaudeOptions", "Effort", "NOTICE_CODES", "TERMINAL_CODES", "NONO_REMOVED",
    "command_profile",
]
