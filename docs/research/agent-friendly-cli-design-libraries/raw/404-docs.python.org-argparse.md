URL: https://docs.python.org/3/library/argparse.html
Fetched: 2026-09-23

> suggest_on_error (default: False) — Whether suggest_on_error should suggest corrections when a user makes a typo, e.g. for choices values.
> Changed in version 3.14: suggest_on_error and color parameters were added.

Secondary source (pythonmorsels.com, fetched 2026-09-23):
> the argparse module now includes a suggest_on_error argument that will suggest corrections when you make a typo in the choices options for a command-line argument

Note: suggest_on_error is opt-in (default False), stdlib argparse, new in Python 3.14 (released Oct 2025). It suggests corrections for mistyped string choices and subparser names (docs: "Enables suggestions for mistyped argument choices and subparser names").
