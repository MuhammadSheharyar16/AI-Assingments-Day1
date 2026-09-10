"""
Day 9 Task 12 -- control-plane configuration
(`config/control-plane.yaml`, `src/aico/control/config.py`).

Proves:
  - the committed `config/control-plane.yaml` loads into a typed
    `ControlPlaneConfig` (registry path, all five lanes enabled by
    default, clarification policy, model-assisted-interpretation
    settings that are present but inert since `enabled: false`);
  - every required-key/invalid-value failure mode is rejected --
    missing file, missing section, an unknown lane id under
    `lanes.enabled`, a non-boolean enabled flag, a non-positive
    clarification value -- via `ControlPlaneConfigurationError`, never a
    silent default;
  - `model_assisted_interpretation.timeout_seconds`/`max_output_tokens`
    are validated as strictly required only when `enabled: true` (same
    "only validate what is actually turned on" rule
    `config/model-routing.yaml`'s optional fallback route already uses);
  - `ControlPlaneConfig.enabled_lanes`, handed to `LaneSelector` (Task 5)
    and to `ControlPlaneAnswerService` (Task 9), can only ever NARROW
    lane routing -- disabling a lane an intent is otherwise allowed to
    use falls back to `block`, and `block` itself can never be disabled
    away, and a genuinely unsupported/blocked decision's real reason is
    never overwritten just because a narrow config omits `block`;
  - no ontology data (concepts/intents/domains) is ever read from this
    config file -- only a path to where it actually lives.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from aico.control.config import (
    DEFAULT_CONTROL_PLANE_CONFIG_PATH,
    ControlPlaneConfig,
    load_control_plane_config,
)
from aico.control.errors import ControlPlaneConfigurationError
from aico.control.gate_a import GateA
from aico.control.lane_selector import LaneSelector
from aico.control.models import GateAStatus
from aico.control.ontology import LaneId
from aico.control.ontology_registry import OntologyRegistry
from aico.rag.answer_service import GroundedAnswerService
from aico.rag.control_plane_answer_service import ControlPlaneAnswerService, GateBlocked, ModeBSelected

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMITTED_CONFIG_PATH = REPO_ROOT / "config" / "control-plane.yaml"


def _valid_config_text() -> str:
    return """
version: "1.0"

ontology:
  registry_path: ontology/registry.v1.json

lanes:
  enabled:
    rag: true
    mode_b: true
    clarify: true
    block: true
    safe_fast_path: true

clarification:
  max_candidate_intents: 5
  min_overlap_score: 2

model_assisted_interpretation:
  enabled: false
  timeout_seconds: 5.0
  max_output_tokens: 200

gate_b:
  enabled: false
  policy_path: policy/gate_b_policy.v1.json
""".strip()


class _NeverCalledGateway:
    def chat(self, request):  # pragma: no cover - only reached on failure
        raise AssertionError("Model Gateway must not be called")


def _never_called_retriever(query):  # pragma: no cover - only reached on failure
    raise AssertionError("retrieval must not be called")


@pytest.fixture(scope="module")
def real_registry() -> OntologyRegistry:
    return OntologyRegistry.load()


# ---------------------------------------------------------------------------
# Loading the real committed file
# ---------------------------------------------------------------------------


def test_default_config_path_points_at_the_committed_file():
    assert DEFAULT_CONTROL_PLANE_CONFIG_PATH == Path("config/control-plane.yaml")


def test_load_reads_the_real_committed_config():
    config = load_control_plane_config()

    assert isinstance(config, ControlPlaneConfig)
    assert config.version == "1.0"
    assert config.registry_path == Path("ontology/registry.v1.json")
    assert config.enabled_lanes == frozenset(LaneId)
    assert config.clarification.max_candidate_intents > 0
    assert config.clarification.min_overlap_score > 0
    assert config.model_assisted_interpretation.enabled is False
    assert config.gate_b.enabled is False
    assert config.gate_b.policy_path == Path("policy/gate_b_policy.v1.json")


def test_load_missing_gate_b_section_raises(tmp_path):
    path = tmp_path / "control-plane.yaml"
    text = _valid_config_text().replace("\n\ngate_b:\n  enabled: false\n  policy_path: policy/gate_b_policy.v1.json", "")
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ControlPlaneConfigurationError, match="gate_b"):
        load_control_plane_config(path)


def test_gate_b_enabled_true_loads(tmp_path):
    path = tmp_path / "control-plane.yaml"
    text = _valid_config_text().replace("gate_b:\n  enabled: false", "gate_b:\n  enabled: true")
    path.write_text(text, encoding="utf-8")
    config = load_control_plane_config(path)
    assert config.gate_b.enabled is True


def test_load_accepts_an_explicit_path(tmp_path):
    explicit_path = tmp_path / "control-plane.yaml"
    explicit_path.write_text(_valid_config_text(), encoding="utf-8")

    config = load_control_plane_config(explicit_path)

    assert config.registry_path == Path("ontology/registry.v1.json")


def test_registry_path_from_config_actually_loads_the_real_registry():
    config = load_control_plane_config()
    registry = OntologyRegistry.load(config.registry_path)
    assert registry.ontology_version == "1.0"


# ---------------------------------------------------------------------------
# No ontology data lives in this config -- only a path
# ---------------------------------------------------------------------------


def test_config_carries_no_ontology_data_of_its_own():
    config = load_control_plane_config()
    config_fields = vars(config)
    # Nothing resembling a concept/intent/domain id or governed phrase --
    # only the path, the lane toggle, and plain policy numbers/booleans.
    assert not any(hasattr(config, name) for name in ("concepts", "intents", "domains", "phrases"))
    assert isinstance(config_fields["registry_path"], Path)


# ---------------------------------------------------------------------------
# Required validation
# ---------------------------------------------------------------------------


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(ControlPlaneConfigurationError, match="not found"):
        load_control_plane_config(tmp_path / "does-not-exist.yaml")


def test_load_missing_section_raises(tmp_path):
    path = tmp_path / "control-plane.yaml"
    text = _valid_config_text().replace("clarification:\n  max_candidate_intents: 5\n  min_overlap_score: 2\n\n", "")
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ControlPlaneConfigurationError, match="clarification"):
        load_control_plane_config(path)


def test_load_unknown_lane_id_raises(tmp_path):
    path = tmp_path / "control-plane.yaml"
    text = _valid_config_text().replace("rag: true", "rag: true\n    made_up_lane: true")
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ControlPlaneConfigurationError, match="unknown lane"):
        load_control_plane_config(path)


def test_load_non_boolean_enabled_flag_raises(tmp_path):
    path = tmp_path / "control-plane.yaml"
    text = _valid_config_text().replace("rag: true", "rag: yesplease")
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ControlPlaneConfigurationError):
        load_control_plane_config(path)


def test_load_non_positive_clarification_value_raises(tmp_path):
    path = tmp_path / "control-plane.yaml"
    text = _valid_config_text().replace("min_overlap_score: 2", "min_overlap_score: 0")
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ControlPlaneConfigurationError):
        load_control_plane_config(path)


def test_interpretation_timeout_and_budget_required_only_when_enabled(tmp_path):
    """Disabled (the committed default) -- no timeout/budget required at
    all. Enabled -- both become strictly required, mirroring
    `config/model-routing.yaml`'s own "only validate what is turned on"
    rule for its optional fallback route."""
    path = tmp_path / "control-plane.yaml"

    disabled_no_budget = _valid_config_text().replace(
        "model_assisted_interpretation:\n  enabled: false\n  timeout_seconds: 5.0\n  max_output_tokens: 200",
        "model_assisted_interpretation:\n  enabled: false",
    )
    path.write_text(disabled_no_budget, encoding="utf-8")
    config = load_control_plane_config(path)
    assert config.model_assisted_interpretation.enabled is False

    enabled_no_budget = _valid_config_text().replace(
        "model_assisted_interpretation:\n  enabled: false\n  timeout_seconds: 5.0\n  max_output_tokens: 200",
        "model_assisted_interpretation:\n  enabled: true",
    )
    path.write_text(enabled_no_budget, encoding="utf-8")
    with pytest.raises(ControlPlaneConfigurationError):
        load_control_plane_config(path)

    enabled_with_budget = _valid_config_text().replace("enabled: false", "enabled: true")
    path.write_text(enabled_with_budget, encoding="utf-8")
    config = load_control_plane_config(path)
    assert config.model_assisted_interpretation.enabled is True
    assert config.model_assisted_interpretation.timeout_seconds == 5.0
    assert config.model_assisted_interpretation.max_output_tokens == 200


# ---------------------------------------------------------------------------
# enabled_lanes wired into LaneSelector: narrows, never widens
# ---------------------------------------------------------------------------


def test_enabled_lanes_none_matches_default_behavior(real_registry: OntologyRegistry):
    unrestricted = LaneSelector(real_registry)
    fully_enabled = LaneSelector(real_registry, enabled_lanes=frozenset(LaneId))
    gate = GateA(real_registry)

    for text in ["What are the payment terms?", "List active contracts.", "What can you help with?"]:
        decision = gate.classify(text)
        assert unrestricted.select(decision) == fully_enabled.select(decision)


def test_disabling_mode_b_falls_back_to_block(real_registry: OntologyRegistry):
    gate = GateA(real_registry)
    selector = LaneSelector(real_registry, enabled_lanes=frozenset({LaneId.RAG, LaneId.CLARIFY, LaneId.BLOCK, LaneId.SAFE_FAST_PATH}))

    decision = gate.classify("List active contracts.")  # would normally be mode_b
    lane_decision = selector.select(decision)

    assert lane_decision.lane is LaneId.BLOCK
    assert lane_decision.reason_code == "lane_disabled_by_config"
    assert lane_decision.intent_id is None
    assert lane_decision.domain is None


def test_config_can_never_widen_a_lane_the_registry_does_not_allow(real_registry: OntologyRegistry):
    """`enabled_lanes` including every lane still cannot make an
    unsupported request suddenly route anywhere but `block` -- the gate
    only narrows what the ontology already decided, it has no path to
    grant a new route."""
    gate = GateA(real_registry)
    selector = LaneSelector(real_registry, enabled_lanes=frozenset(LaneId))

    decision = gate.classify("What is tomorrow's weather?")
    assert decision.status is GateAStatus.UNSUPPORTED
    lane_decision = selector.select(decision)
    assert lane_decision.lane is LaneId.BLOCK


def test_block_cannot_be_disabled_and_real_reason_is_preserved(real_registry: OntologyRegistry):
    """A config that omits `block` from its enabled set must not
    reclassify an already-unsupported decision's reason as
    "lane_disabled_by_config" -- `block` is always implicitly allowed."""
    gate = GateA(real_registry)
    selector = LaneSelector(real_registry, enabled_lanes=frozenset({LaneId.RAG}))  # block NOT listed

    decision = gate.classify("What is tomorrow's weather?")
    lane_decision = selector.select(decision)

    assert lane_decision.lane is LaneId.BLOCK
    assert lane_decision.reason_code == "no_governed_match"  # not overwritten


# ---------------------------------------------------------------------------
# Wired into ControlPlaneAnswerService (Task 9)
# ---------------------------------------------------------------------------


def test_control_plane_answer_service_honors_config_lane_restriction(real_registry: OntologyRegistry):
    config = load_control_plane_config()
    narrowed = ControlPlaneConfig(
        version=config.version,
        registry_path=config.registry_path,
        enabled_lanes=frozenset({LaneId.RAG, LaneId.CLARIFY, LaneId.BLOCK, LaneId.SAFE_FAST_PATH}),
        clarification=config.clarification,
        model_assisted_interpretation=config.model_assisted_interpretation,
        gate_b=config.gate_b,
    )
    rag_service = GroundedAnswerService(gateway=_NeverCalledGateway(), retriever=_never_called_retriever)
    service = ControlPlaneAnswerService(registry=real_registry, rag_service=rag_service, control_plane_config=narrowed)

    result = service.answer("List active contracts.")  # would normally be mode_b

    assert isinstance(result, GateBlocked)
    assert result.reason_code == "lane_disabled_by_config"


def test_control_plane_answer_service_without_config_keeps_mode_b(real_registry: OntologyRegistry):
    rag_service = GroundedAnswerService(gateway=_NeverCalledGateway(), retriever=_never_called_retriever)
    service = ControlPlaneAnswerService(registry=real_registry, rag_service=rag_service)  # no control_plane_config

    result = service.answer("List active contracts.")

    assert isinstance(result, ModeBSelected)
