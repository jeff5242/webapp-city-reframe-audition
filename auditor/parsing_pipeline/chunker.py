"""Phase 3: Semantic chunking via Unstructured.

Splits Markdown output from Phase 2 into context-aware chunks following
urban-renewal chapter structure (章/節 headings), ensuring each chunk
sent to the LLM has complete context and stays within token limits.

Reference: https://github.com/Unstructured-IO/unstructured
Install:   pip install "unstructured[md]"
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class DocumentChunk:
    section_title: str
    parent_title: str | None
    text: str
    char_count: int


_HEADING_RE = re.compile(r'^(#{1,3})\s+(.+)$', re.MULTILINE)


def is_available() -> bool:
    """Return True if unstructured is installed and importable."""
    try:
        from unstructured.partition.md import partition_md  # noqa: F401
        return True
    except ImportError:
        return False


def chunk_markdown(markdown: str, strategy: str = "by_title") -> List[DocumentChunk]:
    """Split *markdown* into DocumentChunk objects using *strategy*.

    strategy="by_title" (default): uses Unstructured's chunk_by_title,
      splitting on Markdown # / ## / ### headings.

    Falls back to a built-in heading-aware splitter if unstructured is not
    installed so callers always get a usable result.

    Raises ImportError for unknown strategies that require unstructured.
    """
    if strategy == "by_title":
        try:
            return _chunk_with_unstructured(markdown)
        except ImportError:
            return _chunk_fallback(markdown)
    raise ValueError(f"Unknown chunking strategy: {strategy!r}")


def _chunk_with_unstructured(markdown: str) -> List[DocumentChunk]:
    """Use Unstructured library for title-based chunking."""
    from unstructured.partition.md import partition_md
    from unstructured.chunking.title import chunk_by_title

    elements = partition_md(file=io.StringIO(markdown))
    chunks = chunk_by_title(elements)

    result: List[DocumentChunk] = []
    for chunk in chunks:
        text = str(chunk)
        meta = chunk.metadata
        title = getattr(meta, "section", "") or ""
        parent = getattr(meta, "parent_id", None)
        result.append(DocumentChunk(
            section_title=title,
            parent_title=str(parent) if parent else None,
            text=text,
            char_count=len(text),
        ))
    return result


def _chunk_fallback(markdown: str) -> List[DocumentChunk]:
    """Heading-aware splitter that works without external dependencies.

    Splits at # / ## / ### Markdown headings, preserving heading hierarchy.
    Used when unstructured is not installed.
    """
    if not markdown.strip():
        return []

    lines = markdown.splitlines(keepends=True)
    chunks: List[DocumentChunk] = []

    current_title = ""
    parent_title: str | None = None
    current_lines: List[str] = []
    title_level = 0

    def _flush() -> None:
        text = "".join(current_lines).strip()
        if text:
            chunks.append(DocumentChunk(
                section_title=current_title,
                parent_title=parent_title,
                text=text,
                char_count=len(text),
            ))

    for line in lines:
        m = _HEADING_RE.match(line.rstrip("\n"))
        if m:
            _flush()
            level = len(m.group(1))
            heading = m.group(2).strip()
            if level <= 1:
                parent_title = None
                title_level = level
            elif level > title_level:
                parent_title = current_title
            current_title = heading
            title_level = level
            current_lines = [line]
        else:
            current_lines.append(line)

    _flush()
    return chunks
