"""
Correction — Task 4's groundedness evaluator can now genuinely call a real
Model Gateway instead of always grading through the deterministic
`well_behaved_verdict` stand-in (see `aico.evals.day07`'s module docstring
and `GROUNDEDNESS_GATEWAY_CHOICES`).

Every test here still uses a duck-typed fake — no real network call, no
Azure identity, no `config/model-routing.yaml` requirement — the same
offline discipline every other Day 7 test file already follows
(`tests/test_day07_groundedness.py`'s own docstring). What changed is
`aico.evals.day07` now has a seam (`groundedness_gateway=`) a fake can be
injected through, proving the wiring is real without needing this test
suite to depend on network access or a live deployment.
"""
from __future__ import annotations

import json
import pathlib
import types

from aico.evals import day07
from aico.evals.dataset import ExpectedSource, GoldenCase, GoldenDataset
from aico.evals.groundedness import GroundednessEvaluation
from aico.platform.errors import GatewayConfigurationError
from aico.platform.model_gateway import CallMetadata, ChatRequest, ChatResult, ModelGateway
from aico.rag.citation_validator import EvidenceChunk

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DATASET_PATH = REPO_ROOT / "evals" / "golden_v1.json"
THRESHOLDS_PATH = REPO_ROOT / "evals" / "thresholds_v1.json"
BASELINE_PATH = REPO_ROOT / "evals" / "baseline_v1.json"
INDEX_DIR = REPO_ROOT / "data" / "index"


class FakeLiveGateway:
    """Duck-typed ModelGateway stand-in - shaped like a real one
    (`.chat()` + `.config`) so `groundedness_evaluator_alias`/
    `evaluate_groundedness` can read both, but backed by a fixed scripted
    response, never a network call."""

    def __init__(self, respond: str, *, provider="microsoft-foundry", region="uk-south", chat_alias="gpt-4.1-mini"):
        self._respond = respond
        self.calls: list[ChatRequest] = []
        self.config = types.SimpleNamespace(
            routing=types.SimpleNamespace(primary=types.SimpleNamespace(provider=provider, region=region)),
            models=types.SimpleNamespace(chat=chat_alias),
        )

    def chat(self, request: ChatRequest) -> ChatResult:
        self.calls.append(request)
        return ChatResult(
            content=self._respond,
            metadata=CallMetadata(
                operation="chat", model_alias="gpt-4.1-mini", latency_ms=42.0, retry_count=0,
                token_usage={"prompt_tokens": 50, "completion_tokens": 20}, budget_status="within_budget",
            ),
        )


GROUNDED_VERDICT_JSON = json.dumps({
    "grounded": True, "critical_facts_covered": [], "critical_facts_missing": [],
    "prohibited_claims_present": [], "confidence": "high", "reasoning": "supported by the retrieved evidence",
})


def _one_case_dataset() -> tuple[GoldenDataset, list[EvidenceChunk]]:
    chunk = EvidenceChunk(chunk_id="C1", source_file="DOC-999-x.md", text="the widget costs ten pounds")
    case = GoldenCase(
        case_id="LG-001", category="answerable", split="train", question="How much does the widget cost?",
        expected_sources=(ExpectedSource(doc_id="DOC-999", anchor="widget costs ten pounds"),),
        answerability="answerable", critical_facts=("The widget costs ten pounds",), prohibited_claims=(),
    )
    dataset = GoldenDataset(version="1.0", dataset_id="test", corpus=("DOC-999",), matching_rule="substring", cases=(case,), raw={})
    return dataset, [chunk]


# ── Unit level: evaluate_all_cases actually uses the injected gateway ────

def test_evaluate_all_cases_uses_the_provided_groundedness_gateway_instead_of_the_scripted_stand_in():
    dataset, chunks = _one_case_dataset()
    gateway = FakeLiveGateway(GROUNDED_VERDICT_JSON)

    evaluations = day07.evaluate_all_cases(
        dataset, retriever=lambda _q: chunks, all_chunks=chunks, top_k=5, groundedness_gateway=gateway,
    )

    assert len(gateway.calls) == 1, "the live gateway, not a per-case ScriptedGateway, must have been called"
    (evaluation,) = evaluations
    assert isinstance(evaluation.groundedness, GroundednessEvaluation)
    assert evaluation.groundedness.evaluator_model_alias == "gpt-4.1-mini"


def test_evaluate_all_cases_defaults_to_the_scripted_stand_in_when_no_gateway_is_given():
    dataset, chunks = _one_case_dataset()

    evaluations = day07.evaluate_all_cases(dataset, retriever=lambda _q: chunks, all_chunks=chunks, top_k=5)

    (evaluation,) = evaluations
    assert isinstance(evaluation.groundedness, GroundednessEvaluation)
    assert evaluation.groundedness.evaluator_model_alias == "day07-well-behaved-grader"


# ── Alias reporting ───────────────────────────────────────────────────

def test_groundedness_evaluator_alias_labels_scripted_stand_in_honestly():
    alias = day07.groundedness_evaluator_alias(None)
    assert alias.startswith("fake:")


def test_groundedness_evaluator_alias_labels_a_live_gateway_with_its_real_deployment_and_region():
    gateway = FakeLiveGateway(GROUNDED_VERDICT_JSON, provider="microsoft-foundry", region="uk-south", chat_alias="gpt-4.1-mini")
    alias = day07.groundedness_evaluator_alias(gateway)
    assert not alias.startswith("fake:")
    assert "microsoft-foundry" in alias and "gpt-4.1-mini" in alias and "uk-south" in alias


def test_model_aliases_for_run_reports_system_under_test_and_evaluator_independently():
    live = FakeLiveGateway(GROUNDED_VERDICT_JSON)

    scripted_aliases = day07.model_aliases_for_run(None)
    live_aliases = day07.model_aliases_for_run(live)

    # The system-under-test's own answers stay scripted in both modes -
    # only the groundedness evaluator role can differ.
    assert scripted_aliases["chat_system_under_test"] == live_aliases["chat_system_under_test"]
    assert scripted_aliases["chat_groundedness_evaluator"].startswith("fake:")
    assert not live_aliases["chat_groundedness_evaluator"].startswith("fake:")


# ── CLI level: --groundedness-gateway live, still fully offline via monkeypatch ──

def _argv(artifacts_dir, **extra):
    argv = [
        "--dataset", str(DATASET_PATH), "--thresholds", str(THRESHOLDS_PATH), "--baseline", str(BASELINE_PATH),
        "--index", str(INDEX_DIR), "--artifacts-dir", str(artifacts_dir),
    ]
    for flag, value in extra.items():
        argv += [f"--{flag.replace('_', '-')}", str(value)] if value is not True else [f"--{flag.replace('_', '-')}"]
    return argv


def test_groundedness_gateway_defaults_to_scripted(tmp_path):
    exit_code = day07.main(_argv(tmp_path / "artifacts"))
    report = json.loads((tmp_path / "artifacts" / "evaluation_report.json").read_text(encoding="utf-8"))
    assert report["model_based_metrics"]["evaluator_gateway"] == "scripted"
    assert exit_code in (0, 1)  # gate verdict isn't this test's concern, only which gateway graded it


def test_groundedness_gateway_live_without_working_config_fails_loudly(tmp_path, monkeypatch):
    def _raise(cls):
        raise GatewayConfigurationError("AICO_FOUNDRY_ENDPOINT is not set")

    monkeypatch.setattr(ModelGateway, "from_config", classmethod(_raise))

    artifacts_dir = tmp_path / "artifacts"
    exit_code = day07.main(_argv(artifacts_dir, groundedness_gateway="live"))

    assert exit_code == 2
    assert not (artifacts_dir / "evaluation_report.json").exists(), (
        "a broken --groundedness-gateway live request must never fall back to a scripted, "
        "silently-different run that still writes reports"
    )


def test_groundedness_gateway_live_wires_a_real_gateway_end_to_end(tmp_path, monkeypatch):
    fake_gateway = FakeLiveGateway(GROUNDED_VERDICT_JSON)
    monkeypatch.setattr(ModelGateway, "from_config", classmethod(lambda cls: fake_gateway))

    artifacts_dir = tmp_path / "artifacts"
    day07.main(_argv(artifacts_dir, groundedness_gateway="live"))

    report = json.loads((artifacts_dir / "evaluation_report.json").read_text(encoding="utf-8"))
    assert report["model_based_metrics"]["evaluator_gateway"] == "live"
    # evaluator_model_aliases reports the real per-call metadata
    # (ChatResult.metadata.model_alias) observed from the injected gateway -
    # distinct from groundedness_evaluator_alias()'s descriptive
    # "provider/model (live, region=...)" string used for baseline reporting.
    assert report["model_based_metrics"]["evaluator_model_aliases"] == ["gpt-4.1-mini"]
    # The 21 real GroundedAnswer cases in golden_v1.json each triggered one
    # real call to the injected gateway - proving this isn't a no-op flag.
    assert len(fake_gateway.calls) > 0


def test_update_baseline_live_records_the_real_evaluator_alias_not_a_fake_one(tmp_path, monkeypatch):
    fake_gateway = FakeLiveGateway(GROUNDED_VERDICT_JSON)
    monkeypatch.setattr(ModelGateway, "from_config", classmethod(lambda cls: fake_gateway))

    baseline_path = tmp_path / "baseline_v1.json"
    argv = [
        "--dataset", str(DATASET_PATH), "--index", str(INDEX_DIR), "--baseline", str(baseline_path),
        "--update-baseline", "--groundedness-gateway", "live",
        "--reviewer", "test@example.com", "--notes", "live groundedness test", "--confirm",
    ]
    exit_code = day07.main(argv)

    assert exit_code == 0
    written = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert written["model_aliases"]["chat_groundedness_evaluator"] == "microsoft-foundry/gpt-4.1-mini (live, region=uk-south)"
    assert written["model_aliases"]["chat_system_under_test"].startswith("fake:")
