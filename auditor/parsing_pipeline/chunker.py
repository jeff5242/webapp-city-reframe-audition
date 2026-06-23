"""Phase 3: Semantic chunking via Unstructured.

Splits Markdown output from phase 2 into context-aware chunks following
urban-renewal chapter structure (章/節 headings), ensuring each chunk
sent to the LLM has complete context and stays within token limits.

Reference: https://github.com/Unstructured-IO/unstructured
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class DocumentChunk:
    section_title: str
    parent_title: str | None
    text: str
    char_count: int


def chunk_markdown(markdown: str, strategy: str = "by_title") -> List[DocumentChunk]:
    """Split *markdown* into chunks using Unstructured's *strategy*.

    strategy options: "by_title" (default), "basic", "by_page"

    Raises ImportError if unstructured is not installed.
    Raises NotImplementedError until this phase is implemented.
    """
    raise NotImplementedError("Phase 3 chunker — to be implemented")
