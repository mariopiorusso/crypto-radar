import unittest

from scripts.social_coverage import prior_coverage


class CoverageTests(unittest.TestCase):
    def row(self, minute, status='available', complete=1, run_minute=None):
        stamp = f'2026-01-01T00:{minute:02}:00Z'
        return dict(observed_ts=stamp, run_ts=f'2026-01-01T00:{(minute if run_minute is None else run_minute):02}:00Z',
                    status=status, complete=complete, run_id=str(minute))

    def test_strict_cutoff_and_freshness(self):
        rows = [self.row(i) for i in range(12)] + [self.row(20), self.row(19, run_minute=20)]
        result = prior_coverage(rows, '2026-01-01T00:20:00Z')
        self.assertEqual(result['changed_runs'], 12)
        self.assertEqual(result['mapped_rows'], 12)
        self.assertEqual(result['usable_30m'], 1)
        self.assertEqual(prior_coverage(rows[:11], '2026-01-01T00:20:00Z')['usable_30m'], 0)

    def test_repeated_response_does_not_refresh_changed_history(self):
        rows = [self.row(i) for i in range(12)] + [self.row(19, 'repeated')]
        result = prior_coverage(rows, '2026-01-01T00:20:00Z')
        self.assertEqual(result['repeated_rows'], 1)
        self.assertEqual(result['changed_runs'], 12)
        self.assertEqual(result['usable_30m'], 0)

    def test_threshold_boundaries(self):
        rows = [self.row(i) for i in range(12)]
        result = prior_coverage(rows, '2026-01-01T01:11:00Z')
        self.assertEqual([result[f'usable_{m}m'] for m in (30, 60, 120)], [0, 1, 1])


if __name__ == '__main__':
    unittest.main()
