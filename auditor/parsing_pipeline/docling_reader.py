"""Phase 2a: Core structured parsing via IBM Docling.

Handles dual-column layouts and converts 審議資料表 / 所有權人清冊 tables
into Markdown with preserved row/column relationships.

Reference: https://github.com/DS4SD/docling
Install:   pip install docling
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from auditor.parsing_pipeline._path_utils import validate_pdf_path


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

    pdf_path = validate_pdf_path(pdf_path)

    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = False          # OCR handled by Surya (Phase 2b)
    pipeline_options.do_table_structure = True  # Enable table structure recovery

    converter = DocumentConverter(
        format_options={"pdf": PdfFormatOption(pipeline_options=pipeline_options)}
    )

    if page_nums is None:
        result = converter.convert(pdf_path, raises_on_error=False)
        return result.document.export_to_markdown()

    # Split non-contiguous page numbers into contiguous runs and call
    # Docling once per run.  Using a single (min, max) range would pull in
    # scanned pages between text pages, contaminating the output.
    parts: List[str] = []
    for start, end in _contiguous_runs(page_nums):
        result = converter.convert(pdf_path, raises_on_error=False, page_range=(start, end))
        parts.append(result.document.export_to_markdown())
    return "\n\n".join(parts)


def _contiguous_runs(page_nums: List[int]) -> List[Tuple[int, int]]:
    """Group sorted page numbers into contiguous (start, end) 1-based ranges."""
    if not page_nums:
        return []
    sorted_nums = sorted(set(page_nums))
    runs: List[Tuple[int, int]] = []
    start = end = sorted_nums[0]
    for n in sorted_nums[1:]:
        if n == end + 1:
            end = n
        else:
            runs.append((start, end))
            start = end = n
    runs.append((start, end))
    return runs


def parse_pages_to_markdown(
    pdf_path: str,
    page_indices: List[int],
) -> str:
    """Convenience wrapper: parse the given 0-based page indices only.

    Converts the page numbers and delegates to parse_pdf_to_markdown.
    """
    page_nums = [i + 1 for i in page_indices]
    return parse_pdf_to_markdown(pdf_path, page_nums=page_nums)
