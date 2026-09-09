"""
Day 9 control-plane boundary. `src/aico/control/` is where every request is
interpreted against governed Mode-A definitions before the system decides
how it may proceed (`day09_pack/README.md`: "Mode A defines governed meaning
and control").

Task 1 exports the typed ontology model (`ontology.py`): a governed Mode-A
document is `OntologyDocument`, made of `Domain` / `Concept` / `Intent`
records and the closed `LaneId` set, self-validating against every rule in
`day09_pack/ontology_requirements.md`. Later tasks add the registry loader
(Task 2, `ontology_registry.py`), Gate-A (Task 3/4, `gate_a.py`) and the lane
selector (Task 5, `lane_selector.py`), all built on these same types.
"""
from aico.control.ontology import (
    Concept,
    Domain,
    Intent,
    LaneId,
    LifecycleStatus,
    OntologyDocument,
)

__all__ = [
    "Concept",
    "Domain",
    "Intent",
    "LaneId",
    "LifecycleStatus",
    "OntologyDocument",
]
