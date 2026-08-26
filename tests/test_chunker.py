"""
- Offset reconstruction: original_text[char_start:char_end] == chunk text, every chunk
- Overlap present: consecutive chunks share the configured overlap
- Unicode survives: accented chars, non-Latin string, em dash pass through intact
- Empty input: empty / whitespace-only files -> zero chunks, no crash
- Invalid configuration: negative tokens, zero tokens, overlap >= token size -> rejected
- Determinism: two identical runs -> identical chunk IDs
"""
import pathlib

import pytest

from aico.retrieval.chunker import chunk_text

DOCUMENTS_DIR = pathlib.Path(__file__).resolve().parents[1] / "data" / "documents"


def test_offsets_reconstruct_the_chunk_text():
    doc_path = sorted(DOCUMENTS_DIR.glob("*.md"))[0]
    text = doc_path.read_text(encoding="utf-8")

    chunks = chunk_text(text, source_file=doc_path.name, max_tokens=200, overlap_tokens=40)

    assert len(chunks) > 0
    for chunk in chunks:
        assert text[chunk.char_start:chunk.char_end] == chunk.text


def test_offsets_reconstruct_on_all_documents_and_a_different_config():
    for doc_path in sorted(DOCUMENTS_DIR.glob("*.md")):
        text = doc_path.read_text(encoding="utf-8")
        chunks = chunk_text(text, source_file=doc_path.name, max_tokens=400, overlap_tokens=80)
        for chunk in chunks:
            assert text[chunk.char_start:chunk.char_end] == chunk.text
            assert chunk.token_count <= 400


def test_overlap_is_shared_between_consecutive_chunks():
    # No punctuation, so every split falls on the raw token limit and the
    # overlap window is easy to predict: 5 words shared each time.
    text = " ".join(f"word{i}" for i in range(60))
    chunks = chunk_text(text, source_file="doc.md", max_tokens=20, overlap_tokens=5)

    assert len(chunks) >= 2
    for i in range(len(chunks) - 1):
        tail = chunks[i].text.split()[-5:]
        head = chunks[i + 1].text.split()[:5]
        assert tail == head


def test_sentence_boundary_is_preferred_over_the_raw_limit():
    text = "One two three four five. Six seven eight nine ten eleven."
    chunks = chunk_text(text, source_file="doc.md", max_tokens=6, overlap_tokens=1)

    # "five." ends a sentence at word 5. Without boundary preference the
    # chunk would run to word 6 ("Six"); it should stop early instead.
    assert chunks[0].text == "One two three four five."


def test_unicode_and_em_dash_survive():
    text = "Café société — naïve façade."
    chunks = chunk_text(text, source_file="doc.md", max_tokens=4, overlap_tokens=1)

    # offsets still have to line up even with multi-byte characters in the mix
    for chunk in chunks:
        assert text[chunk.char_start:chunk.char_end] == chunk.text

    all_text = "".join(chunk.text for chunk in chunks)
    assert "Café" in all_text
    assert "façade" in all_text
    assert "—" in all_text


def test_empty_text_produces_no_chunks():
    assert chunk_text("", source_file="doc.md", max_tokens=100, overlap_tokens=10) == []


def test_whitespace_only_text_produces_no_chunks():
    assert chunk_text("   \n\t  ", source_file="doc.md", max_tokens=100, overlap_tokens=10) == []


def test_invalid_configurations_are_rejected():
    bad_configs = [
        (0, 0),     # zero tokens
        (-10, 0),   # negative tokens
        (10, -1),   # negative overlap
        (10, 10),   # overlap equal to tokens
        (10, 15),   # overlap greater than tokens
    ]
    for max_tokens, overlap_tokens in bad_configs:
        with pytest.raises(ValueError):
            chunk_text("some text", source_file="doc.md", max_tokens=max_tokens, overlap_tokens=overlap_tokens)


def test_same_input_gives_same_chunk_ids():
    doc_path = sorted(DOCUMENTS_DIR.glob("*.md"))[0]
    text = doc_path.read_text(encoding="utf-8")

    run1 = chunk_text(text, source_file="doc.md", max_tokens=150, overlap_tokens=30)
    run2 = chunk_text(text, source_file="doc.md", max_tokens=150, overlap_tokens=30)

    ids1 = [c.chunk_id for c in run1]
    ids2 = [c.chunk_id for c in run2]
    assert ids1 == ids2


def test_chunk_id_is_not_just_a_running_index():
    chunks = chunk_text("Alpha beta gamma delta epsilon zeta.", source_file="doc.md",
                         max_tokens=3, overlap_tokens=1)
    for i, chunk in enumerate(chunks):
        assert chunk.chunk_id != str(i)


def test_section_uses_nearest_preceding_heading():
    text = (
        "# Heading A\n\n"
        "Some content under heading A.\n\n"
        "## Heading B\n\n"
        "Some content under heading B."
    )
    chunks = chunk_text(text, source_file="doc.md", max_tokens=8, overlap_tokens=1)

    assert chunks[0].section == "Heading A"
    assert chunks[-1].section == "Heading B"
