"""Focused tests for the quota forecasting helpers."""
import os
os.environ.setdefault("BOT_TOKEN", "123456789:AA_test_quota_forecast")
os.environ.setdefault("OWNER_ID", "9001")

import bot

BASE_TS = 1_700_000_000.0

# Linear values increasing by one unit per hour yield a +24 units/day slope.
history = [
    {"ts": BASE_TS + i * 3600, "cpu_pct": 10 + i}
    for i in range(10)
]
trend = bot._quota_trend(history, "cpu_pct")
assert trend["ready"] is True
assert trend["count"] == 10
assert abs(trend["per_day"] - 24.0) < 1e-6
assert abs(bot._quota_time_to_limit(trend, 100) - (100 - 19) / 24) < 1e-6

# Flat or declining trends do not produce a predicted quota-crossing date.
flat = bot._quota_trend(
    [{"ts": BASE_TS + i * 3600, "ram_bytes": 1000} for i in range(8)],
    "ram_bytes",
)
assert flat["ready"] and flat["per_day"] == 0
assert bot._quota_time_to_limit(flat, 2000) is None

declining = bot._quota_trend(
    [{"ts": BASE_TS + i * 3600, "storage_bytes": 10000 - i * 200} for i in range(8)],
    "storage_bytes",
)
assert declining["ready"] and declining["per_day"] < 0

# Forecast should stay in the data-collection state until enough history exists.
not_ready = bot._quota_trend(
    [{"ts": BASE_TS + i * 3600, "cpu_pct": i} for i in range(5)],
    "cpu_pct",
)
assert not_ready["ready"] is False

old_snapshot = bot._quota_user_snapshot
old_history = bot._quota_history_load
try:
    bot._quota_user_snapshot = lambda uid: {
        "cpu_pct": 20.0, "cpu_limit_pct": 100.0,
        "ram_bytes": 50 * 1024 * 1024, "ram_limit_bytes": 256 * 1024 * 1024,
        "storage_bytes": 2 * 1024 * 1024, "bot_count": 1, "bot_limit": 2,
    }
    bot._quota_history_load = lambda uid: history
    rendered = bot._format_quota_forecast(123)
    assert "Quota forecast" in rendered
    assert "CPU" in rendered and "RAM" in rendered
    assert "storage has no configured per-user cap" in rendered
    assert "in about" in rendered
finally:
    bot._quota_user_snapshot = old_snapshot
    bot._quota_history_load = old_history

print("Quota forecast regression tests passed")
