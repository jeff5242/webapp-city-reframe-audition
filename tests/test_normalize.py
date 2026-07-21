"""③ 正規化層測試。"""
from auditor.extractors.normalize import (
    normalize_text,
    normalize_area_unit,
    parse_number,
    normalize_date,
    roc_to_ad,
    values_equal,
)


class TestNormalizeText:
    def test_fullwidth_to_halfwidth(self):
        assert normalize_text("ＡＢＣ１２３") == "ABC123"

    def test_strips_spaces(self):
        assert normalize_text("2 812　元") == "2812元"

    def test_tai_variant_unified(self):
        assert normalize_text("台北市") == "臺北市"

    def test_none_returns_empty(self):
        assert normalize_text(None) == ""


class TestAreaUnit:
    def test_variants_unified(self):
        assert normalize_area_unit("100㎡") == "100m2"
        assert normalize_area_unit("100平方公尺") == "100m2"
        assert normalize_area_unit("100平方米") == "100m2"


class TestParseNumber:
    def test_thousands_separator(self):
        assert parse_number("2,812") == 2812.0

    def test_fullwidth_comma_and_digits(self):
        assert parse_number("２，８１２") == 2812.0

    def test_embedded_space(self):
        # 小塊 OCR 常把 2,812 讀成 2 812
        assert parse_number("2 812") == 2812.0

    def test_decimal(self):
        assert parse_number("1,234.56") == 1234.56

    def test_no_number(self):
        assert parse_number("無") is None

    def test_none(self):
        assert parse_number(None) is None


class TestNormalizeDate:
    def test_roc_chinese(self):
        assert normalize_date("112年3月24日") == "2023-03-24"

    def test_roc_dotted(self):
        assert normalize_date("112.03.24") == "2023-03-24"

    def test_ad_dashed(self):
        assert normalize_date("2023-03-24") == "2023-03-24"

    def test_roc_to_ad_helper(self):
        assert roc_to_ad(112) == 2023

    def test_invalid_month(self):
        assert normalize_date("112年13月01日") is None

    def test_no_date(self):
        assert normalize_date("報核中") is None


class TestValuesEqual:
    def test_both_empty(self):
        assert values_equal(None, "") is True

    def test_one_empty(self):
        assert values_equal("2812", None) is False

    def test_number_format_difference_recovered(self):
        # 這正是零樣本被誤扣分的典型：值一樣、寫法不同
        assert values_equal("2,812", "2812") is True
        assert values_equal("２，８１２", "2812") is True

    def test_tai_variant_recovered(self):
        assert values_equal("台北市", "臺北市") is True

    def test_area_unit_recovered(self):
        assert values_equal("100㎡", "100平方公尺") is True

    def test_date_format_recovered(self):
        assert values_equal("112年3月24日", "2023-03-24") is True

    def test_genuine_mismatch_not_recovered(self):
        assert values_equal("2812", "2813") is False

    def test_number_tolerance(self):
        assert values_equal("100.0", "100.4", num_tol=0.5) is True
        assert values_equal("100.0", "101.0", num_tol=0.5) is False

    def test_landnumber_not_numeric_compared(self):
        # 地號含數字但非純數 → 不走數值比對，避免誤判
        assert values_equal("信義段一小段123地號", "信義段一小段124地號") is False
