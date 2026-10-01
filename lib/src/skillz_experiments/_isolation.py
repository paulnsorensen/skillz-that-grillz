from pathlib import Path


def probe(workspace: Path, sealed: Path, engine: Path) -> str:
    denied = [str(sealed), str(workspace / "escape"), str(engine)]
    return (
        "import os,pathlib,socket\n"
        f"for name in {denied!r}:\n"
        " try: pathlib.Path(name).read_bytes()\n"
        " except OSError: pass\n"
        " else: raise SystemExit('read isolation failed')\n"
        "try: pathlib.Path('.agents/skillz-write-probe').write_text('denied')\n"
        "except OSError: pass\n"
        "else: raise SystemExit('candidate write isolation failed')\n"
        "assert 'CODEX_HOME' not in os.environ\n"
        "assert set(os.environ) <= {'PATH','HOME','TMPDIR','LANG','LC_CTYPE'}\n"
        "p=pathlib.Path('allowed');p.write_text('ok');assert p.read_text()=='ok'\n"
        "try: socket.socket(socket.AF_INET,socket.SOCK_STREAM)\n"
        "except OSError: pass\n"
        "else: raise SystemExit('network isolation failed')\n"
        "print('isolation-ok')\n"
    )
