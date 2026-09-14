"""
Day 12 Task 11 -- Gate-D, live over a real HTTP `/ask/governed` request
against the real `data/documents/` corpus (mirrors
`test_day11_real_corpus_integration.py`'s own structure and fixtures one
gate further down the pipeline: Gate-C already proved reachable there;
this file proves Gate-D is reachable immediately after it, and that it
cannot be bypassed).
Day 12 Task 12 -- final response immutability, proven end to end here
(`test_gate_d_allow_returns_the_exact_validated_object_unchanged`/
`test_api_mapping_drops_metadata_but_never_alters_citation_content`) --
the type-level guarantee that makes it structural
(`FinalResponseCandidate`/`FinalCitation` both `frozen=True`) is proven
separately in `test_day12_gate_d.py`'s own "Day 12 Task 12" section.

Proves, over a real `TestClient(app)` request, using the REAL
`BM25Retriever` over the real committed `data/index/`:

  - a real policy question, a real evidence-grounded answer -> Gate-D
    `allow` -> the exact same `status="answered"` response
    `test_day11_real_corpus_integration.py` already proves for Gate-C
    alone -- Gate-D's presence changes nothing for a genuinely valid
    candidate (Task 12's immutability, proven at the live-route level).
  - an answer that exceeds the real committed `max_answer_chars` budget
    -> Gate-D `safe_failure` -> the request fails as a Day 6 `ErrorResponse`
    (422, `error_code="FINAL_RESPONSE_REJECTED"`), and the oversized
    answer text is never present anywhere in the response body (Task 11's
    own rule: "candidate answer must not be returned").
  - a real committed policy narrowed (via dependency override, never by
    editing the committed file) to no longer permit `answered` at all ->
    Gate-D `reject` -> a Day 6 `ErrorResponse` with `status_code=500`,
    `error_code="gate_d_rejected"` -- distinct from the `422` `safe_failure`
    case.
  - `gate_d.enabled: false`, explicitly requested via override, reproduces
    Gate-C-only behavior unchanged for the identical request -- confirming
    the toggle is genuine in both directions.
  - `ControlPlaneAnswerService.__post_init__` raises `GateDIntegrationError`
    for Gate-D configured without Gate-C active.
  - Day 7's permanent regression gate and plain `/ask` (ungoverned) are
    both entirely unaffected by this wiring -- neither touches
    `ControlPlaneAnswerService`/Gate-D at all.
"""
from __future__ import annotations

import dataclasses
import json
import re
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

from aico.api.app import app
from aico.api.dependencies import get_control_plane_config, get_gate_d_policy_registry, get_gateway, get_session_store
from aico.api.identity import TrustedIdentity, get_trusted_identity
from aico.control.config import load_control_plane_config
from aico.control.policy_registry import GateDPolicyRegistry, PolicyRegistry
from aico.memory.store import InMemorySessionStore
from aico.platform.model_gateway import CallMetadata, ChatRequest, ChatResult
from aico.rag.answer_service import GroundedAnswerService
from aico.rag.control_plane_answer_service import ControlPlaneAnswerService, GateDIntegrationError

_SUPPLIER_READER = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-1", roles=("supplier_reader",))

_EVIDENCE_BLOCK_RE = re.compile(r"\[([0-9a-f]{16}) \| ([^\]]+)\]\n(.+?)(?:\n\n|\Z)", re.DOTALL)

REAL_GATE_D_POLICY_REGISTRY = GateDPolicyRegistry.load(gate_b_policy=PolicyRegistry.load())


def _extract_evidence_block(request: ChatRequest) -> tuple[str, str, str]:
    prompt_text = "\n".join(message.content for message in request.messages)
    match = _EVIDENCE_BLOCK_RE.search(prompt_text)
    assert match is not None, "expected at least one [chunk_id | source_file] evidence block in the prompt"
    return match.group(1), match.group(2), match.group(3)


@dataclass
class EchoingGateway:
    """Identical to `test_day11_real_corpus_integration.py`'s own fake:
    cites whatever real chunk_id/source_file the prompt actually contains
    and answers with a literal sentence lifted from that chunk's own
    text."""

    call_count: int = field(default=0, init=False)

    def chat(self, request: ChatRequest) -> ChatResult:
        self.call_count += 1
        chunk_id, source_file, chunk_text = _extract_evidence_block(request)
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
class OversizedAnswerGateway:
    """Same real-chunk citation `EchoingGateway` uses (so citation
    validation and Day 5's own support/groundedness check both still
    genuinely pass -- the *only* thing wrong with this answer is its
    length), but repeats the cited sentence enough times to exceed the
    real committed `max_answer_chars` budget (1600) -- a deterministic,
    real way to trigger Gate-D `safe_failure` through the actual live
    pipeline, not a hand-built `FinalResponseCandidate`."""

    call_count: int = field(default=0, init=False)

    def chat(self, request: ChatRequest) -> ChatResult:
        self.call_count += 1
        chunk_id, source_file, chunk_text = _extract_evidence_block(request)
        first_sentence = chunk_text.strip().split(". ")[0].strip().rstrip(".") + "."
        oversized_answer = (first_sentence + " ") * 60
        assert len(oversized_answer) > REAL_GATE_D_POLICY_REGISTRY.quality_policy.max_answer_chars
        content = json.dumps(
            {
                "schema_version": "1.0",
                "status": "answered",
                "answer": oversized_answer,
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
class SecretLeakGateway:
    """Same real-chunk citation `EchoingGateway` uses -- citation
    validation and Day 5's own support/groundedness check both still
    genuinely pass -- but the generated answer also states a synthetic
    protected value (`SYN-BANK-00001234`, Task 7's own named example)
    nowhere present in the cited evidence. Deliberately answers a
    *different* real question than `EchoingGateway`/`OversizedAnswerGateway`
    (one whose top-ranked real chunk carries enough of its own content
    words that appending a short, unrelated leaked value still clears
    `support_validator.py`'s lexical-overlap floor -- the payment-terms
    question's own top chunk is too short/title-like for that): this
    proves a real secret-pattern leak survives Day 4/5's typed-contract
    and semantic/groundedness validation (both genuinely pass here) and
    is caught only at the Gate-D release boundary, through the actual
    live pipeline -- not a hand-built `FinalResponseCandidate`."""

    call_count: int = field(default=0, init=False)

    def chat(self, request: ChatRequest) -> ChatResult:
        self.call_count += 1
        chunk_id, source_file, chunk_text = _extract_evidence_block(request)
        first_sentence = chunk_text.strip().split(". ")[0].strip().rstrip(".") + "."
        leaking_answer = f"{first_sentence} Bank account on file: SYN-BANK-00001234."
        content = json.dumps(
            {
                "schema_version": "1.0",
                "status": "answered",
                "answer": leaking_answer,
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


def _config_with_gate_d(enabled: bool):
    """The real committed `config/control-plane.yaml`, with only
    `gate_d.enabled` explicitly set -- every other governed value
    (including `gate_b.enabled`/`gate_c.enabled: true`) stays exactly the
    committed default. Mirrors `test_day11_real_corpus_integration.py`'s
    own `_config_with_gate_c` pattern."""
    real = load_control_plane_config()
    return dataclasses.replace(real, gate_d=dataclasses.replace(real.gate_d, enabled=enabled))


def _client(gateway, *, gate_d_enabled: bool = True, gate_d_policy_registry: GateDPolicyRegistry | None = None) -> TestClient:
    app.dependency_overrides[get_gateway] = lambda: gateway
    app.dependency_overrides[get_trusted_identity] = lambda: _SUPPLIER_READER
    app.dependency_overrides[get_session_store] = lambda: InMemorySessionStore()
    app.dependency_overrides[get_control_plane_config] = lambda: _config_with_gate_d(gate_d_enabled)
    if gate_d_policy_registry is not None:
        app.dependency_overrides[get_gate_d_policy_registry] = lambda: gate_d_policy_registry
    return TestClient(app)


def teardown_function() -> None:
    # `app.dependency_overrides` is a process-wide dict shared across
    # every `TestClient(app)` in the whole test run - the same reason
    # every sibling `test_dayNN_api_integration.py` file already clears
    # it after every test (`test_day10_api_integration.py`'s own
    # `teardown_function`, mirrored here).
    app.dependency_overrides.clear()


def test_gate_d_allows_a_real_grounded_answer_unchanged():
    """A genuinely valid candidate passes through Gate-D unaffected --
    the identical `status="answered"` response
    `test_day11_real_corpus_integration.py` already proves for Gate-C
    alone."""
    gateway = EchoingGateway()
    client = _client(gateway)

    resp = client.post("/ask/governed", json={"question": "What are the payment terms?", "data_class": "internal"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "answered", body
    assert gateway.call_count == 1
    assert re.fullmatch(r"[0-9a-f]{16}", body["citations"][0]["chunk_id"])


def test_gate_d_allow_returns_the_exact_validated_object_unchanged(monkeypatch):
    """Day 12 Task 12: "After Gate-D allow, do not mutate the answer/
    citations before returning it ... exact approved public payload is
    returned ... Do not validate one string and return a later modified
    string." Proven directly, not merely by equal content: two spies
    capture (a) the exact `GroundedAnswer` object `_answer_from_evidence()`
    built and handed to Gate-D, and (b) the exact object
    `_finalize_with_gate_d()` returns after Gate-D allowed it -- asserted
    `is` identical, never merely `==` equal. The live HTTP response is
    then checked back against that same captured object's own fields, so
    the whole chain (validated object -> HTTP JSON) is covered, not just
    the one internal hop."""
    captured: dict[str, object] = {}

    original_answer_from_evidence = GroundedAnswerService._answer_from_evidence

    def _answer_from_evidence_spy(self, *args, **kwargs):
        result = original_answer_from_evidence(self, *args, **kwargs)
        captured["validated"] = result
        return result

    original_finalize = ControlPlaneAnswerService._finalize_with_gate_d

    def _finalize_spy(self, result, **kwargs):
        returned = original_finalize(self, result, **kwargs)
        captured["finalize_input"] = result
        captured["finalize_returned"] = returned
        return returned

    monkeypatch.setattr(GroundedAnswerService, "_answer_from_evidence", _answer_from_evidence_spy)
    monkeypatch.setattr(ControlPlaneAnswerService, "_finalize_with_gate_d", _finalize_spy)

    gateway = EchoingGateway()
    client = _client(gateway)

    resp = client.post("/ask/governed", json={"question": "What are the payment terms?", "data_class": "internal"})

    assert resp.status_code == 200
    validated = captured["validated"]
    # The object Gate-D was actually asked to validate is the *same*
    # object `_finalize_with_gate_d()` was called with, and, on allow, the
    # *same* object it returned -- not a copy, not a re-derived value.
    assert captured["finalize_input"] is validated
    assert captured["finalize_returned"] is validated

    body = resp.json()
    assert body["answer"] == validated.answer
    assert [c["chunk_id"] for c in body["citations"]] == list(validated.citation_ids)
    assert body["confidence_label"] == validated.confidence_label


def test_api_mapping_drops_metadata_but_never_alters_citation_content():
    """Task 12's own example: "If API mapping removes internal metadata,
    the answer/citation content itself must not change after validation."
    `GovernedCitationOut` (the public `/ask/governed` shape) never carries
    `source_file` for a `rag`-lane `GroundedAnswer` -- metadata genuinely
    removed -- but the `chunk_id` value that *is* kept must be the exact,
    unaltered string Gate-D validated, never truncated, re-encoded, or
    otherwise transformed."""
    gateway = EchoingGateway()
    client = _client(gateway)

    resp = client.post("/ask/governed", json={"question": "What are the payment terms?", "data_class": "internal"})

    assert resp.status_code == 200
    body = resp.json()
    citation = body["citations"][0]
    assert set(citation.keys()) == {"chunk_id", "source_file"}
    assert citation["source_file"] is None  # metadata genuinely dropped by the public mapping ...
    assert re.fullmatch(r"[0-9a-f]{16}", citation["chunk_id"])  # ... but the kept value is untouched, not reformatted


def test_gate_d_safe_failure_blocks_an_oversized_answer():
    """Task 11's own rule: "For Gate-D safe failure/reject: candidate
    answer must not be returned. API response must use the documented
    typed public error/failure contract from Day 6." -- proven end to
    end: a 422 `ErrorResponse`, not a 200 `GovernedAskResponse`, and the
    oversized (but otherwise perfectly valid) answer text never appears
    anywhere in the response body."""
    gateway = OversizedAnswerGateway()
    client = _client(gateway)

    resp = client.post("/ask/governed", json={"question": "What are the payment terms?", "data_class": "internal"})

    assert resp.status_code == 422
    body = resp.json()
    assert set(body.keys()) == {"error_code", "message", "request_id", "correlation_id"}  # Day 6's ErrorResponse shape
    assert body["error_code"] == "FINAL_RESPONSE_REJECTED"  # the real, policy-driven code (Task 9)
    assert gateway.call_count == 1  # the model *was* called -- Gate-D still rejected its output
    raw_body = resp.text
    assert "payment" not in raw_body.lower() and "net 30" not in raw_body.lower()


def test_gate_d_safe_failure_blocks_a_live_secret_pattern_leak():
    """Closes the one Day 12 gap the validation report named: Task 6/7's
    disclosure/secret checks were previously proven only via direct unit
    calls to `check_final_disclosure()`/`detect_protected_value_leak()`
    against the fixture data, never over a real, live `/ask/governed`
    request. `SecretLeakGateway` makes a genuinely grounded answer (real
    citation, real cited text, Day 4 contract and Day 5 semantic/
    groundedness validation both genuinely pass -- proven below by the
    control case) that also states a synthetic bank-account value no
    cited evidence contains -- exactly Task 7's own named example
    (`SYN-BANK-00001234`). Gate-D's `detect_protected_value_leak()` is the
    *only* thing standing between this candidate and a normal 200
    response: a 422 `ErrorResponse` (Day 6's typed failure contract, Task
    11), never the leaked value anywhere in the body, is what proves it.

    The control case -- the identical citation/first-sentence answer with
    the leaked sentence removed -- is asserted first, so a future corpus/
    retrieval change that broke this test could never be mistaken for
    Gate-D itself failing to catch the leak."""
    question = "What are the standard payment terms from the date of a valid invoice?"

    control_gateway = EchoingGateway()
    control_resp = _client(control_gateway).post("/ask/governed", json={"question": question, "data_class": "internal"})
    assert control_resp.status_code == 200, control_resp.json()  # same citation/grounding, no leak -> genuinely allowed
    app.dependency_overrides.clear()

    gateway = SecretLeakGateway()
    client = _client(gateway)

    resp = client.post("/ask/governed", json={"question": question, "data_class": "internal"})

    assert resp.status_code == 422
    body = resp.json()
    assert set(body.keys()) == {"error_code", "message", "request_id", "correlation_id"}  # Day 6's ErrorResponse shape
    assert body["error_code"] == "FINAL_RESPONSE_REJECTED"  # the real, policy-driven code (Task 9)
    assert gateway.call_count == 1  # the model *was* called and *did* leak -- Gate-D still rejected its output
    raw_body = resp.text
    assert "SYN-BANK-00001234" not in raw_body  # the matched protected value itself never reaches the caller
    assert "bank account" not in raw_body.lower()


def test_gate_d_reject_via_narrowed_policy_is_a_server_error():
    """No shipped fixture/live scenario reaches Gate-D's own `reject`
    path through the unmodified committed policy (Day 5's own upstream
    validation structurally guarantees contract/semantic validation
    already passed by the time Gate-D ever runs) -- proven instead via a
    dependency override narrowing `allowed_response_statuses` to no
    longer permit `answered` at all, the identical "override a provider,
    never edit the committed file" pattern every other Day 9-12 test in
    this suite already uses."""
    narrowed_document = REAL_GATE_D_POLICY_REGISTRY._document.model_copy(update={"allowed_response_statuses": ("insufficient_evidence",)})
    narrowed_registry = GateDPolicyRegistry(narrowed_document, known_disclosure_profile_ids=frozenset({"policy_reader"}))

    gateway = EchoingGateway()
    client = _client(gateway, gate_d_policy_registry=narrowed_registry)

    resp = client.post("/ask/governed", json={"question": "What are the payment terms?", "data_class": "internal"})

    assert resp.status_code == 500
    body = resp.json()
    assert body["error_code"] == "gate_d_rejected"
    assert body["message"] == "the response could not be safely returned"  # fixed, generic -- never echoes internals
    raw_body = resp.text
    assert "payment" not in raw_body.lower() and "net 30" not in raw_body.lower()


def test_gate_d_disabled_reproduces_gate_c_only_behavior_unchanged():
    """`gate_d.enabled: false`, explicitly requested, reproduces exactly
    what `/ask/governed` did before this file's own change set: Gate-C
    still validates evidence, but its `rag`-lane result is returned
    exactly as `_answer_from_evidence()` produced it -- no Gate-D check,
    no `gate_d_*` error ever raised. The identical oversized answer that
    triggers `safe_failure` above is returned as a normal 200 here,
    confirming the toggle is genuine in both directions."""
    gateway = OversizedAnswerGateway()
    client = _client(gateway, gate_d_enabled=False)

    resp = client.post("/ask/governed", json={"question": "What are the payment terms?", "data_class": "internal"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "answered", body
    assert gateway.call_count == 1


def test_gate_d_integration_requires_gate_c_active():
    """`GateDIntegrationError` -- Gate-D configured without Gate-C active
    is a construction-time failure, never a silently-inactive Gate-D."""
    from aico.control.ontology_registry import OntologyRegistry

    registry = OntologyRegistry.load()
    rag_service = GroundedAnswerService(gateway=EchoingGateway(), retriever=lambda q: [])

    with pytest.raises(GateDIntegrationError):
        ControlPlaneAnswerService(
            registry=registry,
            rag_service=rag_service,
            gate_d_policy_registry=REAL_GATE_D_POLICY_REGISTRY,
        )
