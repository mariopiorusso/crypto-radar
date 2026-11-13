"""Fixed snapshot operation; no email-provided commands, paths or destinations."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile
from datetime import datetime, timezone


def requested(text):
    from workflow import instruction_text
    text = instruction_text(text)
    subject=text.splitlines()[0] if text else ''
    return bool(re.fullmatch(r'Subject: \[CRADAR TASK\] SNAPSHOT(?:-[A-Za-z0-9_-]+)?',subject,re.I)
                or re.search(r'^ACTION:[ \t]*(?:ANALYSIS_)?SNAPSHOT[ \t]*$',text,re.M)
                or re.search(r'(?im)^\s*(?:Create|Generate|Export)\b[^\n]*\bcrypto_radar\.analysis_snapshot\b', text))


def run(cfg,task,facts):
    key=hashlib.sha256(task['gmail_message_id'].encode()).hexdigest()
    command=[str(Path(cfg['repository'])/'.venv/Scripts/python.exe'),'-B',str(Path(__file__).resolve()),key]
    try:
        from windows_job import Job
        with Job() as job:
            with subprocess.Popen(command,cwd=cfg['repository'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                  text=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)) as process:
                job.assign(process)
                try:
                    stdout, _ = process.communicate(timeout=7200)
                except subprocess.TimeoutExpired:
                    process.kill()
                    raise
                returncode = process.returncode
        data=json.loads(stdout)
        if returncode or not data.get('success'):
            raise RuntimeError(data.get('error','Snapshot failed'))
        summary=json.dumps(data,indent=2)
        report={k:facts[k] for k in ('repository_detected','branch','head','test_status','github_access_status')}
        return dict(exit_code=0,stdout=json.dumps(report|{'summary':summary,'outcome':'COMPLETED'}),stderr='',facts=facts)
    except Exception as exc:
        detail=str(exc) if isinstance(exc,(ValueError,RuntimeError,subprocess.TimeoutExpired)) else type(exc).__name__
        return dict(exit_code=1,stdout='',stderr=type(exc).__name__,facts=facts,
                    failure_summary='Analysis snapshot failed or timed out: '+detail+'. Check progress.json and volumes.json before retrying; only a recorded upload stage can have remote files.')


def finish_cleanup(client, directory, state):
    """Recheck remote delivery before deleting only this export's large payloads."""
    from crypto_radar.weekly_report import save_state
    directory=directory.resolve()
    name=state['filename']
    if Path(name).name != name or not re.fullmatch(r'crypto-radar-analysis-[\w.\-]+\.zip',name):
        raise ValueError('Unsafe cleanup archive name')
    expected_parts=[]
    count=len(state['parts'])
    if not count:
        raise ValueError('No uploaded volumes to verify')
    for index,part in enumerate(state['parts'],1):
        expected=Path(name).stem+f'.volume{index:04}-of-{count:04}.zip'
        if part['filename'] != expected:
            raise ValueError('Unsafe cleanup volume name')
        expected_parts.append(expected)
    # The small local manifest remains as an audit/checksum record.
    from crypto_radar.drive_report import checksums
    manifest=state['manifest']
    local_manifest=directory/'manifest.json'
    if manifest['filename'] != 'manifest.json' or checksums(local_manifest)[0] != manifest['sha256']:
        raise ValueError('Manifest verification failed before cleanup')
    for entry in [*state['parts'],dict(manifest,md5=checksums(local_manifest)[1])]:
        remote=client.existing(entry['file_id'])
        allowed=('application/json','application/octet-stream') if entry['filename']=='manifest.json' else ('application/zip','application/x-zip','application/x-zip-compressed')
        if (not remote or remote.get('trashed') or remote.get('id') != entry['file_id']
            or remote.get('name') != entry['filename'] or remote.get('md5Checksum') != entry['md5']
            or int(remote.get('size',-1)) != entry['size'] or client.folder not in remote.get('parents',[])
            or remote.get('mimeType') not in allowed):
            raise ValueError('Drive verification failed before cleanup')
    targets=[directory/name,directory/Path(name).with_suffix('.db').name,
             *(directory/part for part in expected_parts)]
    if any(target.resolve().parent != directory or target.is_symlink() for target in targets):
        raise ValueError('Cleanup target outside snapshot directory')
    previous=state.get('cleanup',{})
    removed=list(previous.get('deleted_files',[]))
    removed_bytes=previous.get('deleted_bytes',0)
    errors=[]
    for target in targets:
        try:
            if target.exists():
                size=target.stat().st_size
                target.unlink()
                if target.name not in removed:
                    removed.append(target.name)
                removed_bytes+=size
        except OSError as exc:
            errors.append(dict(filename=target.name,error=type(exc).__name__))
    state['cleanup']=dict(status='pending' if errors else 'complete',deleted_files=removed,
                          deleted_bytes=removed_bytes,errors=errors)
    save_state(directory/'result.json',state)
    save_state(directory/'progress.json',dict(stage='cleanup_pending' if errors else 'complete',timestamp=state['timestamp']))
    return state


def create_snapshot(root,key):
    from crypto_radar.weekly_report import save_state
    from crypto_radar.analysis_snapshot import export_snapshot
    from crypto_radar.drive_report import DriveClient, checksums
    from crypto_radar.snapshot_volumes import upload_volumes
    from crypto_radar.operational import process_lock
    directory=root/'orchestration_worker'/'snapshots'/key
    directory.mkdir(parents=True,exist_ok=True)
    state_path=directory/'result.json'
    # One approved export at a time, even for different email message IDs.
    with process_lock(root/'orchestration_worker'/'analysis-export'), process_lock(directory/'snapshot'):
        state=json.loads(state_path.read_text()) if state_path.exists() else None
        with DriveClient() as client:
            if client.folder != '16ERwJgH4ns3IDA0TBzLacHHEKEhFg_qG':
                raise ValueError('Analysis export destination mismatch')
            if state is None:
                stamp=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%S.%fZ')
                output=directory/('crypto-radar-analysis-'+stamp+'.db')
                save_state(directory/'progress.json',dict(stage='analysis_export',started_utc=stamp))
                def record_progress(update):
                    save_state(directory/'progress.json',update)
                    with (directory/'progress.jsonl').open('a',encoding='utf-8') as history:
                        history.write(json.dumps(update)+'\n')
                try:
                    exported=export_snapshot(root/'data'/'crypto_radar.db',output,history_days=84,zipped=True,
                        progress=record_progress)
                except Exception as exc:
                    progress_path=directory/'progress.json'
                    last=json.loads(progress_path.read_text())
                    save_state(progress_path,dict(stage='failed',failed_stage=last.get('stage'),
                        last_progress=last,error=type(exc).__name__,uploads_started=False))
                    raise
                path=Path(exported['zip_path'])
                with zipfile.ZipFile(path) as archive:
                    if archive.testzip() is not None:
                        raise ValueError('Analysis archive CRC validation failed')
                sha,md5=checksums(path)
                state=dict(filename=path.name,timestamp=stamp,local_zip_path=str(path),size=path.stat().st_size,
                           exporter='crypto_radar.analysis_snapshot',export_metadata=exported,
                           sha256=sha,md5=md5,integrity='Exporter SQLite integrity, quick, foreign-key and relationship checks: ok; ZIP CRC: ok',
                           folder=client.folder,account=client.account,file_id=None,status='ready')
                save_state(state_path,state)
            if state.get('exporter') != 'crypto_radar.analysis_snapshot':
                raise ValueError('Wrong workflow: legacy full-database snapshot cannot be reused')
            path=directory/state['filename']
            if path.parent!=directory or state['folder']!=client.folder or state['account']!=client.account:
                raise ValueError('Snapshot destination mismatch')
            if state.get('success') and state.get('status')=='uploaded':
                return finish_cleanup(client,directory,state)
            if checksums(path)!=(state['sha256'],state['md5']):
                raise ValueError('Snapshot checksum mismatch')
            client.refresh_authorization()
            save_state(directory/'progress.json',dict(stage='packaging_and_upload',timestamp=state['timestamp']))
            volumes=upload_volumes(client,path,directory/'volumes.json',max_bytes=190_000_000)
            whole=hashlib.sha256()
            for part, chunk in zip(volumes['parts'],volumes['manifest']['chunks'],strict=True):
                with zipfile.ZipFile(directory/part['filename']) as archive:
                    if archive.testzip() is not None:
                        raise ValueError('Volume CRC validation failed')
                    digest=hashlib.sha256()
                    size=0
                    with archive.open(chunk['name']) as stream:
                        while block := stream.read(1024*1024):
                            whole.update(block); digest.update(block); size+=len(block)
                    if size != chunk['size'] or digest.hexdigest() != chunk['sha256']:
                        raise ValueError('Reconstruction chunk mismatch')
            if whole.hexdigest() != state['sha256']:
                raise ValueError('Reconstructed export SHA-256 mismatch')
            manifest_path=directory/'manifest.json'
            manifest=dict(**volumes['manifest'],volumes=volumes['parts'],export_metadata=state['export_metadata'],
                          verification=dict(local='SHA-256 of volumes, chunks and reconstructed export; ZIP CRC; exporter SQLite checks',
                                            remote='Drive metadata readback: MD5, size, name, parent, MIME; no remote SHA-256 download'))
            save_state(manifest_path,manifest)
            manifest_state_path=directory/'manifest-upload.json'
            delivery=json.loads(manifest_state_path.read_text()) if manifest_state_path.exists() else dict(file_id=client.generate_id())
            save_state(manifest_state_path,delivery)
            sha,md5=checksums(manifest_path)
            if manifest_path.stat().st_size >= 190_000_000:
                raise ValueError('Manifest exceeds size ceiling')
            remote=client.existing(delivery['file_id'])
            if remote is None:
                client.upload(delivery['file_id'],manifest_path,sha)
            remote=client.existing(delivery['file_id'])
            if (not remote or remote.get('trashed') or remote.get('id') != delivery['file_id']
                or remote.get('name') != manifest_path.name or remote.get('md5Checksum') != md5
                or int(remote.get('size',-1)) != manifest_path.stat().st_size
                or client.folder not in remote.get('parents',[])
                or remote.get('mimeType') not in ('application/octet-stream','application/json')):
                raise ValueError('Drive manifest verification failed')
            delivery.update(filename=manifest_path.name,size=manifest_path.stat().st_size,sha256=sha,web_view_link=remote.get('webViewLink'))
            save_state(manifest_state_path,delivery)
            state.pop('drive_url',None)
            state.update(success=True,status='uploaded',archive_format='zip-volumes-v1',
                         parts=volumes['parts'],restore='Extract all volumes together; run python restore_snapshot.py',
                         manifest=delivery,verification=manifest['verification'],
                         tooling='analysis_snapshot.export_snapshot; snapshot_volumes.upload_volumes')
            save_state(state_path,state)
            return finish_cleanup(client,directory,state)


if __name__=='__main__':
    import sys
    root=Path(__file__).resolve().parent.parent
    sys.path.insert(0,str(root))
    try:
        if len(sys.argv)!=2 or not re.fullmatch('[a-f0-9]{64}',sys.argv[1]):
            raise ValueError('Invalid operation key')
        from dotenv import load_dotenv
        load_dotenv(root/'.env')
        print(json.dumps(create_snapshot(root,sys.argv[1])))
    except Exception as exc:
        detail=str(exc) if isinstance(exc,(ValueError,TimeoutError,FileNotFoundError)) else type(exc).__name__
        print(json.dumps({'success':False,'error':detail}))
        sys.exit(1)
