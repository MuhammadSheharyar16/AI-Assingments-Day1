"""
Day 9 -- typed control-plane failures (Tasks 2/5/12).
Day 10 -- typed Gate-B policy failures (Task 2).

`OntologyLoadError`/`OntologyLookupError` are shared by `OntologyRegistry`
(`ontology_registry.py`, Task 2) so a caller can distinguish "the
committed registry file itself is missing/invalid" from "a caller asked
this registry to resolve a domain/concept/intent id it does not govern"
with one `except OntologyLoadError` / `except OntologyLookupError`, the
same pattern Day 8's `SessionError` family uses for the memory boundary
(`memory/errors.py`) and Day 6's `IdentityError` uses for the trust
boundary (`api/identity.py`). `LaneSelectionError` (Task 5) and
`ControlPlaneConfigurationError` (Task 12) are each unrelated to the
registry itself -- see their own docstrings.

`PolicyLoadError`/`PolicyLookupError` (Day 10 Task 2) are the identical
pattern one layer over, for `PolicyRegistry`
(`policy_registry.py`) and the committed `policy/gate_b_policy.v1.json`.
Kept as a distinct pair from `OntologyLoadError`/`OntologyLookupError`
rather than reusing them -- "the ontology registry failed to load" and
"the Gate-B policy failed to load" are different governed resources with
different committed files; conflating their exception types would make a
caller's `except` clause ambiguous about which boundary actually failed.

`FinalResponseEnvelopeError` (Day 12 Task 1) is the identical pattern one
layer over again, for `final_response.py`'s `FinalResponseCandidate` --
mirrors `EvidenceEnvelopeError`'s (`evidence/errors.py`) role for Gate-C's
own typed envelope.

`GateDPolicyLoadError` (Day 12 Task 2) is the identical pattern one layer
over again, for `GateDPolicyRegistry` (`policy_registry.py`) and the
committed `policy/gate_d_policy.v1.json` -- mirrors `PolicyLoadError`'s
role for Gate-B's own policy. No `GateDPolicyLookupError` companion:
unlike Gate-B's `rules`/Gate-C's `intent_requirements`, Gate-D's v1 policy
has no separate collection of independently-identified records to look up
by id -- see `policy_models.py`'s "Day 12 Task 2" section for why."""
from __future__ import annotations


class OntologyRegistryError(Exception):
    """Base class for every typed failure `aico.control` raises. Never
    raised directly -- see `OntologyLoadError` / `OntologyLookupError`."""


class OntologyLoadError(OntologyRegistryError):
    """Raised by `OntologyRegistry.load()` when the committed registry
    file cannot be read, is not valid JSON, or fails `OntologyDocument`'s
    typed validation (Task 1: duplicate ids, dangling relationship/lane
    references, invalid status, missing version, ...). Carries a single
    sanitized message describing the problem -- callers reason about
    "the registry failed to load", never about Pydantic's own exception
    shape, and there is never a silent fallback to an empty/default
    registry."""


class OntologyLookupError(OntologyRegistryError):
    """Raised by `OntologyRegistry.get_domain()` / `get_concept()` /
    `get_intent()` / `resolve_concepts()` when a given id does not exist
    in the loaded, governed registry. Gate-A (Task 3/4) is expected to
    treat this the same way it treats "no governed match" -- fail closed,
    never invent a fallback record for an id the registry does not
    govern."""

    def __init__(self, kind: str, identifier: str):
        self.kind = kind
        self.identifier = identifier
        super().__init__(f"unknown {kind}: {identifier!r}")


class LaneSelectionError(Exception):
    """Raised by `LaneSelector.select()` (Task 5) when the `GateADecision`
    it was given violates an invariant lane selection depends on -- e.g. a
    `MATCHED` decision with no `intent_id`, or an `intent_id` naming a
    governed intent this selector's own registry does not carry.
    `GateA.classify()` (Task 3) never produces such a decision by
    construction; this exists to fail loudly rather than silently
    misroute if some other caller ever constructs or passes a malformed
    `GateADecision` by hand."""


class ControlPlaneConfigurationError(Exception):
    """Raised by `load_control_plane_config()` (`config.py`, Task 12) for
    anything wrong with `config/control-plane.yaml` itself: missing,
    unreadable, not valid (the small YAML subset this reuses from
    `aico.platform.config`), a missing/invalid required key, or an
    `lanes.enabled` entry naming something outside the closed `LaneId`
    set. Mirrors `aico.platform.errors.GatewayConfigurationError`'s
    fail-loud contract for `config/model-routing.yaml` -- there is never a
    silent fallback to a default/permissive control-plane configuration."""


class PolicyRegistryError(Exception):
    """Base class for every typed failure `PolicyRegistry` raises. Never
    raised directly -- see `PolicyLoadError` / `PolicyLookupError`."""


class PolicyLoadError(PolicyRegistryError):
    """Raised by `PolicyRegistry.load()` (Day 10 Task 2) when the
    committed policy file cannot be read, is not valid JSON, fails
    `GateBPolicyDocument`'s typed validation (Task 1: duplicate ids,
    unknown role/permission/classification/PII-category/disclosure-profile
    reference, invalid status, missing version, a rule referencing an
    ontology intent the loaded `OntologyRegistry` does not govern, ...),
    or fails this registry's own integrity check (more than one rule
    governing the same role/intent/lane combination, making rule lookup
    ambiguous). Carries a single sanitized message describing the
    problem; there is never a silent fallback to an empty/default/
    permissive policy."""


class PolicyLookupError(PolicyRegistryError):
    """Raised by `PolicyRegistry.get_role()` / `get_disclosure_profile()`
    / `get_rule()` when a given id does not exist in the loaded, governed
    policy. `GateB` (Task 3) is expected to treat this the same way it
    treats "no matching rule" -- fail closed, never invent a fallback
    record for an id the policy does not govern."""

    def __init__(self, kind: str, identifier: str):
        self.kind = kind
        self.identifier = identifier
        super().__init__(f"unknown {kind}: {identifier!r}")


class FinalResponseEnvelopeError(Exception):
    """Raised by `parse_final_response_candidate()`
    (`final_response.py`, Day 12 Task 1) when a raw candidate final-response
    payload -- the actual candidate the Model Gateway/contract/semantic
    layers produced, never a hand-picked subset of it (working rule: "Do
    not pass raw unchecked dictionaries into Gate-D") -- fails
    `FinalResponseCandidate`'s typed validation: missing response status,
    malformed citation structure, invalid/negative latency or timing
    values, missing control metadata (e.g. `contract_validation_status`/
    `semantic_validation_status`), or an unknown final status. Carries one
    sanitized message plus, when Pydantic located it, the offending field's
    path -- a caller reasons about "the final-response envelope failed to
    validate", never about Pydantic's own `ValidationError` shape, the
    identical boundary `EvidenceEnvelopeError` draws for Gate-C's own
    envelope. Never raised with the raw candidate answer text in the
    message -- only field names/paths and Pydantic's own structural error
    text, per the working rule against echoing unsafe/raw content into
    telemetry.

    There is never a silent fallback to a partially-validated envelope or
    an unchecked dict standing in for one -- a payload that fails this
    validation simply does not become a `FinalResponseCandidate`, and
    nothing downstream (Gate-D included) is permitted to construct one by
    hand from raw data instead."""

    def __init__(self, message: str, *, field_path: str | None = None):
        self.field_path = field_path
        super().__init__(message)


class GateDPolicyLoadError(Exception):
    """Raised by `GateDPolicyRegistry.load()` (`policy_registry.py`, Day 12
    Task 2) when the committed `policy/gate_d_policy.v1.json` cannot be
    read, is not valid JSON, or fails `GateDPolicyDocument`'s typed
    validation (missing/blank `policy_version`, a duplicate
    `allowed_response_statuses` entry, an unrecognized response status, a
    non-positive `max_answer_chars`/latency budget, an invalid `status`
    enum, or any other malformed shape -- see `policy_models.py`'s "Day 12
    Task 2" section for the full mapping). Mirrors `PolicyLoadError`'s/
    `GateCPolicyLoadError`'s fail-loud contract -- there is never a silent
    fallback to an empty/default/permissive Gate-D policy."""


class GateBError(Exception):
    """Raised by `GateB.authorize()` (Day 10 Task 3) when it is given
    inputs that violate an invariant it depends on -- e.g. a `MATCHED`
    `GateADecision` with no `intent_id`, or a `PermissionRule` returned by
    `PolicyRegistry.find_rule()` naming a `disclosure_profile` the same
    registry does not actually govern. `GateA.classify()` /
    `PolicyRegistry.load()` never produce such inputs by construction;
    this exists to fail loudly rather than silently misauthorize if some
    other caller ever constructs or passes a malformed decision/registry
    by hand -- the identical role `LaneSelectionError` plays for
    `LaneSelector`. Never raised for an ordinary authorization outcome
    (allow/clarify/deny are all normal, typed `GateBDecision` results, not
    exceptions)."""
