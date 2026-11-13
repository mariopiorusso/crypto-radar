import tempfile
import json
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
import sqlite3
from contextlib import closing
import zipfile
import snapshot_action as action


class SnapshotTests(unittest.TestCase):
    def test_cleanup_permission_failure_can_resume_without_export(self):
        from crypto_radar.drive_report import checksums
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp)
            name='crypto-radar-analysis-test.zip'
            names=[name,Path(name).with_suffix('.db').name,'crypto-radar-analysis-test.volume0001-of-0001.zip','manifest.json']
            for filename in names:
                (directory/filename).write_bytes(b'fixture')
            part=dict(filename=names[2],file_id='volume',size=7,sha256=checksums(directory/names[2])[0],md5=checksums(directory/names[2])[1])
            manifest=dict(filename='manifest.json',file_id='manifest',size=7,sha256=checksums(directory/'manifest.json')[0])
            client=Mock(folder='folder')
            remotes={}
            for entry in [part,manifest]:
                remotes[entry['file_id']]=dict(id=entry['file_id'],name=entry['filename'],size=7,
                    md5Checksum=checksums(directory/entry['filename'])[1],parents=['folder'],
                    mimeType='application/json' if entry is manifest else 'application/zip')
            client.existing.side_effect=remotes.get
            state=dict(filename=name,timestamp='fixture',parts=[part],manifest=manifest)
            original_unlink=Path.unlink
            def unlink(target,*args,**kwargs):
                if target.name==name:
                    raise PermissionError('fixture locked file')
                return original_unlink(target,*args,**kwargs)
            with patch.object(Path,'unlink',unlink):
                action.finish_cleanup(client,directory,state)
            self.assertEqual(state['cleanup']['status'],'pending')
            self.assertTrue((directory/name).exists())
            self.assertFalse((directory/names[1]).exists())
            action.finish_cleanup(client,directory,state)
            self.assertEqual(state['cleanup']['status'],'complete')
            self.assertEqual(state['cleanup']['deleted_bytes'],21)
            self.assertTrue((directory/'manifest.json').exists())
            state['parts'][0]['filename']='../source.db'
            with self.assertRaisesRegex(ValueError,'Unsafe cleanup volume'):
                action.finish_cleanup(client,directory,state)

    def test_legacy_export_state_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            directory=root/'orchestration_worker'/'snapshots'/('a'*64)
            directory.mkdir(parents=True)
            (directory/'result.json').write_text(json.dumps(dict(filename='old.zip')))
            with patch('crypto_radar.drive_report.DriveClient') as factory:
                factory.return_value.__enter__.return_value.folder='16ERwJgH4ns3IDA0TBzLacHHEKEhFg_qG'
                with self.assertRaisesRegex(ValueError,'legacy'):
                    action.create_snapshot(root,'a'*64)

    def test_wrong_destination_fails_before_export(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch('crypto_radar.drive_report.DriveClient') as factory, \
                 patch('crypto_radar.analysis_snapshot.export_snapshot') as export:
                factory.return_value.__enter__.return_value.folder='wrong'
                with self.assertRaisesRegex(ValueError,'destination'):
                    action.create_snapshot(Path(temp),'b'*64)
                export.assert_not_called()

    def test_export_failure_records_no_uploads_and_last_stage(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            def failed_export(*args,**kwargs):
                kwargs['progress'](dict(stage='backup',copied_pages=10,restart_indications=2))
                raise TimeoutError('fixture deadline')
            with patch('crypto_radar.drive_report.DriveClient') as factory, \
                 patch('crypto_radar.analysis_snapshot.export_snapshot',side_effect=failed_export):
                client=factory.return_value.__enter__.return_value
                client.folder='16ERwJgH4ns3IDA0TBzLacHHEKEhFg_qG'
                with self.assertRaises(TimeoutError):
                    action.create_snapshot(root,'c'*64)
                directory=root/'orchestration_worker'/'snapshots'/('c'*64)
                progress=json.loads((directory/'progress.json').read_text())
                self.assertEqual(progress['failed_stage'],'backup')
                self.assertFalse(progress['uploads_started'])
                self.assertEqual(progress['last_progress']['restart_indications'],2)
                self.assertTrue((directory/'progress.jsonl').exists())
                client.upload.assert_not_called()

    def test_timeout_kills_child_and_reports_failure(self):
        import subprocess
        facts=dict(repository_detected=True,branch='test',head='head',test_status='fixture',github_access_status='offline')
        with patch('windows_job.Job') as job, patch.object(action.subprocess,'Popen') as popen:
            process=popen.return_value.__enter__.return_value
            process.communicate.side_effect=subprocess.TimeoutExpired('snapshot',7200)
            result=action.run({'repository':'C:/fixture'},{'gmail_message_id':'message'},facts)
            self.assertEqual(result['exit_code'],1)
            process.communicate.assert_called_once_with(timeout=7200)
            process.kill.assert_called_once()
            job.return_value.__enter__.return_value.assign.assert_called_once_with(process)

    def test_explicit_routing(self):
        self.assertTrue(action.requested('Subject: [CRADAR TASK] SNAPSHOT-001\n\ncreate it'))
        self.assertTrue(action.requested('Subject: [CRADAR TASK] question\nACTION: SNAPSHOT\n'))
        self.assertTrue(action.requested('Create a fresh research export using the existing crypto_radar.analysis_snapshot analysis exporter, not the weekly-report workflow.'))
        self.assertTrue(action.requested('ACTION: ANALYSIS_SNAPSHOT'))
        self.assertFalse(action.requested('> Create using crypto_radar.analysis_snapshot'))
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
            from crypto_radar.db import connect
            with closing(connect(root/'data/crypto_radar.db')) as db:
                db.commit()
            before=(root/'data/crypto_radar.db').read_bytes()
            with patch('crypto_radar.drive_report.DriveClient') as client_type, patch('socket.socket.connect',side_effect=AssertionError('offline')):
                client=client_type.return_value.__enter__.return_value
                client.folder='16ERwJgH4ns3IDA0TBzLacHHEKEhFg_qG'; client.account='test@example.invalid'
                client.generate_id.side_effect=['volume1','volume2','manifest']
                remotes={}
                client.existing.side_effect=lambda key:remotes.get(key)
                def upload(file_id,path,sha):
                    if path.suffix=='.zip':
                        source=next(path.parent.glob('crypto-radar-analysis-*.db'))
                        with zipfile.ZipFile(source.with_suffix('.zip')) as z:
                            self.assertEqual(z.namelist(),[source.name])
                    remote=dict(id=file_id,name=path.name,md5Checksum=checksums(path)[1],size=path.stat().st_size,
                                parents=[client.folder],mimeType='application/zip' if path.suffix=='.zip' else 'application/json')
                    remotes[file_id]=remote
                    return remote
                client.upload.side_effect=upload
                first=action.create_snapshot(root,'a'*64)
                second=action.create_snapshot(root,'a'*64)
                self.assertEqual(first,second)
                self.assertEqual(client.upload.call_count,3)
                self.assertEqual(len(first['parts']),2)
                self.assertTrue(all(p['filename'].endswith('.zip') for p in first['parts']))
                self.assertTrue(first['success'])
                self.assertFalse(Path(first['local_zip_path']).exists())
                self.assertFalse(Path(first['export_metadata']['path']).exists())
                directory=Path(first['local_zip_path']).parent
                self.assertFalse(list(directory.glob('*.zip')))
                self.assertEqual(first['cleanup']['status'],'complete')
                self.assertEqual(len(first['cleanup']['deleted_files']),4)
                self.assertTrue((directory/'manifest.json').exists())
                self.assertTrue((directory/'volumes.json').exists())
                self.assertEqual(first['exporter'],'crypto_radar.analysis_snapshot')
                self.assertEqual(first['export_metadata']['integrity_check'],'ok')
                self.assertTrue(all(p['size'] < 190_000_000 for p in first['parts']))
                self.assertEqual(first['manifest']['filename'],'manifest.json')
                self.assertEqual(before,(root/'data/crypto_radar.db').read_bytes())
                remotes[first['parts'][0]['file_id']]=dict(id='wrong')
                Path(first['local_zip_path']).write_bytes(b'keep if remote verification fails')
                with self.assertRaisesRegex(ValueError,'verification'):
                    action.create_snapshot(root,'a'*64)
                self.assertTrue(Path(first['local_zip_path']).exists())
