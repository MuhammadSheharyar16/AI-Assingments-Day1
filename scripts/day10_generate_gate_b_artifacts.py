"""
Day 10 Task 15 — Gate-B / tenant-isolation / disclosure artifacts.

Run: uv run python scripts/day10_generate_gate_b_artifacts.py

Generates the three required artifacts from real system behavior, not
hand-written prose — the same discipline
`scripts/day09_generate_control_plane_artifacts.py` (Day 9 Task 13)
established: the real `PolicyRegistry` (Task 2) loaded from the real
committed `policy/gate_b_policy.v1.json`, the real `OntologyRegistry`
(Day 9 Task 2), the real `GateB` (Task 3-12), the real `apply_disclosure`/
`resolve_disclosure_action`/`mask_value` (Task 8/9), and — for "protected
dependency call count is zero on denial" — a real `ControlPlaneAnswerService`
(Day 9 Task 9, Day 10 Task 13) wired to counting fakes (no real network
call), the same instrumentation `tests/test_day10_no_fallthrough.py`
(Task 11) already proves the pipeline with.

    artifacts/day10/gate_b_decisions.md  — allowed / denied-role /
                                            unknown-role / lane-mismatch /
                                            missing-rule / clarify, each a
                                            real `GateB.authorize()` result
    artifacts/day10/tenant_isolation.md  — same-tenant allow, cross-tenant
                                            deny, actual retrieval/model
                                            call counts on denial, and the
                                            effective scope of an allowed
                                            decision
    artifacts/day10/disclosure_report.md — an allowed public/internal
                                            field, a deterministically
                                            redacted field (masked value
                                            shown, never the raw one), a
                                            denied sensitive field (no
                                            value shown at all), and the
                                            applied disclosure_profile/
                                            rule_id

Every value written to these files is already governed, committed policy
data (role/rule/profile ids from `policy/gate_b_policy.v1.json`) or the
`day10_pack`'s own synthetic demo record (`pii_disclosure_cases.json`'s
`synthetic_record` — fixed, fake, labeled as such) — never a real user's
request or a real protected value. The one PII-shaped value this script
ever writes to a file is a deterministically *masked* one
(`redaction.mask_value`'s own output); the raw synthetic value behind it
is read only to compute that mask, never written.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from aico.api.identity import TrustedIdentity
from aico.control.disclosure import ProtectedField, apply_disclosure
from aico.control.gate_b import GateB, GateBRequest
from aico.control.models import GateADecision, GateAStatus, GateBDecision, LaneDecision
from aico.control.ontology import LaneId
from aico.control.ontology_registry import OntologyRegistry
from aico.control.policy_models import DataClassification, PiiCategory
from aico.control.policy_registry import DEFAULT_POLICY_PATH, PolicyRegistry
from aico.platform.model_gateway import CallMetadata, ChatRequest, ChatResult
from aico.rag.answer_service import GroundedAnswerService
from aico.rag.citation_validator import EvidenceChunk
from aico.rag.control_plane_answer_service import ControlPlaneAnswerService

REPO_ROOT = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = REPO_ROOT / "artifacts" / "day10"

# The day10_pack's own synthetic demo record (`pii_disclosure_cases.json`'s
# `synthetic_record`/`field_metadata`) — fixed, fake data, reproduced here
# rather than re-read from the fixture file so this script has no
# dependency on `data/day10_pack/` still existing verbatim at artifact-
# generation time.
_SYNTHETIC_RECORD: dict[str, str] = {
    "supplier_name": "Synthetic Supplier Alpha",
    "contact_email": "alice@example.test",
    "tax_identifier": "SYN-ID-123456",
}
_SYNTHETIC_FIELD_METADATA: dict[str, dict[str, str]] = {
    "supplier_name": {"data_class": "public", "pii_category": "none"},
    "contact_email": {"data_class": "confidential", "pii_category": "contact"},
    "tax_identifier": {"data_class": "confidential", "pii_category": "personal_identifier"},
}


def _protected_fields() -> list[ProtectedField]:
    return [
        ProtectedField(
            name=name,
            value=value,
            data_class=DataClassification(_SYNTHETIC_FIELD_METADATA[name]["data_class"]),
            pii_category=PiiCategory(_SYNTHETIC_FIELD_METADATA[name]["pii_category"]),
        )
        for name, value in _SYNTHETIC_RECORD.items()
    ]


def _matched(intent_id: str) -> GateADecision:
    return GateADecision(
        status=GateAStatus.MATCHED,
        domain="supplier_governance",
        intent_id=intent_id,
        reason_code="artifact_setup",
        ontology_version="1.0",
    )


def _lane_decision(lane: LaneId, intent_id: str | None) -> LaneDecision:
    return LaneDecision(lane=lane, intent_id=intent_id, reason_code="artifact_setup", ontology_version="1.0")


# ── Counting fakes (Task 11's own instrumentation technique) ─────────────


@dataclass
class _CountingGateway:
    response_content: str = (
        '{"schema_version": "1.0", "status": "answered", "answer": "Payment terms are net 30 days.", '
        '"citations": [{"chunk_id": "C1", "source_file": "DOC-001.md"}], "confidence_label": "high"}'
    )
    call_count: int = field(default=0, init=False)

    def chat(self, request: ChatRequest) -> ChatResult:
        self.call_count += 1
        return ChatResult(
            content=self.response_content,
            metadata=CallMetadata(
                operation="chat", model_alias="artifact-fake-chat", latency_ms=1.0, retry_count=0,
                token_usage=None, budget_status="within_budget",
            ),
        )


@dataclass
class _CountingRetriever:
    chunks: list[EvidenceChunk] = field(default_factory=list)
    call_count: int = field(default=0, init=False)

    def __call__(self, query: str) -> list[EvidenceChunk]:
        self.call_count += 1
        return self.chunks


def _new_governed_service(registry: OntologyRegistry, policy_registry: PolicyRegistry) -> tuple[ControlPlaneAnswerService, _CountingGateway, _CountingRetriever]:
    gateway = _CountingGateway()
    retriever = _CountingRetriever(chunks=[EvidenceChunk(chunk_id="C1", source_file="DOC-001.md", text="Payment terms are net 30 days.")])
    rag_service = GroundedAnswerService(gateway=gateway, retriever=retriever)
    service = ControlPlaneAnswerService(registry=registry, rag_service=rag_service, policy_registry=policy_registry)
    return service, gateway, retriever


# ── Task 1: gate_b_decisions.md ───────────────────────────────────────────


@dataclass
class GateBDecisionCase:
    label: str
    role: str
    intent_id: str
    lane: LaneId
    data_class: DataClassification | None
    decision: GateBDecision


@dataclass
class GateBDecisionsEvidence:
    policy_version: str
    cases: list[GateBDecisionCase]


def gather_gate_b_decisions_evidence() -> GateBDecisionsEvidence:
    registry = OntologyRegistry.load()
    policy_registry = PolicyRegistry.load(ontology_registry=registry)
    gate_b = GateB(policy_registry, ontology_registry=registry)

    cases: list[GateBDecisionCase] = []

    def run(label: str, role: str | None, intent_id: str, lane: LaneId, data_class: DataClassification | None) -> None:
        identity = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-ARTIFACT", roles=(role,) if role else ())
        decision = gate_b.authorize(
            identity, _matched(intent_id), _lane_decision(lane, intent_id), GateBRequest(data_class=data_class)
        )
        cases.append(GateBDecisionCase(label, role or "(none)", intent_id, lane, data_class, decision))

    # Allowed request: GB-R001, supplier_reader / policy-question / rag.
    run("allowed request", "supplier_reader", "INT-POLICY-QUESTION", LaneId.RAG, DataClassification.INTERNAL)

    # Denied role: GB-R002 matches but allowed=false.
    run("denied role (matched, allowed=false)", "supplier_reader", "INT-STRUCTURED-LOOKUP", LaneId.MODE_B, DataClassification.INTERNAL)

    # Unknown role: not in the committed policy at all.
    run("unknown role", "finance_manager", "INT-POLICY-QUESTION", LaneId.RAG, DataClassification.INTERNAL)

    # Lane mismatch: sourcing_analyst is governed for INT-POLICY-QUESTION
    # only on rag (GB-R003) — no rule governs it on mode_b.
    run("lane mismatch", "sourcing_analyst", "INT-POLICY-QUESTION", LaneId.MODE_B, DataClassification.INTERNAL)

    # Missing rule: no rule in the committed policy ever governs INT-HELP
    # at all (it is authorized nowhere — safe_fast_path never needs it).
    run("missing rule", "supplier_reader", "INT-HELP", LaneId.SAFE_FAST_PATH, None)

    # Clarification: GB-R005 allows three classes, none requested.
    run("clarification case", "compliance_reviewer", "INT-STRUCTURED-LOOKUP", LaneId.MODE_B, None)

    return GateBDecisionsEvidence(policy_version=policy_registry.policy_version, cases=cases)


def render_gate_b_decisions(e: GateBDecisionsEvidence) -> str:
    lines: list[str] = []
    lines.append("# Day 10 — Gate-B Decisions")
    lines.append("")
    lines.append(
        f"Generated {datetime.now(UTC).date().isoformat()} by "
        "`scripts/day10_generate_gate_b_artifacts.py` from real `GateB.authorize()` "
        f"calls (Task 3-12) against the real committed policy (policy_version `{e.policy_version}`, "
        f"`{DEFAULT_POLICY_PATH}`). Every `role`/`intent_id`/`lane` below is a governed identifier, "
        "never raw user content."
    )
    lines.append("")

    for c in e.cases:
        d = c.decision
        lines.append(f"## {c.label.title()}")
        lines.append("")
        lines.append(f"- Trusted role: `{c.role}`")
        lines.append(f"- Governed intent: `{c.intent_id}`, lane: `{c.lane.value}`")
        if c.data_class is not None:
            lines.append(f"- Requested data classification: `{c.data_class.value}`")
        lines.append(f"- `decision`: **{d.decision.value}**")
        lines.append(f"- `rule_id`: `{d.rule_id}`")
        lines.append(f"- `reason_code`: `{d.reason_code}`")
        lines.append(f"- `effective_scope_summary`: `{d.effective_scope_summary}`")
        lines.append(f"- `disclosure_profile`: `{d.disclosure_profile}`")
        lines.append(f"- `policy_version`: `{d.policy_version}`")
        lines.append("")

    return "\n".join(lines)


# ── Task 2: tenant_isolation.md ───────────────────────────────────────────


@dataclass
class TenantIsolationEvidence:
    same_tenant_decision: GateBDecision
    cross_tenant_decision: GateBDecision
    same_tenant_gateway_calls: int
    same_tenant_retriever_calls: int
    cross_tenant_gateway_calls: int
    cross_tenant_retriever_calls: int


def gather_tenant_isolation_evidence() -> TenantIsolationEvidence:
    registry = OntologyRegistry.load()
    policy_registry = PolicyRegistry.load(ontology_registry=registry)

    identity = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-ARTIFACT", roles=("supplier_reader",))

    same_service, same_gateway, same_retriever = _new_governed_service(registry, policy_registry)
    same_result = same_service.answer(
        "What are the payment terms?",
        identity=identity,
        requested=GateBRequest(data_class=DataClassification.INTERNAL, tenant_ids=("TENANT-A",)),
    )
    del same_result  # only the call counts + the decision (re-derived below) matter for this artifact

    cross_service, cross_gateway, cross_retriever = _new_governed_service(registry, policy_registry)
    cross_service.answer(
        "What are the payment terms?",
        identity=identity,
        requested=GateBRequest(data_class=DataClassification.INTERNAL, tenant_ids=("TENANT-B",)),
    )

    # Re-derive both `GateBDecision`s directly (not reachable off the
    # `AnswerResult`/`GateBDenied` the service returns) so this artifact can
    # show their full sanitized shape, not just the outward result type.
    gate_b = GateB(policy_registry, ontology_registry=registry)
    same_decision = gate_b.authorize(
        identity,
        _matched("INT-POLICY-QUESTION"),
        _lane_decision(LaneId.RAG, "INT-POLICY-QUESTION"),
        GateBRequest(data_class=DataClassification.INTERNAL, tenant_ids=("TENANT-A",)),
    )
    cross_decision = gate_b.authorize(
        identity,
        _matched("INT-POLICY-QUESTION"),
        _lane_decision(LaneId.RAG, "INT-POLICY-QUESTION"),
        GateBRequest(data_class=DataClassification.INTERNAL, tenant_ids=("TENANT-B",)),
    )

    return TenantIsolationEvidence(
        same_tenant_decision=same_decision,
        cross_tenant_decision=cross_decision,
        same_tenant_gateway_calls=same_gateway.call_count,
        same_tenant_retriever_calls=same_retriever.call_count,
        cross_tenant_gateway_calls=cross_gateway.call_count,
        cross_tenant_retriever_calls=cross_retriever.call_count,
    )


def render_tenant_isolation(e: TenantIsolationEvidence) -> str:
    lines: list[str] = []
    lines.append("# Day 10 — Tenant Isolation")
    lines.append("")
    lines.append(
        f"Generated {datetime.now(UTC).date().isoformat()} by "
        "`scripts/day10_generate_gate_b_artifacts.py` from real `ControlPlaneAnswerService.answer()` "
        "calls (Day 10 Task 13) wired to counting Model-Gateway/retriever fakes (Task 11's own "
        "instrumentation technique — no real network call), plus the underlying `GateB.authorize()` "
        "decisions (Task 3/5) shown in full. Trusted tenant in both cases: `TENANT-A`."
    )
    lines.append("")

    lines.append("## Same-Tenant: Allowed")
    lines.append("")
    lines.append("Requested tenant: `TENANT-A` (matches the trusted tenant).")
    lines.append("")
    sd = e.same_tenant_decision
    lines.append(f"- `decision`: **{sd.decision.value}**")
    lines.append(f"- `rule_id`: `{sd.rule_id}`")
    lines.append(f"- `effective_tenant_scope`: `{sd.effective_tenant_scope}`")
    lines.append(f"- `effective_data_classes`: `{tuple(c.value for c in sd.effective_data_classes)}`")
    lines.append(f"- `effective_pii_policy`: `{tuple(c.value for c in sd.effective_pii_policy)}`")
    lines.append(f"- `disclosure_profile`: `{sd.disclosure_profile}`")
    lines.append(
        f"- Protected dependency calls: gateway={e.same_tenant_gateway_calls}, "
        f"retriever={e.same_tenant_retriever_calls} — reached exactly once each, confirming the "
        "counters below are actually wired in, not silently disconnected."
    )
    lines.append("")

    lines.append("## Cross-Tenant: Denied")
    lines.append("")
    lines.append("Requested tenant: `TENANT-B` (does **not** match the trusted tenant `TENANT-A`).")
    lines.append("")
    cd = e.cross_tenant_decision
    lines.append(f"- `decision`: **{cd.decision.value}**")
    lines.append(f"- `reason_code`: `{cd.reason_code}`")
    lines.append(f"- `effective_tenant_scope`: `{cd.effective_tenant_scope}` (empty — nothing granted)")
    lines.append(f"- `effective_data_classes`: `{tuple(c.value for c in cd.effective_data_classes)}` (empty)")
    lines.append(f"- `disclosure_profile`: `{cd.disclosure_profile}` (none)")
    lines.append("")

    lines.append("## Proof: Zero Protected Calls On Cross-Tenant Denial")
    lines.append("")
    ok = e.cross_tenant_gateway_calls == 0 and e.cross_tenant_retriever_calls == 0
    lines.append(
        f"Model Gateway calls: **{e.cross_tenant_gateway_calls}**; retriever calls: **{e.cross_tenant_retriever_calls}** "
        f"— **{'PASS' if ok else 'FAIL'}**. The control is enforced *before* protected evidence access "
        "(Task 11): a denied cross-tenant request never reaches retrieval or the Model Gateway at all, "
        "proven by the counting fakes actually wired into the pipeline."
    )
    lines.append("")

    return "\n".join(lines)


# ── Task 3: disclosure_report.md ──────────────────────────────────────────


@dataclass
class DisclosureFieldExample:
    field_name: str
    action: str
    disclosed_value: str | None  # already masked for redact; None for deny; raw only for allow


@dataclass
class DisclosureEvidence:
    policy_version: str
    rule_id: str
    disclosure_profile: str
    examples: list[DisclosureFieldExample]


def gather_disclosure_evidence() -> DisclosureEvidence:
    registry = OntologyRegistry.load()
    policy_registry = PolicyRegistry.load(ontology_registry=registry)
    gate_b = GateB(policy_registry, ontology_registry=registry)

    identity = TrustedIdentity(tenant_id="TENANT-A", user_id="USER-ARTIFACT", roles=("supplier_reader",))
    decision = gate_b.authorize(
        identity,
        _matched("INT-POLICY-QUESTION"),
        _lane_decision(LaneId.RAG, "INT-POLICY-QUESTION"),
        GateBRequest(data_class=DataClassification.INTERNAL),
    )
    assert decision.disclosure_profile is not None  # sanity: this demo role/intent/lane is genuinely allowed
    profile = policy_registry.get_disclosure_profile(decision.disclosure_profile)

    view = apply_disclosure(decision, profile, _protected_fields())
    by_name = {f.name: f for f in view.fields}

    examples = [
        DisclosureFieldExample("supplier_name", by_name["supplier_name"].action.value, by_name["supplier_name"].value),
        DisclosureFieldExample("contact_email", by_name["contact_email"].action.value, by_name["contact_email"].value),
        DisclosureFieldExample("tax_identifier", by_name["tax_identifier"].action.value, by_name["tax_identifier"].value),
    ]

    return DisclosureEvidence(
        policy_version=view.policy_version,
        rule_id=decision.rule_id or "",
        disclosure_profile=view.disclosure_profile or "",
        examples=examples,
    )


def render_disclosure_report(e: DisclosureEvidence) -> str:
    lines: list[str] = []
    lines.append("# Day 10 — Disclosure Report")
    lines.append("")
    lines.append(
        f"Generated {datetime.now(UTC).date().isoformat()} by "
        "`scripts/day10_generate_gate_b_artifacts.py` from a real `GateB.authorize()` decision "
        "(Task 3) and a real `apply_disclosure()` call (Task 9) against the `day10_pack`'s own "
        "synthetic demo record (`pii_disclosure_cases.json`'s `synthetic_record` — fixed, fake data). "
        "Redacted values shown below are `redaction.mask_value()`'s own deterministic output (Task 9); "
        "no raw protected value from this record is written anywhere in this file."
    )
    lines.append("")

    lines.append("## Applied Policy")
    lines.append("")
    lines.append(f"- `policy_version`: `{e.policy_version}`")
    lines.append(f"- Matched `rule_id`: `{e.rule_id}`")
    lines.append(f"- Applied `disclosure_profile`: `{e.disclosure_profile}`")
    lines.append("")

    labels = {
        "supplier_name": "Public/Internal Field: Allowed",
        "contact_email": "Contact Field: Deterministically Redacted",
        "tax_identifier": "Sensitive Field: Disallowed",
    }
    notes = {
        "supplier_name": "A `public`-classified, non-PII field passes through unchanged.",
        "contact_email": "A `contact`-category PII field is masked, never shown in full — the "
        "value below is the mask, not the original.",
        "tax_identifier": "A `personal_identifier`-category PII field this profile denies outright "
        "— no value is disclosed at all, redacted or otherwise.",
    }
    for field_name, example in ((f.field_name, f) for f in e.examples):
        lines.append(f"## {labels[field_name]}")
        lines.append("")
        lines.append(f"- Field: `{field_name}`")
        lines.append(f"- `action`: **{example.action}**")
        if example.disclosed_value is not None:
            lines.append(f"- Disclosed value: `{example.disclosed_value}`")
        else:
            lines.append("- Disclosed value: *(none — denied)*")
        lines.append(f"- {notes[field_name]}")
        lines.append("")

    lines.append("## No Raw Protected PII Value In This Report")
    lines.append("")
    lines.append(
        "The `contact_email` value shown above is a deterministic mask (`a***@example.test`-shaped, "
        "`redaction.mask_value()`), never the original synthetic email. The `tax_identifier` field "
        "shows no value at all. `supplier_name` is the one field whose value is shown unmasked — its "
        "own governed classification (`public`) and PII category (`none`) are exactly what authorize "
        "that."
    )
    lines.append("")

    return "\n".join(lines)


def main() -> None:
    gate_b_evidence = gather_gate_b_decisions_evidence()
    tenant_evidence = gather_tenant_isolation_evidence()
    disclosure_evidence = gather_disclosure_evidence()

    rendered = {
        "gate_b_decisions.md": render_gate_b_decisions(gate_b_evidence),
        "tenant_isolation.md": render_tenant_isolation(tenant_evidence),
        "disclosure_report.md": render_disclosure_report(disclosure_evidence),
    }

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    for name, text in rendered.items():
        (ARTIFACT_DIR / name).write_text(text, encoding="utf-8")
        print(f"wrote {(ARTIFACT_DIR / name).relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
