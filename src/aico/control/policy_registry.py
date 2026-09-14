"""
Day 10 Task 2 -- the Gate-B policy registry: loads the committed Gate-B
policy document (Task 1's `GateBPolicyDocument`) and exposes it read-only
for the rest of the control plane.
Day 12 Task 2 -- `GateDPolicyRegistry`, the identical loader/registry
pattern one layer over, for `GateDPolicyDocument`
(`policy_models.py`'s "Day 12 Task 2" section) and the committed
`policy/gate_d_policy.v1.json`. Appended near the end of this file, kept
in this generically-named module for the same reason `GateDPolicyDocument`
itself lives in `policy_models.py` rather than a new file -- see that
class's own docstring.

Responsibilities (`gate_b_policy_requirements.md`; Day 10 assignment,
TASK 2):
    - load `policy/gate_b_policy.v1.json`
    - validate it against typed models
    - expose read-only lookups
    - expose policy version
    - reject invalid policy state

`PolicyRegistry` is the only thing in `aico.control` that ever reads
`policy/gate_b_policy.v1.json` off disk -- the same "one place
deserializes this" convention `OntologyRegistry` already follows for
`ontology/registry.v1.json`. Gate-B (Task 3+) is built against this
class's read-only surface, never against a raw dict or the JSON file
directly.

Read-only at runtime (Day 10 working rules: "Session memory cannot grant
a permission" / "Permission scope is never repaired/widened by an LLM"):
    - `PolicyRegistry` defines no method that writes to the document it
      loaded -- there is no `add_rule()` / `set_role()` / etc.
    - every collection accessor (`roles`, `disclosure_profiles`, `rules`)
      returns an immutable `tuple` built fresh from the loaded document,
      not a reference to a mutable list living inside it -- a caller
      cannot `.append()` its way around the missing mutators.
    - `GateBPolicyDocument` itself is an ordinary (not frozen) Pydantic
      model purely so Task 1's own tests can build/mutate throwaway
      documents in isolation; this module never mutates the one instance
      it loads.

## Ontology cross-reference

`load()` always resolves an `OntologyRegistry` first (the real committed
`ontology/registry.v1.json` by default, or one a caller supplies) and
passes its governed intent ids as `GateBPolicyDocument.model_validate`'s
`known_intent_ids` context -- making Task 1's "unknown ontology intent
rejected" check mandatory in practice for the one policy document that is
ever actually served to the rest of the control plane (see
`policy_models.py`'s "Ontology cross-reference" docstring section for why
that check lives behind an optional context hook rather than a hard
import there).

## Rule lookup

`find_rule(role_id, intent_id, lane)` is the primitive Gate-B (Task 3) is
expected to match a trusted role / governed intent / selected lane
against -- built once at load time into a `dict` keyed by that triple, so
lookup is O(1) and, critically, unambiguous: `load()` rejects a policy
document where more than one rule governs the same role/intent/lane
combination (`PolicyLoadError`), an extension beyond Task 1's own
per-field validation, made here because only the *registry* (which builds
the combined index) can detect it, the same way only `OntologyDocument`
itself -- not any single `Intent` -- can detect a duplicate `intent_id`.
Deterministic permission decisions (Day 10 Task 6) depend on there always
being at most one candidate rule per combination.
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from aico.contracts.models import AnswerStatus
from aico.control.errors import GateDPolicyLoadError, PolicyLoadError, PolicyLookupError
from aico.control.ontology import LaneId
from aico.control.ontology_registry import OntologyRegistry
from aico.control.policy_models import (
    CitationPolicy,
    DisclosureProfile,
    GateBPolicyDocument,
    GateDDisclosurePolicy,
    GateDPolicyDocument,
    LatencyBudgets,
    PermissionRule,
    QualityPolicy,
    Role,
    SafeFailureSpec,
)

# Relative to the process working directory, matching
# `ontology_registry.py`'s `DEFAULT_REGISTRY_PATH` convention -- `uv run`
# always runs from the repository root.
DEFAULT_POLICY_PATH = Path("policy/gate_b_policy.v1.json")
DEFAULT_GATE_D_POLICY_PATH = Path("policy/gate_d_policy.v1.json")


class PolicyRegistry:
    """Read-only, typed access to one loaded Gate-B policy document.

    Construct via `PolicyRegistry.load(...)` in production code (loads
    and validates the committed file, cross-checked against a governed
    ontology); tests may instead build a `GateBPolicyDocument` directly
    and pass it to the plain constructor -- the constructor itself never
    touches disk, it only wraps an already-validated document and builds
    lookup indices over it.
    """

    def __init__(self, document: GateBPolicyDocument):
        self._document = document
        self._roles_by_id: dict[str, Role] = {r.role_id: r for r in document.roles}
        self._disclosure_profiles_by_id: dict[str, DisclosureProfile] = {
            p.profile_id: p for p in document.disclosure_profiles
        }
        self._rules_by_id: dict[str, PermissionRule] = {r.rule_id: r for r in document.rules}

        self._rules_by_combination: dict[tuple[str, str, LaneId], PermissionRule] = {}
        for rule in document.rules:
            key = (rule.role, rule.intent_id, rule.lane)
            existing = self._rules_by_combination.get(key)
            if existing is not None:
                raise PolicyLoadError(
                    f"ambiguous policy: rules {existing.rule_id!r} and {rule.rule_id!r} both govern "
                    f"role={rule.role!r} intent_id={rule.intent_id!r} lane={rule.lane.value!r}"
                )
            self._rules_by_combination[key] = rule

    # ── Loading ──────────────────────────────────────────────────────

    @classmethod
    def load(
        cls,
        path: str | Path = DEFAULT_POLICY_PATH,
        *,
        ontology_registry: OntologyRegistry | None = None,
    ) -> PolicyRegistry:
        """Load and validate the committed policy file at `path` (default
        `policy/gate_b_policy.v1.json` -- committed, read-only governed
        Day 10 data; see `policy/README.md`). Raises `PolicyLoadError` for
        anything wrong with the file itself: missing, unreadable, not
        valid JSON, failing `GateBPolicyDocument`'s typed validation
        (Task 1 -- duplicate ids, unknown role/permission/classification/
        PII-category/disclosure-profile reference, invalid status, missing
        version, a rule referencing an ungoverned ontology intent, ...),
        or failing this registry's own ambiguous-rule-combination check.
        Never falls back to an empty/default/permissive policy.

        `ontology_registry` defaults to `OntologyRegistry.load()` (the
        real committed Mode-A ontology) -- pass an explicit instance only
        to test against a different/throwaway ontology document."""
        if ontology_registry is None:
            ontology_registry = OntologyRegistry.load()

        resolved = Path(path)
        if not resolved.exists():
            raise PolicyLoadError(f"Gate-B policy not found: {resolved}")
        try:
            raw_text = resolved.read_text(encoding="utf-8")
        except OSError as exc:
            raise PolicyLoadError(f"could not read Gate-B policy {resolved}: {exc}") from exc

        try:
            raw_data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise PolicyLoadError(f"Gate-B policy {resolved} is not valid JSON: {exc}") from exc

        known_intent_ids = {intent.intent_id for intent in ontology_registry.intents}
        try:
            document = GateBPolicyDocument.model_validate(raw_data, context={"known_intent_ids": known_intent_ids})
        except ValidationError as exc:
            raise PolicyLoadError(f"Gate-B policy {resolved} failed validation: {exc}") from exc

        return cls(document)

    # ── Active version ───────────────────────────────────────────────

    @property
    def policy_version(self) -> str:
        """The active governed policy version this registry loaded --
        included in every Gate-B decision (Day 10 working rule: "Every
        Gate-B decision must include the policy version and matched rule
        ID when applicable")."""
        return self._document.policy_version

    # ── Read-only collection lookups ─────────────────────────────────

    @property
    def roles(self) -> tuple[Role, ...]:
        """Every governed role, in policy order. A fresh tuple, not a
        reference into the loaded document's own list."""
        return tuple(self._document.roles)

    @property
    def permissions(self) -> tuple[str, ...]:
        """The closed set of permission ids this policy version governs,
        in policy order."""
        return tuple(self._document.permissions)

    @property
    def data_classifications(self) -> tuple[str, ...]:
        """Data classifications enabled for this policy version, in
        policy order."""
        return tuple(c.value for c in self._document.data_classifications)

    @property
    def pii_categories(self) -> tuple[str, ...]:
        """PII categories enabled for this policy version, in policy
        order."""
        return tuple(c.value for c in self._document.pii_categories)

    @property
    def disclosure_profiles(self) -> tuple[DisclosureProfile, ...]:
        """Every governed disclosure profile, in policy order."""
        return tuple(self._document.disclosure_profiles)

    @property
    def rules(self) -> tuple[PermissionRule, ...]:
        """Every governed permission rule, in policy order."""
        return tuple(self._document.rules)

    # ── Resolve by id ────────────────────────────────────────────────

    def has_role(self, role_id: str) -> bool:
        return role_id in self._roles_by_id

    def has_disclosure_profile(self, profile_id: str) -> bool:
        return profile_id in self._disclosure_profiles_by_id

    def has_rule(self, rule_id: str) -> bool:
        return rule_id in self._rules_by_id

    def get_role(self, role_id: str) -> Role:
        """Resolve a governed `role_id` to its typed `Role`. Raises
        `PolicyLookupError` for an id this policy does not govern -- never
        returns `None` or a synthesized default."""
        try:
            return self._roles_by_id[role_id]
        except KeyError:
            raise PolicyLookupError("role", role_id) from None

    def get_disclosure_profile(self, profile_id: str) -> DisclosureProfile:
        """Resolve a governed `profile_id` to its typed `DisclosureProfile`.
        Raises `PolicyLookupError` for an id this policy does not
        govern."""
        try:
            return self._disclosure_profiles_by_id[profile_id]
        except KeyError:
            raise PolicyLookupError("disclosure_profile", profile_id) from None

    def get_rule(self, rule_id: str) -> PermissionRule:
        """Resolve a governed `rule_id` to its typed `PermissionRule`.
        Raises `PolicyLookupError` for an id this policy does not
        govern."""
        try:
            return self._rules_by_id[rule_id]
        except KeyError:
            raise PolicyLookupError("rule", rule_id) from None

    # ── Rule matching ────────────────────────────────────────────────

    def find_rule(self, role_id: str, intent_id: str, lane: LaneId) -> PermissionRule | None:
        """Resolve the single governed `PermissionRule` (if any) matching
        one trusted `role_id` / governed `intent_id` / selected `lane`
        combination -- the primitive `GateB` (Task 3) matches a request
        against. Returns `None` when no rule governs this combination at
        all (Gate-B's deny-by-default behavior, Task 4, applies to that
        case); never guesses or falls back to a partial match. `load()`
        guarantees at most one rule can ever exist per combination, so
        this lookup is always unambiguous."""
        return self._rules_by_combination.get((role_id, intent_id, lane))


class GateDPolicyRegistry:
    """Read-only, typed access to one loaded Gate-D policy document (Day 12
    Task 2).

    Construct via `GateDPolicyRegistry.load(...)` in production code
    (loads and validates the committed file, cross-checked against the
    real Gate-B policy's own governed disclosure profiles); tests may
    instead build a `GateDPolicyDocument` directly and pass it to the
    plain constructor -- the constructor itself never touches disk, it
    only wraps an already-validated document (and, by construction, never
    runs the Gate-B disclosure-profile cross-reference unless a caller
    supplies `known_disclosure_profile_ids` explicitly -- see `load()`).

    Read-only at runtime, the identical guarantee `PolicyRegistry`/
    `GateCPolicyRegistry` already give: no method here ever writes to the
    document it loaded, and every nested policy object is returned exactly
    as loaded (a fresh reference into an already-validated, `extra
    ="forbid"` Pydantic model), never rebuilt from request or model
    output."""

    def __init__(self, document: GateDPolicyDocument, *, known_disclosure_profile_ids: frozenset[str] = frozenset()):
        self._document = document
        # Day 12 Task 2's "unknown disclosure profile/rule reference
        # rejected" -- see `policy_models.py`'s own mapping for why this
        # is a registry-level cross-reference rather than a document
        # field: `disclosure_policy` itself names no specific profile id,
        # a *candidate*'s own `gate_b_disclosure_profile`
        # (`final_response.py`, Task 1) does. `has_disclosure_profile()`
        # below is what Gate-D's own decision logic (Task 6/10) is
        # expected to check that value against.
        self._known_disclosure_profile_ids = known_disclosure_profile_ids

    # ── Loading ──────────────────────────────────────────────────────

    @classmethod
    def load(
        cls,
        path: str | Path = DEFAULT_GATE_D_POLICY_PATH,
        *,
        gate_b_policy: PolicyRegistry | None = None,
    ) -> GateDPolicyRegistry:
        """Load and validate the committed policy file at `path` (default
        `policy/gate_d_policy.v1.json` -- committed, read-only governed
        data; see `policy/README.md`). Raises `GateDPolicyLoadError` for
        anything wrong with the file itself: missing, unreadable, not
        valid JSON, or failing `GateDPolicyDocument`'s typed validation
        (see `policy_models.py`'s "Day 12 Task 2" section for the full
        list). Never falls back to an empty/default/permissive policy.

        `gate_b_policy` defaults to `PolicyRegistry.load()` (the real
        committed Gate-B policy) -- pass an explicit instance only to test
        against a different/throwaway policy. Its governed
        `disclosure_profiles` become this registry's own
        `has_disclosure_profile()` universe."""
        if gate_b_policy is None:
            gate_b_policy = PolicyRegistry.load()

        resolved = Path(path)
        if not resolved.exists():
            raise GateDPolicyLoadError(f"Gate-D policy not found: {resolved}")
        try:
            raw_text = resolved.read_text(encoding="utf-8")
        except OSError as exc:
            raise GateDPolicyLoadError(f"could not read Gate-D policy {resolved}: {exc}") from exc

        try:
            raw_data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise GateDPolicyLoadError(f"Gate-D policy {resolved} is not valid JSON: {exc}") from exc

        try:
            document = GateDPolicyDocument.model_validate(raw_data)
        except ValidationError as exc:
            raise GateDPolicyLoadError(f"Gate-D policy {resolved} failed validation: {exc}") from exc

        known_disclosure_profile_ids = frozenset(profile.profile_id for profile in gate_b_policy.disclosure_profiles)
        return cls(document, known_disclosure_profile_ids=known_disclosure_profile_ids)

    # ── Active version ───────────────────────────────────────────────

    @property
    def policy_version(self) -> str:
        """The active governed Gate-D policy version this registry
        loaded -- included in every Gate-D decision (Task 10's
        `GateDDecision.policy_version`) and Task 13's observability
        metadata."""
        return self._document.policy_version

    # ── Read-only sub-policy lookups ─────────────────────────────────
    # Each nested policy is a single governed object, not a collection --
    # Gate-D's v1 schema has no separate id-keyed records to resolve one
    # at a time (see `policy_models.py`'s "Day 12 Task 2" section), so
    # these are plain properties, not `get_*`/`has_*` lookups the way
    # `PolicyRegistry.get_role()`/`GateCPolicyRegistry.get_rule()` are.

    @property
    def allowed_response_statuses(self) -> tuple[AnswerStatus, ...]:
        """The closed set of `candidate_status` values Gate-D may ever
        release, in policy order. A fresh tuple, not a reference into the
        loaded document's own tuple."""
        return tuple(self._document.allowed_response_statuses)

    @property
    def citation_policy(self) -> CitationPolicy:
        return self._document.citation_policy

    @property
    def quality_policy(self) -> QualityPolicy:
        return self._document.quality_policy

    @property
    def disclosure_policy(self) -> GateDDisclosurePolicy:
        return self._document.disclosure_policy

    @property
    def latency_budgets(self) -> LatencyBudgets:
        return self._document.latency_budgets

    @property
    def safe_failure(self) -> SafeFailureSpec:
        return self._document.safe_failure

    # ── Governed membership checks ───────────────────────────────────

    def allows_response_status(self, status: AnswerStatus) -> bool:
        """Whether this policy version permits releasing a candidate
        carrying `status` at all -- Task 2's own "invalid response status
        rejected" case, applied to a specific candidate rather than the
        policy document itself."""
        return status in self._document.allowed_response_statuses

    def has_disclosure_profile(self, profile_id: str) -> bool:
        """Whether `profile_id` names a disclosure profile the real,
        loaded Gate-B policy actually governs -- Task 2's own "unknown
        disclosure profile ... reference rejected" case, checked against
        the same governed universe Gate-B itself resolves
        `PermissionRule.disclosure_profile` against. Always `False` for a
        registry built via the plain constructor without
        `known_disclosure_profile_ids` supplied -- callers exercising this
        check in isolation must supply that set explicitly, the same
        "no context, check simply does not run" allowance
        `GateCPolicyDocument`'s own optional cross-reference context
        gives."""
        return profile_id in self._known_disclosure_profile_ids

    @property
    def known_disclosure_profile_ids(self) -> frozenset[str]:
        """The same governed universe `has_disclosure_profile()` checks
        membership against, exposed directly so `GateD.evaluate()`
        (`gate_d.py`, Task 10) can forward it into
        `check_final_disclosure()` (Task 6) -- that function's own
        candidate-level "unknown disclosure profile reference rejected"
        cross-check (Task 2's requirement, applied to one candidate rather
        than the static policy document). A fresh `frozenset`, not a
        reference into this registry's own private set."""
        return frozenset(self._known_disclosure_profile_ids)
