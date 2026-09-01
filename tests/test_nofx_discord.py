import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from nofx_discord import build_daily_plan


def test_daily_plan_is_stable_serial_and_complete():
	day = date(2026, 8, 31)
	first = build_daily_plan(day, list(range(1, 21)))
	second = build_daily_plan(day, list(range(1, 21)))
	assert first == second
	assert sorted(item['slot'] for item in first) == list(range(1, 21))
	times = [datetime.fromisoformat(item['scheduled_at']) for item in first]
	assert times == sorted(times)
	assert all(12 * 60 <= (right - left).total_seconds() <= 22 * 60 for left, right in zip(times, times[1:]))
