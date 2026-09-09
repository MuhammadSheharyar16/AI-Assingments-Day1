"""
Day 9 control-plane boundary. `src/aico/control/` is where every request is
interpreted against governed Mode-A definitions before the system decides
how it may proceed (`day09_pack/README.md`: "Mode A defines governed meaning
and control").

Task 1 exports the typed ontology model (`ontology.py`): a governed Mode-A
document is `OntologyDocument`, made of `Domain` / `Concept` / `Intent`
records and the closed `LaneId` set, self-validating against every rule in
`day09_pack/ontology_requirements.md`. Task 2 adds `OntologyRegistry`
(`ontology_registry.py`), the read-only, typed loader/lookup service Gate-A
and the lane selector are built against, plus its typed failures
(`errors.py`). Task 3 adds `GateA` (`gate_a.py`) and its typed result,
`GateADecision`/`GateAStatus` (`models.py`) -- deterministic domain/intent
classification, run before lane selection. Task 5 adds `LaneSelector`
(`lane_selector.py`) and its typed result, `LaneDecision` (`models.py`) --
routing a `GateADecision` onto one of the five governed lanes. Task 12
adds `load_control_plane_config`/`ControlPlaneConfig` (`config.py`),
validated loading of `config/control-plane.yaml` -- registry path, the
deployment-level `enabled_lanes` restriction `LaneSelector` accepts,
clarification policy, and (inert today) model-assisted-interpretation
settings.
"""
from aico.control.config import (
    DEFAULT_CONTROL_PLANE_CONFIG_PATH,
    ClarificationPolicy,
    ControlPlaneConfig,
    ModelAssistedInterpretationConfig,
    load_control_plane_config,
)
from aico.control.errors import (
    ControlPlaneConfigurationError,
    LaneSelectionError,
    OntologyLoadError,
    OntologyLookupError,
    OntologyRegistryError,
)
from aico.control.gate_a import GateA
from aico.control.lane_selector import LaneSelector
from aico.control.models import GateADecision, GateAStatus, LaneDecision
from aico.control.ontology import (
    Concept,
    Domain,
    Intent,
    LaneId,
    LifecycleStatus,
    OntologyDocument,
)
from aico.control.ontology_registry import DEFAULT_REGISTRY_PATH, OntologyRegistry

__all__ = [
    "Concept",
    "Domain",
    "Intent",
    "LaneId",
    "LifecycleStatus",
    "OntologyDocument",
    "OntologyRegistryError",
    "OntologyLoadError",
    "OntologyLookupError",
    "OntologyRegistry",
    "DEFAULT_REGISTRY_PATH",
    "GateA",
    "GateADecision",
    "GateAStatus",
    "LaneSelector",
    "LaneDecision",
    "LaneSelectionError",
    "ControlPlaneConfig",
    "ClarificationPolicy",
    "ModelAssistedInterpretationConfig",
    "load_control_plane_config",
    "ControlPlaneConfigurationError",
    "DEFAULT_CONTROL_PLANE_CONFIG_PATH",
]
