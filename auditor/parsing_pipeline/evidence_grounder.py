"""Phase 5: Evidence grounding for AI findings.

Two layers of evidence support, from cheapest to most authoritative:

1. `verify_quote()` — offline check that a finding's quoted text actually
   appears in the source document. Zero cost, always run. Catches the case
   where the LLM fabricates a quote that is not in the document.

2. `fetch_citation()` — uses the Anthropic **Citations API** to obtain a
   verified, non-hallucinated `cited_text` (and character location) that
   grounds a claim in the source. Reserved for high-value (critical) findings
   because it costs one extra model call per finding.

Design notes:
- Text is normalised with NFKC + whitespace collapsing before comparison, so
  OCR spacing artefacts (e.g. "委  託  書") do not defeat verification.
- Everything degrades gracefully: a missing anthropic SDK, an API error, or an
  empty response yields `verified=False` rather than raising.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import List, Optional

log = logging.getLogger(__name__)

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOKENS = 1024

# A quote shorter than this is too generic to be meaningful evidence.
# 3 keeps common 3-character Traditional Chinese terms (e.g. 委託書) as valid.
_MIN_QUOTE_CHARS = 3


@dataclass(frozen=True)
class Evidence:
    """A grounded piece of supporting text for a finding."""
    cited_text: str
    verified: bool
    start_index: Optional[int] = None
    end_index: Optional[int] = None
    source: str = "offline"  # "offline" (substring check) or "citations_api"


def _normalize(text: str) -> str:
    """NFKC-normalise and collapse all whitespace for tolerant comparison."""
    normalized = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", "", normalized)


def verify_quote(quote: str, source_text: str) -> bool:
    """Return True if *quote* appears in *source_text* (whitespace/width-tolerant).

    Empty or too-short quotes return False — they cannot serve as evidence.
    """
    if not quote or len(quote.strip()) < _MIN_QUOTE_CHARS:
        return False
    return _normalize(quote) in _normalize(source_text)


def _parse_citations(response) -> Optional[Evidence]:
    """Extract the first citation from an Anthropic Citations API response.

    Returns None when the response carries no citation.
    """
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) != "text":
            continue
        citations = getattr(block, "citations", None) or []
        for cit in citations:
            cited_text = _cit_attr(cit, "cited_text")
            if cited_text:
                return Evidence(
                    cited_text=str(cited_text),
                    verified=True,
                    start_index=_cit_attr(cit, "start_char_index"),
                    end_index=_cit_attr(cit, "end_char_index"),
                    source="citations_api",
                )
    return None


def _cit_attr(cit, name):
    """Read a field from a citation whether it is an object or a dict."""
    if isinstance(cit, dict):
        return cit.get(name)
    return getattr(cit, name, None)


def fetch_citation(
    claim: str,
    source_text: str,
    model: str = _DEFAULT_MODEL,
    doc_title: str = "審查文件",
) -> Optional[Evidence]:
    """Ask Claude (Citations API) for the source passage that supports *claim*.

    Passes *source_text* as a citations-enabled text document so the returned
    `cited_text` is guaranteed to be a real substring of the document (the API
    does not hallucinate citations). Returns None on any failure.
    """
    try:
        import anthropic
    except ImportError:
        log.debug("anthropic SDK missing; skipping citation fetch")
        return None

    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=model,
            max_tokens=_MAX_TOKENS,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "text",
                            "media_type": "text/plain",
                            "data": source_text,
                        },
                        "title": doc_title,
                        "citations": {"enabled": True},
                    },
                    {
                        "type": "text",
                        "text": (
                            f"請從文件中找出支持以下審查發現的**原文出處**，並引用該段文字：\n"
                            f"{claim}\n\n"
                            "若文件中確實沒有相關內容，請直接說明找不到。"
                        ),
                    },
                ],
            }],
        )
        return _parse_citations(response)
    except Exception as exc:
        log.error("Citation fetch failed: %s", exc)
        return None


def ground_quote(
    quote: str,
    source_text: str,
    claim: str,
    model: str = _DEFAULT_MODEL,
    use_citations_api: bool = False,
) -> Evidence:
    """Ground a finding's *quote* against *source_text*.

    Always performs the cheap offline check on *quote*. When
    *use_citations_api* is True and the offline check does not already confirm
    the quote, escalates to the Citations API using *claim* to fetch an
    authoritative citation.

    Returns an Evidence; `verified=False` means the quote could not be
    confirmed by either method.
    """
    if verify_quote(quote, source_text):
        return Evidence(cited_text=quote, verified=True, source="offline")

    if use_citations_api:
        citation = fetch_citation(claim, source_text, model=model)
        if citation is not None:
            return citation

    return Evidence(cited_text=quote, verified=False, source="offline")
