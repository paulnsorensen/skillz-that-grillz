"""`watch`: a read-only page on 127.0.0.1 that shows `<out>/run.json` while a run works.

The server reads `run.json` again when its size or modification time changes. It writes no file.
It answers only GET requests for its own three paths, and only for a Host header of its own loopback address.
"""
from __future__ import annotations

import contextlib
import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import final

from skillz_experiments._cases import CodedError
from skillz_experiments._records import read
from skillz_experiments.run._watch_page import PAGE, SCRIPT, STYLE
from skillz_experiments.run._watch_view import view

HOST = "127.0.0.1"
# Same-origin assets only, so a judge reason or a hypothesis in the data cannot run as a script.
POLICY = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; "
          + "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
ASSETS = {"/": ("text/html", PAGE), "/app.js": ("text/javascript", SCRIPT), "/app.css": ("text/css", STYLE)}


@final
class _Source:
    """Read `run.json` and keep its view until the file changes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._key: tuple[int, int] | None = None
        self._view: dict[str, object] = {}

    def current(self) -> dict[str, object]:
        base: dict[str, object] = {"path": str(self.path)}
        try:
            found = os.stat(self.path)
        except FileNotFoundError:
            return base | {"state": "waiting"}
        except OSError as error:
            return base | {"state": "error", "error": error.strerror or "run.json cannot be read"}
        key = (found.st_mtime_ns, found.st_size)
        with self._lock:
            if key != self._key:
                try:
                    self._view = view(read(self.path))
                except (OSError, ValueError) as error:
                    return base | {"state": "error", "error": str(error) or "run.json cannot be read"}
                self._key = key
            return base | {"state": "ok", "modified": found.st_mtime} | self._view


@final
class _Server(ThreadingHTTPServer):
    """A loopback server. `ThreadingHTTPServer` already makes its request threads daemons."""
    def __init__(self, port: int, source: _Source) -> None:
        super().__init__((HOST, port), _Handler)
        self.source = source
        self.capability = secrets.token_urlsafe(32)
        bound = self.server_address[1]
        self.hosts = {f"{HOST}:{bound}", f"localhost:{bound}"}


class _Handler(BaseHTTPRequestHandler):
    server: _Server  # pyright: ignore[reportIncompatibleVariableOverride]

    def do_GET(self) -> None:  # noqa: N802 - the name that BaseHTTPRequestHandler dispatches
        if self.headers.get("Host") not in self.server.hosts:
            self._send(403, "text/plain", "forbidden host\n")
            return
        path = self.path.split("?", 1)[0]
        if path == "/api/run":
            if self.headers.get("X-Watch-Capability") != self.server.capability:
                self._send(403, "text/plain", "forbidden\n")
                return
            current = self.server.source.current()
            try:
                body = json.dumps(current, allow_nan=False)
            except (ValueError, OverflowError) as error:
                body = json.dumps({"path": str(self.server.source.path), "state": "error", "error": str(error)})
            self._send(200, "application/json", body)
        elif path in ASSETS:
            self._send(200, *ASSETS[path])
        else:
            self._send(404, "text/plain", "not found\n")

    def _send(self, status: int, kind: str, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        for name, value in (("Content-Type", f"{kind}; charset=utf-8"), ("Content-Length", str(len(data))),
                            ("Cache-Control", "no-store"), ("Content-Security-Policy", POLICY),
                            ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer")):
            self.send_header(name, value)
        self.end_headers()
        _ = self.wfile.write(data)

    # typing.override needs Python 3.12, and the archive ships no typing_extensions.
    def log_message(self, format: str, *args: object) -> None:  # pyright: ignore[reportImplicitOverride]  # noqa: A002
        """Log nothing: a request line can carry a private path."""


def watch(out: Path, port: int = 0) -> None:
    """Serve the page for `out/run.json` on 127.0.0.1 until an interrupt. Print the URL as one JSON line first."""
    if not 0 <= port <= 65535:
        raise CodedError("usage", "--port must be from 0 to 65535")
    source = _Source(out.resolve() / "run.json")
    try:
        server = _Server(port, source)
    except OSError as error:
        raise CodedError("watch-port", f"cannot listen on {HOST}:{port}: {error.strerror or error}") from None
    with server:
        print(json.dumps({"url": f"http://{HOST}:{server.server_address[1]}/#{server.capability}", "run": str(source.path)}), flush=True)
        with contextlib.suppress(KeyboardInterrupt):
            server.serve_forever()
