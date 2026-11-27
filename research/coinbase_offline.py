"""Offline-only Coinbase Exchange match processor. No network or trading code."""
import argparse
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path


def utc(value):
    if not isinstance(value, str):
        raise ValueError('timestamp must be a string')
    t = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if t.tzinfo is None or t.utcoffset() != timezone.utc.utcoffset(t):
        raise ValueError('timestamp must be UTC')
    return t.isoformat().replace('+00:00', 'Z')


def positive(value):
    try:
        n = Decimal(str(value))
        if not n.is_finite() or n <= 0:
            raise ValueError('number must be finite and positive')
        return str(n)
    except InvalidOperation as exc:
        raise ValueError('invalid decimal') from exc


class Processor:
    def __init__(self, product, max_events=10000):
        if not product or max_events < 1:
            raise ValueError('product and positive event bound required')
        self.product, self.max_events = product, max_events
        self.seen = set()
        self.last_sequence = None
        self.valid = False
        self.count = 0

    def invalidate(self):
        self.valid = False
        self.last_sequence = None

    def begin_snapshot(self, sequence):
        # Only a caller-supplied, independently verified snapshot can restore validity.
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
            raise ValueError('invalid snapshot sequence')
        self.last_sequence = sequence
        self.valid = True

    def process(self, message, receipt_utc):
        if self.count >= self.max_events:
            raise ValueError('event bound reached')
        self.count += 1
        try:
            receipt = utc(receipt_utc)
            if message.get('type') != 'match' or message.get('product_id') != self.product:
                raise ValueError('unexpected type or product')
            seq, trade = message['sequence'], message['trade_id']
            if any(not isinstance(x, int) or isinstance(x, bool) or x < 0 for x in (seq, trade)):
                raise ValueError('invalid identifier')
            side = {'buy': 'sell', 'sell': 'buy'}.get(message['side'])
            if side is None:
                raise ValueError('unknown maker side')
            event = utc(message['time']); price = positive(message['price']); size = positive(message['size'])
            duplicate = trade in self.seen
            if not self.valid or self.last_sequence is None or seq != self.last_sequence + 1:
                self.invalidate()
            else:
                self.last_sequence = seq
            self.seen.add(trade)
            return {'source': 'coinbase_exchange', 'product': self.product, 'sequence': seq,
                    'trade_id': trade, 'event_utc': event, 'receipt_utc': receipt,
                    'maker_side': message['side'], 'taker_side': side, 'price': price,
                    'size': size, 'duplicate': duplicate, 'valid': self.valid and not duplicate,
                    'complete': self.valid and not duplicate}
        except (KeyError, TypeError, ValueError):
            self.invalidate()
            raise


def main():
    p = argparse.ArgumentParser(description='Process bounded synthetic fixture JSONL only; disabled by default.')
    p.add_argument('--fixture', type=Path); p.add_argument('--output', type=Path)
    p.add_argument('--enable-offline', action='store_true'); p.add_argument('--product', default='VVV-USD')
    p.add_argument('--snapshot-sequence', type=int); p.add_argument('--max-events', type=int, default=1000)
    p.add_argument('--max-seconds', type=int, default=60, help='Offline processing deadline (1..3600 seconds).')
    a = p.parse_args()
    if not a.enable_offline or not a.fixture or not a.output or a.snapshot_sequence is None:
        p.error('explicit offline enable, fixture, output and snapshot sequence required')
    if a.fixture.resolve() == a.output.resolve(): p.error('output must differ from input')
    if not 1 <= a.max_events <= 10000: p.error('max-events must be 1..10000')
    if not 1 <= a.max_seconds <= 3600: p.error('max-seconds must be 1..3600')
    if not a.fixture.is_file(): p.error('fixture must be a regular file')
    processor = Processor(a.product, a.max_events); processor.begin_snapshot(a.snapshot_sequence)
    deadline = time.monotonic() + a.max_seconds
    # Stage in the destination directory; publish exclusively only after successful EOF.
    fd, temp_name = tempfile.mkstemp(prefix='.coinbase-incomplete-', dir=a.output.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as dest, a.fixture.open(encoding='utf-8') as source:
            for line in source:
                if time.monotonic() >= deadline:
                    raise TimeoutError('offline runtime limit reached')
                item = json.loads(line)
                record = processor.process(item['message'], item['receipt_utc'])
                dest.write(json.dumps(record, sort_keys=True) + '\n')
            if time.monotonic() >= deadline:
                raise TimeoutError('offline runtime limit reached')
        os.link(temp_name, a.output)
    finally:
        os.unlink(temp_name)


if __name__ == '__main__': main()
