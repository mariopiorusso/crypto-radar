"""Fixed snapshot operation; no email-provided commands, paths or destinations."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
from datetime import datetime, timezone


def requested(text):
    subject=text.splitlines()[0] if text else ''
    return bool(re.fullmatch(r'Subject: \[CRADAR TASK\] SNAPSHOT(?:-[A-Za-z0-9_-]+)?',subject,re.I)
                or re.search(r'^ACTION:[ \t]*SNAPSHOT[ \t]*$',text,re.M))


def run(cfg,task,facts):
    key=hashlib.sha256(task['gmail_message_id'].encode()).hexdigest()
    command=[str(Path(cfg['repository'])/'.venv/Scripts/python.exe'),'-B',str(Path(__file__).resolve()),key]
    try:
        result=subprocess.run(command,cwd=cfg['repository'],capture_output=True,text=True,
                              timeout=1200,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        data=json.loads(result.stdout)
        if result.returncode or not data.get('success'):
            raise RuntimeError(data.get('error','Snapshot failed'))
        summary=json.dumps(data,indent=2)
        report={k:facts[k] for k in ('repository_detected','branch','head','test_status','github_access_status')}
        return dict(exit_code=0,stdout=json.dumps(report|{'summary':summary,'outcome':'COMPLETED'}),stderr='',facts=facts)
    except Exception as exc:
        return dict(exit_code=1,stdout='',stderr=type(exc).__name__,facts=facts,
                    failure_summary='Snapshot action failed or timed out ('+type(exc).__name__+'). Check worker snapshot state before retrying; upload may be uncertain.')


def create_snapshot(root,key):
    from crypto_radar.weekly_report import build_archive, save_state
    from crypto_radar.drive_report import DriveClient, checksums
    from crypto_radar.operational import process_lock
    directory=root/'orchestration_worker'/'snapshots'/key
    directory.mkdir(parents=True,exist_ok=True)
    state_path=directory/'result.json'
    with process_lock(directory/'snapshot'):
        state=json.loads(state_path.read_text()) if state_path.exists() else None
        with DriveClient() as client:
            if state is None:
                stamp=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%S.%fZ')
                path=build_archive(root,directory/('crypto-radar-'+stamp+'.zip'))
                sha,md5=checksums(path)
                state=dict(filename=path.name,timestamp=stamp,local_zip_path=str(path),size=path.stat().st_size,
                           sha256=sha,md5=md5,integrity='SQLite backup API and PRAGMA quick_check: ok',
                           folder=client.folder,account=client.account,file_id=client.generate_id(),status='ready')
                save_state(state_path,state)
            path=directory/state['filename']
            if path.parent!=directory or state['folder']!=client.folder or state['account']!=client.account:
                raise ValueError('Snapshot destination mismatch')
            if checksums(path)!=(state['sha256'],state['md5']):
                raise ValueError('Snapshot checksum mismatch')
            remote=client.existing(state['file_id'])
            if remote is None:
                remote=client.upload(state['file_id'],path,state['sha256'])
            if (remote.get('trashed') or remote.get('id')!=state['file_id']
                or remote.get('md5Checksum')!=state['md5'] or int(remote.get('size',-1))!=state['size']
                or remote.get('name')!=path.name or client.folder not in remote.get('parents',[])
                or remote.get('mimeType')!='application/zip'):
                raise ValueError('Drive verification failed')
            state.update(success=True,status='uploaded',drive_url=remote.get('webViewLink') or 'https://drive.google.com/file/d/'+state['file_id']+'/view',
                         tooling='weekly_report.build_archive; drive_report.DriveClient')
            save_state(state_path,state)
            return state


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
        print(json.dumps({'success':False,'error':type(exc).__name__}))
        sys.exit(1)
