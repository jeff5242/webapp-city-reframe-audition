"""審查速度基準量測（副總 #9：固定環境、跑 N 次取平均，別再一個講 1 分鐘一個講 8 分鐘）。

量測「一件案子從上傳到產出報告」的端到端耗時，重複 N 次後回報 平均／中位數／
p95／最小／最大／標準差，並記錄執行環境（機種、CPU、RAM、是否有 PaddleOCR），
讓速度數字可重現、可驗證。第一次（cold start：載入 OCR 模型）預設不計入平均。

CLI:
    python -m auditor.eval.benchmark <事業計畫.pdf> [權利變換.pdf] --runs 10 --warmup 1
    python -m auditor.eval.benchmark <bp.pdf> --runs 10 --json out.json
"""
from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter
from typing import Callable, List, Optional, Sequence


@dataclass(frozen=True)
class TimingStats:
    """單位皆為秒。純由一組耗時計算，無副作用（便於測試）。"""
    runs: int
    mean: float
    median: float
    p95: float
    minimum: float
    maximum: float
    stdev: float


def compute_stats(durations: Sequence[float]) -> TimingStats:
    """由每次耗時（秒）算出統計量。空輸入視為錯誤（呼叫端需保證至少一筆）。"""
    if not durations:
        raise ValueError("durations 不可為空")
    ordered = sorted(durations)
    n = len(ordered)
    # p95：最近秩次法，單筆時即為該值
    idx = max(0, min(n - 1, int(round(0.95 * (n - 1)))))
    return TimingStats(
        runs=n,
        mean=statistics.mean(ordered),
        median=statistics.median(ordered),
        p95=ordered[idx],
        minimum=ordered[0],
        maximum=ordered[-1],
        stdev=statistics.stdev(ordered) if n > 1 else 0.0,
    )


def capture_environment() -> dict:
    """記錄官方基準所需的環境資訊（機種／CPU／RAM／OCR 後端）。"""
    env: dict = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or platform.machine(),
        "python": platform.python_version(),
    }
    try:
        import psutil

        env["cpu_logical"] = psutil.cpu_count(logical=True)
        env["cpu_physical"] = psutil.cpu_count(logical=False)
        env["ram_gb"] = round(psutil.virtual_memory().total / (1024 ** 3), 1)
    except Exception:
        import os

        env["cpu_logical"] = os.cpu_count()
        env["cpu_physical"] = None
        env["ram_gb"] = None
    env["paddleocr"] = _paddleocr_available()
    return env


def _paddleocr_available() -> bool:
    try:
        import paddleocr  # noqa: F401

        return True
    except Exception:
        return False


@dataclass(frozen=True)
class BenchmarkReport:
    stats: TimingStats
    per_run_seconds: List[float]
    warmup_seconds: List[float]
    environment: dict
    case: str

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def _default_runner(pdf_paths: Sequence[Path]) -> None:
    """跑一次真實審查流程（端到端，含擷取／規則／標註／產報告）。"""
    from ..main import _AUDIT_TASKS, _TASKS_LOCK, _run_audit_sync

    bp = pdf_paths[0]
    re = pdf_paths[1] if len(pdf_paths) > 1 else None
    task_id = f"bench-{uuid.uuid4().hex[:8]}"
    with _TASKS_LOCK:
        _AUDIT_TASKS[task_id] = {"status": "queued", "progress": ""}
    try:
        _run_audit_sync(
            task_id, None, None,
            bp.read_bytes(), bp.name,
            re.read_bytes() if re else None, re.name if re else None,
        )
        with _TASKS_LOCK:
            status = _AUDIT_TASKS.get(task_id, {}).get("status")
        if status != "done":
            raise RuntimeError(f"審查未完成：status={status}")
    finally:
        with _TASKS_LOCK:
            _AUDIT_TASKS.pop(task_id, None)


def benchmark_audit(
    pdf_paths: Sequence[Path],
    runs: int = 10,
    warmup: int = 1,
    runner: Optional[Callable[[Sequence[Path]], None]] = None,
    on_progress: Optional[Callable[[str], None]] = None,
) -> BenchmarkReport:
    """重複跑 N 次審查，回報統計。前 `warmup` 次不計入平均（排除 cold start）。"""
    if runs < 1:
        raise ValueError("runs 至少為 1")
    run = runner or _default_runner

    def _emit(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    warmup_times: List[float] = []
    for i in range(max(0, warmup)):
        _emit(f"暖機 {i + 1}/{warmup}…")
        t0 = perf_counter()
        run(pdf_paths)
        warmup_times.append(perf_counter() - t0)

    durations: List[float] = []
    for i in range(runs):
        _emit(f"量測 {i + 1}/{runs}…")
        t0 = perf_counter()
        run(pdf_paths)
        durations.append(perf_counter() - t0)

    return BenchmarkReport(
        stats=compute_stats(durations),
        per_run_seconds=[round(d, 3) for d in durations],
        warmup_seconds=[round(d, 3) for d in warmup_times],
        environment=capture_environment(),
        case=Path(pdf_paths[0]).name,
    )


def _format_report(report: BenchmarkReport) -> str:
    s = report.stats
    env = report.environment
    lines = [
        "═══ 審查速度基準 ═══",
        f"案例：{report.case}",
        f"環境：{env.get('platform')} · {env.get('cpu_logical')} vCPU · "
        f"{env.get('ram_gb')} GB RAM · PaddleOCR={'有' if env.get('paddleocr') else '無'}",
        f"樣本：{s.runs} 次（暖機 {len(report.warmup_seconds)} 次不計入）",
        "",
        f"  平均    {s.mean:8.2f} 秒",
        f"  中位數  {s.median:8.2f} 秒",
        f"  p95     {s.p95:8.2f} 秒",
        f"  最快    {s.minimum:8.2f} 秒",
        f"  最慢    {s.maximum:8.2f} 秒",
        f"  標準差  {s.stdev:8.2f} 秒",
    ]
    if report.warmup_seconds:
        lines.append(f"  (cold start：{report.warmup_seconds[0]:.2f} 秒)")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="審查速度基準量測（固定環境、取平均）")
    parser.add_argument("pdfs", nargs="+", type=Path, help="事業計畫 PDF（可再加權利變換 PDF）")
    parser.add_argument("--runs", type=int, default=10, help="計入平均的次數（預設 10）")
    parser.add_argument("--warmup", type=int, default=1, help="暖機次數，不計入（預設 1）")
    parser.add_argument("--json", type=Path, default=None, help="另存 JSON 結果")
    args = parser.parse_args(argv)

    for p in args.pdfs:
        if not p.exists():
            print(f"找不到檔案：{p}", file=sys.stderr)
            return 2

    report = benchmark_audit(
        args.pdfs, runs=args.runs, warmup=args.warmup,
        on_progress=lambda m: print(m, file=sys.stderr, flush=True),
    )
    print(_format_report(report))
    if args.json:
        args.json.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        print(f"\nJSON → {args.json}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
