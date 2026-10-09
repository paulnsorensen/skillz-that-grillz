import socket
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

from skillz_experiments._cases import CodedError

# Linux and macOS limit an AF_UNIX path to about 104-108 bytes, including the terminating NUL.
SOCKET_PATH_LIMIT = 100


@contextmanager
def _server() -> Generator[socket.socket]:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(16)
        yield server


@contextmanager
def listening() -> Generator[int]:
    """Listen on host loopback. A sandbox with network isolation cannot connect to the port."""
    with _server() as server:
        yield cast(int, server.getsockname()[1])


def _connected(server: socket.socket) -> Callable[[str], bool]:
    def connected(token: str) -> bool:
        while True:
            try:
                client = server.accept()[0]
            except BlockingIOError:
                return False
            with client:
                client.settimeout(1)
                try:
                    if token.encode() in client.recv(256):
                        return True
                except TimeoutError:
                    continue
                except OSError:
                    return True
    return connected


@contextmanager
def watched() -> Generator[tuple[int, Callable[[str], bool]]]:
    """Listen on host loopback and yield the port with a check.

    The check is true when a client sent the token. It is also true when a client resets the connection,
    because that client reached the port. A stray client that sends nothing, or sends other bytes, does not count.
    """
    with _server() as server:
        server.setblocking(False)
        yield cast(int, server.getsockname()[1]), _connected(server)


@contextmanager
def watched_unix(directory: Path, *, active: bool = True) -> Generator[tuple[Path, Callable[[str], bool]]]:
    """Listen on a host Unix socket in `directory` and yield its path with the same check as `watched`.

    When `active` is False, nothing listens and the check is always False.
    """
    path = directory / "u.sock"
    if not active:
        yield path, lambda _token: False
        return
    if len(str(path).encode()) > SOCKET_PATH_LIMIT:
        raise CodedError("sandbox-unavailable", f"Claude Code sandbox unavailable: the Unix-socket probe path {path} is too "
                         + "long for AF_UNIX; no unsafe fallback; fix: set TMPDIR to a short directory such as /tmp, then run again")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(path))
        server.listen(16)
        server.setblocking(False)
        try:
            yield path, _connected(server)
        finally:
            path.unlink(missing_ok=True)


@contextmanager
def watched_abstract(name: str, *, active: bool = True) -> Generator[Callable[[str], bool]]:
    """Listen on a host abstract Unix socket named `name` (Linux) and yield the same check as `watched`.

    An abstract socket has no file, so filesystem rules cannot hide it; only a network namespace or Landlock
    scoping can. When `active` is False, nothing listens and the check is always False.
    """
    if not active:
        yield lambda _token: False
        return
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind("\0" + name)
        server.listen(16)
        server.setblocking(False)
        yield _connected(server)

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
        " sock.close()\n"
        " try:\n"
        "  udp=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)\n"
        "  udp.connect(('192.0.2.1',9))\n"
        " except OSError as error:\n"
        "  import errno\n"
        "  if error.errno not in (errno.ENETUNREACH,errno.EPERM,errno.EACCES): raise SystemExit('routed network isolation failed')\n"
        " else: raise SystemExit('network isolation failed')\n"
        "print('isolation-ok')\n"
    )
