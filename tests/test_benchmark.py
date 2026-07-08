"""審查速度基準（副總 #9）：純統計 + 量測迴圈（假 runner，不跑真實審查）。"""
from __future__ import annotations

import pytest

from auditor.eval.benchmark import (
    BenchmarkReport,
    benchmark_audit,
    capture_environment,
    compute_stats,
)


# ── compute_stats（純函式）────────────────────────────────────────────────────

def test_stats_single_value():
    s = compute_stats([4.0])
    assert s.runs == 1
    assert s.mean == s.median == s.p95 == s.minimum == s.maximum == 4.0
    assert s.stdev == 0.0


def test_stats_basic_aggregates():
    s = compute_stats([10.0, 20.0, 30.0])
    assert s.runs == 3
    assert s.mean == 20.0
    assert s.median == 20.0
    assert s.minimum == 10.0
    assert s.maximum == 30.0
    assert s.stdev == pytest.approx(10.0)


def test_stats_ignores_input_order():
    a = compute_stats([30.0, 10.0, 20.0])
    b = compute_stats([10.0, 20.0, 30.0])
    assert a == b


def test_stats_p95_picks_high_end():
    # 1..100，最近秩次：idx = round(0.95*(100-1)) = 94 → ordered[94] = 95.0
    s = compute_stats([float(i) for i in range(1, 101)])
    assert s.p95 == 95.0
    assert s.median == pytest.approx(50.5)
    assert s.maximum == 100.0


def test_stats_empty_raises():
    with pytest.raises(ValueError):
        compute_stats([])


# ── benchmark_audit（假 runner）───────────────────────────────────────────────

def test_benchmark_runs_counted_warmup_excluded():
    calls = {"n": 0}

    def fake_runner(paths):
        calls["n"] += 1

    report = benchmark_audit(["x.pdf"], runs=5, warmup=2, runner=fake_runner)
    assert calls["n"] == 7                       # 2 暖機 + 5 量測
    assert report.stats.runs == 5                # 只有 5 次計入
    assert len(report.per_run_seconds) == 5
    assert len(report.warmup_seconds) == 2


def test_benchmark_zero_warmup():
    report = benchmark_audit(["x.pdf"], runs=3, warmup=0, runner=lambda p: None)
    assert report.stats.runs == 3
    assert report.warmup_seconds == []


def test_benchmark_invalid_runs_raises():
    with pytest.raises(ValueError):
        benchmark_audit(["x.pdf"], runs=0, runner=lambda p: None)


def test_benchmark_records_case_name():
    from pathlib import Path
    report = benchmark_audit([Path("事業計畫.pdf")], runs=1, warmup=0, runner=lambda p: None)
    assert report.case == "事業計畫.pdf"


def test_benchmark_report_serializable():
    report = benchmark_audit(["x.pdf"], runs=2, warmup=0, runner=lambda p: None)
    d = report.to_dict()
    assert set(d) >= {"stats", "per_run_seconds", "environment", "case"}
    assert d["stats"]["runs"] == 2


# ── environment capture ───────────────────────────────────────────────────────

def test_capture_environment_has_core_keys():
    env = capture_environment()
    for k in ("platform", "machine", "python", "cpu_logical", "paddleocr"):
        assert k in env
    assert isinstance(env["paddleocr"], bool)
