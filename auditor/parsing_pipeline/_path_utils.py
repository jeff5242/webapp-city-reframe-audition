"""Shared path validation for parsing pipeline entry points.

Call validate_pdf_path() at the top of any function that accepts a
user-supplied pdf_path to prevent path traversal before file I/O.
"""
from __future__ import annotations

import os
from pathlib import Path


def validate_pdf_path(pdf_path: str, allowed_dir: str | None = None) -> str:
    """Resolve and validate *pdf_path*.

    Checks:
    - File exists and is a regular file
    - Extension is .pdf (case-insensitive)
    - If *allowed_dir* is given, the resolved path must be inside it

    Returns the resolved absolute path string.
    Raises ValueError for invalid paths, FileNotFoundError if not found.
    """
    resolved = Path(pdf_path).resolve()

    if not resolved.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path!r}")
    if not resolved.is_file():
        raise ValueError(f"Not a regular file: {pdf_path!r}")
    if resolved.suffix.lower() != ".pdf":
        raise ValueError(f"Expected a .pdf file, got: {pdf_path!r}")

    if allowed_dir is not None:
        allowed = Path(allowed_dir).resolve()
        # os.path.commonpath is traversal-safe; str prefix check is not
        try:
            resolved.relative_to(allowed)
        except ValueError:
            raise ValueError(
                f"Path {pdf_path!r} is outside the allowed directory {str(allowed)!r}"
            )

    return str(resolved)
