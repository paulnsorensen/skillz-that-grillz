# Click and Typer source checks via gh api (fetched 2026-09-23)
pallets/click src/click/parser.py:367: raise NoSuchOption(opt, possibilities=self._long_opt, ctx=self.ctx)
fastapi/typer typer/core.py:6: from difflib import get_close_matches
fastapi/typer typer/core.py:1005: suggest_commands: bool = True,
fastapi/typer typer/core.py:1181: matches = get_close_matches(typo, available_commands)
fastapi/typer typer/core.py:1183: suggestions = ", ".join(f"{m!r}" for m in matches)
