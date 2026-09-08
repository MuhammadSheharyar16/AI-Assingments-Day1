"""
Day 8 Task 4 — shared test fixtures.

`POST /ask` (`api/app.py`) gained a required session-resolution
dependency (`get_memory_service`, itself wrapping `get_session_store`)
once Day 8 wired session participation into it. Every Day 6/7 test file
that exercises `/ask` through `TestClient(app)` predates that change and
only overrides `get_answer_service`/`get_trusted_identity` - without this
fixture, those tests would fall through to the real default
`get_session_store` (a `SqliteSessionStore` writing to `data/sessions/`
on disk), breaking the "no network/disk side effects in tests"
discipline every other dependency in this codebase already follows (real
vs. fake/in-memory: `ModelGateway`/`FakeGateway`, `BM25Retriever`/a fake
retriever function, and Day 8's own `SqliteSessionStore`/
`InMemorySessionStore`, `memory/store.py`).

This autouse fixture applies a fresh `InMemorySessionStore` override for
every test in the suite - session-participation-unaware Day 6/7 files
included - so each test gets an isolated, in-memory store with no shared
state between tests and no file left on disk. A test that wants to
exercise session behavior specifically (Day 8's own test files) overrides
`get_session_store`/`get_memory_service` again itself, inside the test
function body - that assignment simply runs after this fixture's and
wins, exactly like any other dependency override in this project (see
`api/dependencies.py`'s "replace one ingredient" pattern).
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest

from aico.api.app import app
from aico.api.dependencies import get_session_store
from aico.memory.store import InMemorySessionStore


@pytest.fixture(autouse=True)
def _isolated_session_store_for_ask() -> Iterator[None]:
    app.dependency_overrides[get_session_store] = lambda: InMemorySessionStore()
    yield
    app.dependency_overrides.pop(get_session_store, None)
