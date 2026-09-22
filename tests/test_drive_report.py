import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from crypto_radar.drive_report import DriveClient, checksums, run_drive_report
from crypto_radar.weekly_report import MEMBERS


class DriveReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for member in MEMBERS:
            (self.root / member).parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.root / MEMBERS[0])) as conn:
            conn.execute('CREATE TABLE example(value INTEGER)')
        (self.root / MEMBERS[1]).write_text('currency: usd')
        (self.root / MEMBERS[2]).write_text('# scoring')
        self.now = datetime(2026,9,21,9,tzinfo=timezone.utc)
        self.client = MagicMock(folder='folder-id', account='owner@example.invalid')
        self.client.generate_id.return_value = 'file-id'
        self.client.existing.return_value = None

        def uploaded(file_id, path, sha):
            self.remote = dict(id=file_id, name=path.name, parents=['folder-id'],
                mimeType='application/zip', md5Checksum=checksums(path)[1], size=str(path.stat().st_size))
            return self.remote
        self.client.upload.side_effect = uploaded
        self.factory = patch('crypto_radar.drive_report.DriveClient')
        self.factory.start().return_value.__enter__.return_value = self.client
        self.addCleanup(self.factory.stop)
        self.network = patch('socket.socket.connect',side_effect=AssertionError('Offline test'))
        self.network.start()
        self.addCleanup(self.network.stop)

    def test_timestamp_zip_and_retry_without_duplicate(self):
        path = run_drive_report(self.root,now=self.now)
        self.assertEqual(path.name,'crypto-radar-2026-09-21T090000Z.zip')
        self.client.existing.return_value = self.remote
        self.assertEqual(run_drive_report(self.root,now=self.now),path)
        self.client.upload.assert_called_once()
        state = json.loads(next(path.parent.glob('*.drive.json')).read_text())
        self.assertEqual(state['status'],'uploaded')
        self.assertEqual(state['sha256'],checksums(path)[0])

    def test_response_loss_recovers_reserved_id(self):
        original = self.client.upload.side_effect
        def lost(*args):
            original(*args)
            raise TimeoutError()
        self.client.upload.side_effect = lost
        with self.assertRaises(TimeoutError):
            run_drive_report(self.root,now=self.now)
        self.client.existing.return_value = self.remote
        run_drive_report(self.root,now=self.now)
        self.client.upload.assert_called_once()

    def test_destination_change_is_rejected(self):
        run_drive_report(self.root,now=self.now)
        self.client.folder = 'other-folder'
        with self.assertRaisesRegex(ValueError,'Destination changed'):
            run_drive_report(self.root,now=self.now)

    def test_dry_run_does_not_access_drive(self):
        with patch('crypto_radar.drive_report.DriveClient') as factory:
            path=run_drive_report(self.root,dry_run=True,now=self.now)
            self.assertTrue(path.exists())
            factory.assert_not_called()

    def test_upload_explicit_zip_mime_and_parent(self):
        # Exercise the API adapter without constructing credentials or making requests.
        client = object.__new__(DriveClient)
        client.folder = 'folder-id'
        client.session = MagicMock()
        client.session.post.return_value.headers = {'Location':'https://www.googleapis.com/upload/session'}
        path = self.root / 'test.zip'
        path.write_bytes(b'zip-bytes')
        client.upload('file-id',path,'checksum')
        request=client.session.post.call_args.kwargs
        self.assertEqual(request['json']['mimeType'],'application/zip')
        self.assertEqual(request['json']['parents'],['folder-id'])
        self.assertEqual(request['json']['id'],'file-id')
        self.assertEqual(client.session.put.call_args.kwargs['headers']['Content-Type'],'application/zip')
