"""
Day 6 Task 10 (seeded in Task 1) — dependency-injection seams for the API.

Route handlers in `app.py` must depend on the provider functions below
(via FastAPI's `Depends`) rather than construct `GroundedAnswerService` /
`ModelGateway` themselves. That is what lets a test replace the whole
pipeline with a deterministic fake through
`app.dependency_overrides[get_answer_service] = lambda: fake_service`
without touching `app.py` - no hardwired production dependency lives
inside a route handler.

`_default_gateway()` is `lru_cache`d so a real deployment builds exactly
one `ModelGateway` (and therefore loads `config/model-routing.yaml`
exactly once) across the process lifetime, and - just as importantly -
so that importing this module, building the FastAPI app, or generating
OpenAPI never triggers config loading: `ModelGateway.from_config()` only
runs the first time `get_answer_service()` is actually resolved for a
request that was not overridden.
"""
from __future__ import annotations

from functools import lru_cache

from aico.platform.model_gateway import ModelGateway
from aico.rag.answer_service import GroundedAnswerService


@lru_cache(maxsize=1)
def _default_gateway() -> ModelGateway:
    return ModelGateway.from_config()


def get_answer_service() -> GroundedAnswerService:
    """Default provider: the real Day 5 pipeline (real Model Gateway,
    default `BM25Retriever` over `data/index`). Tests override this
    dependency rather than call it."""

    return GroundedAnswerService(gateway=_default_gateway())
