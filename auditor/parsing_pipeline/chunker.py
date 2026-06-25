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
    current_level = 0
    current_lines: List[str] = []
    # Ancestor stack: entries are (level, title) of proper ANCESTORS of current_title.
    # current_title itself is never in the stack while its content is accumulating.
    ancestor_stack: List[tuple] = []

    def _flush() -> None:
        text = "".join(current_lines).strip()
        if text:
            parent = ancestor_stack[-1][1] if ancestor_stack else None
            chunks.append(DocumentChunk(
                section_title=current_title,
                parent_title=parent,
                text=text,
                char_count=len(text),
            ))

    for line in lines:
        m = _HEADING_RE.match(line.rstrip("\n"))
        if m:
            _flush()
            new_level = len(m.group(1))
            new_heading = m.group(2).strip()

            # Remove stack entries that are siblings or descendants of new_heading
            while ancestor_stack and ancestor_stack[-1][0] >= new_level:
                ancestor_stack.pop()

            # Push the outgoing heading onto the stack only if it is a proper
            # ancestor of the incoming heading (i.e. its level is shallower)
            if current_level > 0 and current_level < new_level:
                ancestor_stack.append((current_level, current_title))

            current_title = new_heading
            current_level = new_level
            current_lines = [line]
        else:
            current_lines.append(line)

    _flush()
    return chunks
