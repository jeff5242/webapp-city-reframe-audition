"""
Tests for OCR reader and updated front_docs extractor patterns.
"""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock


# ── ocr_reader module ─────────────────────────────────────────────────────────

def test_ocr_reader_imports():
    from auditor.parsers.ocr_reader import ocr_available, ocr_page, ocr_pages
    assert callable(ocr_available)
    assert callable(ocr_page)
    assert callable(ocr_pages)


def test_ocr_available_returns_bool():
    from auditor.parsers.ocr_reader import ocr_available
    result = ocr_available()
    assert isinstance(result, bool)


def test_ocr_page_returns_empty_when_fitz_unavailable(tmp_path):
    """ocr_page must return '' gracefully if pymupdf is missing."""
    import auditor.parsers.ocr_reader as mod
    with patch.object(mod, "_FITZ_OK", False):
        result = mod.ocr_page("nonexistent.pdf", 0)
    assert result == ""


def test_ocr_pages_returns_empty_dict_when_fitz_unavailable():
    import auditor.parsers.ocr_reader as mod
    with patch.object(mod, "_FITZ_OK", False):
        result = mod.ocr_pages("nonexistent.pdf", [0, 1, 2])
    assert result == {}


# ── front_docs pattern improvements ───────────────────────────────────────────

def test_match_doc_type_combined_variant():
    """Real 申請書 content page (title + applicant details) must match '申請書'."""
    from auditor.extractors.front_docs import _match_doc_type
    text = "都市更新事業計畫及權利變換計畫申請書\n申請人：某某更新會　統一編號 12345678"
    assert _match_doc_type(text) == "申請書"


def test_match_doc_type_original_variant():
    from auditor.extractors.front_docs import _match_doc_type
    assert _match_doc_type("都市更新事業計畫申請書\n茲申請都市更新事業計畫報核") == "申請書"


def test_match_doc_type_bare_application_title_is_toc():
    """Bare title with dotted leader (TOC entry) must NOT match — fixes 6/22 bug."""
    from auditor.extractors.front_docs import _match_doc_type
    assert _match_doc_type("都市更新事業計畫及權利變換計畫申請書 ............ I") is None


# ── 審議資料表 辦理過程 報核日 (理事長 item 1) ────────────────────────────────

def test_filing_date_dot_format_latest():
    """辦理過程多筆報核，取最新一筆（dot 格式）。"""
    from auditor.extractors.review_table import _extract_filing_date_from_process
    text = (
        "4  申請事業計畫報核  102.04.30  冠字第1020430017號\n"
        "7  申請權利變換計畫報核  112.09.08  世座都更112字09001號\n"
        "8  權利變換計畫公開展覽 113.01.15~113.02.15\n"
    )
    assert _extract_filing_date_from_process(text) == "112年9月8日"


def test_filing_date_roc_format():
    from auditor.extractors.review_table import _extract_filing_date_from_process
    assert _extract_filing_date_from_process("申請事業計畫報核 112年9月8日") == "112年9月8日"


def test_filing_date_ignores_non_baohe_lines():
    """只看含『報核』的行；公開展覽日期不算報核日。"""
    from auditor.extractors.review_table import _extract_filing_date_from_process
    text = "更新單元公告 96.11.20\n權利變換計畫公開展覽 113.07.02~113.07.31\n"
    assert _extract_filing_date_from_process(text) is None


def test_filing_date_none_when_absent():
    from auditor.extractors.review_table import _extract_filing_date_from_process
    assert _extract_filing_date_from_process("沒有任何日期的文字") is None


def test_filing_date_from_docnum():
    """報核文號編碼日期（合家歡實案）：字第11311140047號 → 113年11月14日。"""
    from auditor.extractors.review_table import _extract_filing_date_from_process
    text = "1申請都市更新事業及權變計畫\n東湖一更新會字第11311140047號\n"
    assert _extract_filing_date_from_process(text) == "113年11月14日"


def test_filing_date_docnum_beats_earlier_baohe():
    """文號報核日(113/11/14) 應勝過較早的報核日(112/09/08)。"""
    from auditor.extractors.review_table import _extract_filing_date_from_process
    text = (
        "申請權利變換計畫報核 112.09.08\n"
        "補正報核 東湖一更新會字第11311140047號\n"
    )
    assert _extract_filing_date_from_process(text) == "113年11月14日"


def test_filing_date_excludes_public_exhibition_context():
    """公開展覽日期即使較新也不算報核日。"""
    from auditor.extractors.review_table import _extract_filing_date_from_process
    text = (
        "申請權利變換計畫報核 112.09.08\n"
        "權利變換計畫公開展覽 113.07.26\n"
    )
    assert _extract_filing_date_from_process(text) == "112年9月8日"


def test_find_review_table_by_scoring_when_ocr_garbles_title(tmp_path):
    """審議資料表 must still be found when OCR garbles its title and 填表日期.

    Reproduces 合家歡: title unmatched, 填表日期→填表耳期, but characteristic
    fields (基準容積/獎勵樓地板/報核/送審) survive → scoring detection finds it.
    """
    import auditor.extractors.review_table as mod
    from unittest.mock import patch

    garbled_table = (
        "填表耳期: 113年4月30日\n送審類別\n辦理過程\n"
        "基準容積 1000\n獎勵樓地板面示 520\n權利變換計畫報核 112.09.08\n"
    )
    other_page = "申請獎勵容積面積 原建築基地基準容積之30% 報核版"

    fake_pages = [
        {"page_num": 1, "text": "", "tables": [], "_image_page": True},
        {"page_num": 2, "text": "", "tables": [], "_image_page": True},
    ]

    with patch.object(mod, "get_pdf_metadata", return_value={"total_pages": 2}), \
         patch.object(mod, "extract_pages_text", return_value=fake_pages), \
         patch("auditor.parsers.ocr_reader.ocr_available", return_value=True), \
         patch("auditor.parsers.ocr_reader.ocr_pages",
               return_value={0: other_page, 1: garbled_table}):
        page_num, text = mod._find_review_table_page("fake.pdf")

    assert page_num == 2, "scoring must pick the real 審議資料表 page, not the 獎勵計算頁"
    assert "報核" in text


def test_review_table_data_carries_filing_date():
    """ReviewTableData 應帶 report_filing_date 欄位（預設 None）。"""
    from auditor.models import ReviewTableData
    rt = ReviewTableData(
        case_name=None, implementer=None, implementer_id=None, submission_type=None,
        fill_date=None, land_area=None, base_floor_area=None, bonus_floor_area=None,
        bonus_limit=None, legal_parking=None, actual_parking=None, accessible_parking=None,
        ev_parking=None, owner_consent_ratio=None, raw_page=17,
    )
    assert rt.report_filing_date is None
    rt2 = ReviewTableData(
        case_name=None, implementer=None, implementer_id=None, submission_type=None,
        fill_date=None, land_area=None, base_floor_area=None, bonus_floor_area=None,
        bonus_limit=None, legal_parking=None, actual_parking=None, accessible_parking=None,
        ev_parking=None, owner_consent_ratio=None, raw_page=17,
        report_filing_date="112年9月8日",
    )
    assert rt2.report_filing_date == "112年9月8日"


def test_match_doc_type_jiangjie_shu():
    from auditor.extractors.front_docs import _match_doc_type
    assert _match_doc_type("切結書") == "切結書"


def test_match_doc_type_weituoshu():
    from auditor.extractors.front_docs import _match_doc_type
    # Real 委託書 page: spaced OCR title + content marker → matches
    assert _match_doc_type("委  託  書\n茲委託都更顧問公司辦理申請事宜") == "委託書"
    # Bare title only → treated as TOC reference, not a real page
    assert _match_doc_type("委  託  書") is None


# ── OCR-permissive date extraction ────────────────────────────────────────────

def test_extract_roc_date_ocr_compact():
    """OCR date: compact form without 中華民國 prefix."""
    from auditor.extractors.front_docs import _extract_roc_date_ocr
    assert _extract_roc_date_ocr("114年5月28日") == "114年5月28日"


def test_extract_roc_date_ocr_with_prefix():
    """OCR date: full form with prefix."""
    from auditor.extractors.front_docs import _extract_roc_date_ocr
    assert _extract_roc_date_ocr("中華民國114年5月28日") == "114年5月28日"


def test_extract_roc_date_ocr_spaced():
    """OCR date: spaces between chars (common in spread layouts)."""
    from auditor.extractors.front_docs import _extract_roc_date_ocr
    assert _extract_roc_date_ocr("114 年 5 月 28 日") == "114年5月28日"


def test_extract_roc_date_ocr_rejects_invalid_year():
    from auditor.extractors.front_docs import _extract_roc_date_ocr
    # year 99 is below 100 — too old to be a valid ROC year
    assert _extract_roc_date_ocr("99年5月28日") is None


def test_extract_roc_date_ocr_rejects_invalid_month():
    from auditor.extractors.front_docs import _extract_roc_date_ocr
    assert _extract_roc_date_ocr("114年13月1日") is None


# ── TOC detection in extract_front_docs ──────────────────────────────────────

def test_extract_front_docs_detects_all_docs_from_toc():
    """TOC page should register all doc types even without content pages."""
    from auditor.extractors.front_docs import extract_front_docs

    toc_text = (
        "目 錄\n"
        "都市更新事業計畫及權利變換計畫申請書 ...... I\n"
        "切結書 ........................................... II\n"
        "委託書(都更規劃) ......................... III-1\n"
        "臺北市都市更新審議資料表 ............... IV\n"
    )

    fake_pages = [{"page_num": 1, "text": toc_text, "tables": [], "_image_page": False}]

    with patch("auditor.extractors.front_docs.extract_pages_text", return_value=fake_pages), \
         patch("auditor.extractors.front_docs.scan_pages", return_value=[]):
        fd, _ = extract_front_docs("fake.pdf", use_ocr=False)

    assert fd.has_application
    assert fd.has_affidavit
    assert fd.poa_count >= 1


def test_extract_front_docs_supplement_fallback_date():
    """When front pages are image-based, date inferred from 謄本 reference."""
    from auditor.extractors.front_docs import extract_front_docs

    pages = [
        {"page_num": 1, "text": "目 錄\n切結書 ........ II", "tables": [], "_image_page": False},
        # image page — no text
        {"page_num": 2, "text": "", "tables": [], "_image_page": True},
        # supplementary text mentioning 謄本 date
        {"page_num": 25, "text": "依據114年5月28日謄本修正所有權人", "tables": [], "_image_page": False},
    ]

    with patch("auditor.extractors.front_docs.extract_pages_text", return_value=pages), \
         patch("auditor.extractors.front_docs.scan_pages", return_value=[]):
        fd, _ = extract_front_docs("fake.pdf", use_ocr=False)

    assert fd.report_date == "114年5月28日"
    assert "謄本" in fd.report_date_source


def test_extract_front_docs_no_ocr_flag_skips_ocr():
    """use_ocr=False must not call ocr_pages."""
    from auditor.extractors.front_docs import extract_front_docs

    pages = [{"page_num": i, "text": "", "tables": [], "_image_page": True} for i in range(1, 5)]

    with patch("auditor.extractors.front_docs.extract_pages_text", return_value=pages), \
         patch("auditor.extractors.front_docs.scan_pages", return_value=[]), \
         patch("auditor.parsers.ocr_reader.ocr_pages") as mock_ocr:
        extract_front_docs("fake.pdf", use_ocr=False)
        mock_ocr.assert_not_called()


# ── PaddleOCR integration (Item 1) ───────────────────────────────────────────

def _predict_result(pairs):
    """Build a mock paddle-3.x predict() result from (text, conf) pairs."""
    return [{"res": {
        "rec_texts": [t for t, _ in pairs],
        "rec_scores": [c for _, c in pairs],
    }}]


def test_detections_from_predict_empty_on_none():
    from auditor.parsers.ocr_reader import _detections_from_predict
    assert _detections_from_predict(None) == []
    assert _detections_from_predict([]) == []


def test_detections_from_predict_filters_low_confidence():
    """Detections below _OCR_MIN_CONFIDENCE must be excluded."""
    from auditor.parsers.ocr_reader import _detections_from_predict, _OCR_MIN_CONFIDENCE
    result = _predict_result([
        ("高信心", 0.95),
        ("低信心", _OCR_MIN_CONFIDENCE - 0.1),
        ("邊界值", _OCR_MIN_CONFIDENCE),
    ])
    texts = [d["text"] for d in _detections_from_predict(result)]
    assert "高信心" in texts
    assert "邊界值" in texts
    assert "低信心" not in texts


def test_detections_from_predict_extracts_all_texts():
    from auditor.parsers.ocr_reader import _detections_from_predict
    result = _predict_result([("line one", 0.9), ("line two", 0.8)])
    texts = [d["text"] for d in _detections_from_predict(result)]
    assert texts == ["line one", "line two"]


def test_detections_from_predict_converts_simplified_to_traditional():
    """PP-OCRv5 偶爾吐簡體，_to_traditional（OpenCC s2t）應轉回繁體。"""
    import importlib.util
    import pytest
    if importlib.util.find_spec("opencc") is None:
        pytest.skip("opencc not installed")
    from auditor.parsers.ocr_reader import _detections_from_predict
    result = _predict_result([("送審类别", 0.9), ("内湖區", 0.9)])
    texts = [d["text"] for d in _detections_from_predict(result)]
    assert "送審類別" in texts   # 类别 → 類別
    assert "內湖區" in texts      # 内 → 內


def test_cap_size_downscales_large_and_passes_small():
    """_cap_size caps the longest side to _OCR_MAX_DIM (keeps aspect); small→untouched."""
    import numpy as np
    from auditor.parsers.ocr_reader import _cap_size, _OCR_MAX_DIM
    big = np.zeros((3000, 5000, 3), dtype=np.uint8)
    capped = _cap_size(big)
    assert max(capped.shape[:2]) == _OCR_MAX_DIM
    assert capped.shape[1] > capped.shape[0]  # width>height aspect preserved
    small = np.zeros((100, 200, 3), dtype=np.uint8)
    assert _cap_size(small).shape == small.shape


def test_ocr_page_uses_paddle_reader(tmp_path):
    """ocr_page must call reader.predict() and return parsed text."""
    import auditor.parsers.ocr_reader as mod
    from unittest.mock import MagicMock, patch

    fake_reader = MagicMock()
    fake_reader.predict.return_value = _predict_result([("測試文字", 0.95)])

    fake_doc = MagicMock()
    fake_pix = MagicMock()
    fake_pix.tobytes.return_value = _make_png_bytes()
    fake_doc.__len__ = lambda self: 5
    fake_doc.__getitem__ = lambda self, i: MagicMock(
        get_pixmap=lambda **kw: fake_pix
    )
    fake_doc.__enter__ = lambda self: self
    fake_doc.__exit__ = MagicMock(return_value=False)

    with patch.object(mod, "_FITZ_OK", True), \
         patch.object(mod, "_paddle_reader", fake_reader), \
         patch.object(mod, "_OCR_AVAILABLE", True), \
         patch.object(mod._fitz, "open", return_value=fake_doc):
        result = mod.ocr_page("fake.pdf", 0)

    assert "測試文字" in result
    fake_reader.predict.assert_called_once()


def test_ocr_pages_per_page_call(tmp_path):
    """ocr_pages must call reader.predict() once per page with a single image."""
    import auditor.parsers.ocr_reader as mod
    from unittest.mock import MagicMock, patch

    fake_reader = MagicMock()
    # Each per-page call returns a single-image predict result
    fake_reader.predict.side_effect = [
        _predict_result([("頁一", 0.9)]),
        _predict_result([("頁二", 0.8)]),
    ]

    fake_pix = MagicMock()
    fake_pix.tobytes.return_value = _make_png_bytes()
    fake_page = MagicMock()
    fake_page.get_pixmap.return_value = fake_pix

    fake_doc = MagicMock()
    fake_doc.__len__ = lambda self: 10
    fake_doc.__getitem__ = lambda self, i: fake_page
    fake_doc.close = MagicMock()

    with patch.object(mod, "_FITZ_OK", True), \
         patch.object(mod, "_paddle_reader", fake_reader), \
         patch.object(mod, "_OCR_AVAILABLE", True), \
         patch.object(mod, "_file_hash", return_value=None), \
         patch.object(mod._fitz, "open", return_value=fake_doc):
        result = mod.ocr_pages("fake.pdf", [0, 1])

    assert fake_reader.predict.call_count == 2, "Each page must be a separate reader.predict() call"
    # every call must pass a single image (ndarray), never a list
    for call in fake_reader.predict.call_args_list:
        assert not isinstance(call.args[0], list), "must not pass a list to reader.predict()"
    assert "頁一" in result.get(0, "")
    assert "頁二" in result.get(1, "")


def _make_png_bytes() -> bytes:
    """Return minimal valid PNG bytes (1x1 white pixel) for mocking."""
    from PIL import Image
    import io
    img = Image.new("RGB", (1, 1), color=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── deskew (Item 3) ──────────────────────────────────────────────────────────

def test_deskew_handles_no_lines():
    """_deskew must return the original image when no lines are detected."""
    import numpy as np
    from auditor.parsers.ocr_reader import _deskew

    gray = np.zeros((100, 100), dtype=np.uint8)  # blank image → no edges → no lines
    result = _deskew(gray)
    assert result.shape == gray.shape


def test_deskew_skips_large_angles():
    """_deskew must return the original image when detected skew > 15°."""
    import numpy as np
    import cv2
    from auditor.parsers.ocr_reader import _deskew

    # Create an image with diagonal lines at ~30° to trigger the guard
    gray = np.zeros((200, 200), dtype=np.uint8)
    for i in range(200):
        j = int(i * np.tan(np.radians(30)))
        if 0 <= j < 200:
            gray[i, j] = 255

    original_sum = gray.sum()
    result = _deskew(gray)
    # Since angle > 15°, result should be the original (unchanged)
    assert result.sum() == original_sum


def test_deskew_corrects_slight_rotation():
    """_deskew must reduce the skew angle for slightly tilted horizontal lines."""
    import numpy as np
    import cv2
    from auditor.parsers.ocr_reader import _deskew

    # Create an image with horizontal lines then rotate it by 5°
    gray = np.zeros((300, 400), dtype=np.uint8)
    for y in [75, 150, 225]:
        gray[y, 50:350] = 255  # horizontal white lines

    angle_deg = 5.0
    M = cv2.getRotationMatrix2D((200, 150), angle_deg, 1.0)
    rotated = cv2.warpAffine(gray, M, (400, 300), borderMode=cv2.BORDER_REPLICATE)

    corrected = _deskew(rotated)

    # Measure line angles in corrected image — they should be closer to 0 than in rotated
    edges_rot = cv2.Canny(rotated, 50, 150, apertureSize=3)
    edges_cor = cv2.Canny(corrected, 50, 150, apertureSize=3)

    def _median_angle(edges: np.ndarray) -> float:
        lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=30, minLineLength=30, maxLineGap=5)
        if lines is None:
            return 0.0
        angles = []
        for ln in lines:
            x1, y1, x2, y2 = ln[0]
            if x2 != x1:
                a = np.degrees(np.arctan2(y2 - y1, x2 - x1))
                if abs(a) < 45:
                    angles.append(a)
        return float(np.median(angles)) if angles else 0.0

    angle_before = abs(_median_angle(edges_rot))
    angle_after = abs(_median_angle(edges_cor))
    assert angle_after < angle_before, (
        f"Deskew did not reduce skew: before={angle_before:.2f}°, after={angle_after:.2f}°"
    )


# ── Sauvola binarization (Item 4) ────────────────────────────────────────────

def test_binarize_produces_binary_image():
    """Output of _binarize must contain only pixel values 0 and 255."""
    import numpy as np
    from auditor.parsers.ocr_reader import _binarize

    rng = np.random.default_rng(42)
    gray = rng.integers(0, 256, (100, 100), dtype=np.uint8)
    result = _binarize(gray)

    unique = set(result.flatten().tolist())
    assert unique <= {0, 255}, f"Non-binary values found: {unique - {0, 255}}"


def test_binarize_fallback_when_skimage_unavailable(monkeypatch):
    """_binarize must fall back to adaptive threshold if scikit-image is absent."""
    import sys
    import numpy as np
    from unittest.mock import patch

    # Remove skimage from sys.modules to simulate unavailability
    with patch.dict(sys.modules, {"skimage": None, "skimage.filters": None}):
        import importlib
        import auditor.parsers.ocr_reader as mod
        importlib.reload(mod)

        rng = np.random.default_rng(0)
        gray = rng.integers(0, 256, (64, 64), dtype=np.uint8)
        result = mod._binarize(gray)

    # Must still produce a valid binary image
    unique = set(result.flatten().tolist())
    assert unique <= {0, 255}


def test_binarize_sauvola_preferred_when_available():
    """When scikit-image is importable, _binarize must use Sauvola (not adaptive)."""
    import numpy as np
    from unittest.mock import patch, MagicMock
    import auditor.parsers.ocr_reader as mod

    mock_sauvola = MagicMock(return_value=np.zeros((10, 10)))
    with patch.dict("sys.modules", {"skimage": MagicMock(), "skimage.filters": MagicMock(threshold_sauvola=mock_sauvola)}):
        import importlib
        importlib.reload(mod)
        gray = np.zeros((10, 10), dtype=np.uint8)
        mod._binarize(gray)

    mock_sauvola.assert_called_once()


# ── confidence filtering (Item 2) ─────────────────────────────────────────────

def test_ocr_min_confidence_constant_exists():
    from auditor.parsers.ocr_reader import _OCR_MIN_CONFIDENCE
    assert 0.0 < _OCR_MIN_CONFIDENCE < 1.0


def test_detections_from_predict_skips_none_items():
    """None entries in the predict result list must be skipped gracefully."""
    from auditor.parsers.ocr_reader import _detections_from_predict
    result = [None, {"res": {"rec_texts": ["valid"], "rec_scores": [0.9]}}, None]
    dets = _detections_from_predict(result)
    assert [d["text"] for d in dets] == ["valid"]


# ── cache version key (Item 7) ────────────────────────────────────────────────

def test_cache_schema_includes_preprocess_version(tmp_path):
    """The SQLite cache table must have a preprocess_version column."""
    import sqlite3
    import auditor.parsers.ocr_reader as mod
    from unittest.mock import patch

    db_path = tmp_path / "ocr_cache.db"
    with patch.object(mod, "_CACHE_DB", db_path), \
         patch.object(mod, "_CACHE_DIR", tmp_path):
        conn = mod._get_cache_conn()
        assert conn is not None
        cols = {row[1] for row in conn.execute("PRAGMA table_info(ocr_cache)")}
        assert "preprocess_version" in cols
        conn.close()


def test_cache_get_uses_preprocess_version(tmp_path):
    """_cache_get must not return entries from a different preprocess_version."""
    import sqlite3
    import auditor.parsers.ocr_reader as mod
    from unittest.mock import patch

    db_path = tmp_path / "ocr_cache.db"
    with patch.object(mod, "_CACHE_DB", db_path), \
         patch.object(mod, "_CACHE_DIR", tmp_path):
        conn = mod._get_cache_conn()
        # Write with version 1 directly
        conn.execute(
            "INSERT INTO ocr_cache (file_hash, page_idx, zoom, preprocess_version, text)"
            " VALUES (?,?,?,?,?)",
            ("abc", 0, 2.0, 1, "old-text"),
        )
        conn.commit()
        # _cache_get with current _PREPROCESS_VERSION must not return old entry
        result = mod._cache_get(conn, "abc", 0, 2.0)
        assert result is None
        conn.close()


def test_cache_put_uses_preprocess_version(tmp_path):
    """_cache_put must store the current _PREPROCESS_VERSION."""
    import auditor.parsers.ocr_reader as mod
    from unittest.mock import patch

    db_path = tmp_path / "ocr_cache.db"
    with patch.object(mod, "_CACHE_DB", db_path), \
         patch.object(mod, "_CACHE_DIR", tmp_path):
        conn = mod._get_cache_conn()
        mod._cache_put(conn, "abc", 0, 2.0, "hello")
        row = conn.execute(
            "SELECT preprocess_version FROM ocr_cache WHERE file_hash=? AND page_idx=? AND zoom=?",
            ("abc", 0, 2.0),
        ).fetchone()
        assert row is not None
        assert row[0] == mod._PREPROCESS_VERSION
        conn.close()


def test_cache_migration_drops_old_schema(tmp_path):
    """If the existing table is missing preprocess_version, it must be recreated."""
    import sqlite3
    import auditor.parsers.ocr_reader as mod
    from unittest.mock import patch

    db_path = tmp_path / "ocr_cache.db"
    # Create old-schema table
    old_conn = sqlite3.connect(str(db_path))
    old_conn.execute(
        "CREATE TABLE ocr_cache"
        " (file_hash TEXT, page_idx INTEGER, zoom REAL, text TEXT,"
        "  PRIMARY KEY (file_hash, page_idx, zoom))"
    )
    old_conn.execute("INSERT INTO ocr_cache VALUES ('x', 0, 2.0, 'old')")
    old_conn.commit()
    old_conn.close()

    with patch.object(mod, "_CACHE_DB", db_path), \
         patch.object(mod, "_CACHE_DIR", tmp_path):
        conn = mod._get_cache_conn()
        assert conn is not None
        cols = {row[1] for row in conn.execute("PRAGMA table_info(ocr_cache)")}
        assert "preprocess_version" in cols
        # Old data should be gone
        count = conn.execute("SELECT COUNT(*) FROM ocr_cache").fetchone()[0]
        assert count == 0
        conn.close()
