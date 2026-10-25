from enum import IntEnum

class ExitCode(IntEnum):
    NoDeadCode = 0
    InvalidInput = 1
    InvalidCmdlineArguments = 2
    DeadCode = 3
