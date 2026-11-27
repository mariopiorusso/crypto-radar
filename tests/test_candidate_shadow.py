import unittest
from datetime import datetime, timezone, timedelta
from scripts.candidate_shadow import outcome, shadow_time

class ShadowTests(unittest.TestCase):
    def setUp(self):
        self.t=datetime(2026,11,25,tzinfo=timezone.utc)
        self.episode={'anchor_ts':self.t.isoformat(),'anchor_price':100}
    def test_crossing_is_strictly_after_anchor_and_first_sampled(self):
        points=[(self.t.isoformat(),110),((self.t+timedelta(minutes=5)).isoformat(),104),((self.t+timedelta(minutes=10)).isoformat(),105)]
        kind,when=outcome(self.episode,points)
        self.assertEqual((kind,when),('mover',self.t+timedelta(minutes=10)))
    def test_no_crossing_requires_complete_observed_window(self):
        points=[((self.t+timedelta(minutes=5*i)).isoformat(),100) for i in range(289)]
        self.assertEqual(outcome(self.episode,points)[0],'control')
        self.assertEqual(outcome(self.episode,points[:10]+points[20:])[0],'unknown')
        self.assertEqual(outcome(self.episode,points[:-5])[0],'unknown')
    def test_shadow_requires_available_inputs_and_eligible_reason(self):
        end=self.t+timedelta(hours=1); decision=self.t+timedelta(minutes=10)
        row=dict(reason='below_threshold',decision='rejected',eligible=0,pre_score=35,detected_ts=self.t.isoformat(),evidence_cutoff_ts=self.t.isoformat(),market_ts=self.t.isoformat(),started_ts=self.t.isoformat(),completed_ts=decision.isoformat())
        self.assertEqual(shadow_time([row],self.t,end),decision)
        self.assertIsNone(shadow_time([{**row,'market_ts':end.isoformat()}],self.t,end))
        self.assertIsNone(shadow_time([{**row,'reason':'cooldown','decision':'suppressed','eligible':1}],self.t,end))
        self.assertIsNone(shadow_time([{**row,'completed_ts':None}],self.t,end))
        self.assertIsNone(shadow_time([{**row,'pre_score':34.99}],self.t,end))
if __name__=='__main__':unittest.main()
