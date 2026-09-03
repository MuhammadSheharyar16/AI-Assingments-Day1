"""
Day 6 Task 6 — health endpoints.

Proves, against `tests/fixtures/day06/dependency_health_cases.json`:
- HLT-001: all dependencies healthy -> liveness healthy, dependency
  detail healthy, readiness ready (200).
- HLT-002 / HLT-003: one dependency unavailable -> liveness STILL
  healthy (the working rule under test), dependency detail reports the
  unavailable one separately from the healthy one, and readiness matches
  this service's documented policy (not_ready / 503 - health.py's
  `READINESS_POLICY`: ready only when every dependency is healthy).

And beyond the fixture pack:
- Liveness never depends on any dependency check at all (proven by
  overriding both checks to raise, and liveness still succeeding).
- Dependency health and readiness are separate endpoints - one attaches
  no HTTP-status meaning to a degraded dependency, the other does.
- Dependency injection (Task 10): both checks are replaced with
  deterministic fakes via `app.dependency_overrides` - no real
  `data/index` or `config/model-routing.yaml` read in most tests here.
"""
from __future__ import annotations

import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from aico.api.app import app
from aico.api.dependencies import get_model_gateway_health_check, get_retrieval_health_check
from aico.api.health import DependencyCheckResult, DependencyStatus, READINESS_POLICY

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "fixtures" / "day06"
HEALTH_CASES = {
    c["id"]: c for c in json.loads((FIXTURES_DIR / "dependency_health_cases.json").read_text(encoding="utf-8"))["cases"]
}

_HEALTHY = DependencyCheckResult(status=DependencyStatus.HEALTHY, detail="ok")
_UNAVAILABLE = DependencyCheckResult(status=DependencyStatus.UNAVAILABLE, detail="unavailable in test")


def teardown_function() -> None:
    app.dependency_overrides.clear()


def _client_with(retrieval: DependencyCheckResult, gateway: DependencyCheckResult) -> TestClient:
    app.dependency_overrides[get_retrieval_health_check] = lambda: (lambda: retrieval)
    app.dependency_overrides[get_model_gateway_health_check] = lambda: (lambda: gateway)
    return TestClient(app)


# ── Liveness never depends on a dependency check ─────────────────────────


def test_liveness_is_healthy_with_no_dependency_overrides():
    client = TestClient(app)
    resp = client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "alive"}


def test_liveness_does_not_fail_when_every_dependency_check_raises():
    def _raise():
        raise RuntimeError("dependency completely unreachable")

    app.dependency_overrides[get_retrieval_health_check] = lambda: _raise
    app.dependency_overrides[get_model_gateway_health_check] = lambda: _raise
    client = TestClient(app)

    resp = client.get("/health/live")

    assert resp.status_code == 200
    assert resp.json()["status"] == "alive"


# ── Fixture-driven: dependency health + readiness ────────────────────────


def _status_from_fixture(word: str) -> DependencyCheckResult:
    return _HEALTHY if word == "healthy" else _UNAVAILABLE


@pytest.mark.parametrize("case_id", ["HLT-001", "HLT-002", "HLT-003"])
def test_dependency_health_and_readiness_match_fixture(case_id):
    case = HEALTH_CASES[case_id]
    retrieval = _status_from_fixture(case["dependencies"]["retrieval"])
    gateway = _status_from_fixture(case["dependencies"]["model_gateway"])
    client = _client_with(retrieval, gateway)

    # Liveness: always healthy regardless of dependency state (HLT-*'s
    # expected_liveness is "healthy" in every case, including outages).
    live_resp = client.get("/health/live")
    assert live_resp.status_code == 200
    assert case["expected_liveness"] == "healthy"

    # Dependency health: reports each dependency separately.
    dep_resp = client.get("/health/dependencies")
    assert dep_resp.status_code == 200
    reports = {d["name"]: d["status"] for d in dep_resp.json()["dependencies"]}
    assert reports["retrieval"] == retrieval.status.value
    assert reports["model_gateway"] == gateway.status.value
    if case["expected_dependency_health"] == "healthy":
        assert retrieval.status is DependencyStatus.HEALTHY
        assert gateway.status is DependencyStatus.HEALTHY
    else:  # "degraded_or_unhealthy"
        assert DependencyStatus.UNAVAILABLE in (retrieval.status, gateway.status)

    # Readiness: must match this service's *documented* policy - ready
    # only when every dependency is healthy (health.py's READINESS_POLICY).
    ready_resp = client.get("/health/ready")
    all_healthy = retrieval.status is DependencyStatus.HEALTHY and gateway.status is DependencyStatus.HEALTHY
    if case["expected_readiness"] == "ready":
        assert all_healthy  # sanity: this fixture case really is the all-healthy one
    if all_healthy:
        assert ready_resp.status_code == 200
        assert ready_resp.json()["status"] == "ready"
    else:
        assert ready_resp.status_code == 503
        assert ready_resp.json()["status"] == "not_ready"
    assert ready_resp.json()["policy"] == READINESS_POLICY


# ── Endpoint separation ───────────────────────────────────────────────────


def test_dependency_health_endpoint_returns_200_even_when_a_dependency_is_down():
    """Unlike readiness, the dependency-health endpoint itself always
    succeeds (200) - it reports status, it does not gate traffic."""
    client = _client_with(_HEALTHY, _UNAVAILABLE)
    resp = client.get("/health/dependencies")
    assert resp.status_code == 200
    reports = {d["name"]: d["status"] for d in resp.json()["dependencies"]}
    assert reports["model_gateway"] == "unavailable"
    assert reports["retrieval"] == "healthy"


def test_dependency_detail_never_leaks_raw_exception_text():
    """A raising check is a caller bug outside this service's control in
    practice (the real checks never raise - they catch internally), but
    prove the endpoint doesn't surface a raw exception either way: it
    should 500 via the shared error envelope, never a stack trace.
    `raise_server_exceptions=False` is needed here because Starlette's
    `ServerErrorMiddleware` re-raises the original exception after
    sending the safe response a real client receives - see
    test_day06_errors.py's identical note."""

    def _raise():
        raise RuntimeError("connection failed to https://internal-foundry.example.com key=sk-secret-123")

    app.dependency_overrides[get_retrieval_health_check] = lambda: (lambda: _HEALTHY)
    app.dependency_overrides[get_model_gateway_health_check] = lambda: _raise
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get("/health/dependencies")
    assert resp.status_code == 500
    body = resp.json()
    assert "sk-secret-123" not in json.dumps(body)
    assert "internal-foundry.example.com" not in json.dumps(body)


# ── Real default checks (no dependency_overrides) ────────────────────────


def test_real_retrieval_health_check_runs_without_raising():
    from aico.api.health import check_retrieval_health

    result = check_retrieval_health()
    assert result.status in (DependencyStatus.HEALTHY, DependencyStatus.UNAVAILABLE)
    # The real repo's data/index is checked into the project - it should
    # actually be healthy in this environment.
    assert result.status is DependencyStatus.HEALTHY


def test_real_model_gateway_health_check_runs_without_raising_or_leaking():
    from aico.api.health import check_model_gateway_health

    result = check_model_gateway_health()
    assert result.status in (DependencyStatus.HEALTHY, DependencyStatus.UNAVAILABLE)
    # Whatever the outcome (depends on whether the endpoint env var is set
    # in this environment), the detail must never contain a URL/secret.
    assert "://" not in result.detail
