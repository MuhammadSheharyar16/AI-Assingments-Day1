"""
CLI: python -m aico.retrieval.search --query "termination notice period" --top-k 5

Responsibilities (to implement):
- Load the BM25 index built over chunks (never over raw documents)
- Print rank, score, chunk_id, source_file, character span, and matched text
  for each of the top-k results
"""

import argparse
import json
import pathlib

from aico.retrieval.bm25 import BM25Index


def load_chunks(index_dir: pathlib.Path) -> list[dict]:
    chunks_path = index_dir / "index.json"

    if not chunks_path.exists():
        raise FileNotFoundError(
            f"No index.json in {index_dir} - run `python -m aico.retrieval.ingest` first"
        )

    data = json.loads(chunks_path.read_text(encoding="utf-8"))

    return data["chunks"]

def main():
    parser = argparse.ArgumentParser(description="Search a chunk index with BM25.")
    parser.add_argument("--query", required=True)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--index", type=pathlib.Path, default=pathlib.Path("data/index"))
    args = parser.parse_args()

    chunks = load_chunks(args.index)
    index = BM25Index(chunks)
    results = index.search(args.query, top_k=args.top_k)

    if not results or results[0].score == 0:
        print(f"No matching chunks for: {args.query!r}")
        return

    for rank, r in enumerate(results, start=1):
        c = r.chunk
        preview = c["text"][:160].replace("\n", " ")
        print(f"[{rank}] score={r.score:.3f} chunk_id={c['chunk_id']} "
              f"source={c['source_file']} span=({c['char_start']},{c['char_end']})")
        print(f"    {preview}...")


if __name__ == "__main__":
    main()
