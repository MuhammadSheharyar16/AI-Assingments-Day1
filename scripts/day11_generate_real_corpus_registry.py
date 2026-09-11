"""
Day 11 Task 13 (real-corpus extension) — generates the governed data
Gate-C needs to run against the real `data/documents/` corpus, from real
sources only, never invented values:

    evidence/real_corpus_source_registry.v1.json  -- one governed
        `SourceRecord` per real document, `source_id`/`source_version`/
        `owner`/`data_classification` all read from that document's own
        front-matter (`**Document ID:**`/`**Version:**`/`**Owner:**`/
        `**Classification:**`), never fabricated.
    evidence/real_corpus_manifest.v1.json  -- the same per-document facts
        plus `source_updated_at`, taken from that file's real last-commit
        timestamp (`git log -1 --format=%aI -- <file>`) -- a genuine,
        externally-verifiable signal, unlike filesystem mtime (which a
        fresh checkout resets to checkout time).

Deliberately separate from the pinned Day 11 pack fixtures
(`evidence/source_registry.v1.json` / `policy/gate_c_policy.v1.json`,
copied verbatim from `data/day11_pack/fixtures/` and never edited by this
script or anything else) -- this is new, additional governed data for the
real corpus, not a modification of the graded synthetic fixtures.

Every document in this corpus declares an identical
`**Classification:** Internal — Synthetic Training Material` -- mapped
to Gate-B's own governed `DataClassification.INTERNAL`, the value
`policy/gate_b_policy.v1.json`'s own `GB-R001`/`GB-R003` rules already
allow for `INT-POLICY-QUESTION`/`rag` (`allowed_data_classes: ["public",
"internal"]`), so this is not a new classification vocabulary -- see
`aico.rag.real_corpus_evidence_adapter`'s own module docstring for the
rest of the real-corpus wiring this feeds.

`tenant_id`: the whole corpus is shared reference material within this
deployment's one governed tenant (`TENANT-A` -- `config/control-plane.yaml`
gate_b section / `policy/gate_b_policy.v1.json`'s own committed roles);
recorded once at the manifest level, not fabricated per document.

Re-run this script any time `data/documents/` changes; it is idempotent
and deterministic (the only external, non-reproducible-by-a-caller input
is `git log`, and only that file's own commit history feeds its own
record).
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCUMENTS_DIR = REPO_ROOT / "data" / "documents"
SOURCE_REGISTRY_OUT = REPO_ROOT / "evidence" / "real_corpus_source_registry.v1.json"
MANIFEST_OUT = REPO_ROOT / "evidence" / "real_corpus_manifest.v1.json"

# The one real Mode-A intent that routes to the `rag` lane
# (`ontology/registry.v1.json`) -- the only intent this real-corpus rule
# needs to be usable for.
ALLOWED_INTENT = "INT-POLICY-QUESTION"
SOURCE_TYPE = "internal_policy_document"
AUTHORITY_LEVEL = 100  # Peer governance documents of equal standing -- no document here outranks another.
FRESHNESS_POLICY_ID = "FRESH-POLICY-DOCS-365D"
TENANT_ID = "TENANT-A"

# The one facet every real document supports -- "governed policy content
# was actually returned for this request" -- shared across all five
# documents rather than a per-topic facet per document: real retrieval
# over this corpus returns whichever 1-2 documents are actually relevant
# to one question, never all five, so a completeness rule requiring every
# document's own facet would make every real request fail regardless of
# whether it was correctly answered (see `real_corpus_evidence_adapter.py`
# module docstring, "Why one shared facet" section). Each document also
# gets its own informational facet (`doc_NNN_<slug>`) for observability --
# never required by the governed policy, only ever additive.
SHARED_FACET = "supplier_governance_policy"

_FRONT_MATTER_RE = {
    "document_id": re.compile(r"^\*\*Document ID:\*\*\s*(.+)$", re.MULTILINE),
    "version": re.compile(r"^\*\*Version:\*\*\s*(.+)$", re.MULTILINE),
    "owner": re.compile(r"^\*\*Owner:\*\*\s*(.+)$", re.MULTILINE),
    "classification": re.compile(r"^\*\*Classification:\*\*\s*(.+)$", re.MULTILINE),
}


def _extract_front_matter(text: str, path: Path) -> dict:
    values = {}
    for field_name, pattern in _FRONT_MATTER_RE.items():
        match = pattern.search(text)
        if not match:
            raise ValueError(f"{path}: missing required front-matter field {field_name!r}")
        values[field_name] = match.group(1).strip()
    return values


def _map_classification(raw: str) -> str:
    """Every document in this corpus declares `Internal — ...` today; this
    only maps the one real value observed rather than inventing a broader
    vocabulary the corpus does not actually use."""
    if raw.lower().startswith("internal"):
        return "internal"
    if raw.lower().startswith("public"):
        return "public"
    raise ValueError(f"unrecognized real-corpus classification: {raw!r} -- extend this mapping deliberately, do not guess")


def _last_commit_timestamp(path: Path) -> str:
    """The real last-commit timestamp for `path`, in ISO 8601 with UTC
    offset -- `git log`'s own `%aI`, not filesystem mtime (a fresh
    checkout resets mtime to checkout time, which is not a real
    freshness signal at all)."""
    result = subprocess.run(
        ["git", "log", "-1", "--format=%aI", "--", str(path.relative_to(REPO_ROOT))],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    timestamp = result.stdout.strip()
    if not timestamp:
        raise ValueError(f"{path}: no git commit history found -- cannot derive a real source_updated_at")
    return timestamp


def _slug(document_id: str, title: str) -> str:
    """`doc_001_sourcing_policy`-shaped informational facet name, derived
    from the document's own id/filename -- deterministic, never guessed."""
    return f"{document_id.lower().replace('-', '_')}_{title}"


def main() -> None:
    document_paths = sorted(DOCUMENTS_DIR.glob("*.md"))
    if not document_paths:
        raise SystemExit(f"no documents found under {DOCUMENTS_DIR}")

    sources = []
    documents = []
    for path in document_paths:
        text = path.read_text(encoding="utf-8")
        front_matter = _extract_front_matter(text, path)
        document_id = front_matter["document_id"]
        source_id = f"SRC-{document_id}"
        classification = _map_classification(front_matter["classification"])
        updated_at = _last_commit_timestamp(path)

        # `doc_001_sourcing_policy` from `DOC-001-sourcing-policy.md`'s own
        # filename stem (minus the `DOC-NNN-` prefix) -- real, derived
        # from the committed filename, not invented per document.
        stem = path.stem  # "DOC-001-sourcing-policy"
        topic_slug = stem.split("-", 2)[-1].replace("-", "_")
        facet = _slug(document_id, topic_slug)

        sources.append({
            "source_id": source_id,
            "source_type": SOURCE_TYPE,
            "authority_level": AUTHORITY_LEVEL,
            "owner": front_matter["owner"],
            "status": "active",
            "allowed_intents": [ALLOWED_INTENT],
            "supported_facets": [SHARED_FACET, facet],
            "freshness_policy_id": FRESHNESS_POLICY_ID,
        })
        documents.append({
            "source_file": path.name,
            "source_id": source_id,
            "source_version": front_matter["version"],
            "data_classification": classification,
            "source_updated_at": updated_at,
            "facets": [SHARED_FACET, facet],
        })

    source_registry = {"registry_version": "1.0", "sources": sources}
    manifest = {"manifest_version": "1.0", "tenant_id": TENANT_ID, "documents": documents}

    SOURCE_REGISTRY_OUT.write_text(json.dumps(source_registry, indent=2) + "\n", encoding="utf-8")
    MANIFEST_OUT.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {SOURCE_REGISTRY_OUT.relative_to(REPO_ROOT)} ({len(sources)} sources)")
    print(f"wrote {MANIFEST_OUT.relative_to(REPO_ROOT)} ({len(documents)} documents)")


if __name__ == "__main__":
    main()
