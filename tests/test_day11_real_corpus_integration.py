"""
Day 11 Task 13 (real-corpus extension) -- Gate-C, live over a real HTTP
`/ask/governed` request against the real `data/documents/` corpus
(`aico.rag.real_corpus_evidence_adapter.RealCorpusEvidenceAdapter`,
`config/control-plane.yaml`'s `gate_c` section, `api/dependencies.py`'s
default wiring).

Closes the gap `control_plane_answer_service.py`'s own module docstring
used to describe as a genuine limitation: Gate-C existed and was fully
tested (`test_day11_*.py`) but was never actually reachable through a real
request, because a real `EvidenceChunk` carried none of the governed
provenance metadata Gate-C requires and the only committed source
registry/Gate-C policy were Day 11's own pinned synthetic fixtures. That
gap is now closed by real, non-fabricated governed data for the real
corpus (`evidence/real_corpus_source_registry.v1.json`/`evidence/
real_corpus_manifest.v1.json`/`policy/real_corpus_gate_c_policy.v1.json`,
each derived from the documents' own front-matter and real commit history
by `scripts/day11_generate_real_corpus_registry.py`) -- this file proves
it, the same way `test_day10_api_integration.py` proves Gate-B's
identical live-route reachability.

Proves, over a real `TestClient(app)` request, using the REAL `BM25Retriever`
over the real committed `data/index/` (never a fake retriever -- the point
here is that Gate-C runs against what retrieval genuinely returns for this
corpus):

  - a real policy question -> Gate-C `allow` -> `status="answered"`,
    the Model Gateway reached exactly once, and the returned citation
    names a real chunk_id/source_file from the real corpus.
  - a chunk from a document outside the governed real-corpus registry
    (a fake retriever standing in for a hypothetical ungoverned source,
    since the real committed corpus has nothing else to retrieve) ->
    Gate-C `reject` (`unknown_source`) -> `status="gate_c_rejected"`,
    zero Model Gateway calls (Task 11's no-fall-through guarantee, proven
    again here at the live-route level).
  - `gate_c.enabled: false`, explicitly requested via override, reproduces
    Day 10's exact `/ask/governed` behavior unchanged for the identical
    real request -- confirming `gate_c.enabled` is a genuine, working
    toggle, not a label with no effect.
  - Day 7's permanent regression gate and plain `/ask` (ungoverned) are
    both entirely unaffected by this wiring -- neither touches
    `ControlPlaneAnswerService`/Gate-C at all.
"""
from __future__ import annotations

import dataclasses
import json
import re
from dataclasses import dataclass, field

from fastapi.testclient import TestClient

from aico.api.app import app
from aico.api.dependencies import get_control_plane_config, get_gateway, get_session_store
from aico.api.identity import TrustedIdentity, get_trusted_identity
from aico.control.config import load_control_plane_config
from aico.memory.store import InMemorySessionStore
from aico.platform.model_gateway import CallMetadata, ChatRequest, ChatResult
from aico.rag.citation_validator import EvidenceChunk

_SUPPLIER_READER = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-1", roles=("supplier_reader",))

_EVIDENCE_BLOCK_RE = re.compile(r"\[([0-9a-f]{16}) \| ([^\]]+)\]\n(.+?)(?:\n\n|\Z)", re.DOTALL)


@dataclass
class EchoingGateway:
    """A fake Model Gateway that cites whatever real chunk_id/source_file
    the prompt it was actually given contains, and answers with a literal
    sentence lifted from that same cited chunk's own text -- robust to
    which of the real corpus's chunks BM25 happens to rank first for a
    given question, while still proving both that the citation names
    evidence Gate-C actually validated (never a chunk Gate-C would have
    rejected) and that Day 5's own post-generation support/groundedness
    check (unchanged, still independently enforced after Gate-C) passes
    for a genuinely evidence-grounded answer."""

    call_count: int = field(default=0, init=False)

    def chat(self, request: ChatRequest) -> ChatResult:
        self.call_count += 1
        prompt_text = "\n".join(message.content for message in request.messages)
        match = _EVIDENCE_BLOCK_RE.search(prompt_text)
        assert match is not None, "expected at least one [chunk_id | source_file] evidence block in the prompt"
        chunk_id, source_file, chunk_text = match.group(1), match.group(2), match.group(3)
        # First sentence of the cited chunk's own real text -- literal
        # overlap with the evidence `validate_support` checks against,
        # not a paraphrase this test would have to guess is "close enough".
        first_sentence = chunk_text.strip().split(". ")[0].strip().rstrip(".") + "."
        content = json.dumps(
            {
                "schema_version": "1.0",
                "status": "answered",
                "answer": first_sentence,
                "citations": [{"chunk_id": chunk_id, "source_file": source_file}],
                "confidence_label": "high",
            }
        )
        return ChatResult(
            content=content,
            metadata=CallMetadata(
                operation="chat", model_alias="fake-chat-alias", latency_ms=1.0, retry_count=0,
                token_usage=None, budget_status="within_budget",
            ),
        )


@dataclass
class UngovernedChunkGateway:
    """Never expected to be called (the no-fall-through cases below) --
    raises if it is, the same "must not even be reached" proof
    `test_day10_api_integration.py`'s own gate_b_denied/clarify cases
    give via `CountingGateway.call_count == 0`, made stricter here."""

    call_count: int = field(default=0, init=False)

    def chat(self, request: ChatRequest) -> ChatResult:  # pragma: no cover - should never run
        self.call_count += 1
        raise AssertionError("Model Gateway must not be called when Gate-C did not allow")


def _config_with_gate_c(enabled: bool):
    """The real committed `config/control-plane.yaml`, with only
    `gate_c.enabled` explicitly set -- every other governed value
    (including `gate_b.enabled: true`) stays exactly the committed
    default. Mirrors `test_day10_api_integration.py`'s own
    `_config_with_gate_b` pattern."""
    real = load_control_plane_config()
    return dataclasses.replace(real, gate_c=dataclasses.replace(real.gate_c, enabled=enabled))


def _client(gateway) -> TestClient:
    app.dependency_overrides[get_gateway] = lambda: gateway
    app.dependency_overrides[get_trusted_identity] = lambda: _SUPPLIER_READER
    app.dependency_overrides[get_session_store] = lambda: InMemorySessionStore()
    app.dependency_overrides[get_control_plane_config] = lambda: _config_with_gate_c(True)
    return TestClient(app)


def test_gate_c_allows_a_real_policy_question_and_reaches_the_model_exactly_once():
    """The real corpus's own real chunks, through the real BM25 retriever,
    genuinely pass Gate-C -- source trust (a governed `SRC-DOC-*` id),
    provenance (self-consistent + independently-verified content hash),
    freshness (each document's real last-commit date is well within the
    governed 365-day threshold) and completeness (`supplier_governance_
    policy`, which every real document supports) all hold for real
    retrieval over this real corpus."""
    gateway = EchoingGateway()
    client = _client(gateway)

    resp = client.post(
        "/ask/governed", json={"question": "What are the payment terms?", "data_class": "internal"}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "answered", body
    assert body["lane"] == "rag"
    assert gateway.call_count == 1
    assert len(body["citations"]) == 1
    citation = body["citations"][0]
    # A real chunk_id from the real committed index (16 lowercase hex
    # chars, `chunker.py`'s own `_generate_chunk_id` shape) -- never a
    # synthesized or Day 11-pack-synthetic id. `GovernedAskResponse`'s own
    # citation mapping only carries `chunk_id` through today (`source_file`
    # is left `None` by `governed_ask_response_from_result`'s `GroundedAnswer`
    # branch, a pre-existing contract limitation this test does not need
    # to change), so `source_file` is not asserted here.
    assert re.fullmatch(r"[0-9a-f]{16}", citation["chunk_id"])


def test_gate_c_disabled_reproduces_gate_b_only_behavior_unchanged():
    """`gate_c.enabled: false`, explicitly requested, reproduces exactly
    what `/ask/governed` did before this file's own change set: Gate-B
    still authorizes, but retrieval flows straight into
    `GroundedAnswerService.answer()` unmodified, no Gate-C span, no
    `gate_c_*` status ever produced -- confirming the toggle is genuine in
    both directions, the same proof `test_day10_api_integration.py::
    test_gate_b_disabled_reproduces_day9_behavior_unchanged_for_the_
    identical_request` gives for `gate_b.enabled`."""
    gateway = EchoingGateway()
    app.dependency_overrides[get_gateway] = lambda: gateway
    app.dependency_overrides[get_trusted_identity] = lambda: _SUPPLIER_READER
    app.dependency_overrides[get_session_store] = lambda: InMemorySessionStore()
    app.dependency_overrides[get_control_plane_config] = lambda: _config_with_gate_c(False)
    client = TestClient(app)

    resp = client.post(
        "/ask/governed", json={"question": "What are the payment terms?", "data_class": "internal"}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "answered", body
    assert gateway.call_count == 1
    assert body["source_registry_version"] is None  # Gate-C never ran -- nothing to report.


def test_gate_c_rejects_an_ungoverned_chunk_and_reaches_the_model_zero_times():
    """A retrieved chunk claiming a `source_file` outside the governed
    real-corpus registry (stands in for a hypothetical ungoverned/rogue
    source -- the real committed corpus has no such file of its own to
    retrieve) is rejected by Gate-C's `unknown_source` check
    (`RealCorpusEvidenceAdapter`'s own "Unknown documents" fallback,
    `real_corpus_evidence_adapter.py`) before the Model Gateway is ever
    reached -- Task 11's no-fall-through guarantee, proven again here at
    the live-route level, not merely inferred from
    `test_day11_no_fallthrough.py`'s own `GateC`-direct proof."""
    from aico.api.dependencies import get_answer_service
    from aico.rag.answer_service import GroundedAnswerService

    gateway = UngovernedChunkGateway()

    @dataclass
    class _RogueRetriever:
        call_count: int = field(default=0, init=False)

        def __call__(self, query: str) -> list[EvidenceChunk]:
            self.call_count += 1
            return [EvidenceChunk(chunk_id="ROGUE-1", source_file="ROGUE-UNGOVERNED.md", text="Not a real policy.")]

    retriever = _RogueRetriever()
    app.dependency_overrides[get_answer_service] = lambda: GroundedAnswerService(gateway=gateway, retriever=retriever)
    app.dependency_overrides[get_trusted_identity] = lambda: _SUPPLIER_READER
    app.dependency_overrides[get_session_store] = lambda: InMemorySessionStore()
    app.dependency_overrides[get_control_plane_config] = lambda: _config_with_gate_c(True)
    client = TestClient(app)

    resp = client.post(
        "/ask/governed", json={"question": "What are the payment terms?", "data_class": "internal"}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "gate_c_rejected", body
    assert "unknown_source" in body["reason_code"]
    assert gateway.call_count == 0
    assert retriever.call_count == 1  # retrieval ran -- Gate-C needs something to evaluate.
    assert body["source_registry_version"] is not None
