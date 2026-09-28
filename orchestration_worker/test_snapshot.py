import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sqlite3
from contextlib import closing
import zipfile
import snapshot_action as action


class SnapshotTests(unittest.TestCase):
    def test_explicit_routing(self):
        self.assertTrue(action.requested('Subject: [CRADAR TASK] SNAPSHOT-001\n\ncreate it'))
        self.assertTrue(action.requested('Subject: [CRADAR TASK] question\nACTION: SNAPSHOT\n'))
        for text in ('Subject: Re: [CRADAR TASK] SNAPSHOT-001','What is a snapshot?', 'Subject: [CRADAR TASK] delete everything'):
            self.assertFalse(action.requested(text))

    def test_consistent_archive_and_idempotent_upload(self):
        from crypto_radar.drive_report import checksums
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'data').mkdir()
            (root/'crypto_radar/intelligence').mkdir(parents=True)
            (root/'config.yaml').write_text('test: true')
            (root/'crypto_radar/intelligence/statistical.py').write_text('# test')
            with closing(sqlite3.connect(root/'data/crypto_radar.db')) as db:
                db.execute('CREATE TABLE observations (value INTEGER)')
                db.execute('INSERT INTO observations VALUES (42)'); db.commit()
            before=(root/'data/crypto_radar.db').read_bytes()
            with patch('crypto_radar.drive_report.DriveClient') as client_type, patch('socket.socket.connect',side_effect=AssertionError('offline')):
                client=client_type.return_value.__enter__.return_value
                client.folder='folder'; client.account='test@example.invalid'
                client.generate_id.return_value='file123'
                client.existing.return_value=None
                def upload(file_id,path,sha):
                    remote=dict(id=file_id,name=path.name,md5Checksum=checksums(path)[1],size=path.stat().st_size,
                                parents=['folder'],mimeType='application/zip')
                    client.existing.return_value=remote
                    return remote
                client.upload.side_effect=upload
                first=action.create_snapshot(root,'a'*64)
                second=action.create_snapshot(root,'a'*64)
                self.assertEqual(first,second)
                client.upload.assert_called_once()
                self.assertTrue(first['success'])
                with zipfile.ZipFile(first['local_zip_path']) as z:
                    self.assertEqual(set(z.namelist()),{'data/crypto_radar.db','config.yaml','crypto_radar/intelligence/statistical.py'})
                self.assertEqual(before,(root/'data/crypto_radar.db').read_bytes())
                client.existing.return_value=dict(id='wrong')
                with self.assertRaisesRegex(ValueError,'verification'):
                    action.create_snapshot(root,'a'*64)
