"""
Chunking logic: turns raw document text into offset-traceable chunks.

Responsibilities (to implement):
- Split text into chunks bounded by --tokens / --overlap
- Prefer sentence boundary, fall back to word boundary, break mid-word only
  when no safe boundary exists inside the limit
- Compute char_start / char_end such that original_text[char_start:char_end]
  reconstructs the chunk exactly
- Derive a stable, content-based chunk_id (no timestamps/UUIDs/running index)
- Track nearest preceding markdown heading as `section`
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

WORD_RE = re.compile(r"\S+")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)
SENTENCE_END_RE = re.compile(r"[.!?]+")

INGESTION_VERSION = "day1-chunker-1.0"  # Increment this if chunking logic changes


@dataclass
class Chunk:
    chunk_id: str
    source_file: str
    char_start: int
    char_end: int
    token_count: int
    content_hash: str
    ingestion_version: str
    section: str
    text: str = field(repr=False)  # Exclude text from repr for brevity

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "source_file": self.source_file,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "token_count": self.token_count,
            "content_hash": self.content_hash,
            "ingestion_version": self.ingestion_version,
            "section": self.section,
            "text": self.text
        }

def _word_spans(text: str)-> list[tuple[int, int]]:
    """
    Generate spans for each word in the text "Hello world", [(0, 5), (6, 11)].
    """
    return [(m.start(), m.end()) for m in WORD_RE.finditer(text)]

def _sentence_boundaries(text: str, spans: list[tuple[int, int]]) -> list[bool]:
    """
    Given a list of word spans, determine which words are sentence boundaries.
    A word is considered a sentence boundary if it ends with a sentence-ending punctuation
    or if there is a blank line after it.
    """
    boundaries = [False] * len(spans)
    for i, (start, end) in enumerate(spans):
        word = text[start:end]

        # Check if this word ends a sentence
        if SENTENCE_END_RE.search(word):
            boundaries[i] = True

        # Check if there is a blank line after this word
        elif i + 1 < len(spans):
            next_start = spans[i + 1][0]
            gap = text[end:next_start]

            if "\n\n" in gap:
                boundaries[i] = True

    # Last word can always end a chunk
    if spans:
        boundaries[-1] = True

    return boundaries

def _headings(text: str) -> list[tuple[int, int, str]]:
    """
    Extract headings from the text and return a list of tuples containing
    (start_index, end_index, heading_text).
    """
    headings = []

    for match in HEADING_RE.finditer(text):
        position = match.start()
        heading = match.group(2).strip()

        headings.append((position, heading))

    return headings

def _section_headings(offset: int, headings: list[tuple[int, str]]) -> str | None:
    """
    Given an offset and a list of headings, return the nearest preceding heading
    for the given offset. If no preceding heading exists, return None.
    """
    section_headings = None

    for position, heading in headings:
        if position <= offset:
            section_headings = heading
        else:
            break

    return section_headings

def _content_hash(text: str) -> str:
    """
    Generate a stable, content-based hash of the text using SHA-256.
    """
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def _generate_chunk_id(source_file: str, char_start: int, char_end: int) -> str:
    """
    Generate a stable, content-based chunk_id using SHA-256 hash of the text.
    """
    return hashlib.sha256(f"{source_file}:{char_start}:{char_end}".encode('utf-8')).hexdigest()[:16]  # Use first 16 characters for brevity

def chunk_text(
   text: str, 
   source_file: str,
   max_tokens: int, 
   overlap_tokens: int,
   ingestion_version: str = INGESTION_VERSION,
) -> list[Chunk]:
    if max_tokens <= 0:
        raise ValueError(f"max_tokens must be a positive integer ({max_tokens})")
    if overlap_tokens < 0:
        raise ValueError(f"overlap_tokens must be a non-negative integer ({overlap_tokens})")
    if overlap_tokens >= max_tokens:
        raise ValueError(f"overlap_tokens ({overlap_tokens}) must be less than max_tokens ({max_tokens})")
    
    spans = _word_spans(text)
    if not spans:
        return []
    
    boundaries = _sentence_boundaries(text, spans)
    headings = _headings(text)

    chunks: list[Chunk] = []
    n = len(spans)
    start_idx = 0

    while start_idx < n:
        limit_idx = min(start_idx + max_tokens - 1, n - 1)

        # look for the furthest sentence boundary we can land on without
        # going over the limit. if there isn't one, we just take the limit -
        # that's still a word boundary because our tokens are words.
        end_idx = None
        for i in range(limit_idx, start_idx - 1, -1):
            if boundaries[i]:
                end_idx = i
                break
        if end_idx is None:
            end_idx = limit_idx

        char_start = spans[start_idx][0]
        char_end = spans[end_idx][1]
        chunk_text = text[char_start:char_end]
        token_count = end_idx - start_idx + 1

        chunks.append(
            Chunk(
                chunk_id= _generate_chunk_id(source_file, char_start, char_end),
                source_file=source_file,
                char_start=char_start,
                char_end=char_end,
                token_count=token_count,
                content_hash=_content_hash(chunk_text),
                ingestion_version=ingestion_version,
                section=_section_headings(char_start, headings),
                text=chunk_text,
            )
        )

        if end_idx >= n - 1:
            break

        # step back from end_idx to leave ~overlap_tokens words for the head
        # of the next chunk, then move start forward from there
        back = end_idx
        covered = 0
        while back > start_idx and covered < overlap_tokens:
            covered += 1
            back -= 1
        next_start = back + 1
        # guarantee forward progress even on a pathologically short chunk
        start_idx = max(next_start, start_idx + 1)

    return chunks
