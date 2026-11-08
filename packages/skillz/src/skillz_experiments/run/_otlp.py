"""Export finished spans as OTLP/HTTP with a JSON body, through the standard library only.

`exporter_from_environment` reads the standard OpenTelemetry exporter variables. It returns None when no traces
endpoint is set, when `OTEL_SDK_DISABLED` is `true`, or when `OTEL_TRACES_EXPORTER` names `none`.
The body is always OTLP JSON. The `http/json` and `http/protobuf` protocols both send it: an OTLP/HTTP receiver reads
the body by its content type. The `grpc` protocol, or an endpoint that is not `http` or `https`, prints one warning
and turns the export off. A failed export prints one warning and never stops the run.
Header values reach only the request. No warning names them, and no warning shows the user part of the endpoint.
"""
from __future__ import annotations

import http.client
import json
import math
import sys
import threading
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol, cast, final

Attribute = str | int | float | bool | list[str]
HTTP_PROTOCOLS = ("http/json", "http/protobuf")
DEFAULT_TIMEOUT_SECONDS = 10.0
SERVICE_NAME = "skillz-experiment"


@dataclass
class Span:
    """One span. `start` and `end` are Unix times in nanoseconds; `error` names the failure of a span that failed."""

    name: str
    trace_id: str
    span_id: str
    parent_id: str | None
    start: int
    end: int = 0
    attributes: dict[str, Attribute] = field(default_factory=dict)
    error: str | None = None


class Exporter(Protocol):
    def export(self, spans: Sequence[Span]) -> None: ...


def warn(message: str) -> None:
    print(f"skillz-experiment: {message}", file=sys.stderr, flush=True)


def _value(value: Attribute) -> dict[str, object]:
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, str):
        return {"stringValue": value}
    return {"arrayValue": {"values": [{"stringValue": item} for item in value]}}


def _attributes(values: Mapping[str, Attribute]) -> list[dict[str, object]]:
    return [{"key": key, "value": _value(value)} for key, value in values.items()]


def encode(spans: Sequence[Span], resource: Mapping[str, Attribute]) -> bytes:
    """Return the OTLP JSON body of one export request. Trace and span ids are hex text, as OTLP JSON requires."""
    rows = [{"traceId": span.trace_id, "spanId": span.span_id,
             **({"parentSpanId": span.parent_id} if span.parent_id else {}),
             "name": span.name, "kind": 1, "startTimeUnixNano": str(span.start), "endTimeUnixNano": str(span.end),
             "attributes": _attributes(span.attributes),
             "status": {"code": 2, "message": span.error} if span.error is not None else {"code": 0}}
            for span in spans]
    return json.dumps({"resourceSpans": [{"resource": {"attributes": _attributes(resource)}, "scopeSpans": [
        {"scope": {"name": SERVICE_NAME}, "spans": rows}]}]}).encode()


def _shown(endpoint: str) -> str:
    """Return the endpoint without its user part, query, and fragment, for a warning."""
    parts = urllib.parse.urlsplit(endpoint)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc.rpartition("@")[2], parts.path, "", ""))


@final
class OtlpJson:
    """POST each batch of spans to one OTLP/HTTP traces endpoint. The first failure prints one warning."""

    def __init__(self, endpoint: str, headers: Mapping[str, str], timeout: float, resource: Mapping[str, Attribute],
                 report: Callable[[str], None] = warn) -> None:
        self.endpoint = endpoint
        self._headers = dict(headers)
        self._timeout = timeout
        self._resource = dict(resource)
        self._report = report
        self._warned = False

    def export(self, spans: Sequence[Span]) -> None:
        request = urllib.request.Request(self.endpoint, data=encode(spans, self._resource), method="POST",
                                         headers={**self._headers, "Content-Type": "application/json"})
        try:
            with cast(http.client.HTTPResponse, urllib.request.urlopen(request, timeout=self._timeout)) as response:
                _ = response.read()
        except (OSError, ValueError, OverflowError, http.client.HTTPException) as error:
            if not self._warned:
                self._warned = True
                self._report(f"the OTLP trace export to {_shown(self.endpoint)} failed ({type(error).__name__}); "
                             + "the run continues without it")


def _pairs(text: str) -> dict[str, str]:
    """Read the `key=value,key=value` list of the header and resource variables. Keys and values are URL-decoded."""
    found: dict[str, str] = {}
    for item in text.split(","):
        key, separator, value = item.partition("=")
        if separator and key.strip():
            found[urllib.parse.unquote(key.strip())] = urllib.parse.unquote(value.strip())
    return found


def _timeout(text: str | None) -> float:
    try:
        milliseconds = float(text) if text else DEFAULT_TIMEOUT_SECONDS * 1000
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    seconds = milliseconds / 1000
    return seconds if math.isfinite(seconds) and 0 < seconds < threading.TIMEOUT_MAX else DEFAULT_TIMEOUT_SECONDS


def exporter_from_environment(environ: Mapping[str, str], report: Callable[[str], None] = warn) -> OtlpJson | None:
    """Return the exporter that the OpenTelemetry variables configure, or None when they configure no trace export."""
    def signal(name: str) -> str:
        return (environ.get(f"OTEL_EXPORTER_OTLP_TRACES_{name}") or environ.get(f"OTEL_EXPORTER_OTLP_{name}") or "").strip()

    exporters = {item.strip().lower() for item in environ.get("OTEL_TRACES_EXPORTER", "otlp").split(",")}
    if environ.get("OTEL_SDK_DISABLED", "").strip().lower() == "true" or "none" in exporters:
        return None
    endpoint = environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "").strip()
    if not endpoint:
        base = environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
        if not base:
            return None
        endpoint = base.rstrip("/") + "/v1/traces"
    protocol = signal("PROTOCOL") or "http/json"
    if protocol not in HTTP_PROTOCOLS:
        report(f"the OTLP protocol {protocol!r} is not supported; set OTEL_EXPORTER_OTLP_PROTOCOL=http/json. "
               + "The run continues without a trace export")
        return None
    try:
        scheme = urllib.parse.urlsplit(endpoint).scheme
    except ValueError:
        scheme = ""
    if scheme not in ("http", "https"):
        report("the OTLP traces endpoint must be an http or https URL; the run continues without a trace export")
        return None
    resource: dict[str, Attribute] = {**_pairs(environ.get("OTEL_RESOURCE_ATTRIBUTES", "")),
                                      "service.name": environ.get("OTEL_SERVICE_NAME", "").strip() or SERVICE_NAME}
    return OtlpJson(endpoint, _pairs(signal("HEADERS")), _timeout(signal("TIMEOUT")), resource, report)
