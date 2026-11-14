"""Backup retention must never delete what it promised to keep."""
import datetime
import importlib.util
import pathlib

spec = importlib.util.spec_from_file_location('backup', pathlib.Path(__file__).parents[3] / 'infra/backup/backup.py')
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)
NOW = datetime.datetime(2027, 6, 15, 2, 20, tzinfo=datetime.timezone.utc)


def days(*ages):
    return {f'a{age}': NOW - datetime.timedelta(days=age) for age in ages}


def test_everything_recent_is_kept():
    assert backup.expired(days(*range(0, 30)), NOW, 30, 12) == []


def test_older_than_daily_window_keeps_newest_of_each_month():
    archives = days(0, 1, 2, 3, 4, 5, 6, 40, 45, 50, 75, 80)
    removed = set(backup.expired(archives, NOW, 30, 12))
    # 40/45 days ago fall in May (newest: 40), 50 in April... by calendar month of the archive date
    kept = set(archives) - removed
    by_month = {}
    for name in kept:
        if (NOW - archives[name]).days > 30:
            by_month.setdefault(archives[name].strftime('%Y%m'), []).append(name)
    assert all(len(v) == 1 for v in by_month.values()) and by_month
    for month, (name,) in by_month.items():
        same = [n for n in archives if archives[n].strftime('%Y%m') == month]
        assert archives[name] == max(archives[n] for n in same)


def test_beyond_monthly_window_is_removed_but_never_the_newest_seven():
    archives = days(400, 401, 402, 500, 600, 700, 800, 900)
    removed = backup.expired(archives, NOW, 30, 12)
    assert removed == ['a900'] and len(archives) - len(removed) == backup.KEEP_MIN
