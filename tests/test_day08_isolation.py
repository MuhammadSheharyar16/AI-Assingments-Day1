"""
Day 8 Task 3 — session isolation, proven through `MemorySessionService`
(`src/aico/memory/service.py`), not only against the raw `SessionStore`
(Task 2 already proves the store's own ownership-scoped-lookup guarantee
in `test_day08_session_lifecycle.py`). This file proves nothing sitting
between the Day 6 trusted identity and the store widens that boundary.

Every test runs against both `InMemorySessionStore` and
`SqliteSessionStore` (the `service` fixture below), so the isolation
guarantee is proven for both backends, not just the fake.

Case matrix is exactly the assignment brief's Task 3 list, matching
`data/day08_pack/fixtures/isolation_cases.json`:
    ISO-001  same tenant + same user + correct session   -> allow
    ISO-002  same tenant + different user                -> deny_without_disclosure
    ISO-003  different tenant + same-looking user         -> deny_without_disclosure
    ISO-004  different session under same owner           -> no_cross_session_memory
    ISO-005  guessed/nonexistent session id                -> not_found_without_foreign_session_disclosure

`TrustedIdentity` is used exactly as Day 6's `get_trusted_identity`
dependency would hand it to a route - constructed directly here (this
layer's tests are not about token verification, which
`test_day06_identity.py` already covers) but never through anything but
that real dataclass, per Task 3's "use the trusted identity dependency
created on Day 6."
"""
from __future__ import annotations

import inspect
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from aico.api.identity import TrustedIdentity
from aico.memory.errors import SessionNotFoundError
from aico.memory.models import SessionTurn, TurnRole
from aico.memory.service import MemorySessionService
from aico.memory.store import InMemorySessionStore, SessionStore, SqliteSessionStore

TENANT_A = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-1")
TENANT_A_OTHER_USER = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-2")
TENANT_B_SAME_LOOKING_USER = TrustedIdentity(tenant_id="TENANT-B", user_id="USER-1")  # ISO-003: "same-looking user"


class FakeClock:
    def __init__(self, start: datetime):
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


@pytest.fixture(params=["memory", "sqlite"])
def service_and_store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[tuple[MemorySessionService, SessionStore]]:
    clock = FakeClock(datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC))
    store: SessionStore
    if request.param == "memory":
        store = InMemorySessionStore(clock=clock)
    else:
        store = SqliteSessionStore(db_path=tmp_path / "sessions.db", clock=clock)
    yield MemorySessionService(store), store
    close = getattr(store, "close", None)
    if close is not None:
        close()


@pytest.fixture
def service(service_and_store: tuple[MemorySessionService, SessionStore]) -> MemorySessionService:
    return service_and_store[0]


class TestIsolationMatrix:
    """The five required cases, in the brief's own order."""

    def test_iso_001_same_tenant_same_user_correct_session_allowed(self, service: MemorySessionService) -> None:
        created = service.create_session(TENANT_A)

        loaded = service.load_session(TENANT_A, created.session_id)

        assert loaded.session_id == created.session_id
        assert loaded.tenant_id == TENANT_A.tenant_id
        assert loaded.user_id == TENANT_A.user_id

    def test_iso_002_same_tenant_different_user_denied(self, service: MemorySessionService) -> None:
        created = service.create_session(TENANT_A)

        with pytest.raises(SessionNotFoundError) as excinfo:
            service.load_session(TENANT_A_OTHER_USER, created.session_id)
        assert excinfo.value.reason == "not_found"  # deny_without_disclosure

    def test_iso_003_different_tenant_same_looking_user_denied(self, service: MemorySessionService) -> None:
        created = service.create_session(TENANT_A)

        with pytest.raises(SessionNotFoundError) as excinfo:
            service.load_session(TENANT_B_SAME_LOOKING_USER, created.session_id)
        assert excinfo.value.reason == "not_found"  # deny_without_disclosure

    def test_iso_004_different_session_under_same_owner_no_leakage(self, service: MemorySessionService) -> None:
        session_a = service.create_session(TENANT_A)
        session_b = service.create_session(TENANT_A)

        turn_a = SessionTurn(turn_id="TURN-A1", role=TurnRole.USER, timestamp=session_a.created_at, content="About Supplier Alpha")
        service.save_session(TENANT_A, session_a.model_copy(update={"recent_turns": [turn_a]}))

        # Loading the *other* session under the same identity must never
        # surface session_a's turn.
        reloaded_b = service.load_session(TENANT_A, session_b.session_id)
        assert reloaded_b.recent_turns == []
        assert reloaded_b.session_id != session_a.session_id

    def test_iso_005_guessed_nonexistent_session_id_denied(self, service: MemorySessionService) -> None:
        service.create_session(TENANT_A)  # a real session exists, just not this id

        with pytest.raises(SessionNotFoundError) as excinfo:
            service.load_session(TENANT_A, "SES-guessed-not-real")
        assert excinfo.value.reason == "not_found"  # not_found_without_foreign_session_disclosure


class TestFailClosedWithoutDisclosure:
    def test_denial_is_identical_for_every_cause(self, service: MemorySessionService) -> None:
        # "Fail closed without revealing whether another tenant's session
        # exists": wrong-user, wrong-tenant and nonexistent-id must be
        # indistinguishable from outside this service.
        created = service.create_session(TENANT_A)

        outcomes = []
        for identity, session_id in [
            (TENANT_A_OTHER_USER, created.session_id),
            (TENANT_B_SAME_LOOKING_USER, created.session_id),
            (TENANT_A, "SES-never-created"),
        ]:
            with pytest.raises(SessionNotFoundError) as excinfo:
                service.load_session(identity, session_id)
            outcomes.append((str(excinfo.value), excinfo.value.reason))

        assert len(set(outcomes)) == 1

    def test_clear_denial_reveals_nothing(self, service: MemorySessionService) -> None:
        created = service.create_session(TENANT_A)
        with pytest.raises(SessionNotFoundError):
            service.clear_session(TENANT_A_OTHER_USER, created.session_id)

    def test_delete_denial_reveals_nothing_and_session_survives(self, service: MemorySessionService) -> None:
        created = service.create_session(TENANT_A)

        with pytest.raises(SessionNotFoundError):
            service.delete_session(TENANT_B_SAME_LOOKING_USER, created.session_id)

        # The denied delete attempt must not have removed the real owner's session.
        assert service.load_session(TENANT_A, created.session_id).session_id == created.session_id

    def test_save_denial_when_session_object_ownership_does_not_match_identity(self, service: MemorySessionService) -> None:
        created = service.create_session(TENANT_A)
        # Simulates a session object whose ownership fields do not match
        # the caller's trusted identity - defense in depth, never trusting
        # the object's own tenant_id/user_id over the caller's identity.
        forged = created.model_copy(update={"tenant_id": TENANT_B_SAME_LOOKING_USER.tenant_id})

        with pytest.raises(SessionNotFoundError):
            service.save_session(TENANT_A, forged)


class TestNeverAcceptsUnverifiedOwnership:
    """Structural guard: no public method on MemorySessionService can even
    be called with a raw tenant_id/user_id - ownership can only ever come
    from a TrustedIdentity object. Prevents a future edit from
    accidentally reintroducing a tenant_id/user_id parameter that a
    request body could populate (Day 8 working rule)."""

    @pytest.mark.parametrize(
        "method_name",
        ["create_session", "load_session", "save_session", "clear_session", "delete_session", "expire_session"],
    )
    def test_no_public_method_accepts_raw_tenant_or_user_id(self, method_name: str) -> None:
        signature = inspect.signature(getattr(MemorySessionService, method_name))
        param_names = set(signature.parameters.keys())
        assert "tenant_id" not in param_names
        assert "user_id" not in param_names
        assert "identity" in param_names

    def test_every_public_method_requires_a_trusted_identity_first(self) -> None:
        for method_name in ["create_session", "load_session", "save_session", "clear_session", "delete_session", "expire_session"]:
            signature = inspect.signature(getattr(MemorySessionService, method_name))
            first_real_param = list(signature.parameters.values())[1]  # skip `self`
            assert first_real_param.name == "identity"
            assert first_real_param.annotation in ("TrustedIdentity", TrustedIdentity)


class TestExpireAndClearThroughService:
    def test_expire_then_load_is_denied_without_disclosing_expiry_to_a_stranger(self, service: MemorySessionService) -> None:
        created = service.create_session(TENANT_A)
        service.expire_session(TENANT_A, created.session_id)

        with pytest.raises(SessionNotFoundError) as owner_attempt:
            service.load_session(TENANT_A, created.session_id)
        assert owner_attempt.value.reason == "expired"  # the rightful owner learns it expired

        with pytest.raises(SessionNotFoundError) as stranger_attempt:
            service.load_session(TENANT_A_OTHER_USER, created.session_id)
        assert stranger_attempt.value.reason == "not_found"  # a stranger never learns it merely expired

    def test_clear_through_service_keeps_ownership_and_wipes_history(self, service: MemorySessionService) -> None:
        created = service.create_session(TENANT_A)
        turn = SessionTurn(turn_id="TURN-001", role=TurnRole.USER, timestamp=created.created_at, content="hi")
        service.save_session(TENANT_A, created.model_copy(update={"recent_turns": [turn]}))

        cleared = service.clear_session(TENANT_A, created.session_id)

        assert cleared.recent_turns == []
        assert cleared.tenant_id == TENANT_A.tenant_id
        assert cleared.user_id == TENANT_A.user_id
