import json
import unittest
from unittest.mock import Mock

from crypto_radar.observation_timing import candidate_features, delivery_receipt


class ObservationTimingTests(unittest.TestCase):
    def test_features_do_not_mutate_score_or_inputs(self):
        stats={'pre_score':45, 'components':{'volume_z':12}}
        captured=candidate_features(stats,'2026-11-02T09:00:00+00:00','2026-11-02T09:00:02+00:00')
        self.assertEqual(captured['pre_score'],stats['pre_score'])
        self.assertNotIn('observation_timing',stats)
        self.assertEqual(captured['observation_timing']['candidate_computed_ts'],'2026-11-02T09:00:02+00:00')
        self.assertIsNone(candidate_features(None,'a','b'))

    def test_alert_receipt_records_completion_not_delivery_claim(self):
        logger=Mock()
        delivery_receipt(logger,5,'email','delivery_unknown','2026-11-02T09:00:05+00:00')
        event=json.loads(logger.info.call_args.args[1])
        self.assertEqual(event['event'],'alert_sender_returned')
        self.assertEqual(event['status'],'delivery_unknown')
        self.assertEqual(set(event),{'event','alert_event_id','channel','status','sender_returned_ts'})
