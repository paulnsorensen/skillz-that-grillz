import socket
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import cast


@contextmanager
def listening() -> Generator[int]:
    """Listen on host loopback. A sandbox with network isolation cannot connect to the port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        yield cast(int, server.getsockname()[1])


def probe(workspace: Path, sealed: Path, engine: Path, port: int) -> str:
    """Build the isolation probe script. It must fail to reach the host loopback listener on `port`."""
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
        "try: sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM)\n"
        "except OSError: pass\n"
        "else:\n"
        " sock.settimeout(2)\n"
        f" try: sock.connect(('127.0.0.1',{port}))\n"
        " except OSError: pass\n"
        " else: raise SystemExit('network isolation failed')\n"
        "print('isolation-ok')\n"
    )
