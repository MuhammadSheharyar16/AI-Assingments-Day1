"""
Hand-built BM25 index and scorer (no ranking library allowed).

Responsibilities (to implement):
- Tokenisation shared identically between query and document
- Term frequency, inverse document frequency, length normalisation
- Named constants for k1 and b (not inline numbers)
- A defined tie-break rule for equal scores
- Deterministic ranking across runs on unchanged input
"""

import math
import re
from collections import Counter
from dataclasses import dataclass

BM25_K1 = 1.5
BM25_B = 0.75

TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())

@dataclass
class ChunkScore:
    chunk: dict
    score: float

class BM25Index:
    def __init__(self, chunks: list[dict], k1: float=BM25_K1, b: float=BM25_B):
        self.k1 = k1
        self.b = b
        self.chunks = chunks

        self.doc_tokens: list[list[str]] = [tokenize(c["text"]) for c in chunks]
        self.doc_lens: list[int] = [len(toks) for toks in self.doc_tokens]
        self.avgdl = sum(self.doc_lens) / len(self.doc_lens) if self.doc_lens else 0
        self.term_freqs: list[Counter] = [Counter(toks) for toks in self.doc_tokens]

        # Compute document frequency (df) for each term across all documents
        self.df = Counter()
        for tf in self.term_freqs:
            for term in tf:
                self.df[term] += 1

        self.n_docs = len(chunks)
        self._idf_cache: dict[str, float] = {}

    def idf(self, term: str) -> float:
        if term not in self._idf_cache:
            df = self.df.get(term, 0)
            # +1 keeps this from going negative on a small corpus
            self._idf_cache[term] = math.log(1 + (self.n_docs - df + 0.5) / (df + 0.5))
        return self._idf_cache[term]

    def score(self, query_terms: list[str], doc_idx: int) -> float:
        tf = self.term_freqs[doc_idx]
        dl = self.doc_lens[doc_idx]

        score = 0.0
        for term in query_terms:
            f = tf.get(term, 0)
            if f == 0:
                continue
            denom = f + self.k1 * (1 - self.b + self.b * dl / self.avgdl) if self.avgdl else f
            score += self.idf(term) * (f * (self.k1 + 1)) / denom
        return score

    def search(self, query: str, top_k: int = 5) -> list[ChunkScore]:
        query_terms = tokenize(query)
        results = [
            (self.score(query_terms, i), self.chunks[i]["chunk_id"], i)
            for i in range(self.n_docs)
        ]
        # sort by score desc, break ties by chunk_id so order is stable
        results.sort(key=lambda r: (-r[0], r[1]))

        return [ChunkScore(chunk=self.chunks[i], score=s) for s, _, i in results[:top_k]]