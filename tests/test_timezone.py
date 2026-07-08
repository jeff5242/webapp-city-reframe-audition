"""審查時間以台北時區呈現（副總回饋：目前顯示 UTC）。"""
from __future__ import annotations

from datetime import datetime, timezone

from auditor.main import _TAIPEI_TZ


def test_taipei_tz_is_utc_plus_8():
    assert _TAIPEI_TZ.utcoffset(None).total_seconds() == 8 * 3600


def test_taipei_time_is_8h_ahead_of_utc():
    utc = datetime.now(timezone.utc)
    tpe = datetime.now(_TAIPEI_TZ)
    # 同一瞬間，台北時鐘數字比 UTC 多 8 小時（比較 hour 差，容許跨日）
    diff = (tpe.utcoffset().total_seconds() - utc.utcoffset().total_seconds()) / 3600
    assert diff == 8
