"""Local-day bounds: midnight, and both DST transitions.

America/Los_Angeles in 2026 springs forward on March 8 and falls back on
November 1. Instants below are UTC. The helper returns [start, end).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from tracker.days import local_date, local_day_bounds

LA = 'America/Los_Angeles'
UTC = timezone.utc


def _utc(year, month, day, hour, minute=0, second=0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=UTC)


def test_standard_day_midnight_boundary_is_eight_hours_ahead_in_winter():
    day = date(2026, 1, 15)
    start, end = local_day_bounds(day, LA)

    assert start == _utc(2026, 1, 15, 8)
    assert end == _utc(2026, 1, 16, 8)
    assert end - start == timedelta(hours=24)
    assert local_date(_utc(2026, 1, 16, 7, 59), LA) == date(2026, 1, 15)
    assert local_date(start, LA) == day
    assert local_date(end - timedelta(seconds=1), LA) == day
    assert local_date(end, LA) == date(2026, 1, 16)


def test_summer_midnight_splits_2359_and_0001():
    # PDT is UTC-7. 06:59 UTC is 23:59 the previous local day.
    late = _utc(2026, 6, 15, 6, 59)
    early = _utc(2026, 6, 15, 7, 1)
    assert local_date(late, LA) == date(2026, 6, 14)
    assert local_date(early, LA) == date(2026, 6, 15)

    start, end = local_day_bounds(date(2026, 6, 14), LA)
    assert start <= late < end
    assert not (start <= early < end)
    assert end - start == timedelta(hours=24)


def test_spring_forward_day_is_23_hours():
    day = date(2026, 3, 8)
    start, end = local_day_bounds(day, LA)

    # Midnight is still PST (UTC-8). The next midnight is PDT (UTC-7).
    assert start == _utc(2026, 3, 8, 8)
    assert end == _utc(2026, 3, 9, 7)
    assert end - start == timedelta(hours=23)
    assert local_date(_utc(2026, 3, 8, 7, 59), LA) == date(2026, 3, 7)
    assert local_date(_utc(2026, 3, 8, 8, 1), LA) == day
    assert local_date(_utc(2026, 3, 9, 6, 59), LA) == day
    assert local_date(end, LA) == date(2026, 3, 9)
    # 01:30 PST and 03:30 PDT are both on the short day. 02:30 does not exist.
    assert local_date(_utc(2026, 3, 8, 9, 30), LA) == day
    assert local_date(_utc(2026, 3, 8, 10, 30), LA) == day


def test_fall_back_day_is_25_hours_and_keeps_both_folds():
    day = date(2026, 11, 1)
    start, end = local_day_bounds(day, LA)

    # Midnight is still PDT (UTC-7). The next midnight is PST (UTC-8).
    assert start == _utc(2026, 11, 1, 7)
    assert end == _utc(2026, 11, 2, 8)
    assert end - start == timedelta(hours=25)
    assert local_date(_utc(2026, 11, 1, 6, 59), LA) == date(2026, 10, 31)
    assert local_date(_utc(2026, 11, 1, 7, 1), LA) == day
    assert local_date(_utc(2026, 11, 2, 7, 59), LA) == day
    assert local_date(end, LA) == date(2026, 11, 2)
    # Both 01:30 occurrences (PDT, then PST) belong to November 1.
    assert local_date(_utc(2026, 11, 1, 8, 30), LA) == day
    assert local_date(_utc(2026, 11, 1, 9, 30), LA) == day


def test_new_york_spring_forward_is_also_23_hours():
    start, end = local_day_bounds(date(2026, 3, 8), 'America/New_York')
    assert start == _utc(2026, 3, 8, 5)
    assert end == _utc(2026, 3, 9, 4)
    assert end - start == timedelta(hours=23)


def test_pre_epoch_winter_midnight_uses_the_same_offset():
    start, end = local_day_bounds(date(1965, 1, 15), LA)
    assert start == _utc(1965, 1, 15, 8)
    assert end - start == timedelta(hours=24)


def test_naive_instant_and_unknown_zone_are_rejected():
    with pytest.raises(ValueError, match='timezone-aware'):
        local_date(datetime(2026, 1, 15, 12, 0), LA)
    with pytest.raises(ValueError, match='IANA'):
        local_day_bounds(date(2026, 1, 15), 'PST')
    with pytest.raises(TypeError):
        local_day_bounds(datetime(2026, 1, 15, tzinfo=UTC), LA)
