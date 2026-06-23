"""Phase 2a: Core structured parsing via IBM Docling.

Handles dual-column layouts and converts 審議資料表 / 所有權人清冊 tables
into Markdown with preserved row/column relationships.

Reference: https://github.com/DS4SD/docling
"""
from __future__ import annotations

from typing import List


def parse_pdf_to_markdown(pdf_path: str, page_nums: List[int] | None = None) -> str:
    """Parse *pdf_path* with Docling and return a Markdown string.

    *page_nums* restricts processing to the given 1-based page numbers;
    None means all pages.

    Raises ImportError if docling is not installed.
    Raises NotImplementedError until this phase is implemented.
    """
    raise NotImplementedError("Phase 2a Docling parser — to be implemented")
