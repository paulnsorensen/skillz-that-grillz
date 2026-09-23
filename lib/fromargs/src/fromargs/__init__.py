"""Self-healing Cyclopts CLI helpers for agent-friendly command lines.

``run`` parses argv once and invokes one command. Before the parse it moves
leading ``--json``/``--full`` flags after the command and splits shell-merged
arguments, announcing each repair on stderr. ``emit`` prints JSON or truncated
text output. There is no JSON input mode.
"""

from fromargs._argv import repair_argv
from fromargs._errors import CliError, contract_error
from fromargs._output import emit
from fromargs._run import run

__all__ = ["CliError", "contract_error", "emit", "repair_argv", "run"]
