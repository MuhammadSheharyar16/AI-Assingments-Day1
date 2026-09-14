"""
Day 12 Task 14 — Gate-D decision / final-citation / disclosure-latency
artifacts.

Run: uv run python scripts/day12_generate_gate_d_artifacts.py

Generates the three required artifacts from real system behavior, not
hand-written prose — the same discipline
`scripts/day11_generate_gate_c_artifacts.py` already established: the
real `PolicyRegistry` (Day 10 Task 2) loaded from the real committed
`policy/gate_b_policy.v1.json`, the real `GateDPolicyRegistry` (Task 2)
loaded from the real committed `policy/gate_d_policy.v1.json`, and the
real `GateD`/`reconcile_final_citations()`/`check_final_disclosure()`/
`detect_protected_value_leak()`/`check_latency_budget()`/
`build_safe_failure_response()` (Tasks 3-10).

    artifacts/day12/gate_d_decisions.md        — fully valid response /
                                                   invalid citation /
                                                   quality failure /
                                                   disclosure leak /
                                                   latency budget failure /
                                                   (bonus) invalid internal
                                                   candidate, each a real
                                                   `GateD.evaluate()` result
    artifacts/day12/final_citation_report.md   — Gate-C approved evidence
                                                   IDs, final citation IDs,
                                                   valid reconciliation,
                                                   forged citation
                                                   rejection, Gate-C-
                                                   rejected citation
                                                   rejection, each a real
                                                   `reconcile_final_
                                                   citations()` result
    artifacts/day12/disclosure_latency_report.md
                                               — allowed disclosure case,
                                                   redaction/denied-field/
                                                   secret-pattern leak
                                                   rejections, the governed
                                                   model/total latency
                                                   thresholds, an exactly-
                                                   at-threshold result, and
                                                   one safe-failure result
                                                   (`SafeFailureResponse`'s
                                                   own sanitized shape)

Every candidate answer/citation/protected-field value below is synthetic
demo data this script constructs directly (mirroring `final_citation_
cases.json`/`disclosure_leak_cases.json`/`latency_budget_cases.json`'s own
fixture shapes, reproduced inline rather than re-read from `data/
day12_pack/` at artifact-generation time — the identical "no dependency on
the pack still existing verbatim" discipline `day11_generate_gate_c_
artifacts.py` already documents) — never a real user's request or a real
protected record. No raw candidate answer text, raw protected value, or
matched secret substring is ever written to any of these files; only
governed ids, counts, sanitized labels, and Gate-D's own typed decision/
reason fields are.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from aico.control.disclosure import ProtectedField
from aico.control.final_response import FinalResponseCandidate, parse_final_response_candidate
from aico.control.gate_d import (
    CitationReconciliationReport,
    DisclosureCheckReport,
    GateCEvidenceRecord,
    GateD,
    GateDDecision,
    LatencyCheckReport,
    SafeFailureResponse,
    SecretDetectionReport,
    build_safe_failure_response,
    check_final_disclosure,
    check_latency_budget,
    detect_protected_value_leak,
    reconcile_final_citations,
)
from aico.control.models import GateBDecision, GateBStatus
from aico.control.ontology import LaneId
from aico.control.policy_models import DataClassification, PiiCategory
from aico.control.policy_registry import DEFAULT_GATE_D_POLICY_PATH, GateDPolicyRegistry, PolicyRegistry

REPO_ROOT = Path(__file__).resolve().parent.parent
ARTIFACT_DIR = REPO_ROOT / "artifacts" / "day12"

_STARTED_AT = "2026-09-14T12:00:00+00:00"
_TENANT = "TENANT-A"
_PROFILE = "policy_reader"

_VALID_CITATION = {"evidence_id": "E-101", "chunk_id": "CH-101", "source_id": "SRC-POLICY-A", "source_version": "3"}
_VALID_EVIDENCE = (GateCEvidenceRecord.model_validate(_VALID_CITATION),)


# ── Shared helpers ────────────────────────────────────────────────────────


def _gate_b_decision(gate_b_policy: PolicyRegistry, *, profile_id: str = _PROFILE) -> GateBDecision:
    return GateBDecision(
        decision=GateBStatus.ALLOW,
        effective_tenant_scope=(_TENANT,),
        effective_data_classes=(DataClassification.PUBLIC, DataClassification.INTERNAL),
        effective_pii_policy=(PiiCategory.NONE, PiiCategory.CONTACT, PiiCategory.PERSONAL_IDENTIFIER),
        disclosure_profile=profile_id,
        lane=LaneId.RAG,
        reason_code="artifact_setup",
        policy_version=gate_b_policy.policy_version,
    )


def _candidate(**overrides: object) -> FinalResponseCandidate:
    payload: dict = {
        "request_id": "REQ-ARTIFACT",
        "correlation_id": "CORR-ARTIFACT",
        "candidate_status": "answered",
        "candidate_answer": "Synthetic Supplier Alpha uses net 30 payment terms.",
        "candidate_citations": [],
        "gate_c_validated_evidence_ids": [],
        "gate_b_disclosure_profile": _PROFILE,
        "started_at": _STARTED_AT,
        "elapsed_ms": 1200,
        "model_latency_ms": 800,
        "contract_validation_status": "passed",
        "semantic_validation_status": "passed",
    }
    payload.update(overrides)
    return parse_final_response_candidate(payload)


# ── Task 1: gate_d_decisions.md ────────────────────────────────────────────


@dataclass
class GateDDecisionCase:
    label: str
    decision: GateDDecision


@dataclass
class GateDDecisionsEvidence:
    policy_version: str
    cases: list[GateDDecisionCase]


def gather_gate_d_decisions_evidence() -> GateDDecisionsEvidence:
    gate_b_policy = PolicyRegistry.load()
    gate_d_policy = GateDPolicyRegistry.load(gate_b_policy=gate_b_policy)
    gate_d = GateD(policy_registry=gate_d_policy)

    cases: list[GateDDecisionCase] = []

    def run(
        label: str,
        candidate: FinalResponseCandidate,
        *,
        gate_c_validated_evidence: tuple[GateCEvidenceRecord, ...] = _VALID_EVIDENCE,
        protected_fields: tuple[ProtectedField, ...] = (),
    ) -> None:
        decision = gate_d.evaluate(
            candidate=candidate,
            gate_c_validated_evidence=gate_c_validated_evidence,
            gate_b_decision=_gate_b_decision(gate_b_policy),
            disclosure_profile=gate_b_policy.get_disclosure_profile(_PROFILE),
            protected_fields=protected_fields,
        )
        cases.append(GateDDecisionCase(label, decision))

    # 1. Fully valid response -> allow.
    run(
        "fully valid response",
        _candidate(candidate_citations=[_VALID_CITATION], gate_c_validated_evidence_ids=["E-101"]),
    )

    # 2. Invalid (forged) citation -> safe_failure.
    run(
        "invalid citation",
        _candidate(
            candidate_citations=[{"evidence_id": "E-999", "chunk_id": "CH-999", "source_id": "SRC-FAKE", "source_version": "1"}],
            gate_c_validated_evidence_ids=[],
        ),
        gate_c_validated_evidence=(),
    )

    # 3. Quality failure (oversized answer) -> safe_failure.
    oversized_answer = "x" * (gate_d_policy.quality_policy.max_answer_chars + 1)
    run(
        "quality failure",
        _candidate(
            candidate_answer=oversized_answer, candidate_citations=[_VALID_CITATION], gate_c_validated_evidence_ids=["E-101"]
        ),
    )

    # 4. Disclosure leak (a denied field's raw value reappearing) -> safe_failure.
    run(
        "disclosure leak",
        _candidate(
            candidate_answer="Tax identifier is SYN-ID-000000.",
            candidate_citations=[_VALID_CITATION],
            gate_c_validated_evidence_ids=["E-101"],
        ),
        protected_fields=(
            ProtectedField(
                name="tax_identifier",
                value="SYN-ID-000000",
                data_class=DataClassification.CONFIDENTIAL,
                pii_category=PiiCategory.PERSONAL_IDENTIFIER,
            ),
        ),
    )

    # 5. Latency budget failure (total budget exceeded) -> safe_failure.
    run(
        "latency budget failure",
        _candidate(
            elapsed_ms=gate_d_policy.latency_budgets.max_total_latency_ms + 1,
            candidate_citations=[_VALID_CITATION],
            gate_c_validated_evidence_ids=["E-101"],
        ),
    )

    # 6. (Bonus, beyond Task 14's own required five) invalid internal
    # candidate -- an already-failed typed contract reaching this far ->
    # reject, distinct from every safe_failure case above.
    run(
        "invalid internal candidate (reject)",
        _candidate(
            contract_validation_status="failed", candidate_citations=[_VALID_CITATION], gate_c_validated_evidence_ids=["E-101"]
        ),
    )

    return GateDDecisionsEvidence(policy_version=gate_d_policy.policy_version, cases=cases)


def render_gate_d_decisions(e: GateDDecisionsEvidence) -> str:
    lines: list[str] = []
    lines.append("# Day 12 — Gate-D Decisions")
    lines.append("")
    lines.append(
        f"Generated {datetime.now(UTC).date().isoformat()} by "
        "`scripts/day12_generate_gate_d_artifacts.py` from real `GateD.evaluate()` calls (Task 10) "
        f"against the real committed Gate-D policy (`policy_version` `{e.policy_version}`, "
        f"`{DEFAULT_GATE_D_POLICY_PATH}`). Every candidate answer/citation/protected-field value below "
        "is synthetic demo data this script constructs directly; no raw candidate answer text or "
        "protected value is written below, only Gate-D's own typed decision."
    )
    lines.append("")

    for c in e.cases:
        d = c.decision
        lines.append(f"## {c.label.title()}")
        lines.append("")
        lines.append(f"- `decision`: **{d.decision.value}**")
        lines.append(f"- `reason_codes`: `{d.reason_codes}`")
        lines.append(f"- `validated_citation_ids`: `{d.validated_citation_ids}`")
        lines.append(f"- `safe_failure_code`: `{d.safe_failure_code}`")
        lines.append(
            f"- sub-checks — quality: **{_pass_fail(d.quality_checks.passed)}**, "
            f"citation: **{_pass_fail(d.citation_checks.passed)}**, "
            f"disclosure: **{_pass_fail(d.disclosure_checks.passed)}**, "
            f"latency: **{_pass_fail(d.latency_checks.passed)}**"
        )
        lines.append("")

    return "\n".join(lines)


def _pass_fail(passed: bool) -> str:
    return "passed" if passed else "failed"


def _reason_values(reason_codes: Sequence[Enum]) -> tuple[str, ...]:
    """Render a tuple of governed reason-code enum members as their plain
    `.value` strings -- `str(SomeEnum.MEMBER)` in Python's own default
    `Enum.__repr__` includes the class/member name
    (`<SomeEnum.MEMBER: 'value'>`), which is still fully sanitized but
    needlessly noisy for a reader-facing artifact; every report below
    prints the bare value instead, the same string
    `GateDDecision.reason_codes` (already plain strings) already renders
    as."""
    return tuple(code.value for code in reason_codes)


# ── Task 2: final_citation_report.md ───────────────────────────────────────


@dataclass
class FinalCitationCase:
    label: str
    gate_c_validated_evidence_ids: tuple[str, ...]
    final_citation_ids: tuple[str, ...]
    report: CitationReconciliationReport


@dataclass
class FinalCitationEvidence:
    citation_policy_version: str
    cases: list[FinalCitationCase]


def gather_final_citation_evidence() -> FinalCitationEvidence:
    gate_b_policy = PolicyRegistry.load()
    gate_d_policy = GateDPolicyRegistry.load(gate_b_policy=gate_b_policy)

    cases: list[FinalCitationCase] = []

    def run(
        label: str,
        citations: list[dict],
        *,
        gate_c_validated_evidence_ids: tuple[str, ...],
        gate_c_validated_evidence: tuple[GateCEvidenceRecord, ...],
    ) -> None:
        candidate = _candidate(candidate_citations=citations, gate_c_validated_evidence_ids=list(gate_c_validated_evidence_ids))
        report = reconcile_final_citations(
            candidate, gate_c_validated_evidence=gate_c_validated_evidence, policy=gate_d_policy.citation_policy
        )
        cases.append(
            FinalCitationCase(
                label=label,
                gate_c_validated_evidence_ids=gate_c_validated_evidence_ids,
                final_citation_ids=tuple(c["evidence_id"] for c in citations),
                report=report,
            )
        )

    # Valid reconciliation: the one final citation names exactly the one
    # Gate-C-validated evidence item, with matching full provenance.
    run(
        "valid reconciliation",
        [_VALID_CITATION],
        gate_c_validated_evidence_ids=("E-101",),
        gate_c_validated_evidence=_VALID_EVIDENCE,
    )

    # Forged citation rejection: the cited evidence_id was never Gate-C
    # validated at all.
    run(
        "forged citation rejection",
        [{"evidence_id": "E-999", "chunk_id": "CH-999", "source_id": "SRC-FAKE", "source_version": "1"}],
        gate_c_validated_evidence_ids=(),
        gate_c_validated_evidence=(),
    )

    # Gate-C-rejected citation rejection: E-101 is the (only) evidence
    # Gate-C actually validated; the candidate instead cites E-202, an id
    # Gate-C saw and rejected -- indistinguishable, by design, from a
    # forged id at this boundary (see `gate_d.py`'s own "What Gate-D's
    # final citation check proves" section).
    run(
        "Gate-C-rejected citation rejection",
        [{"evidence_id": "E-202", "chunk_id": "CH-202", "source_id": "SRC-ARCHIVE-A", "source_version": "8"}],
        gate_c_validated_evidence_ids=("E-101",),
        gate_c_validated_evidence=_VALID_EVIDENCE,
    )

    return FinalCitationEvidence(citation_policy_version=gate_d_policy.policy_version, cases=cases)


def render_final_citation_report(e: FinalCitationEvidence) -> str:
    lines: list[str] = []
    lines.append("# Day 12 — Final Citation Report")
    lines.append("")
    lines.append(
        f"Generated {datetime.now(UTC).date().isoformat()} by "
        "`scripts/day12_generate_gate_d_artifacts.py` from real `reconcile_final_citations()` calls "
        f"(Task 3/4) against the real committed Gate-D citation policy (`policy_version` "
        f"`{e.citation_policy_version}`, `{DEFAULT_GATE_D_POLICY_PATH}`)."
    )
    lines.append("")

    for c in e.cases:
        r = c.report
        lines.append(f"## {c.label.title()}")
        lines.append("")
        lines.append(f"- Gate-C approved evidence IDs: `{c.gate_c_validated_evidence_ids}`")
        lines.append(f"- Final citation IDs: `{c.final_citation_ids}`")
        lines.append(f"- `passed`: **{r.passed}**")
        lines.append(f"- `reason_codes`: `{_reason_values(r.reason_codes)}`")
        lines.append(f"- `valid_citation_evidence_ids`: `{r.valid_citation_evidence_ids}`")
        lines.append(f"- `invalid_citation_evidence_ids`: `{r.invalid_citation_evidence_ids}`")
        lines.append("")

    lines.append("## No Raw Protected Evidence Content")
    lines.append("")
    lines.append(
        "Every case above is reported through `CitationReconciliationReport`'s own typed fields "
        "(`evidence_id`/`chunk_id`/`source_id`/`source_version` identities and governed reason codes "
        "only) — no `EvidenceItem`/`GateCEvidenceRecord` content is ever read for this report, let "
        "alone written to it."
    )
    lines.append("")

    return "\n".join(lines)


# ── Task 3: disclosure_latency_report.md ────────────────────────────────────


@dataclass
class DisclosureCase:
    label: str
    field_report: DisclosureCheckReport
    secret_report: SecretDetectionReport


@dataclass
class LatencyCase:
    label: str
    report: LatencyCheckReport


@dataclass
class DisclosureLatencyEvidence:
    disclosure_policy_version: str
    disclosure_cases: list[DisclosureCase]
    max_total_latency_ms: int
    max_model_latency_ms: int
    latency_cases: list[LatencyCase]
    safe_failure: SafeFailureResponse


def gather_disclosure_latency_evidence() -> DisclosureLatencyEvidence:
    gate_b_policy = PolicyRegistry.load()
    gate_d_policy = GateDPolicyRegistry.load(gate_b_policy=gate_b_policy)
    disclosure_policy = gate_d_policy.disclosure_policy
    latency_budgets = gate_d_policy.latency_budgets

    protected_fields = (
        ProtectedField(
            name="contact_email", value="alice@example.test", data_class=DataClassification.CONFIDENTIAL, pii_category=PiiCategory.CONTACT
        ),
        ProtectedField(
            name="tax_identifier",
            value="SYN-ID-000000",
            data_class=DataClassification.CONFIDENTIAL,
            pii_category=PiiCategory.PERSONAL_IDENTIFIER,
        ),
    )
    gate_b_decision = _gate_b_decision(gate_b_policy)
    profile = gate_b_policy.get_disclosure_profile(_PROFILE)

    def disclosure_case(label: str, answer: str) -> DisclosureCase:
        candidate = _candidate(candidate_answer=answer)
        field_report = check_final_disclosure(
            candidate,
            gate_b_decision=gate_b_decision,
            disclosure_profile=profile,
            protected_fields=protected_fields,
            policy=disclosure_policy,
        )
        secret_report = detect_protected_value_leak(candidate, policy=disclosure_policy)
        return DisclosureCase(label, field_report, secret_report)

    disclosure_cases = [
        disclosure_case("allowed disclosure case", "Synthetic Supplier Alpha uses net 30 payment terms."),
        disclosure_case("redaction leak rejection", "Contact email is alice@example.test."),
        disclosure_case("denied protected-value leak rejection", "Tax identifier is SYN-ID-000000."),
        disclosure_case("secret-pattern leak rejection", "Authorization value: Bearer SYNTHETIC_SECRET_TOKEN"),
    ]

    max_total = latency_budgets.max_total_latency_ms
    max_model = latency_budgets.max_model_latency_ms

    def latency_case(label: str, elapsed_ms: int, model_latency_ms: int) -> LatencyCase:
        candidate = _candidate(elapsed_ms=elapsed_ms, model_latency_ms=model_latency_ms)
        return LatencyCase(label, check_latency_budget(candidate, policy=latency_budgets))

    latency_cases = [
        latency_case("within budget", max_total - 100, max_model - 100),
        latency_case("exactly-at-threshold result", max_total, max_model),
        latency_case("model budget exceeded", max_total - 100, max_model + 1),
        latency_case("total budget exceeded", max_total + 1, max_model - 100),
    ]

    safe_failure = build_safe_failure_response(
        request_id="REQ-ARTIFACT",
        correlation_id="CORR-ARTIFACT",
        policy=gate_d_policy.safe_failure,
        reason_codes=("total_latency_budget_exceeded", "denied_field_value_leaked"),
    )

    return DisclosureLatencyEvidence(
        disclosure_policy_version=gate_d_policy.policy_version,
        disclosure_cases=disclosure_cases,
        max_total_latency_ms=max_total,
        max_model_latency_ms=max_model,
        latency_cases=latency_cases,
        safe_failure=safe_failure,
    )


def render_disclosure_latency_report(e: DisclosureLatencyEvidence) -> str:
    lines: list[str] = []
    lines.append("# Day 12 — Disclosure & Latency Report")
    lines.append("")
    lines.append(
        f"Generated {datetime.now(UTC).date().isoformat()} by "
        "`scripts/day12_generate_gate_d_artifacts.py` from real `check_final_disclosure()`/"
        "`detect_protected_value_leak()`/`check_latency_budget()`/`build_safe_failure_response()` "
        f"calls (Task 6/7/8/9) against the real committed Gate-D policy (`policy_version` "
        f"`{e.disclosure_policy_version}`, `{DEFAULT_GATE_D_POLICY_PATH}`). No raw protected value "
        "or matched secret substring is ever printed below — only governed field names, resolved "
        "`DisclosureAction`s, matched pattern *names*, and pass/fail results."
    )
    lines.append("")

    lines.append("## Disclosure Cases")
    lines.append("")
    for c in e.disclosure_cases:
        lines.append(f"### {c.label.title()}")
        lines.append("")
        lines.append(f"- field check `passed`: **{c.field_report.passed}**")
        if c.field_report.field_checks:
            leaked_fields = [fc.field_name for fc in c.field_report.field_checks if fc.leaked]
            lines.append(f"- leaked field names: `{tuple(leaked_fields)}`")
        lines.append(f"- field check `reason_codes`: `{_reason_values(c.field_report.reason_codes)}`")
        lines.append(f"- secret-pattern check `passed`: **{c.secret_report.passed}**")
        lines.append(f"- matched pattern names: `{c.secret_report.matched_pattern_names}`")
        lines.append("")

    lines.append("## Governed Latency Budgets")
    lines.append("")
    lines.append(f"- `max_total_latency_ms`: `{e.max_total_latency_ms}`")
    lines.append(f"- `max_model_latency_ms`: `{e.max_model_latency_ms}`")
    lines.append("")

    lines.append("## Latency Cases")
    lines.append("")
    for c in e.latency_cases:
        r = c.report
        lines.append(f"### {c.label.title()}")
        lines.append("")
        lines.append(f"- `total_latency_ms`: `{r.total_latency_ms}` (budget `{r.max_total_latency_ms}`)")
        lines.append(f"- `model_latency_ms`: `{r.model_latency_ms}` (budget `{r.max_model_latency_ms}`)")
        lines.append(f"- `passed`: **{r.passed}**")
        lines.append(f"- `reason_codes`: `{_reason_values(r.reason_codes)}`")
        lines.append("")

    lines.append("## Safe-Failure Result")
    lines.append("")
    sf = e.safe_failure
    lines.append(f"- `status`: `{sf.status}`")
    lines.append(f"- `error_code`: `{sf.error_code}`")
    lines.append(f"- `message`: `{sf.message}`")
    lines.append(f"- `reason_codes`: `{sf.reason_codes}`")
    lines.append(
        "- Fixed, policy-authored text (Task 9) — never the failing candidate's own answer text, "
        "regardless of which check(s) actually failed."
    )
    lines.append("")

    return "\n".join(lines)


def main() -> None:
    gate_d_decisions_evidence = gather_gate_d_decisions_evidence()
    final_citation_evidence = gather_final_citation_evidence()
    disclosure_latency_evidence = gather_disclosure_latency_evidence()

    rendered = {
        "gate_d_decisions.md": render_gate_d_decisions(gate_d_decisions_evidence),
        "final_citation_report.md": render_final_citation_report(final_citation_evidence),
        "disclosure_latency_report.md": render_disclosure_latency_report(disclosure_latency_evidence),
    }

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    for name, text in rendered.items():
        (ARTIFACT_DIR / name).write_text(text, encoding="utf-8")
        print(f"wrote {(ARTIFACT_DIR / name).relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
