import unittest
from research.coinbase_offline import Processor


def msg(seq=2, trade=4, side='sell'):
    return {'type':'match','product_id':'VVV-USD','sequence':seq,'trade_id':trade,'side':side,'time':'2026-01-01T00:00:00Z','size':'2','price':'3'}

class Tests(unittest.TestCase):
    def setUp(self): self.p=Processor('VVV-USD'); self.p.begin_snapshot(1)
    def test_maker_inversion_and_times(self):
        r=self.p.process(msg(), '2026-01-01T00:00:01Z'); self.assertEqual(r['taker_side'],'buy'); self.assertEqual(r['event_utc'],'2026-01-01T00:00:00Z'); self.assertEqual(r['receipt_utc'],'2026-01-01T00:00:01Z'); self.assertTrue(r['complete'])
    def test_buy_inversion(self): self.assertEqual(self.p.process(msg(side='buy'),'2026-01-01T00:00:01Z')['taker_side'],'sell')
    def test_unknown_side_invalidates(self):
        with self.assertRaises(ValueError): self.p.process(msg(side='other'),'2026-01-01T00:00:01Z')
        self.assertFalse(self.p.valid)
    def test_gap_stays_invalid_until_snapshot(self):
        self.assertFalse(self.p.process(msg(seq=3),'2026-01-01T00:00:01Z')['complete']);self.assertFalse(self.p.process(msg(seq=4,trade=5),'2026-01-01T00:00:02Z')['valid']);self.p.begin_snapshot(4);self.assertTrue(self.p.process(msg(seq=5,trade=6),'2026-01-01T00:00:03Z')['valid'])
    def test_deduplicate(self):
        self.p.process(msg(),'2026-01-01T00:00:01Z');self.assertFalse(self.p.process(msg(seq=3),'2026-01-01T00:00:02Z')['valid'])
    def test_disconnect_and_bound(self):
        self.p.invalidate();self.assertFalse(self.p.process(msg(),'2026-01-01T00:00:01Z')['valid']); p=Processor('VVV-USD',1);p.begin_snapshot(1);p.process(msg(),'2026-01-01T00:00:01Z')
        with self.assertRaises(ValueError): p.process(msg(seq=3),'2026-01-01T00:00:02Z')
    def test_naive_time_rejected(self):
        with self.assertRaises(ValueError): self.p.process(msg(),'2026-01-01T00:00:01')
