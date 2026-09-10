"""
Day 9 Task 3/5/6 -- shared control-plane decision models.
Day 10 Task 3 -- adds Gate-B's typed decision.

`OntologyDocument`/`OntologyRegistry` (Tasks 1/2) define what is governed;
this module defines what a *decision* about a request looks like once it
has been checked against that governance. Three decisions live here:

- `GateADecision` (Day 9 Task 3, `gate_a.py`) -- Gate-A's typed intent/domain
  classification, made before lane selection ever runs.
- `LaneDecision` (Day 9 Task 5, `lane_selector.py`) -- the lane selector's
  typed routing decision, made *from* a `GateADecision`.
- `GateBDecision` (Day 10 Task 3, `gate_b.py`) -- Gate-B's typed
  authorization/disclosure decision, made *from* a `TrustedIdentity`, a
  `GateADecision` and a `LaneDecision` -- "is this trusted caller allowed
  to do that, for which tenant/data scope, with what disclosed" (Day 10
  assignment).

Kept separate from both `ontology.py`/`policy_models.py` (governed *data*)
and `gate_a.py`/`lane_selector.py`/`gate_b.py` (the *logic* that produces
these decisions), so a later stage (Day 9 Task 9's API integration, Day 9
Task 11's observability layer, Day 10 Task 13's routing integration) can
import the decision shapes without importing the classification/routing/
authorization logic that builds them.

All three models set `extra="forbid"`. `GateADecision`/`LaneDecision` carry
`ontology_version` + `reason_code` (Day 9 working rule: "Route decisions
include ontology version and reason"); `GateBDecision` carries
`policy_version` + `reason_code` (Day 10 working rule: "Every Gate-B
decision must include the policy version and matched rule ID when
applicable"). None of the three is ever constructed with a
`domain`/`intent_id`/`lane`/`role_id`/`rule_id` value that did not come from
a governed `OntologyRegistry`/`PolicyRegistry` lookup -- these types
describe the *shape* of a decision; `gate_a.py`/`lane_selector.py`/
`gate_b.py` own making that guarantee true.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from aico.control.ontology import LaneId
from aico.control.policy_models import DataClassification, PiiCategory


class GateAStatus(str, Enum):
    """The four required Gate-A outcomes (Day 9 Task 3). Deliberately not
    extensible at the type level -- a fifth status would need a registry
    change to `ontology.py`'s `LaneId`-style enums plus a matching
    `lane_selector.py` policy update, never an ad hoc string."""

    MATCHED = "matched"
    AMBIGUOUS = "ambiguous"
    UNSUPPORTED = "unsupported"
    BLOCKED = "blocked"


class GateADecision(BaseModel):
    """Gate-A's typed classification of one request (Task 3's required
    field list, `status`/`domain`/`intent_id`/`matched_concepts`/
    `reason_code`/`ontology_version`, plus `candidate_intents` -- see
    below). Gate-A itself (`gate_a.py`) is the only thing that constructs
    one; nothing downstream is permitted to synthesize a `GateADecision`
    from a raw dict or hand-picked field values (Day 9 working rule:
    "Model output cannot create a new ontology entry, intent, lane or
    policy").

    `intent_id`/`domain` are populated only for `MATCHED` (exactly one
    governed intent, in exactly one governed domain) -- `None` for every
    other status, including `AMBIGUOUS`, where by definition no single
    intent/domain has been chosen yet.

    `candidate_intents` is an extension beyond the assignment's minimum
    field list, populated only for `AMBIGUOUS`: the governed intent ids a
    request plausibly matches (`ambiguity_cases.json`'s own
    `possible_governed_intents`), when there are specific competing
    intents to name. Can be empty even for `AMBIGUOUS` -- an input with no
    governed content to disambiguate *among* at all (e.g. a bare dangling
    reference, `ambiguity_cases.json` AMB-002, "What about it?") is still
    ambiguous (it needs clarification, it is not simply out of scope), it
    just has nothing specific to list yet. Empty for every other status.

    `clarification_question` (Task 6) is a second extension, populated
    only for `AMBIGUOUS`: a concise question generated deterministically
    from `candidate_intents`' own governed `Intent.description` text (or,
    when `candidate_intents` is empty, from the registry's governed
    `Domain.name`s) -- never free text a caller invents, and never a call
    to the Model Gateway (`gate_a.py`'s `_build_clarification_question`).
    `None` for every other status.

    `matched_concepts` names every active governed concept the request
    text itself referenced (`gate_a.py`'s "multiple known concepts"
    behavior, Task 4 -- a request can reference more than one governed
    concept while still resolving, or failing to resolve, to a single
    intent). Populated for `MATCHED`/`AMBIGUOUS`; empty for
    `UNSUPPORTED`/`BLOCKED`, where by definition nothing governed was
    recognized (or the request never reached classification at all)."""

    model_config = ConfigDict(extra="forbid")

    status: GateAStatus
    domain: str | None = Field(default=None, description="domain_id, populated only when status is MATCHED.")
    intent_id: str | None = Field(default=None, description="intent_id, populated only when status is MATCHED.")
    matched_concepts: list[str] = Field(
        default_factory=list, description="concept_ids that drove this decision (see class docstring)."
    )
    candidate_intents: list[str] = Field(
        default_factory=list,
        description="intent_ids this request plausibly matches, populated only when status is AMBIGUOUS.",
    )
    clarification_question: str | None = Field(
        default=None,
        min_length=1,
        description="Deterministically generated clarification question, populated only when status is AMBIGUOUS.",
    )
    reason_code: str = Field(min_length=1, description="Short, sanitized, machine-checkable reason for this decision.")
    ontology_version: str = Field(min_length=1, description="The governed ontology version this decision was made against.")


class LaneDecision(BaseModel):
    """The lane selector's typed routing decision (Task 5's required field
    list, `lane`/`intent_id`/`domain`/`reason_code`/`ontology_version`).
    `LaneSelector` (`lane_selector.py`) is the only thing that constructs
    one, always *from* a `GateADecision` -- never from a raw dict or an
    arbitrary lane string (`lane_policy.md`: "no arbitrary lane strings").

    `lane` is always one of the five governed `LaneId` values, and for a
    request that reached a specific governed intent, always one of that
    intent's own `Intent.allowed_lanes` (Task 1) -- the lane selector
    picks among what the intent itself is governed to allow, it never
    invents a route. `intent_id`/`domain` mirror the `GateADecision` this
    decision was made from: both `None` unless the underlying Gate-A
    status was `MATCHED`."""

    model_config = ConfigDict(extra="forbid")

    lane: LaneId
    intent_id: str | None = Field(default=None, description="intent_id this lane was selected for, when one exists.")
    domain: str | None = Field(default=None, description="domain_id this lane was selected for, when one exists.")
    reason_code: str = Field(min_length=1, description="Short, sanitized, machine-checkable reason for this decision.")
    ontology_version: str = Field(min_length=1, description="The governed ontology version this decision was made against.")


class GateBStatus(str, Enum):
    """The three required Gate-B outcomes (Day 10 Task 3). Deliberately
    not extensible at the type level -- a fourth status would need a
    policy/registry change, never an ad hoc string. `ALLOW` is the only
    status that ever carries a non-empty `effective_*`
    scope/`disclosure_profile` -- `CLARIFY`/`DENY` never grant anything
    (Day 10 working rule: "deny-by-default", "effective scope... may
    never widen any input scope"): least privilege means nothing is
    granted until a decision actually resolves to `ALLOW`."""

    ALLOW = "allow"
    CLARIFY = "clarify"
    DENY = "deny"


class GateBDecision(BaseModel):
    """Gate-B's typed authorization/disclosure decision (Task 3's required
    field list: `decision` / `effective_tenant_scope` /
    `effective_data_classes` / `effective_pii_policy` /
    `disclosure_profile` / `rule_id` / `reason_code` / `policy_version`,
    plus `role_id`/`intent_id`/`lane` -- an extension beyond the minimum
    list, the same way `LaneDecision` adds `intent_id`/`domain` beyond
    *its* minimum list, kept for the same reason: full decision
    provenance (Day 10 Task 14) without a caller needing to re-derive
    which role/intent/lane this decision was actually made for from the
    inputs it was given). `GateB` (`gate_b.py`) is the only thing that
    constructs one -- nothing downstream is permitted to synthesize a
    `GateBDecision` from a raw dict or hand-picked field values (Day 10
    working rule: "Permission scope is never repaired/widened by an
    LLM").

    `effective_tenant_scope`, `effective_data_classes` and
    `effective_pii_policy` are each the *narrowed intersection* Task 5
    defines (`requested scope INTERSECT trusted identity scope INTERSECT
    policy rule scope`) -- never a value wider than what the matched,
    `allowed=true` `PermissionRule` itself declares, and always empty
    unless `decision` is `ALLOW` (see `GateBStatus`'s docstring).
    `effective_pii_policy` is named to match Task 3's own required field
    name; what it actually holds is the bounded set of `PiiCategory`
    values this decision authorizes disclosure to even consider for --
    the per-field allow/redact/deny mapping itself is `disclosure_profile`
    (a `policy_registry.py`-governed `profile_id`) applied by Task 9's
    `disclosure.py`, not reproduced here.

    `rule_id` is populated whenever a `PermissionRule` was actually
    matched, even for a `DENY` (e.g. a matched rule with `allowed=false`,
    or one denied on tenant/classification grounds) -- distinct from `None`,
    which means no rule matched this role/intent/lane combination at all.
    `disclosure_profile` is populated only for `ALLOW` -- see above."""

    model_config = ConfigDict(extra="forbid")

    decision: GateBStatus
    effective_tenant_scope: tuple[str, ...] = Field(
        default_factory=tuple, description="Tenant ids this decision is bounded to. Empty unless decision is ALLOW."
    )
    effective_data_classes: tuple[DataClassification, ...] = Field(
        default_factory=tuple,
        description="Data classifications this decision authorizes. Empty unless decision is ALLOW.",
    )
    effective_pii_policy: tuple[PiiCategory, ...] = Field(
        default_factory=tuple,
        description="PII categories this decision authorizes disclosure to consider. Empty unless decision is ALLOW.",
    )
    disclosure_profile: str | None = Field(
        default=None, description="Governed disclosure profile_id to apply. Populated only for ALLOW."
    )
    role_id: str | None = Field(default=None, description="Trusted role_id this decision was evaluated for, when known.")
    intent_id: str | None = Field(default=None, description="Governed intent_id this decision was evaluated for, when known.")
    lane: LaneId | None = Field(default=None, description="Governed lane this decision was evaluated for, when known.")
    rule_id: str | None = Field(
        default=None, description="Matched PermissionRule.rule_id, when a rule matched this combination at all."
    )
    reason_code: str = Field(min_length=1, description="Short, sanitized, machine-checkable reason for this decision.")
    policy_version: str = Field(min_length=1, description="The governed Gate-B policy version this decision was made against.")
