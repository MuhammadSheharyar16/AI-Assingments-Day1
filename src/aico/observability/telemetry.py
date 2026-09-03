"""
Day 6 Task 9 — OpenTelemetry tracing setup.

Configures the process-global OTel `TracerProvider` once
(`configure_tracing()`, idempotent - same pattern as `logging.
configure_logging()`), with a `SimpleSpanProcessor` over an
`InMemorySpanExporter` - a local/in-memory exporter, which the assignment
explicitly allows for tests ("A local/in-memory exporter is acceptable
for tests. A real external telemetry backend is not required."). Every
span this codebase creates (see `answer_service.py` and `app.py`) calls
`opentelemetry.trace.get_tracer(__name__)` directly - the standard OTel
usage pattern - rather than importing anything from this module, so
`aico.rag`/`aico.platform` never import `aico.observability` (same
boundary `logging.py`/`metrics.py` already keep - see
`aico/observability/__init__.py`). This module only owns *configuring*
where spans go, never *creating* them.

`get_finished_spans()` is the test-and-Task-11-artifact-facing read path:
every span recorded since the process started (a `SimpleSpanProcessor`
exports synchronously as each span ends, so this is always up to date,
unlike a batched processor).
"""
from __future__ import annotations

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

_exporter = InMemorySpanExporter()
_CONFIGURED = False


def configure_tracing() -> None:
    """Install the in-memory-exporting `TracerProvider` as the OTel
    global provider. Safe to call more than once - only the first call
    has any effect, so importing `app.py` repeatedly (as pytest does
    across test modules) never installs a second provider."""

    global _CONFIGURED
    if _CONFIGURED:
        return

    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(_exporter))
    trace.set_tracer_provider(provider)
    _CONFIGURED = True


def get_finished_spans():
    """Every span exported so far, oldest first. Test/artifact-facing
    only - nothing in the request path reads this."""

    return _exporter.get_finished_spans()


def clear_finished_spans() -> None:
    """Test-facing only: reset the in-memory exporter's buffer so a test
    can assert on exactly the spans its own request produced, without an
    earlier test's spans still present."""

    _exporter.clear()
