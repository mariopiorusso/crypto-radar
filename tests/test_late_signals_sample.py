import unittest
from scripts.late_signals_sample import available_evaluation, select_episodes


def stamp(hour, minute=0):
    return f'2026-01-01T{hour:02}:{minute:02}:00+00:00'


def episode(i, anchor=1, anomaly=2, move=3, candidate=4):
    return dict(id=i, coin_id='coin', anchor_ts=stamp(anchor),
                first_market_anomaly_ts=stamp(anomaly), significant_move_ts=stamp(move),
                first_v11_candidate_ts=stamp(candidate))


class LateSignalsTests(unittest.TestCase):
    def test_strict_boundaries_cutoff_and_stable_order(self):
        rows = [episode(2), episode(1), episode(3, anomaly=3), episode(4, candidate=3),
                episode(5, candidate=6), episode(6, anchor=2)]
        self.assertEqual([r['id'] for r in select_episodes(rows, stamp(5))], [1, 2, 6])
        self.assertEqual(len(select_episodes(rows, stamp(5), limit=1)), 1)

    def test_future_or_incomplete_scan_never_used(self):
        def evaluation(i, completed, cutoff=stamp(2)):
            return dict(id=i, detected_ts=stamp(2), evidence_cutoff_ts=cutoff,
                        market_ts=stamp(2), started_ts=stamp(2), completed_ts=completed)
        rows = [evaluation(1, stamp(2, 30)), evaluation(2, stamp(3)),
                evaluation(3, None), evaluation(4, stamp(2, 30), stamp(4))]
        self.assertEqual(available_evaluation(rows, stamp(3))['id'], 1)
        self.assertIsNone(available_evaluation(rows[1:], stamp(3)))

    def test_timezone_offsets_and_naive_rejection(self):
        row = episode(1); row['first_market_anomaly_ts'] = '2026-01-01T04:00:00+02:00'
        self.assertEqual(len(select_episodes([row], stamp(5))), 1)
        row['anchor_ts'] = '2026-01-01T01:00:00'
        with self.assertRaises(ValueError):
            select_episodes([row], stamp(5))


if __name__ == '__main__':
    unittest.main()
