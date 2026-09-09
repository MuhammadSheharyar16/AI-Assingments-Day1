"""
Day 9 Task 12 -- control-plane configuration.

Validated loading of `config/control-plane.yaml`: operational settings
for the control plane (Tasks 1-11) --

    registry path                          -> ontology.registry_path
    enabled lane IDs                       -> lanes.enabled
    clarification policy                   -> clarification
    optional model-assisted interpretation -> model_assisted_interpretation
    classification timeout/budget          -> model_assisted_interpretation
                                               (only meaningful "if model
                                               assistance exists" - Task 12's
                                               own wording; see below)

Ontology definitions themselves (domains/concepts/intents, and which
lanes an intent is allowed to route through) are never here -- only a
*path* to the committed registry that actually governs them
(`ontology_requirements.md`; Task 12's own rule: "Ontology definitions
remain in the ontology registry, not scattered config"). No secrets:
every field is operational policy, matching `aico.platform.config`'s
identical rule for `config/model-routing.yaml`.

Reuses `aico.platform.config`'s tiny YAML-subset parser
(`_parse_simple_yaml`) and its typed-scalar helpers rather than a second
copy of them -- pure, stateless utilities with no Model-Gateway-specific
behavior, so importing them here pulls in no network/credential code.
Those helpers raise `aico.platform.errors.GatewayConfigurationError` (a
platform-specific type unrelated to this module's own errors);
`load_control_plane_config` catches it and re-raises as
`ControlPlaneConfigurationError`, so a caller of this module only ever
needs to handle one exception type, never `aico.platform`'s.

`enabled_lanes` (`ControlPlaneConfig.enabled_lanes`) is meant to be handed
to `LaneSelector(registry, enabled_lanes=...)` (Task 5/12): a
deployment-level restriction that can only ever narrow what a request may
route to, never widen what a governed intent's own `Intent.allowed_lanes`
(Task 1) already permits -- see `lane_selector.py`'s module docstring.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from aico.control.errors import ControlPlaneConfigurationError
from aico.control.ontology import LaneId
from aico.platform.config import (
    _parse_simple_yaml,
    _require,
    _require_bool,
    _require_positive_int,
    _require_positive_number,
)
from aico.platform.errors import GatewayConfigurationError

DEFAULT_CONTROL_PLANE_CONFIG_PATH = Path("config/control-plane.yaml")


@dataclass(frozen=True)
class ClarificationPolicy:
    """Task 6 policy values the clarification-question generator is
    built around (`gate_a.py`'s `_build_clarification_question` /
    `_MIN_OVERLAP_SCORE`). Recorded here as the governing policy this
    build's hard-coded values match today -- see `config.py`'s own
    module docstring for why nothing currently reads these back."""

    max_candidate_intents: int
    min_overlap_score: int


@dataclass(frozen=True)
class ModelAssistedInterpretationConfig:
    """Task 4's optional model-assisted interpreter settings. `enabled`
    is `false` in the committed config -- this Gate-A is fully
    deterministic (`gate_a.py`'s own module docstring) -- so
    `timeout_seconds`/`max_output_tokens` ("classification timeout/budget
    if model assistance exists", Task 12) are validated but unused today,
    exactly as inert as the assignment's own conditional wording expects."""

    enabled: bool
    timeout_seconds: float
    max_output_tokens: int


@dataclass(frozen=True)
class ControlPlaneConfig:
    """The fully validated, typed contents of `config/control-plane.yaml`."""

    version: str
    registry_path: Path
    enabled_lanes: frozenset[LaneId]
    clarification: ClarificationPolicy
    model_assisted_interpretation: ModelAssistedInterpretationConfig


def _build_config(raw: dict, *, source: Path) -> ControlPlaneConfig:
    def section(d: dict, key: str, path: str) -> dict:
        if key not in d or not isinstance(d[key], dict):
            raise ControlPlaneConfigurationError(f"{source}: missing required config section: {path}.{key}")
        return d[key]

    version = str(_require(raw, "version", "$"))

    ontology_raw = section(raw, "ontology", "$")
    registry_path = Path(str(_require(ontology_raw, "registry_path", "ontology")))

    lanes_raw = section(raw, "lanes", "$")
    enabled_raw = section(lanes_raw, "enabled", "lanes")
    known_lane_ids = {lane.value for lane in LaneId}
    unknown_keys = sorted(set(enabled_raw) - known_lane_ids)
    if unknown_keys:
        raise ControlPlaneConfigurationError(f"{source}: lanes.enabled has unknown lane id(s): {unknown_keys}")
    enabled_lanes = frozenset(
        lane for lane in LaneId if _require_bool(enabled_raw, lane.value, "lanes.enabled")
    )

    clarification_raw = section(raw, "clarification", "$")
    clarification = ClarificationPolicy(
        max_candidate_intents=_require_positive_int(clarification_raw, "max_candidate_intents", "clarification"),
        min_overlap_score=_require_positive_int(clarification_raw, "min_overlap_score", "clarification"),
    )

    interp_raw = section(raw, "model_assisted_interpretation", "$")
    interp_enabled = _require_bool(interp_raw, "enabled", "model_assisted_interpretation")
    # Only validated as strictly required when the interpreter is
    # actually turned on -- mirrors config/model-routing.yaml's own rule
    # for its optional fallback route (`platform/config.py`'s
    # `_build_config`: "only validated ... when fallback is actually
    # turned on").
    if interp_enabled:
        timeout_seconds = _require_positive_number(interp_raw, "timeout_seconds", "model_assisted_interpretation")
        max_output_tokens = _require_positive_int(interp_raw, "max_output_tokens", "model_assisted_interpretation")
    else:
        timeout_seconds = float(interp_raw.get("timeout_seconds") or 0.0)
        max_output_tokens = int(interp_raw.get("max_output_tokens") or 0)
    model_assisted_interpretation = ModelAssistedInterpretationConfig(
        enabled=interp_enabled,
        timeout_seconds=timeout_seconds,
        max_output_tokens=max_output_tokens,
    )

    return ControlPlaneConfig(
        version=version,
        registry_path=registry_path,
        enabled_lanes=enabled_lanes,
        clarification=clarification,
        model_assisted_interpretation=model_assisted_interpretation,
    )


def load_control_plane_config(path: str | Path = DEFAULT_CONTROL_PLANE_CONFIG_PATH) -> ControlPlaneConfig:
    """Load and validate `config/control-plane.yaml`. Raises
    `ControlPlaneConfigurationError` for anything wrong with the file
    itself -- missing, unreadable, malformed, or a missing/invalid
    required key -- never a silent fallback to a default/permissive
    configuration (no lane is ever treated as enabled unless the file
    itself says so)."""
    resolved = Path(path)
    if not resolved.exists():
        raise ControlPlaneConfigurationError(f"control-plane configuration not found: {resolved}")
    text = resolved.read_text(encoding="utf-8")
    try:
        raw = _parse_simple_yaml(text)
        return _build_config(raw, source=resolved)
    except GatewayConfigurationError as exc:
        # Translate the reused helpers' platform-specific exception type
        # into this module's own -- see module docstring.
        raise ControlPlaneConfigurationError(str(exc)) from exc
