"""
Day 6 Task 10 (seeded in Task 1) — dependency-injection seams for the API.

Route handlers in `app.py`/`health.py` must depend on the provider
functions below (via FastAPI's `Depends`) rather than construct
`GroundedAnswerService` / `ModelGateway` / a retriever / a policy
evaluator / a health check themselves. No hardwired production dependency
lives inside a route handler.

Two levels of override are both supported:

1. Replace the whole pipeline at once - most tests across this project do
   this, since it is simplest:
   `app.dependency_overrides[get_answer_service] = lambda: fake_service`

2. Replace *one* ingredient and let `get_answer_service` assemble the
   rest normally - `get_gateway`, `get_retriever` and `get_policy_evaluator`
   are each their own FastAPI dependency, declared as `Depends(...)`
   parameters of `get_answer_service` itself, so overriding e.g. just
   `get_policy_evaluator` changes what a real, otherwise-unmodified
   `get_answer_service()` builds:
   `app.dependency_overrides[get_policy_evaluator] = lambda: fake_policy`
   This is what makes "gateway", "retriever" and "input/policy component"
   independently replaceable (Task 10's own list), not only replaceable as
   a bundle.

`_default_gateway()` is `lru_cache`d so a real deployment builds exactly
one `ModelGateway` (and therefore loads `config/model-routing.yaml`
exactly once) across the process lifetime, and - just as importantly -
so that importing this module, building the FastAPI app, or generating
OpenAPI never triggers config loading: `ModelGateway.from_config()` only
runs the first time `get_gateway()` is actually resolved for a request
that was not overridden.

Day 8 Task 4: `get_session_store`/`get_memory_service` are the same
pattern - `_default_session_store()` is `lru_cache`d so a real deployment
opens exactly one `SqliteSessionStore` (one SQLite connection, reused
across requests - the store itself serializes concurrent access
internally, see `memory/store.py`) rather than one per request, and so
importing this module never touches `data/sessions/` on disk until a
request actually resolves the dependency. `app.py`'s `/ask` handler
depends on `get_memory_service`, never on `SqliteSessionStore`/
`InMemorySessionStore` directly - tests override `get_memory_service`
(or just `get_session_store`, letting `get_memory_service` assemble a
real `MemorySessionService` around a fake store) exactly like every other
dependency here.

Task 8: `get_answer_service` wraps `get_gateway`/`get_retriever`'s results
in `MetricsGateway`/`MetricsRetriever` (instrumentation.py) before handing
them to `GroundedAnswerService` - metrics are recorded at this DI
boundary, never inside Day 5 itself. A test that overrides `get_gateway`/
`get_retriever` directly still gets its fake wrapped for metrics, exactly
like the real ones - only a test that overrides `get_answer_service`
itself bypasses the wrapping (reasonable: at that point the test owns the
whole service construction).

Day 8 Task 6/7: `get_summarizer` defaults to `FakeSummarizer`, not a real
`ModelGatewaySummarizer` - deliberately, unlike every other provider in
this module. Compaction (Task 6) is optional bookkeeping triggered only
when a session's memory outgrows its budget, not something any `/ask`
call needs to succeed; defaulting it to a real model call would make an
otherwise-unrelated request's success depend on Model Gateway
configuration for what is, from the caller's point of view, invisible
background housekeeping - a materially different risk than `get_gateway`/
`get_retriever`, which the main answer path always genuinely needs.
Real, model-backed compaction is still fully supported (Task 6's own
rule: "if used, must call through the Model Gateway") - opt in with
`app.dependency_overrides[get_summarizer] = lambda: ModelGatewaySummarizer(get_gateway())`,
the same override pattern as every other dependency here.
"""
from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from fastapi import Depends

from aico.api.instrumentation import MetricsGateway, MetricsRetriever, MetricsSessionStore
from aico.memory.service import MemorySessionService
from aico.memory.store import DEFAULT_SESSION_DB_PATH, SessionStore, SqliteSessionStore
from aico.memory.summarizer import FakeSummarizer, Summarizer
from aico.platform.model_gateway import ModelGateway
from aico.rag.answer_service import BM25Retriever, GroundedAnswerService, PolicyEvaluator, Retriever
from aico.security.input_policy import evaluate_policy

if TYPE_CHECKING:
    from aico.api.health import DependencyCheck


@lru_cache(maxsize=1)
def _default_gateway() -> ModelGateway:
    return ModelGateway.from_config()


def get_gateway() -> ModelGateway:
    """Default provider: the real Model Gateway (Day 3)."""

    return _default_gateway()


def get_retriever() -> Retriever:
    """Default provider: the real `BM25Retriever` over `data/index`
    (Day 5). Constructed fresh per call (not cached) - unchanged from
    this project's pre-Task-10 behavior."""

    return BM25Retriever()


def get_policy_evaluator() -> PolicyEvaluator:
    """Default provider: the real input/policy component (Day 5's
    `evaluate_policy`)."""

    return evaluate_policy


def get_answer_service(
    gateway: ModelGateway = Depends(get_gateway),
    retriever: Retriever = Depends(get_retriever),
    policy_evaluator: PolicyEvaluator = Depends(get_policy_evaluator),
) -> GroundedAnswerService:
    """Default provider: the real Day 5 pipeline, assembled from the
    three independently-overridable providers above. Tests override
    either this dependency as a whole, or any one of `get_gateway`/
    `get_retriever`/`get_policy_evaluator` individually."""

    return GroundedAnswerService(
        gateway=MetricsGateway(gateway),
        retriever=MetricsRetriever(retriever),
        policy_evaluator=policy_evaluator,
    )


@lru_cache(maxsize=1)
def _default_session_store() -> SessionStore:
    return SqliteSessionStore(db_path=DEFAULT_SESSION_DB_PATH)


def get_session_store() -> SessionStore:
    """Default provider: the real local `SqliteSessionStore` (Day 8
    Task 2), reused across requests via `_default_session_store`'s cache."""

    return _default_session_store()


def get_memory_service(store: SessionStore = Depends(get_session_store)) -> MemorySessionService:
    """Default provider: `MemorySessionService` (Day 8 Task 3) wrapping
    the store in `MetricsSessionStore` (Task 11) - the same "wrap at
    assembly, not at the individual provider" pattern `get_answer_service`
    already uses for gateway/retriever. Overriding `get_session_store`
    alone (e.g. with an `InMemorySessionStore` in tests) still produces a
    real, metrics-wrapped `MemorySessionService` around it; only a test
    that overrides `get_memory_service` itself bypasses the wrapping."""

    return MemorySessionService(MetricsSessionStore(store))


def get_summarizer() -> Summarizer:
    """Default provider: `FakeSummarizer` (Day 8 Task 6) - deterministic,
    free, no external dependency. See module docstring for why this
    default is deliberately different from every other provider here."""

    return FakeSummarizer()


def get_retrieval_health_check() -> DependencyCheck:
    """Default provider: the real retrieval/index health check
    (health.py). Tests override this to force a deterministic
    healthy/unavailable result without touching `data/index` on disk.

    Imports `health.py` locally (not at module import time) because
    `health.py` in turn depends on this module for its routes' `Depends`
    - a module-level import here would be circular."""
    from aico.api.health import check_retrieval_health

    return check_retrieval_health


def get_model_gateway_health_check() -> DependencyCheck:
    """Default provider: the real Model Gateway configuration health
    check (health.py). Tests override this to force a deterministic
    healthy/unavailable result without depending on `config/model-routing.yaml`
    or environment variables being set in the test environment. See
    `get_retrieval_health_check` for why the import is local."""
    from aico.api.health import check_model_gateway_health

    return check_model_gateway_health
