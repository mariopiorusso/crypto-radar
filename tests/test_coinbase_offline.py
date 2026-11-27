import unittest
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
from research.coinbase_offline import main
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

    def test_failed_or_interrupted_run_never_publishes_partial_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp)/'input', Path(tmp)/'output'
            valid = json.dumps({'message': msg(), 'receipt_utc': '2026-01-01T00:00:01Z'})
            args = ['test', '--enable-offline', '--fixture', str(source), '--output', str(output), '--snapshot-sequence', '1']
            source.write_text(valid + '\ninvalid json\n')
            with patch.object(sys, 'argv', args), self.assertRaises(ValueError): main()
            self.assertFalse(output.exists())
            source.write_text(valid + '\n')
            with patch.object(sys, 'argv', args), patch('research.coinbase_offline.Processor.process', side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt): main()
            self.assertFalse(output.exists())
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ['input'])

    def test_deadline_prevents_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp)/'input', Path(tmp)/'output'
            source.write_text(json.dumps({'message': msg(), 'receipt_utc': '2026-01-01T00:00:01Z'}) + '\n')
            args = ['test', '--enable-offline', '--fixture', str(source), '--output', str(output), '--snapshot-sequence', '1', '--max-seconds', '1']
            with patch.object(sys, 'argv', args), patch('research.coinbase_offline.time.monotonic', side_effect=[0, 1]), self.assertRaises(TimeoutError): main()
            self.assertFalse(output.exists())

    def test_complete_run_publishes_once_without_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp)/'input', Path(tmp)/'output'
            source.write_text(json.dumps({'message': msg(), 'receipt_utc': '2026-01-01T00:00:01Z'}) + '\n')
            args = ['test', '--enable-offline', '--fixture', str(source), '--output', str(output), '--snapshot-sequence', '1']
            with patch.object(sys, 'argv', args): main()
            published = output.read_text()
            self.assertTrue(json.loads(published)['complete'])
            with patch.object(sys, 'argv', args), self.assertRaises(FileExistsError): main()
            self.assertEqual(output.read_text(), published)
