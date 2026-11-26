import unittest
from scripts.candidate_timing import timing, target
from scripts.late_signals_sample import available_evaluation

class CandidateTimingTests(unittest.TestCase):
    def test_strict_signal_boundaries_and_missing(self):
        move='2026-01-01T03:00:00Z'
        def e(anomaly, candidate):
            return dict(significant_move_ts=move, first_market_anomaly_ts=anomaly, first_v11_candidate_ts=candidate)
        early='2026-01-01T02:00:00Z'; late='2026-01-01T04:00:00Z'
        rows=[e(early,late),e(early,None),e(move,late),e(early,move)]
        self.assertEqual([target(r) for r in rows],[True,True,False,False])
        self.assertEqual(timing(rows,'first_v11_candidate_ts'),dict(present=3,early=0,simultaneous=1,late=2))

    def test_completed_scan_and_all_timestamps_must_precede_crossing(self):
        t='2026-01-01T02:00:00Z'; move='2026-01-01T03:00:00Z'
        row=dict(id=1,detected_ts=t,evidence_cutoff_ts=t,market_ts=t,started_ts=t,completed_ts=move)
        self.assertIsNone(available_evaluation([row],move))
        row['completed_ts']=t
        self.assertEqual(available_evaluation([row],move)['id'],1)

if __name__ == '__main__': unittest.main()
