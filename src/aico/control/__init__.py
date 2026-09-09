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
(Task 3/4, `gate_a.py`) and the lane selector (Task 5, `lane_selector.py`)
are built against, plus its typed failures (`errors.py`).
"""
from aico.control.errors import OntologyLoadError, OntologyLookupError, OntologyRegistryError
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
]
