"""
Day 6 Task 7 — structured logs.

One JSON object per log line (structured, machine-parseable - not the
free-text style Day 3's `model_gateway.py` uses, though the discipline is
the same one that module already documents: "Nothing here configures
handlers/level/output - that is the application's responsibility"; this
module IS that application-layer responsibility for the API service).

`log_event()` is the one function every stage of request handling calls
to emit a log line. `request_id`/`correlation_id` are required, explicit
keyword arguments - every call site in `aico.api` already holds them
(`correlation.py` decided them; `app.py` gets them via
`RequestContext`/`Depends(get_request_context)`), so this module never
reads them back out of `correlation.py`'s contextvars itself - that would
make this module depend on `aico.api.correlation`, which depends on this
module (Task 3's `CorrelationMiddleware` is what emits the
"http_request" start/end events below), a cycle. Every event also
carries:

    stage                          which operation this event describes
                                  ("http_request", "ask_pipeline", ...)
    outcome                        what happened ("start", "success",
                                  "rejected", a typed AskStatus value, ...)
    latency_ms                     when the event marks completion
    error_category                 a stable, already-safe category string
                                  (e.g. AskResponse.category /
                                  ApiError.error_code) - never a raw
                                  exception message

and NEVER (Task 7 "do not log raw" list, enforced by construction - no
call site in this codebase passes any of these to `log_event`):

    the user's question/prompt text, retrieved evidence content, a model
    completion, an Authorization header/claim value, a token/secret, or a
    raw vector.

`configure_logging()` wires a single stdout handler that prints each
record's already-JSON message as-is. It is idempotent (checked via
`_CONFIGURED`) so importing `app.py` more than once in a process (as
pytest does across test modules) never accumulates duplicate handlers -
without idempotency, every log line would be printed N times after N
imports.
"""
from __future__ import annotations

import json
import logging
import sys
from typing import Any, Optional

API_LOGGER_NAME = "aico.api"

logger = logging.getLogger(API_LOGGER_NAME)

_CONFIGURED = False


def configure_logging(level: int = logging.INFO) -> None:
    """Attach one stdout handler to the `aico.api` logger, printing each
    record's message (already a JSON string - see `log_event`) verbatim.
    Safe to call more than once - only the first call has any effect."""

    global _CONFIGURED
    if _CONFIGURED:
        return

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(level)
    _CONFIGURED = True


def log_event(
    *,
    request_id: Optional[str],
    correlation_id: Optional[str],
    stage: str,
    outcome: str,
    latency_ms: Optional[float] = None,
    error_category: Optional[str] = None,
    level: int = logging.INFO,
    **safe_fields: Any,
) -> None:
    """Emit one structured JSON log line. `safe_fields` is for additional
    already-sanitized, low-cardinality context (e.g. `status_code`,
    `method`) - never raw request/response content. Every call site in
    this codebase is expected to only ever pass values it already knows
    are safe; this function itself does not attempt content-based
    redaction, so a call site that ignores that contract can still leak -
    the discipline is enforced by review/tests
    (`tests/test_day06_observability.py`), not by scrubbing here."""

    payload: dict[str, Any] = {
        "request_id": request_id,
        "correlation_id": correlation_id,
        "stage": stage,
        "outcome": outcome,
    }
    if latency_ms is not None:
        payload["latency_ms"] = round(latency_ms, 2)
    if error_category is not None:
        payload["error_category"] = error_category
    payload.update(safe_fields)

    logger.log(level, json.dumps(payload, default=str, sort_keys=True))
