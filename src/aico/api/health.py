"""
Day 6 Task 6 — health endpoints.

Three distinct, separately-answerable questions (assignment brief):

    GET /health/live          Is this process alive enough to serve the
                               liveness check?
    GET /health/dependencies  Separate visibility into each remote/index
                               dependency the RAG pipeline needs.
    GET /health/ready         Should this instance currently receive
                               normal application traffic?

They are three routes, not one `/health` that mixes all three concerns
(working rule / common cause of failure: "one /health endpoint that mixes
liveness, readiness and dependency health without distinction").

Liveness never calls a dependency check - it cannot, by construction,
fail because a remote dependency is slow or down (working rule: "Do not
mark process liveness unhealthy merely because a temporary remote
dependency is unavailable"). It only proves this process can still
execute a request handler.

Degraded-mode policy (Task 6 "Degraded mode" - explicit, testable, and
matched by the runtime behavior below):

    Readiness is READY only when every monitored dependency
    (retrieval index, Model Gateway configuration) reports healthy.
    Any dependency reporting unavailable makes this instance NOT_READY
    (HTTP 503) - this service chooses the "mark readiness false" option
    from the brief's two documented choices, rather than a narrower
    degraded mode, because both retrieval and the Model Gateway are
    required for `/ask` to produce anything other than a typed failure -
    there is no reduced-but-still-useful mode to stay ready in.

Dependency checks:

    - `check_retrieval_health` - the Day 2 chunk index (`data/index/index.json`)
      loads and is non-empty. This IS the "retrieval/index readiness"
      dependency the brief names.
    - `check_model_gateway_health` - `config/model-routing.yaml` loads and
      its Foundry endpoint environment variable is set. This check
      intentionally covers BOTH "model gateway/provider" and "required
      configuration" from the brief's dependency list: in this codebase
      those are the same thing (`aico.platform.config.load_gateway_config`) -
      a separate, third check would just re-validate the identical
      config. It never makes a real network call to the provider (no
      avoidable real cloud call, per the working rules) - it proves the
      gateway is *configured*, not that the provider is currently
      reachable.

Never leaked in any health response (working rule): credentials, the
actual Foundry endpoint URL, prompts, retrieved evidence, or a raw
exception message - `detail` fields are short, safe, pre-written strings
or, at most, an exception's class name.
"""
from __future__ import annotations

import pathlib
from enum import Enum
from typing import Callable

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict

from aico.api.dependencies import get_model_gateway_health_check, get_retrieval_health_check
from aico.platform.config import DEFAULT_CONFIG_PATH, load_gateway_config
from aico.platform.errors import GatewayConfigurationError
from aico.retrieval.search import load_chunks

DEFAULT_INDEX_DIR = pathlib.Path("data/index")


class DependencyStatus(str, Enum):
    HEALTHY = "healthy"
    UNAVAILABLE = "unavailable"


class DependencyCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: DependencyStatus
    detail: str


# A dependency check is any zero-arg callable returning a result - the
# seam Task 10 requires tests be able to replace (see dependencies.py's
# get_retrieval_health_check / get_model_gateway_health_check).
DependencyCheck = Callable[[], DependencyCheckResult]


def check_retrieval_health(index_dir: pathlib.Path = DEFAULT_INDEX_DIR) -> DependencyCheckResult:
    try:
        chunks = load_chunks(index_dir)
    except FileNotFoundError:
        return DependencyCheckResult(status=DependencyStatus.UNAVAILABLE, detail="retrieval index not found")
    except Exception as exc:  # noqa: BLE001 - normalized into a safe, generic detail below
        return DependencyCheckResult(
            status=DependencyStatus.UNAVAILABLE,
            detail=f"retrieval index unavailable ({exc.__class__.__name__})",
        )
    if not chunks:
        return DependencyCheckResult(status=DependencyStatus.UNAVAILABLE, detail="retrieval index is empty")
    return DependencyCheckResult(status=DependencyStatus.HEALTHY, detail=f"{len(chunks)} chunk(s) indexed")


def check_model_gateway_health(config_path: pathlib.Path = DEFAULT_CONFIG_PATH) -> DependencyCheckResult:
    try:
        config = load_gateway_config(config_path)
        _ = config.endpoint  # validates the endpoint env var is set; never returned/logged
    except GatewayConfigurationError:
        return DependencyCheckResult(
            status=DependencyStatus.UNAVAILABLE, detail="gateway configuration missing or invalid"
        )
    except Exception as exc:  # noqa: BLE001 - normalized into a safe, generic detail below
        return DependencyCheckResult(
            status=DependencyStatus.UNAVAILABLE,
            detail=f"gateway unavailable ({exc.__class__.__name__})",
        )
    return DependencyCheckResult(status=DependencyStatus.HEALTHY, detail="gateway configuration valid")


# ── Response contracts ───────────────────────────────────────────────────


class LivenessStatus(str, Enum):
    ALIVE = "alive"


class LivenessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: LivenessStatus = LivenessStatus.ALIVE


class DependencyName(str, Enum):
    RETRIEVAL = "retrieval"
    MODEL_GATEWAY = "model_gateway"


class DependencyReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: DependencyName
    status: DependencyStatus
    detail: str


class DependencyHealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dependencies: list[DependencyReport]


class ReadinessStatus(str, Enum):
    READY = "ready"
    NOT_READY = "not_ready"


READINESS_POLICY = (
    "ready only when every monitored dependency (retrieval, model_gateway) reports healthy; "
    "otherwise not_ready (503) so this instance is removed from traffic until dependencies recover"
)


class ReadinessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ReadinessStatus
    dependencies: list[DependencyReport]
    policy: str = READINESS_POLICY


# ── Routes ───────────────────────────────────────────────────────────────

router = APIRouter(tags=["health"])


def _current_dependency_reports(retrieval: DependencyCheckResult, gateway: DependencyCheckResult) -> list[DependencyReport]:
    return [
        DependencyReport(name=DependencyName.RETRIEVAL, status=retrieval.status, detail=retrieval.detail),
        DependencyReport(name=DependencyName.MODEL_GATEWAY, status=gateway.status, detail=gateway.detail),
    ]


@router.get("/health/live", response_model=LivenessResponse, summary="Liveness probe")
def liveness() -> LivenessResponse:
    # Deliberately calls no dependency check - see module docstring.
    return LivenessResponse()


@router.get("/health/dependencies", response_model=DependencyHealthResponse, summary="Per-dependency health detail")
def dependency_health(
    retrieval_check: DependencyCheck = Depends(get_retrieval_health_check),
    gateway_check: DependencyCheck = Depends(get_model_gateway_health_check),
) -> DependencyHealthResponse:
    retrieval = retrieval_check()
    gateway = gateway_check()
    return DependencyHealthResponse(dependencies=_current_dependency_reports(retrieval, gateway))


@router.get("/health/ready", response_model=ReadinessResponse, summary="Readiness probe")
def readiness(
    response: Response,
    retrieval_check: DependencyCheck = Depends(get_retrieval_health_check),
    gateway_check: DependencyCheck = Depends(get_model_gateway_health_check),
) -> ReadinessResponse:
    retrieval = retrieval_check()
    gateway = gateway_check()
    reports = _current_dependency_reports(retrieval, gateway)

    all_healthy = all(report.status is DependencyStatus.HEALTHY for report in reports)
    response.status_code = 200 if all_healthy else 503

    return ReadinessResponse(
        status=ReadinessStatus.READY if all_healthy else ReadinessStatus.NOT_READY,
        dependencies=reports,
    )
