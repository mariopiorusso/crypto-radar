import hashlib
import json
import subprocess
import sys
import unittest

from crypto_radar.episode_report import build_report
import test_analysis_snapshot


class EpisodeReportTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_analysis_snapshot.AnalysisSnapshotTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.db = self.fixture.db
        self.source = self.fixture.source
        self.db.execute('UPDATE experiment_outcomes SET return_pct=2,excess_btc_pct=1 WHERE evaluation_id=1 AND horizon_hours=1')
        self.db.execute('UPDATE experiment_outcomes SET return_pct=6,excess_btc_pct=3 WHERE evaluation_id=2 AND horizon_hours=1')
        self.fixture.insert('research_episodes', id=2, coin_id='other',
                            started_ts=self.fixture.recent, last_signal_ts=self.fixture.recent,
                            anchor_ts=self.fixture.recent, anchor_price=10)
        self.fixture.insert('experiment_evaluations', id=3, scan_id=2, coin_id='other',
                            signal_version='1.2', experiment_group='no_signal', episode_id=2,
                            detected_ts=self.fixture.recent, market_ts=self.fixture.recent)
        self.fixture.insert('experiment_outcomes', evaluation_id=3, horizon_hours=1,
                            target_ts=self.fixture.recent, return_pct=10, excess_btc_pct=8,
                            timing_status='within_tolerance', timing_error_seconds=0)
        self.db.commit()

    def horizon(self, report=None, index=0):
        return (report or build_report(self.source))['groups'][0]['horizons'][index]

    def test_equal_episode_weight_and_missing_benchmarks(self):
        h = self.horizon()
        self.assertEqual(h['distinct_episodes'], 2)
        self.assertEqual(h['metrics']['return_pct']['mean'], 7)
        self.assertEqual(h['metrics']['return_pct']['median'], 7)
        self.assertEqual(h['metrics']['excess_btc_pct']['mean'], 5)
        self.assertIsNone(h['metrics']['excess_eth_pct']['mean'])
        self.assertEqual(h['metrics']['excess_eth_pct']['missing_episodes'], 2)
        self.assertEqual(h['timing']['late'], dict(evaluations=2, episodes=1))
        self.assertEqual(h['timing']['absolute_error_seconds']['mean'], 1800)

    def test_pending_absent_partial_and_unassigned(self):
        h = self.horizon(index=1)
        self.assertEqual(h['metrics']['return_pct']['missing_episodes'], 2)
        self.assertEqual(h['timing']['pending']['episodes'], 1)
        self.assertEqual(h['timing']['absent']['episodes'], 1)
        self.db.execute('UPDATE experiment_outcomes SET return_pct=NULL WHERE evaluation_id=2')
        self.db.execute('UPDATE experiment_evaluations SET episode_id=NULL WHERE id=3')
        self.db.commit()
        report = build_report(self.source)
        self.assertEqual(report['excluded_unassigned_evaluations'], 1)
        stat = self.horizon(report)['metrics']['return_pct']
        self.assertEqual(stat['mean'], 2)
        self.assertEqual(stat['partially_measured_episodes'], 1)
        self.assertEqual(stat['missing_evaluations'], 1)

    def test_social_missing_is_not_zero(self):
        report = build_report(self.source)
        self.assertEqual(report['social']['observed_zero_rows'], 1)
        self.assertIn('unavailable', [r['status'] for r in report['social']['feature_statuses']])
        self.db.execute('DELETE FROM social_observations')
        self.db.commit()
        report = build_report(self.source)
        self.assertEqual(report['social']['observation_rows'], 0)
        self.assertTrue(any('not measured zero' in w for w in report['warnings']))
        self.assertEqual(report['collectors'][0]['status'], 'unavailable')

    def test_chronology_counts_episode_once_with_signed_lags(self):
        self.db.execute("UPDATE research_episodes SET first_news_event_ts='2026-09-27T01:00:00+00:00', significant_move_ts='2026-09-27T02:00:00+00:00' WHERE id=1")
        self.db.execute("UPDATE research_episodes SET first_news_event_ts='2026-09-27T03:00:00+00:00', significant_move_ts='2026-09-27T02:00:00+00:00' WHERE id=2")
        self.db.commit()
        news = build_report(self.source)['groups'][0]['signal_to_move_minutes']['news_event']
        self.assertEqual(news['measured_episodes'], 2)
        self.assertEqual(news['mean'], 0)
        self.assertEqual(news['before_move'], 1)
        self.assertEqual(news['after_move'], 1)

    def test_compact_equivalence_determinism_and_read_only(self):
        before = hashlib.sha256(self.source.read_bytes()).hexdigest()
        report = build_report(self.source)
        self.assertEqual(report, build_report(self.source))
        self.assertEqual(before, hashlib.sha256(self.source.read_bytes()).hexdigest())
        result, _ = self.fixture.export()
        self.assertEqual(report, build_report(result['path']))

    def test_cli_and_missing_source(self):
        result = subprocess.run([sys.executable, '-B', '-m', 'crypto_radar.episode_report',
                                 '--db', str(self.source)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), build_report(self.source))
        missing = self.fixture.root/'missing.db'
        result = subprocess.run([sys.executable, '-B', '-m', 'crypto_radar.episode_report',
                                 '--db', str(missing)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(missing.exists())

    def test_versions_and_groups_are_separate(self):
        self.db.execute("UPDATE experiment_evaluations SET signal_version='1.1', experiment_group='market_only' WHERE id=3")
        self.db.commit()
        groups = build_report(self.source)['groups']
        self.assertEqual([(g['signal_version'], g['experiment_group'], g['distinct_episodes'])
                          for g in groups], [('1.1', 'market_only', 1), ('1.2', 'no_signal', 1)])
        self.assertEqual(groups[0]['horizons'][0]['metrics']['return_pct']['mean'], 10)
        self.assertEqual(groups[1]['horizons'][0]['metrics']['return_pct']['mean'], 4)


if __name__ == '__main__':
    unittest.main()
