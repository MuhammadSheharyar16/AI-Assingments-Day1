"""
Day 11 Task 15 — Gate-C decision / provenance / freshness-completeness
artifacts.

Run: uv run python scripts/day11_generate_gate_c_artifacts.py

Generates the three required artifacts from real system behavior, not
hand-written prose — the same discipline `scripts/day09_generate_control_
plane_artifacts.py` / `scripts/day10_generate_gate_b_artifacts.py` already
established: the real `SourceRegistry` (Task 2) loaded from the real
committed `evidence/source_registry.v1.json`, the real `GateCPolicyRegistry`
(Task 3) loaded from the real committed `policy/gate_c_policy.v1.json`, the
real `GateC` (Task 9-12), the real `validate_provenance()`/
`evaluate_freshness()`/`validate_completeness()`/`evaluate_conflict()`
(Task 4/6/7/8), and — for "was the Model Gateway called" — a real,
instrumented `CountingGateway` fake driven through the identical
`_generate_if_allowed()` discipline `tests/test_day11_no_fallthrough.py`
(Task 11) already proves the pipeline with.

    artifacts/day11/gate_c_decisions.md            — fully valid evidence /
                                                       unknown source /
                                                       broken provenance /
                                                       stale evidence /
                                                       incomplete evidence /
                                                       unresolved conflict,
                                                       each a real
                                                       `GateC.evaluate()`
                                                       result, with the
                                                       actual Model Gateway
                                                       call count for each
    artifacts/day11/provenance_report.md            — source registry
                                                       version, valid /
                                                       content-hash-
                                                       mismatch / source-
                                                       version-mismatch /
                                                       Gate-B-scope-mismatch
                                                       cases, each a real
                                                       `validate_provenance()`
                                                       result
    artifacts/day11/freshness_completeness_report.md — governed freshness
                                                       thresholds, fresh/
                                                       threshold-edge/stale
                                                       cases (the real
                                                       `gate_c_cases.json`
                                                       `freshness_cases`
                                                       values), a required/
                                                       covered/missing-facet
                                                       completeness case, an
                                                       unresolved and a
                                                       governed-authority-
                                                       resolved conflict
                                                       case, and one combined
                                                       scenario's final
                                                       `GateC.evaluate()`
                                                       decision

Every evidence item/claim value below is synthetic demo data this script
constructs directly (mirroring `gate_c_cases.json`'s/`conflict_cases.json`'s
own fixture shapes, reproduced inline rather than re-read from `data/
day11_pack/` at artifact-generation time — the identical "no dependency on
the pack still existing verbatim" discipline `day10_generate_gate_b_
artifacts.py` already documents) — never a real user's request or a real
protected record. No evidence `content`/`claims` value is ever written to
any of these files; only governed ids, counts, and Gate-C's own typed
decision/reason fields are.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from aico.control.gate_c import GateC, GateCRequest, GateCStatus
from aico.control.models import GateBDecision, GateBStatus
from aico.control.ontology import LaneId
from aico.control.ontology_registry import OntologyRegistry
from aico.control.policy_models import DataClassification
from aico.evidence.completeness import validate_completeness
from aico.evidence.conflicts import ConflictClaim, evaluate_conflict
from aico.evidence.freshness import evaluate_freshness
from aico.evidence.models import EvidenceItem, EvidencePackage
from aico.evidence.policy import DEFAULT_GATE_C_POLICY_PATH, ConflictPolicy, GateCPolicyRegistry
from aico.evidence.provenance import ProvenanceReport, stable_content_hash, validate_provenance
from aico.evidence.source_registry import DEFAULT_SOURCE_REGISTRY_PATH, SourceRegistry
from aico.platform.model_gateway import CallMetadata, ChatRequest, ChatResult

REPO_ROOT = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = REPO_ROOT / "artifacts" / "day11"

_AS_OF = "2026-09-11T12:00:00+05:00"
_TENANT = "TENANT-A"


# ── Shared helpers ────────────────────────────────────────────────────────


def _item(evidence_id: str, *, source_id: str, facets: list[str], claims: dict[str, str] | None = None, **overrides) -> dict:
    content = overrides.pop("content", f"Synthetic demo evidence text for {evidence_id}.")
    data = {
        "evidence_id": evidence_id,
        "chunk_id": f"CH-{evidence_id}",
        "source_id": source_id,
        "source_version": "3",
        "source_updated_at": "2026-09-05T09:00:00+05:00",
        "retrieved_at": "2026-09-11T11:59:00+05:00",
        "content_hash": stable_content_hash(content),
        "tenant_id": _TENANT,
        "data_classification": "internal",
        "evidence_facets": facets,
        "content": content,
        "claims": claims or {},
    }
    data.update(overrides)
    return EvidenceItem.model_validate(data)


def _package(items: list[EvidenceItem], *, intent_id: str = "INT-POLICY-QUESTION", required_facets: list[str] | None = None) -> EvidencePackage:
    return EvidencePackage.model_validate(
        {
            "request_id": "REQ-ARTIFACT",
            "intent_id": intent_id,
            "lane": "rag",
            "as_of": _AS_OF,
            "required_facets": required_facets or [],
            "items": items,
        }
    )


def _allow_decision(*, tenant_ids=(_TENANT,), data_classes=(DataClassification.INTERNAL,)) -> GateBDecision:
    return GateBDecision(
        decision=GateBStatus.ALLOW,
        effective_tenant_scope=tenant_ids,
        effective_data_classes=data_classes,
        reason_code="artifact_setup",
        policy_version="1.0",
        lane=LaneId.RAG,
    )


# ── Task 11's own instrumentation technique: a counting fake Model
# Gateway, gated behind the decision the same way `_generate_if_allowed()`
# is in `tests/test_day11_no_fallthrough.py` ────────────────────────────


@dataclass
class _CountingGateway:
    response_content: str = (
        '{"schema_version": "1.0", "status": "answered", "answer": "Synthetic demo answer.", '
        '"citations": [{"chunk_id": "CH-DEMO", "source_file": "SRC-POLICY-A"}], "confidence_label": "high"}'
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


def _model_gateway_call_count_if_allowed(decision, gateway: _CountingGateway) -> int:
    """Task 10/11's own discipline, reproduced for this artifact: the
    Model Gateway is only ever reached for `allow` -- checked *before*
    calling `.chat()`, never after."""
    if decision.decision is GateCStatus.ALLOW:
        gateway.chat(ChatRequest(messages=[]))
    return gateway.call_count


# ── Task 1: gate_c_decisions.md ────────────────────────────────────────────


@dataclass
class GateCDecisionCase:
    label: str
    decision: object  # GateCDecision
    model_gateway_calls: int


@dataclass
class GateCDecisionsEvidence:
    policy_version: str
    source_registry_version: str
    cases: list[GateCDecisionCase]


def gather_gate_c_decisions_evidence() -> GateCDecisionsEvidence:
    ontology_registry = OntologyRegistry.load()
    source_registry = SourceRegistry.load(ontology_registry=ontology_registry)
    policy_registry = GateCPolicyRegistry.load(ontology_registry=ontology_registry, source_registry=source_registry)
    gate_c = GateC(source_registry=source_registry, policy_registry=policy_registry)

    cases: list[GateCDecisionCase] = []

    def run(label: str, package: EvidencePackage, request_kind: str, gate_b_decision: GateBDecision | None = None) -> None:
        decision = gate_c.evaluate(
            gate_b_decision=gate_b_decision or _allow_decision(),
            package=package,
            request=GateCRequest(request_kind=request_kind),
        )
        calls = _model_gateway_call_count_if_allowed(decision, _CountingGateway())
        cases.append(GateCDecisionCase(label, decision, calls))

    # Fully valid evidence -> allow.
    run(
        "fully valid evidence",
        _package([_item("E-VALID", source_id="SRC-POLICY-A", facets=["supplier_identity", "payment_terms"])]),
        "payment_terms_only",
    )

    # Unknown source -> reject.
    run(
        "unknown source",
        _package([_item("E-UNKNOWN", source_id="SRC-UNKNOWN", facets=["payment_terms"])]),
        "payment_terms_only",
    )

    # Broken provenance (content-hash mismatch) -> reject.
    run(
        "broken provenance",
        _package(
            [
                _item(
                    "E-BROKEN", source_id="SRC-POLICY-A", facets=["supplier_identity", "payment_terms"],
                    content_hash="not-a-real-hash",
                )
            ]
        ),
        "payment_terms_only",
    )

    # Stale evidence -> item filtered out on freshness grounds.
    run(
        "stale evidence",
        _package(
            [
                _item(
                    "E-STALE", source_id="SRC-CONTRACT-A", facets=["supplier_identity", "payment_terms"],
                    source_updated_at="2026-08-01T00:00:00+05:00",  # well beyond the 7-day/168h policy
                )
            ]
        ),
        "payment_terms_only",
    )

    # Incomplete evidence -> insufficient_evidence (missing payment_terms).
    run(
        "incomplete evidence",
        _package([_item("E-PARTIAL", source_id="SRC-POLICY-A", facets=["supplier_identity"])]),
        "payment_terms_only",
    )

    # Unresolved conflict -> reject.
    run(
        "unresolved conflict",
        _package(
            [
                _item(
                    "E-C1", source_id="SRC-POLICY-A", facets=["supplier_identity", "payment_terms"],
                    claims={"payment_terms": "net 30"},
                ),
                _item("E-C2", source_id="SRC-POLICY-A", facets=["payment_terms"], claims={"payment_terms": "net 45"}),
            ]
        ),
        "payment_terms_only",
    )

    return GateCDecisionsEvidence(
        policy_version=policy_registry.policy_version, source_registry_version=source_registry.registry_version, cases=cases
    )


def render_gate_c_decisions(e: GateCDecisionsEvidence) -> str:
    lines: list[str] = []
    lines.append("# Day 11 — Gate-C Decisions")
    lines.append("")
    lines.append(
        f"Generated {datetime.now(UTC).date().isoformat()} by "
        "`scripts/day11_generate_gate_c_artifacts.py` from real `GateC.evaluate()` calls (Task 9-12) "
        f"against the real committed source registry (`source_registry_version` `{e.source_registry_version}`, "
        f"`{DEFAULT_SOURCE_REGISTRY_PATH}`) and Gate-C policy (`policy_version` `{e.policy_version}`, "
        f"`{DEFAULT_GATE_C_POLICY_PATH}`). Every evidence item is synthetic demo data this script "
        "constructs directly; no raw evidence content is written below, only Gate-C's own typed decision."
    )
    lines.append("")

    for c in e.cases:
        d = c.decision
        lines.append(f"## {c.label.title()}")
        lines.append("")
        lines.append(f"- `decision`: **{d.decision.value}**")
        lines.append(f"- `validated_evidence_ids`: `{d.validated_evidence_ids}`")
        lines.append(f"- `rejected_evidence_ids`: `{d.rejected_evidence_ids}`")
        lines.append(f"- `reason_codes`: `{d.reason_codes}`")
        if d.missing_facets:
            lines.append(f"- `missing_facets`: `{d.missing_facets}`")
        if d.conflict_facets:
            lines.append(f"- `conflict_facets`: `{d.conflict_facets}`")
        lines.append(f"- `freshness_summary`: `{d.freshness_summary}`")
        lines.append(
            f"- Model Gateway calls: **{c.model_gateway_calls}** "
            f"({'reached, exactly once' if c.model_gateway_calls else 'zero — Task 11 no-fall-through'})"
        )
        lines.append("")

    return "\n".join(lines)


# ── Task 2: provenance_report.md ───────────────────────────────────────────


@dataclass
class ProvenanceCase:
    label: str
    report: ProvenanceReport
    evidence_id: str


@dataclass
class ProvenanceEvidence:
    source_registry_version: str
    cases: list[ProvenanceCase]


def gather_provenance_evidence() -> ProvenanceEvidence:
    ontology_registry = OntologyRegistry.load()
    source_registry = SourceRegistry.load(ontology_registry=ontology_registry)

    cases: list[ProvenanceCase] = []

    def run(label: str, package: EvidencePackage, evidence_id: str, gate_b_decision: GateBDecision | None = None) -> None:
        report = validate_provenance(package, source_registry=source_registry, gate_b_decision=gate_b_decision or _allow_decision())
        cases.append(ProvenanceCase(label, report, evidence_id))

    # Valid provenance case.
    run(
        "valid provenance",
        _package([_item("E-VALID", source_id="SRC-POLICY-A", facets=["payment_terms"])]),
        "E-VALID",
    )

    # Content-hash mismatch.
    run(
        "content-hash mismatch",
        _package([_item("E-HASH", source_id="SRC-POLICY-A", facets=["payment_terms"], content_hash="not-a-real-hash")]),
        "E-HASH",
    )

    # Source-version mismatch (package-level cross-item consistency).
    run(
        "source-version mismatch",
        _package(
            [
                _item("E-V3", source_id="SRC-POLICY-A", facets=["payment_terms"], source_version="3"),
                _item("E-V4", source_id="SRC-POLICY-A", facets=["payment_terms"], source_version="4"),
            ]
        ),
        "E-V3",
    )

    # Gate-B scope mismatch (cross-tenant evidence).
    run(
        "Gate-B scope mismatch",
        _package([_item("E-SCOPE", source_id="SRC-POLICY-A", facets=["payment_terms"], tenant_id="TENANT-B")]),
        "E-SCOPE",
        gate_b_decision=_allow_decision(tenant_ids=("TENANT-A",)),
    )

    return ProvenanceEvidence(source_registry_version=source_registry.registry_version, cases=cases)


def render_provenance_report(e: ProvenanceEvidence) -> str:
    lines: list[str] = []
    lines.append("# Day 11 — Provenance Report")
    lines.append("")
    lines.append(
        f"Generated {datetime.now(UTC).date().isoformat()} by "
        "`scripts/day11_generate_gate_c_artifacts.py` from real `validate_provenance()` calls (Task 4) "
        f"against the real committed source registry (`source_registry_version` `{e.source_registry_version}`, "
        f"`{DEFAULT_SOURCE_REGISTRY_PATH}`)."
    )
    lines.append("")

    lines.append("## Source Registry Version")
    lines.append("")
    lines.append(f"`{e.source_registry_version}`")
    lines.append("")

    for c in e.cases:
        result = c.report.result_for(c.evidence_id)
        lines.append(f"## {c.label.title()}")
        lines.append("")
        lines.append(f"- `evidence_id`: `{c.evidence_id}`")
        lines.append(f"- `valid`: **{result.valid}**")
        lines.append(f"- `reasons`: `{tuple(r.value for r in result.reasons)}`")
        lines.append("")

    lines.append("## No Raw Protected Content")
    lines.append("")
    lines.append(
        "Every case above is reported through `ProvenanceItemResult`'s own typed fields "
        "(`evidence_id`/`valid`/`reasons`) only — no `EvidenceItem.content` or `.claims` value is ever "
        "read for this report, let alone written to it."
    )
    lines.append("")

    return "\n".join(lines)


# ── Task 3: freshness_completeness_report.md ───────────────────────────────


@dataclass
class FreshnessCase:
    label: str
    as_of: str
    source_updated_at: str
    max_age_hours: int
    status: str


@dataclass
class ConflictCase:
    label: str
    facet: str
    claims: list[ConflictClaim]
    outcome: str
    winning_value: str | None


@dataclass
class FreshnessCompletenessEvidence:
    freshness_policies: list[tuple[str, int]]
    freshness_cases: list[FreshnessCase]
    required_facets: list[str]
    covered_facets: list[str]
    missing_facets: list[str]
    conflict_cases: list[ConflictCase]
    final_decision: object  # GateCDecision


def gather_freshness_completeness_evidence() -> FreshnessCompletenessEvidence:
    ontology_registry = OntologyRegistry.load()
    source_registry = SourceRegistry.load(ontology_registry=ontology_registry)
    policy_registry = GateCPolicyRegistry.load(ontology_registry=ontology_registry, source_registry=source_registry)

    freshness_policies = [(p.policy_id, p.max_age_hours) for p in policy_registry.freshness_policies]

    # The real `gate_c_cases.json` `freshness_cases` values (FRESH-001..003),
    # reproduced inline (see module docstring) -- fresh / exactly-at-
    # threshold / stale.
    freshness_cases: list[FreshnessCase] = []
    for label, source_updated_at, max_age_hours in (
        ("fresh", "2026-09-01T12:00:00+05:00", 720),
        ("threshold-edge (exactly at max age)", "2026-08-12T12:00:00+05:00", 720),
        ("stale", "2026-08-12T11:59:59+05:00", 720),
    ):
        status = evaluate_freshness(
            as_of=datetime.fromisoformat(_AS_OF), source_updated_at=datetime.fromisoformat(source_updated_at), max_age_hours=max_age_hours
        )
        freshness_cases.append(FreshnessCase(label, _AS_OF, source_updated_at, max_age_hours, status.value))

    # Completeness: required facets partially covered.
    required_facets = ["supplier_identity", "payment_terms", "invoice_window"]
    items = [
        _item("E-A", source_id="SRC-POLICY-A", facets=["supplier_identity", "payment_terms"]),
    ]
    completeness_package = _package(items, required_facets=required_facets)
    completeness_result = validate_completeness(
        completeness_package, valid_evidence_ids={"E-A"}, source_registry=source_registry
    )

    # Conflicts: unresolved (same authority) + resolved by governed authority.
    conflict_cases = [
        ConflictCase(
            "unresolved (same authority)",
            "payment_terms",
            [ConflictClaim(evidence_id="E-1", authority=90, value="net 30"), ConflictClaim(evidence_id="E-2", authority=90, value="net 45")],
            "", None,
        ),
        ConflictCase(
            "resolved by governed authority",
            "payment_terms",
            [
                ConflictClaim(evidence_id="E-contract", authority=100, value="net 30"),
                ConflictClaim(evidence_id="E-policy", authority=90, value="net 45"),
            ],
            "", None,
        ),
    ]
    for case in conflict_cases:
        resolution = evaluate_conflict(case.facet, case.claims, policy=ConflictPolicy.AUTHORITY_THEN_REJECT_TIE)
        case.outcome = resolution.outcome.value
        case.winning_value = resolution.winning_value

    # Final combined Gate-C decision: fresh, complete, non-conflicting ->
    # allow -- ties freshness + completeness + conflicts into one real
    # decision for this report's own closing section.
    gate_c = GateC(source_registry=source_registry, policy_registry=policy_registry)
    final_package = _package(
        [
            _item("E-FINAL-A", source_id="SRC-POLICY-A", facets=["supplier_identity", "payment_terms"]),
            _item("E-FINAL-B", source_id="SRC-REFERENCE-A", facets=["invoice_window"]),
        ]
    )
    final_decision = gate_c.evaluate(
        gate_b_decision=_allow_decision(), package=final_package, request=GateCRequest(request_kind="payment_and_invoice")
    )

    return FreshnessCompletenessEvidence(
        freshness_policies=freshness_policies,
        freshness_cases=freshness_cases,
        required_facets=required_facets,
        covered_facets=list(completeness_result.covered_facets),
        missing_facets=list(completeness_result.missing_facets),
        conflict_cases=conflict_cases,
        final_decision=final_decision,
    )


def render_freshness_completeness_report(e: FreshnessCompletenessEvidence) -> str:
    lines: list[str] = []
    lines.append("# Day 11 — Freshness & Completeness Report")
    lines.append("")
    lines.append(
        f"Generated {datetime.now(UTC).date().isoformat()} by "
        "`scripts/day11_generate_gate_c_artifacts.py` from real `evaluate_freshness()`/"
        "`validate_completeness()`/`evaluate_conflict()`/`GateC.evaluate()` calls (Task 6/7/8/9) "
        "against the real committed Gate-C policy."
    )
    lines.append("")

    lines.append("## Governed Freshness Thresholds")
    lines.append("")
    for policy_id, max_age_hours in e.freshness_policies:
        lines.append(f"- `{policy_id}`: `max_age_hours={max_age_hours}`")
    lines.append("")

    lines.append("## Freshness Cases")
    lines.append("")
    for c in e.freshness_cases:
        lines.append(f"### {c.label.title()}")
        lines.append("")
        lines.append(f"- `as_of`: `{c.as_of}`")
        lines.append(f"- `source_updated_at`: `{c.source_updated_at}`")
        lines.append(f"- `max_age_hours`: `{c.max_age_hours}`")
        lines.append(f"- `status`: **{c.status}**")
        lines.append("")

    lines.append("## Completeness")
    lines.append("")
    lines.append(f"- Required facets: `{e.required_facets}`")
    lines.append(f"- Covered facets: `{e.covered_facets}`")
    lines.append(f"- Missing facets: `{e.missing_facets}`")
    lines.append("")

    lines.append("## Conflict Cases")
    lines.append("")
    for c in e.conflict_cases:
        lines.append(f"### {c.label.title()}")
        lines.append("")
        lines.append(f"- Facet: `{c.facet}`")
        lines.append(f"- `outcome`: **{c.outcome}**")
        if c.winning_value is not None:
            lines.append(f"- `winning_value`: `{c.winning_value}`")
        lines.append("")

    lines.append("## Final Gate-C Decision (Combined Scenario)")
    lines.append("")
    d = e.final_decision
    lines.append("Two fresh, non-conflicting, source-governed items covering every required facet " '(`payment_and_invoice`):')
    lines.append("")
    lines.append(f"- `decision`: **{d.decision.value}**")
    lines.append(f"- `validated_evidence_ids`: `{d.validated_evidence_ids}`")
    lines.append(f"- `missing_facets`: `{d.missing_facets}`")
    lines.append(f"- `conflict_facets`: `{d.conflict_facets}`")
    lines.append(f"- `freshness_summary`: `{d.freshness_summary}`")
    lines.append("")

    return "\n".join(lines)


def main() -> None:
    gate_c_evidence = gather_gate_c_decisions_evidence()
    provenance_evidence = gather_provenance_evidence()
    freshness_completeness_evidence = gather_freshness_completeness_evidence()

    rendered = {
        "gate_c_decisions.md": render_gate_c_decisions(gate_c_evidence),
        "provenance_report.md": render_provenance_report(provenance_evidence),
        "freshness_completeness_report.md": render_freshness_completeness_report(freshness_completeness_evidence),
    }

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    for name, text in rendered.items():
        (ARTIFACT_DIR / name).write_text(text, encoding="utf-8")
        print(f"wrote {(ARTIFACT_DIR / name).relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
