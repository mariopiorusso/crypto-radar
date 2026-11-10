import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock
import zipfile

from crypto_radar.snapshot_volumes import build_volumes, upload_volumes
from crypto_radar.drive_report import checksums


class SnapshotVolumeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.source=self.root/'snapshot.zip'
        self.payload=bytes(range(251))*1000
        self.source.write_bytes(self.payload)

    def test_multiple_real_zips_strict_cap_determinism_and_restore(self):
        manifest, volumes=build_volumes(self.source,max_bytes=131072)
        self.assertGreater(len(volumes),1)
        hashes=[checksums(p) for p in volumes]
        extracted=self.root/'extracted';extracted.mkdir()
        for p in volumes:
            self.assertLess(p.stat().st_size,131072)
            with zipfile.ZipFile(p) as archive:
                self.assertIsNone(archive.testzip())
                archive.extractall(extracted)
        build_volumes(self.source,max_bytes=131072)
        self.assertEqual(hashes,[checksums(p) for p in volumes])
        result=subprocess.run([sys.executable,str(extracted/'restore_snapshot.py')],capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual((extracted/'snapshot.zip').read_bytes(),self.payload)
        self.assertEqual(manifest['source_sha256'],hashlib.sha256(self.payload).hexdigest())
        self.assertNotEqual(subprocess.run([sys.executable,str(extracted/'restore_snapshot.py')],capture_output=True).returncode,0)

    def test_missing_or_corrupt_chunks_do_not_publish_partial_snapshot(self):
        manifest,volumes=build_volumes(self.source,131072)
        extracted=self.root/'extracted';extracted.mkdir()
        for p in volumes:
            with zipfile.ZipFile(p) as z:z.extractall(extracted)
        chunk=extracted/manifest['chunks'][1]['name']
        chunk.write_bytes(b'wrong')
        result=subprocess.run([sys.executable,str(extracted/'restore_snapshot.py')],capture_output=True)
        self.assertNotEqual(result.returncode,0)
        self.assertFalse((extracted/'snapshot.zip').exists())
        self.assertFalse((extracted/'snapshot.zip.restoring').exists())

    def test_existing_pending_restore_is_preserved(self):
        _,volumes=build_volumes(self.source,131072)
        extracted=self.root/'extracted';extracted.mkdir()
        for p in volumes:
            with zipfile.ZipFile(p) as z:z.extractall(extracted)
        pending=extracted/'snapshot.zip.restoring';pending.write_bytes(b'keep')
        subprocess.run([sys.executable,str(extracted/'restore_snapshot.py')],capture_output=True)
        self.assertEqual(pending.read_bytes(),b'keep')

    def test_upload_response_loss_resumes_ids_and_checks_destination(self):
        client=Mock(folder='folder',account='owner')
        client.generate_id.side_effect=[f'id{i}' for i in range(10)]
        remotes={};lost=[False]
        client.existing.side_effect=lambda key:remotes.get(key)
        def upload(key,path,sha):
            remotes[key]=dict(id=key,name=path.name,md5Checksum=checksums(path)[1],size=path.stat().st_size,
                parents=['folder'],mimeType='application/zip',webViewLink='https://drive.google.com/file/d/'+key+'/view')
            if not lost[0]:
                lost[0]=True
                raise TimeoutError('lost response')
            return remotes[key]
        client.upload.side_effect=upload
        state=self.root/'upload.json'
        with self.assertRaises(TimeoutError):upload_volumes(client,self.source,state,131072)
        result=upload_volumes(client,self.source,state,131072)
        self.assertEqual(client.upload.call_count,len(result['parts']))
        upload_volumes(client,self.source,state,131072)
        self.assertEqual(client.upload.call_count,len(result['parts']))
        client.folder='changed'
        with self.assertRaisesRegex(ValueError,'destination'):upload_volumes(client,self.source,state,131072)

    def test_tiny_limit_and_changed_source_rejected(self):
        with self.assertRaises(ValueError):build_volumes(self.source,100)
        client=Mock(folder='folder',account='owner')
        state=self.root/'upload.json'
        manifest,_=build_volumes(self.source,131072)
        state.write_text(json.dumps(dict(manifest=manifest,folder='folder',account='owner',parts=[])))
        self.source.write_bytes(b'different')
        with self.assertRaisesRegex(ValueError,'changed'):upload_volumes(client,self.source,state,131072)
