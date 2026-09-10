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

Day 10 Task 1 adds the typed Gate-B policy model (`policy_models.py`):
`GateBPolicyDocument`, made of `Role` / `DisclosureProfile` /
`PermissionRule` records and the closed `DataClassification` /
`PiiCategory` / `DisclosureAction` / `TenantScopeKind` enums, self-
validating against every rule in `gate_b_policy_requirements.md` /
`disclosure_rules.md`. Task 2 adds `PolicyRegistry` (`policy_registry.py`),
the read-only, typed loader/lookup service Gate-B (Task 3) is built
against -- loading the committed `policy/gate_b_policy.v1.json` through
`GateBPolicyDocument`, cross-checked against the real committed
`OntologyRegistry`'s governed intent ids -- plus its typed failures
(`errors.py`). Task 3 adds `GateB` (`gate_b.py`) and its typed result,
`GateBDecision`/`GateBStatus` (`models.py`) -- the deterministic
authorization/disclosure boundary, run from a trusted identity plus a
`GateADecision`/`LaneDecision`, before any protected evidence access.
Task 7 adds `is_data_classification_permitted()` (`policy_models.py`) --
the single membership check a data classification is ever tested against
an authorized set with; `GateB.authorize()` is its only caller (the
*requested* classification, before a `GateBDecision` exists). Task 8 adds
its PII analog, `is_pii_category_permitted()` (`policy_models.py`) -- used
a second time by Task 9's `disclosure.py`, per protected field, as a
defense-in-depth check against an already-decided
`GateBDecision.effective_pii_policy` -- and `resolve_disclosure_action()`
(`policy_models.py`), the one place a protected field name is resolved to
a governed `DisclosureAction` (fail-closed `DENY` for an undeclared
field), both pure/deterministic and built directly on the committed
policy's own `disclosure_profiles` data. Task 9 adds `mask_value()` and
friends (`redaction.py`) -- deterministic value masking, never a model --
and `apply_disclosure()`/`SafeDisclosureView` (`disclosure.py`), the safe-
disclosure view builder that combines all of the above into the final,
policy-approved output for one `GateBDecision` and its candidate
`ProtectedField`s.
"""
from aico.control.config import (
    DEFAULT_CONTROL_PLANE_CONFIG_PATH,
    ClarificationPolicy,
    ControlPlaneConfig,
    ModelAssistedInterpretationConfig,
    load_control_plane_config,
)
from aico.control.disclosure import DisclosedField, ProtectedField, SafeDisclosureView, apply_disclosure
from aico.control.errors import (
    ControlPlaneConfigurationError,
    GateBError,
    LaneSelectionError,
    OntologyLoadError,
    OntologyLookupError,
    OntologyRegistryError,
    PolicyLoadError,
    PolicyLookupError,
    PolicyRegistryError,
)
from aico.control.gate_a import GateA
from aico.control.gate_b import GateB, GateBRequest
from aico.control.lane_selector import LaneSelector
from aico.control.models import GateADecision, GateAStatus, GateBDecision, GateBStatus, LaneDecision
from aico.control.ontology import (
    Concept,
    Domain,
    Intent,
    LaneId,
    LifecycleStatus,
    OntologyDocument,
)
from aico.control.ontology_registry import DEFAULT_REGISTRY_PATH, OntologyRegistry
from aico.control.policy_models import (
    DataClassification,
    DisclosureAction,
    DisclosureProfile,
    GateBPolicyDocument,
    PermissionRule,
    PiiCategory,
    Role,
    TenantScopeKind,
    is_data_classification_permitted,
    is_pii_category_permitted,
    resolve_disclosure_action,
)
from aico.control.policy_registry import DEFAULT_POLICY_PATH, PolicyRegistry
from aico.control.redaction import mask_email, mask_identifier, mask_phone, mask_value

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
    "GateBPolicyDocument",
    "Role",
    "PermissionRule",
    "DisclosureProfile",
    "DataClassification",
    "PiiCategory",
    "DisclosureAction",
    "TenantScopeKind",
    "PolicyRegistry",
    "DEFAULT_POLICY_PATH",
    "PolicyRegistryError",
    "PolicyLoadError",
    "PolicyLookupError",
    "GateB",
    "GateBRequest",
    "GateBDecision",
    "GateBStatus",
    "GateBError",
    "is_data_classification_permitted",
    "is_pii_category_permitted",
    "resolve_disclosure_action",
    "mask_value",
    "mask_email",
    "mask_phone",
    "mask_identifier",
    "apply_disclosure",
    "ProtectedField",
    "DisclosedField",
    "SafeDisclosureView",
]
