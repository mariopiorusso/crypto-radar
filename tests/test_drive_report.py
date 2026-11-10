import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from crypto_radar.drive_report import DriveClient, checksums, run_drive_report, upload_parts
from crypto_radar.weekly_report import MEMBERS


class DriveReportTests(unittest.TestCase):
    def test_parts_reassemble_and_recover_after_lost_upload_response(self):
        path = self.root / 'analysis.zip'
        payload = bytes(range(251)) * 5
        path.write_bytes(payload)
        state_path = self.root / 'parts.json'
        remotes = {}
        self.client.generate_id.side_effect = ['part-1', 'part-2']
        self.client.existing.side_effect = lambda key: remotes.get(key)
        def upload(key, part, sha):
            remotes[key] = dict(id=key, name=part.name, parents=['folder-id'],
                mimeType='application/octet-stream', size=str(part.stat().st_size),
                md5Checksum=checksums(part)[1])
            if len(remotes) == 1:
                raise TimeoutError('Response lost')
            return remotes[key]
        self.client.upload.side_effect = upload
        with patch('crypto_radar.drive_report.MAX_PART_BYTES', 700):
            with self.assertRaises(TimeoutError):
                upload_parts(self.client, path, state_path)
            # Drive may identify the first chunk from its ZIP signature.
            remotes['part-1']['mimeType'] = 'application/x-zip'
            state = upload_parts(self.client, path, state_path)
            upload_parts(self.client, path, state_path)
        parts = sorted(self.root.glob('*.part*'))
        self.assertEqual([p.stat().st_size for p in parts], [700, 555])
        self.assertEqual(b''.join(p.read_bytes() for p in parts), payload)
        self.assertEqual(state['status'], 'uploaded')
        self.assertEqual(self.client.upload.call_count, 2)
        remotes['part-1']['md5Checksum'] = 'wrong'
        with patch('crypto_radar.drive_report.MAX_PART_BYTES', 700):
            with self.assertRaisesRegex(ValueError, 'verification'):
                upload_parts(self.client, path, state_path)

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

    def test_analysis_upload_uses_exporter_and_separate_weekly_state(self):
        full=run_drive_report(self.root,now=self.now)
        def export(source, destination, **kwargs):
            self.assertEqual(source,self.root/'data/crypto_radar.db')
            self.assertEqual(kwargs['history_days'],84)
            self.assertTrue(kwargs['zipped'])
            path=destination.with_suffix('.zip');path.write_bytes(b'analysis-zip')
            return {'zip_path':str(path)}
        with patch('crypto_radar.analysis_snapshot.export_snapshot',side_effect=export) as exporter, \
             patch('crypto_radar.drive_report.send_alert') as mail:
            path=run_drive_report(self.root,now=self.now,analysis=True,notify=True)
            self.assertNotEqual(path,full)
            self.assertTrue(path.name.startswith('crypto-radar-analysis-'))
            self.client.existing.return_value=self.remote
            self.assertEqual(run_drive_report(self.root,now=self.now,analysis=True,notify=True),path)
            exporter.assert_called_once();mail.assert_called_once()
            with self.assertRaisesRegex(ValueError,'History window changed'):
                run_drive_report(self.root,now=self.now,analysis=True,history_days=7)
        self.assertEqual(len(list(path.parent.glob('*.drive.json'))),2)

    def test_analysis_dry_run_never_uploads_or_emails(self):
        path=self.root/'analysis.zip'
        with patch('crypto_radar.analysis_snapshot.export_snapshot',return_value={'zip_path':str(path)}), \
             patch('crypto_radar.drive_report.DriveClient') as drive, \
             patch('crypto_radar.drive_report.send_alert') as mail:
            self.assertEqual(run_drive_report(self.root,analysis=True,dry_run=True,notify=True),path)
            drive.assert_not_called();mail.assert_not_called()

    def test_zip_volume_job_uploads_and_notifies_once(self):
        parts={'parts':[{'filename':'one.zip','file_id':'one','web_view_link':'https://drive.google.com/one'},
                        {'filename':'two.zip','file_id':'two','web_view_link':'https://drive.google.com/two'}]}
        with patch('crypto_radar.snapshot_volumes.upload_volumes',return_value=parts) as volumes, \
             patch('crypto_radar.drive_report.send_alert') as mail:
            path=run_drive_report(self.root,now=self.now,zip_volumes=True,notify=True)
            self.assertEqual(run_drive_report(self.root,now=self.now,zip_volumes=True,notify=True),path)
            self.assertEqual(volumes.call_count,2)
            mail.assert_called_once()
            self.assertIn('restore_snapshot.py',mail.call_args.args[0])
        state=json.loads(next(path.parent.glob('*.drive.json')).read_text())
        self.assertEqual(state['archive_format'],'zip-volumes-v1')
        self.client.upload.assert_not_called()

    def test_analysis_job_splits_and_notifies_once(self):
        remotes = {}
        self.client.generate_id.side_effect = ['one', 'two']
        self.client.existing.side_effect = lambda key: remotes.get(key)
        def upload(key, part, sha):
            remotes[key] = dict(id=key, name=part.name, parents=['folder-id'],
                mimeType='application/octet-stream', size=str(part.stat().st_size),
                md5Checksum=checksums(part)[1])
            return remotes[key]
        self.client.upload.side_effect = upload
        def export(source, destination, **kwargs):
            path = destination.with_suffix('.zip')
            path.write_bytes(b'abcdefghij')
            return {'zip_path': str(path)}
        with patch('crypto_radar.analysis_snapshot.export_snapshot', side_effect=export) as exporter, \
             patch('crypto_radar.drive_report.MAX_PART_BYTES', 6), \
             patch('crypto_radar.drive_report.send_alert') as mail:
            path = run_drive_report(self.root, now=self.now, analysis=True, notify=True)
            run_drive_report(self.root, now=self.now, analysis=True, notify=True)
        exporter.assert_called_once()
        mail.assert_called_once()
        self.assertIn('2 parts', mail.call_args.args[0])
        self.assertEqual(self.client.upload.call_count, 2)
        self.assertEqual(b''.join(p.read_bytes() for p in sorted(path.parent.glob('*.zip.part[0-9][0-9][0-9]'))), b'abcdefghij')

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

    def test_id_reservation_failure_reuses_completed_archive(self):
        self.client.generate_id.side_effect=[ConnectionError('offline'),'file-id']
        with self.assertRaises(ConnectionError): run_drive_report(self.root,now=self.now)
        with patch('crypto_radar.drive_report.build_archive',side_effect=AssertionError('Must reuse archive')):
            run_drive_report(self.root,now=self.now)
        self.client.upload.assert_called_once()

    def test_confirmation_once_after_verified_upload(self):
        with patch('crypto_radar.drive_report.send_alert') as mail:
            run_drive_report(self.root,now=self.now,notify=True)
            self.client.existing.return_value=self.remote
            run_drive_report(self.root,now=self.now,notify=True)
            mail.assert_called_once()
            self.assertIn('file-id',mail.call_args.args[0])

    def test_uncertain_confirmation_not_resent(self):
        with patch('crypto_radar.drive_report.send_alert',side_effect=TimeoutError) as mail:
            with self.assertRaises(TimeoutError): run_drive_report(self.root,now=self.now,notify=True)
            self.client.existing.return_value=self.remote
            with self.assertRaisesRegex(RuntimeError,'uncertain'):
                run_drive_report(self.root,now=self.now,notify=True)
            mail.assert_called_once()

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
