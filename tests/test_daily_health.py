import json
from pathlib import Path
import tempfile
import unittest
import sqlite3
from datetime import datetime, timezone, timedelta

from crypto_radar.daily_health import save_report, build_health


class DailyHealthTests(unittest.TestCase):
    def test_archives_and_replaces_latest_without_overwriting_history(self):
        with tempfile.TemporaryDirectory() as directory:
            a = {'checked_at': '2026-11-02T09:00:00+00:00', 'data_status': 'unavailable'}
            first = save_report(a, directory)
            b = dict(a, checked_at='2026-11-03T09:00:00+00:00', data_status='available')
            save_report(b, directory)
            self.assertEqual(json.loads(first.read_text()), a)
            self.assertEqual(json.loads((Path(directory)/'latest.json').read_text()), b)
            with self.assertRaises(FileExistsError):
                save_report(a, directory)

    def test_missing_database_is_not_created(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'missing.db'
            with self.assertRaises(sqlite3.OperationalError):
                build_health(path, datetime(2026,11,2,tzinfo=timezone.utc))
            self.assertFalse(path.exists())

    def test_health_reads_runtime_and_actual_wall_clock_freshness(self):
        from test_validation_report import ValidationReportTests, START
        fixture = ValidationReportTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        result = build_health(fixture.path, START+timedelta(hours=31))
        self.assertTrue(result['runtime_enabled'])
        self.assertEqual(result['configured_assets'], 1)
        self.assertEqual(result['distinct_resolved_assets'], 0)
        self.assertEqual(result['scanner_age_seconds'], 3600)
