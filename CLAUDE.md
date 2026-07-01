# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a web application for Taipei City urban renewal audition (臺北市都市更新審議 webapp). The project supports the planning and review of urban renewal proposals under Taipei's Urban Renewal Office (都市更新處).

## Source Documents

`事業計劃報告書及權利變更計劃書.zip` contains the reference PDFs for this project:

- **1130902 1-事業計畫書** — Urban renewal business plan (事業計畫書) template, 111-year version
- **1130902 2-事業概要計畫書** — Business overview plan (事業概要計畫書) template
- **1131114 權利變換計畫書** — Rights exchange plan (權利變換計畫書) template (two copies: 113年 and 111年 version)

The applicable regulation is the **111年3月24日修正公布版** (Revised March 24, 2022), based on:
- 都市更新條例
- 臺北市都市更新自治條例
- 臺北市都市更新建築容積獎勵辦法

## Domain Knowledge

See `docs/urban-renewal-111-wiki.md` for a structured reference of the 111-year regulations, including the 14 major revisions, full business plan structure (18 chapters + 24 appendices), formatting requirements, and personal data masking rules.

## Key Domain Concepts

| Term | Meaning |
|------|---------|
| 事業計畫書 | Urban Renewal Business Plan — the main planning document |
| 事業概要計畫書 | Business Overview Plan — preliminary/summary plan |
| 權利變換計畫書 | Rights Exchange Plan — property rights redistribution plan |
| 更新單元 | Urban renewal unit — the geographic scope of a renewal project |
| 審議 | Deliberation/review by the Urban Renewal Review Committee |
| 實施者 | Implementer — the party executing the renewal project |
| 容積獎勵 | Floor area ratio bonus incentives |
| 公聽會 | Public hearing |
| 聽證 | Formal hearing |
| 幹事會 | Executive committee (pre-review stage) |

---

## Development Commands

```bash
# Install (editable)
pip install -e ".[dev]"

# Run locally
uvicorn auditor.main:app --reload --port 8000

# Run all tests
python -m pytest tests/ -q

# Run a single test file
python -m pytest tests/test_parsing_pipeline.py -q

# Run a single test by name
python -m pytest tests/ -k "test_fallback_parent_title_hierarchy" -v

# Coverage report
pytest --cov=auditor --cov-report=term-missing tests/

# Docker build & run
docker build -t urban-renewal-auditor .
docker run -p 80:80 --env-file .env urban-renewal-auditor
```

Required env vars (`.env`):
```
ANTHROPIC_API_KEY=...
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=ap-northeast-1
S3_BUCKET=urban-renewal-uploads
```

---

## Architecture

### Two Parallel Audit Tracks

The app has two distinct audit systems that currently coexist but are **not yet wired together**:

**Track A — Classic rule engine** (production-ready, used by `/audit`):
```
PDF upload → extractors/* → RuleEngine → annotator → HTML report
```

**Track B — AI parsing pipeline** (implemented, not yet connected to `/audit`):
```
PDF → triage → docling_reader / surya_reader → chunker → llm_auditor
                                                       → field_auditor
                                                       → cross_doc_comparator
```

The next sprint task is wiring Track B into the `/audit` endpoint.

---

### Track A: Classic Rule Engine

**Entry point:** `auditor/main.py` → `POST /audit`

1. **Extractors** (`auditor/extractors/`) — parse raw data from PDFs:
   - `review_table.py` — 審議資料表 (submission type, fill date, bonus FAR fields)
   - `front_docs.py` — 申請書/切結書/委託書 (report date, implementer name, PII)
   - `term_checker.py` — wrong terminology + number context extraction

2. **Rule engine** (`auditor/rules/`):
   - `engine.py` — `Rule` ABC + `RuleEngine`; `build_default_engine()` wires 14 rules
   - `document.py` — checks presence of required document types
   - `form.py` — submission type, fill date, bonus FAR ≤ 40%, parking ratios
   - `pii.py` — high-risk PII detection
   - `consistency.py` — wrong term scan, number consistency
   - `calc.py` — parking slot count calculations, bonus limit verify

3. **Annotator** (`auditor/annotator.py`) — draws red highlight boxes on the PDF using PyMuPDF for every failing finding.

4. **Reporter** (`auditor/reporters/html_reporter.py`) — renders the Jinja2 HTML report.

5. **Storage:**
   - `storage/history.py` — SQLite-based audit history (case name → previous findings)
   - `s3.py` — presigned upload/download URLs, annotated PDF persistence, AES-256 SSE

6. **Version selector** (`auditor/version_selector.py`) — maps 報核日期 (from 申請書/切結書/委託書) to the correct regulation year (107/108/111/113). Falls back to 填表日期 from 審議資料表 if 報核日期 is absent.

---

### Track B: AI Parsing Pipeline

All modules are in `auditor/parsing_pipeline/`. Each phase is independently importable; missing optional deps raise `ImportError` with install instructions rather than failing silently.

| Phase | Module | Role |
|-------|--------|------|
| 0 | `_path_utils.py` | `validate_pdf_path()` — shared path traversal guard used by all phases |
| 1 | `triage.py` | PyMuPDF per-page char count → `PageClass(is_scanned)` |
| 2a | `docling_reader.py` | Docling for text pages; non-contiguous page runs call Docling once per run to avoid pulling in scanned pages between text pages |
| 2b | `surya_reader.py` | Surya OCR for scanned pages; models loaded once via module-level singleton + `threading.Lock` (double-checked locking) |
| 3 | `chunker.py` | Unstructured `chunk_by_title` with pure-Python `_chunk_fallback`; `ancestor_stack` correctly resolves parent headings across level step-backs |
| 4 | `llm_auditor.py` | Claude Haiku (default) `tool_use` → `LlmFinding`; optional Sonnet re-verify for critical findings |
| 4b | `field_auditor.py` | LLM extraction of structured fields + rule validation (FAR ≤ 40%, consent ratios, date consistency) |
| 4c | `cross_doc_comparator.py` | Compares 事業計畫書 vs 權利變換計畫書: land area (±0.5 m²), values (±1萬元), owner count (exact), implementer name (exact) |
| 5 | `evidence_grounder.py` | Grounds LLM findings: `verify_quote()` offline substring check (NFKC + whitespace-tolerant) always runs; `fetch_citation()` escalates critical findings to the Anthropic **Citations API** for an authoritative, non-hallucinated cited quote. Populates `AiFinding.evidence_text`/`evidence_verified`. |

**Key invariants:**
- Use `page_range=(start, end)` (1-based, inclusive) in Docling — never `max_num_pages`, which marks any PDF with more pages as `valid=False`.
- `ancestor_stack` in `_chunk_fallback`: entries are proper ancestors of the current heading. The current heading is NOT pushed onto the stack while its content is accumulating; it is only pushed when a deeper heading arrives.
- LLM `error_type` and `severity` are validated at runtime in `_parse_finding()`; unknown values are logged and skipped rather than raising.

---

### Data Flow for `/audit`

```
POST /audit (multipart or S3 key)
  ↓
extract_review_table()      # 審議資料表
extract_front_docs()        # 申請書/切結書/委託書 → report_date, PII
scan_for_wrong_terms()
extract_number_contexts()
  ↓
RuleEngine.evaluate(AuditData) → List[Finding]
  ↓
select_version(report_date)   # 107/108/111/113年版
annotate_pdf()                # PyMuPDF red-box annotation
generate_report()             # Jinja2 HTML
  ↓
save_run() → SQLite history
save_case_meta() → S3 (if configured)
```

---

### Models (`auditor/models.py`)

All models are frozen dataclasses:
- `AuditData` — aggregates all extractor output passed to the rule engine
- `Finding` — single rule result with `status ∈ {pass, fail, warn, skip}`
- `AuditReport` — full report payload rendered by the HTML template
- `FindingDiff` — change between current and previous audit (`new / improved / regressed / changed`)

---

### Wiki Browser (`/wiki`)

`auditor/wiki.py` + `templates/wiki.html` — loads Markdown files from `docs/` for each regulation year and renders a multi-year tabbed diff view.

---

## Test Layout

| File | Coverage |
|------|----------|
| `tests/test_parsing_pipeline.py` | triage, docling, surya, chunker, llm_auditor, path_utils |
| `tests/test_field_auditor.py` | validate_fields, _parse_extracted, extract_and_validate (LLM mocked) |
| `tests/test_cross_doc_comparator.py` | _compare (pure), compare_documents (LLM mocked) |
| `tests/test_rules.py` | all 14 rule classes |
| `tests/test_api.py` | FastAPI endpoints |
| `tests/test_ocr.py` | EasyOCR extraction |
| `tests/test_s3.py` | S3 helpers (mocked) |

LLM-dependent tests mock `anthropic` via `sys.modules` patching + `importlib.reload()`. Path-validation-dependent tests patch `validate_pdf_path` with `side_effect=lambda p, **kw: p`.

---

## Pending Work

1. ~~**Wire AI pipeline into `/audit`**~~ — ✅ Done. `_run_ai_pipeline()` in `main.py` runs llm_auditor + field_auditor + cross_doc_comparator (when a secondary PDF is present) and surfaces results as `AiFinding` (source ∈ `llm`/`field`/`cross`).
2. **`ANTHROPIC_API_KEY` in `.env`** — required for Phase 4 live runs.
3. **Merge `fix/high-issues-from-review` into `main`.**
4. ~~**Regulation version switching in `field_auditor`**~~ — ✅ Done. `main.py` derives `reg_year` from the selected `RegulationVersion` (via `_reg_year_from_version()`) and passes it to `extract_and_validate()`; thresholds for 107/108/111/113 live in `field_auditor._CONSENT_THRESHOLDS`.
5. **Docker prebake Docling models** — first cold start downloads ~300 MB layout model; bake into image at build time.
6. **`verify_critical` path tests** — covered in `tests/test_parsing_pipeline.py` (keeps/drops/skips-warning/exception/false-path).
