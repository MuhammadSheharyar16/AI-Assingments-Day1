"""
- End-to-end ingest over data/documents produces chunk records with all
  required fields: chunk_id, source_file, char_start, char_end, token_count,
  content_hash, ingestion_version, section
- --tokens / --overlap are respected and visibly change output
"""
import pathlib

import pytest

from aico.retrieval.ingest import ingest

DOCUMENTS_DIR = pathlib.Path(__file__).resolve().parents[1] / "data" / "documents"


def test_ingest_produces_valid_records(tmp_path):
    records = ingest(DOCUMENTS_DIR, tmp_path, tokens=200, overlap=40)
    sources = {p.name: p.read_text(encoding="utf-8") for p in DOCUMENTS_DIR.glob("*.md")}

    assert len(records) > 0
    required = {"chunk_id", "source_file", "char_start", "char_end",
                "token_count", "content_hash", "ingestion_version", "section"}
    for record in records:
        assert required.issubset(record.keys())
        assert record["char_start"] < record["char_end"]
        assert record["token_count"] <= 200
        # offsets must reconstruct the exact chunk text from the real source file
        original = sources[record["source_file"]]
        assert original[record["char_start"]:record["char_end"]] == record["text"]


def test_smaller_tokens_makes_more_chunks(tmp_path):
    small = ingest(DOCUMENTS_DIR, tmp_path / "small", tokens=80, overlap=10)
    large = ingest(DOCUMENTS_DIR, tmp_path / "large", tokens=400, overlap=80)

    assert len(small) > len(large)
    assert max(r["token_count"] for r in small) <= 80
    assert max(r["token_count"] for r in large) <= 400


def test_invalid_config_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        ingest(DOCUMENTS_DIR, tmp_path, tokens=0, overlap=0)


def test_ingest_is_deterministic(tmp_path):
    run1 = ingest(DOCUMENTS_DIR, tmp_path / "run1", tokens=200, overlap=40)
    run2 = ingest(DOCUMENTS_DIR, tmp_path / "run2", tokens=200, overlap=40)

    assert [r["chunk_id"] for r in run1] == [r["chunk_id"] for r in run2]
