"""Tests for Phase 5 evidence grounding (offline verify + Citations API)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ── verify_quote (offline) ────────────────────────────────────────────────────

def test_verify_quote_exact_match():
    from auditor.parsing_pipeline.evidence_grounder import verify_quote
    assert verify_quote("容積獎勵合計45%", "本案容積獎勵合計45%，超過上限。") is True


def test_verify_quote_tolerates_whitespace_and_width():
    """OCR spacing and full/half-width differences must not defeat matching."""
    from auditor.parsing_pipeline.evidence_grounder import verify_quote
    # quote has no spaces, source has OCR spaces + full-width digits
    assert verify_quote("委託書", "本頁為 委  託  書 內容") is True


def test_verify_quote_rejects_absent_text():
    from auditor.parsing_pipeline.evidence_grounder import verify_quote
    assert verify_quote("完全不存在的內容", "本案容積獎勵合計45%") is False


def test_verify_quote_rejects_too_short():
    from auditor.parsing_pipeline.evidence_grounder import verify_quote
    assert verify_quote("的", "的的的的的") is False


def test_verify_quote_rejects_empty():
    from auditor.parsing_pipeline.evidence_grounder import verify_quote
    assert verify_quote("", "anything") is False


# ── fetch_citation (Citations API, mocked) ────────────────────────────────────

def _mock_anthropic_with_citation(cited_text, start=10, end=20):
    """Build a mock anthropic module whose response carries one citation."""
    citation = MagicMock()
    citation.cited_text = cited_text
    citation.start_char_index = start
    citation.end_char_index = end

    text_block = MagicMock()
    text_block.type = "text"
    text_block.citations = [citation]

    response = MagicMock()
    response.content = [text_block]

    client = MagicMock()
    client.messages.create.return_value = response

    anthropic_mod = MagicMock()
    anthropic_mod.Anthropic.return_value = client
    return anthropic_mod, client


def test_fetch_citation_returns_evidence():
    from auditor.parsing_pipeline import evidence_grounder as mod

    anthropic_mod, client = _mock_anthropic_with_citation("容積獎勵合計45%")
    with patch.dict("sys.modules", {"anthropic": anthropic_mod}):
        ev = mod.fetch_citation("容積超標", "本案容積獎勵合計45%。")

    assert ev is not None
    assert ev.verified is True
    assert ev.cited_text == "容積獎勵合計45%"
    assert ev.source == "citations_api"
    assert ev.start_index == 10


def test_fetch_citation_enables_citations_in_request():
    """The document block must be sent with citations enabled."""
    from auditor.parsing_pipeline import evidence_grounder as mod

    anthropic_mod, client = _mock_anthropic_with_citation("x 引用內容 y")
    with patch.dict("sys.modules", {"anthropic": anthropic_mod}):
        mod.fetch_citation("claim", "source text 引用內容")

    _, kwargs = client.messages.create.call_args
    doc_block = kwargs["messages"][0]["content"][0]
    assert doc_block["type"] == "document"
    assert doc_block["citations"] == {"enabled": True}


def test_fetch_citation_returns_none_on_no_citation():
    from auditor.parsing_pipeline import evidence_grounder as mod

    text_block = MagicMock()
    text_block.type = "text"
    text_block.citations = []
    response = MagicMock()
    response.content = [text_block]
    client = MagicMock()
    client.messages.create.return_value = response
    anthropic_mod = MagicMock()
    anthropic_mod.Anthropic.return_value = client

    with patch.dict("sys.modules", {"anthropic": anthropic_mod}):
        assert mod.fetch_citation("claim", "source") is None


def test_fetch_citation_returns_none_on_api_error():
    from auditor.parsing_pipeline import evidence_grounder as mod

    client = MagicMock()
    client.messages.create.side_effect = RuntimeError("api down")
    anthropic_mod = MagicMock()
    anthropic_mod.Anthropic.return_value = client

    with patch.dict("sys.modules", {"anthropic": anthropic_mod}):
        assert mod.fetch_citation("claim", "source") is None


def test_fetch_citation_parses_dict_style_citation():
    """Citations returned as plain dicts (not objects) must also parse."""
    from auditor.parsing_pipeline import evidence_grounder as mod

    text_block = MagicMock()
    text_block.type = "text"
    text_block.citations = [{
        "cited_text": "字典型引用",
        "start_char_index": 3,
        "end_char_index": 8,
    }]
    response = MagicMock()
    response.content = [text_block]
    client = MagicMock()
    client.messages.create.return_value = response
    anthropic_mod = MagicMock()
    anthropic_mod.Anthropic.return_value = client

    with patch.dict("sys.modules", {"anthropic": anthropic_mod}):
        ev = mod.fetch_citation("claim", "abc字典型引用def")

    assert ev is not None
    assert ev.cited_text == "字典型引用"


# ── ground_quote (orchestration) ──────────────────────────────────────────────

def test_ground_quote_offline_hit_skips_api():
    """When the quote is found offline, the Citations API must NOT be called."""
    from auditor.parsing_pipeline import evidence_grounder as mod

    with patch.object(mod, "fetch_citation") as mock_fetch:
        ev = mod.ground_quote(
            "容積獎勵合計45%",
            "本案容積獎勵合計45%，超過上限。",
            claim="容積超標",
            use_citations_api=True,
        )

    assert ev.verified is True
    assert ev.source == "offline"
    mock_fetch.assert_not_called()


def test_ground_quote_escalates_to_api_when_offline_miss():
    from auditor.parsing_pipeline import evidence_grounder as mod
    from auditor.parsing_pipeline.evidence_grounder import Evidence

    api_evidence = Evidence(cited_text="API 找到的原文", verified=True, source="citations_api")
    with patch.object(mod, "fetch_citation", return_value=api_evidence) as mock_fetch:
        ev = mod.ground_quote(
            "LLM 幻覺的引用不存在於原文",
            "完全不同的文件內容",
            claim="某發現",
            use_citations_api=True,
        )

    mock_fetch.assert_called_once()
    assert ev.verified is True
    assert ev.source == "citations_api"


def test_ground_quote_unverified_when_both_fail():
    from auditor.parsing_pipeline import evidence_grounder as mod

    with patch.object(mod, "fetch_citation", return_value=None):
        ev = mod.ground_quote(
            "不存在的引用",
            "無關的原文",
            claim="某發現",
            use_citations_api=True,
        )

    assert ev.verified is False
    assert ev.source == "offline"


def test_ground_quote_offline_only_does_not_call_api():
    """use_citations_api=False must never call the API even on offline miss."""
    from auditor.parsing_pipeline import evidence_grounder as mod

    with patch.object(mod, "fetch_citation") as mock_fetch:
        ev = mod.ground_quote(
            "不存在的引用",
            "無關的原文",
            claim="某發現",
            use_citations_api=False,
        )

    mock_fetch.assert_not_called()
    assert ev.verified is False
