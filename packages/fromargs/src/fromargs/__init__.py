"""Self-healing Cyclopts CLI helpers for agent-friendly command lines.

``App`` composes a ``cyclopts.App``. Register a command with ``@app.command``
and a nested command group with ``app.group(name)``. A handler returns data,
not text: ``run`` parses argv once, invokes one handler, and prints the
return value as one JSON document on stdout. ``None`` means no stdout and
exit 0.

``run`` strips a bare ``--json`` or ``--full`` token from anywhere before the
end-of-options marker: ``--json`` is a no-op accepted for agents that pass it
by habit, and ``--full`` turns off result truncation. It also repairs one
verified shell-merged argument before it reports an error, and announces
each repair on stderr. Every error is one JSON line on stderr:
``{"error": <message>, "exit_code": <n>}``. There is no JSON input mode.
"""

from cyclopts import Group, Parameter

from fromargs._app import App
from fromargs._errors import CliError, contract_error

__all__ = ["App", "CliError", "Group", "Parameter", "contract_error"]