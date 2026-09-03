"""
Day 6 Task 10 (seeded in Task 1) — dependency-injection seams for the API.

Route handlers in `app.py`/`health.py` must depend on the provider
functions below (via FastAPI's `Depends`) rather than construct
`GroundedAnswerService` / `ModelGateway` / a health check themselves.
That is what lets a test replace the whole pipeline - or just one
dependency-health check - with a deterministic fake through
`app.dependency_overrides[get_answer_service] = lambda: fake_service`
without touching `app.py`/`health.py` - no hardwired production
dependency lives inside a route handler.

`_default_gateway()` is `lru_cache`d so a real deployment builds exactly
one `ModelGateway` (and therefore loads `config/model-routing.yaml`
exactly once) across the process lifetime, and - just as importantly -
so that importing this module, building the FastAPI app, or generating
OpenAPI never triggers config loading: `ModelGateway.from_config()` only
runs the first time `get_answer_service()` is actually resolved for a
request that was not overridden.

Task 8: the real gateway/retriever are wrapped in `MetricsGateway`/
`MetricsRetriever` (instrumentation.py) before being handed to
`GroundedAnswerService` - metrics are recorded at this DI boundary, never
inside Day 5 itself.
"""
from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from aico.api.instrumentation import MetricsGateway, MetricsRetriever
from aico.platform.model_gateway import ModelGateway
from aico.rag.answer_service import BM25Retriever, GroundedAnswerService

if TYPE_CHECKING:
    from aico.api.health import DependencyCheck


@lru_cache(maxsize=1)
def _default_gateway() -> ModelGateway:
    return ModelGateway.from_config()


def get_answer_service() -> GroundedAnswerService:
    """Default provider: the real Day 5 pipeline (real Model Gateway,
    default `BM25Retriever` over `data/index`), both wrapped for metrics
    (Task 8). Tests override this dependency rather than call it."""

    return GroundedAnswerService(
        gateway=MetricsGateway(_default_gateway()),
        retriever=MetricsRetriever(BM25Retriever()),
    )


def get_retrieval_health_check() -> "DependencyCheck":
    """Default provider: the real retrieval/index health check
    (health.py). Tests override this to force a deterministic
    healthy/unavailable result without touching `data/index` on disk.

    Imports `health.py` locally (not at module import time) because
    `health.py` in turn depends on this module for its routes' `Depends`
    - a module-level import here would be circular."""
    from aico.api.health import check_retrieval_health

    return check_retrieval_health


def get_model_gateway_health_check() -> "DependencyCheck":
    """Default provider: the real Model Gateway configuration health
    check (health.py). Tests override this to force a deterministic
    healthy/unavailable result without depending on `config/model-routing.yaml`
    or environment variables being set in the test environment. See
    `get_retrieval_health_check` for why the import is local."""
    from aico.api.health import check_model_gateway_health

    return check_model_gateway_health
