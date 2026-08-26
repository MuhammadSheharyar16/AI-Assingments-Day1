"""
CLI: python -m aico.retrieval.ingest --input data/documents --out data/index --tokens 300 --overlap 50

Responsibilities (to implement):
- Read all documents from --input
- Chunk each document via chunker.py
- Attach chunk_id, source_file, char_start, char_end, token_count,
  content_hash, ingestion_version, section to every chunk
- Write chunk records to --out
- All four of input path, output path, tokens, and overlap must be arguments
"""
import argparse
import json
from pathlib import Path

from aico.retrieval.chunker import chunk_text


def ingest(input_dir: Path, out_dir: Path, tokens: int, overlap: int) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    md_files = sorted(input_dir.glob("*.md"))

    records = []
    for doc_path in md_files:
        text = doc_path.read_text(encoding="utf-8")
        chunks = chunk_text(text, source_file=doc_path.name,
                             max_tokens=tokens, overlap_tokens=overlap)
        for chunk in chunks:
            records.append(chunk.to_dict())

    manifest = {
        "tokens": tokens,
        "overlap": overlap,
        "chunk_count": len(records),
        "source_dir": str(input_dir),
        "source_files": [p.name for p in md_files],
    }

    # Store manifest + chunks in ONE file
    index = {
        "manifest": manifest,
        "chunks": records,
    }

    with open(out_dir / "index.json", "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2)

    return records


def main():
    parser = argparse.ArgumentParser(description="Chunk a folder of documents into an index.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--tokens", required=True, type=int)
    parser.add_argument("--overlap", required=True, type=int)
    args = parser.parse_args()

    records = ingest(args.input, args.out, args.tokens, args.overlap)

    # count chunks per source file
    counts = {}
    for r in records:
        counts[r["source_file"]] = counts.get(r["source_file"], 0) + 1

    print(f"Ingested {len(counts)} documents into {len(records)} chunks "
          f"(tokens={args.tokens}, overlap={args.overlap}) -> {args.out}")
    for fname in sorted(counts):
        print(f"  {fname}: {counts[fname]} chunks")


if __name__ == "__main__":
    main()
