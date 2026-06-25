"""Phase 2a: Core structured parsing via IBM Docling.

Handles dual-column layouts and converts 審議資料表 / 所有權人清冊 tables
into Markdown with preserved row/column relationships.

Reference: https://github.com/DS4SD/docling
Install:   pip install docling
"""
from __future__ import annotations

from typing import List, Optional


def is_available() -> bool:
    """Return True if docling is installed and importable."""
    try:
        import docling  # noqa: F401
        return True
    except ImportError:
        return False


def parse_pdf_to_markdown(
    pdf_path: str,
    page_nums: Optional[List[int]] = None,
) -> str:
    """Parse *pdf_path* with Docling and return a Markdown string.

    *page_nums* is a list of 1-based page numbers to extract.
    None means all pages (Docling converts the whole document).

    Docling preserves dual-column flow and renders tables as GFM Markdown.

    Raises ImportError if docling is not installed.
    """
    try:
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.pipeline_options import PdfPipelineOptions
    except ImportError:
        raise ImportError(
            "docling is required for Phase 2a parsing. Install: pip install docling"
        )

    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = False          # OCR handled by Surya (Phase 2b)
    pipeline_options.do_table_structure = True  # Enable table structure recovery

    converter = DocumentConverter(
        format_options={"pdf": PdfFormatOption(pipeline_options=pipeline_options)}
    )

    # Use page_range=(start, end) instead of max_num_pages:
    # max_num_pages is treated as a hard limit that marks documents
    # with more pages as invalid; page_range only processes the slice.
    if page_nums is not None:
        page_range = (min(page_nums), max(page_nums))
    else:
        page_range = None

    kwargs = {}
    if page_range is not None:
        kwargs["page_range"] = page_range

    result = converter.convert(pdf_path, raises_on_error=False, **kwargs)
    return result.document.export_to_markdown()


def parse_pages_to_markdown(
    pdf_path: str,
    page_indices: List[int],
) -> str:
    """Convenience wrapper: parse the given 0-based page indices only.

    Converts the page numbers and delegates to parse_pdf_to_markdown.
    """
    page_nums = [i + 1 for i in page_indices]
    return parse_pdf_to_markdown(pdf_path, page_nums=page_nums)
