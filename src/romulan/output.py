"""Standardized JSON-lines output schema (v1) for the romulan CLI.

Every object the CLI emits — command results, domain events, trace events,
and errors — is a single self-contained JSON object on its own line
(NDJSON) with two mandatory fields:

- ``"v": 1`` — schema version (kept in step with wire protocol v1).
- ``"type"`` — one of ``"result"``, ``"event"``, or ``"error"``.

Shapes by type:

- ``result``: ``{"v":1,"type":"result","cmd":"<cmd>","data":{...}}`` —
  the outcome of a command (``reset``, ``upload_rom``, ``status``, …).
- ``event``: ``{"v":1,"type":"event","event":"<name>","data":{...}}`` —
  asynchronous or progress information (``cycle``, ``port_detected``,
  verbose trace events like ``send``/``recv``/``ack``). ``data`` is
  omitted when empty.
- ``error``: ``{"v":1,"type":"error","error":"<code>","detail":"<msg>"}``
  with an optional ``"errors":[...]`` list for multi-item failures
  (e.g. ROM build errors). Mirrors the firmware's error frames.

Streams: results and domain events go to **stdout**; errors go to
**stderr**; verbose trace events (see :class:`romulan.hardware_api.HardwareAPI`)
also go to stderr so stdout stays parseable as pure command output.
"""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

SCHEMA_VERSION = 1


def _dump(obj: dict[str, Any]) -> str:
    """Serialize one schema object as compact JSON."""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def emit_result(cmd: str, data: dict[str, Any], *, stream: TextIO | None = None) -> None:
    """Print a ``result`` object for a completed command.

    Args:
        cmd: The command name (e.g. ``"reset"``, ``"upload_rom"``).
        data: Command-specific result fields.
        stream: Output stream; defaults to ``sys.stdout`` (resolved at call
            time so test capture works).
    """
    print(
        _dump({"v": SCHEMA_VERSION, "type": "result", "cmd": cmd, "data": data}),
        file=stream if stream is not None else sys.stdout,
        flush=True,
    )


def emit_event(
    event: str,
    data: dict[str, Any] | None = None,
    *,
    stream: TextIO | None = None,
) -> None:
    """Print an ``event`` object.

    Args:
        event: The event name (e.g. ``"cycle"``, ``"port_detected"``).
        data: Optional event payload; omitted from the object when empty.
        stream: Output stream; defaults to ``sys.stdout`` (resolved at call
            time so test capture works).
    """
    obj: dict[str, Any] = {"v": SCHEMA_VERSION, "type": "event", "event": event}
    if data:
        obj["data"] = data
    print(_dump(obj), file=stream if stream is not None else sys.stdout, flush=True)


def emit_error(
    error: str,
    detail: str,
    *,
    errors: list[str] | None = None,
    stream: TextIO | None = None,
) -> None:
    """Print an ``error`` object.

    Args:
        error: Short machine-readable error code (e.g. ``"hardware_api"``).
        detail: Human-readable error message.
        errors: Optional list of individual failure descriptions.
        stream: Output stream; defaults to ``sys.stderr`` (resolved at call
            time so test capture works).
    """
    obj: dict[str, Any] = {
        "v": SCHEMA_VERSION,
        "type": "error",
        "error": error,
        "detail": detail,
    }
    if errors:
        obj["errors"] = errors
    print(_dump(obj), file=stream if stream is not None else sys.stderr, flush=True)
