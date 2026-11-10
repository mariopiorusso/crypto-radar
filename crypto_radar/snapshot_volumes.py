"""Portable ZIP volumes containing checksum-verified chunks of one snapshot ZIP."""
import hashlib
import json
import math
from pathlib import Path
import zipfile

DEFAULT_MAX_BYTES = 200_000_000
RESTORE = '''"""Extract every volume into this directory, then run this script with Python."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parent
manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
name = manifest['source_name']
if Path(name).name != name or '/' in name or '\\\\' in name:
    raise SystemExit('Unsafe snapshot name')
target = root / name
if target.exists():
    raise SystemExit('Output already exists; keep it or move it before restoring')
pending = root / (name + '.restoring')
whole = hashlib.sha256()
owned = False
try:
    with pending.open('xb') as output:
        owned = True
        for part in manifest['chunks']:
            name = part['name']
            if Path(name).name != name or '/' in name or '\\\\' in name:
                raise ValueError('Unsafe chunk name')
            chunk = root / name
            digest = hashlib.sha256()
            size = 0
            with chunk.open('rb') as source:
                while block := source.read(1024 * 1024):
                    output.write(block)
                    digest.update(block)
                    whole.update(block)
                    size += len(block)
            if size != part['size'] or digest.hexdigest() != part['sha256']:
                raise ValueError('Missing or corrupt chunk: ' + name)
    if pending.stat().st_size != manifest['source_size'] or whole.hexdigest() != manifest['source_sha256']:
        raise ValueError('Restored snapshot checksum mismatch')
    # Exclusive publication: never overwrite a concurrently restored output.
    import os
    os.link(pending, target)
    pending.unlink()
    print('Restored and verified:', target)
except BaseException:
    if owned:
        pending.unlink(missing_ok=True)
    raise
'''
README = '''CryptoRadar snapshot ZIP volumes

Each volume is an ordinary ZIP. Download all volumes from this snapshot set and
extract them into one folder (the identical manifest and restore script may be
replaced by the copies from the next volume). Do not mix different snapshot sets.

Run: python restore_snapshot.py

The script validates every chunk and the complete SHA-256 before publishing the
original snapshot ZIP. Extract that restored ZIP to obtain the SQLite snapshot.
All volumes are required for the single database; a volume is not a standalone DB.
Existing restored output is never overwritten. No network or credentials needed.
'''


def build_volumes(path, max_bytes=DEFAULT_MAX_BYTES):
    path = Path(path)
    if type(max_bytes) is not int or max_bytes < 131_072:
        raise ValueError('ZIP volume ceiling must be at least 131072 bytes')
    size = path.stat().st_size
    if size < 2:
        raise ValueError('Snapshot is too small to split into multiple volumes')
    reserve = 65536
    # Leave space for a complete manifest and scripts in every ZIP, including headers.
    while True:
        payload = min(max_bytes-reserve-1, math.ceil(size/2))
        if payload < 1:
            raise ValueError('ZIP ceiling is too small for volume metadata')
        count = math.ceil(size/payload)
        required = 4096 + len(RESTORE.encode()) + len(README.encode()) + count*(len(path.name.encode())+256)
        if required <= reserve:
            break
        reserve = required
    chunks = []
    whole = hashlib.sha256()
    with path.open('rb') as source:
        for index in range(count):
            digest = hashlib.sha256()
            length = 0
            while length < payload:
                block = source.read(min(1024*1024, payload-length))
                if not block:
                    break
                digest.update(block)
                whole.update(block)
                length += len(block)
            chunks.append(dict(name=path.name+f'.chunk{index+1:04}',size=length,sha256=digest.hexdigest()))
        if source.read(1):
            raise ValueError('Snapshot grew while planning volumes')
    if sum(c['size'] for c in chunks) != size:
        raise ValueError('Snapshot changed while planning volumes')
    manifest = dict(format='cryptoradar-zip-volumes-v1',source_name=path.name,source_size=size,
                    source_sha256=whole.hexdigest(),max_zip_bytes=max_bytes,chunks=chunks)
    metadata = json.dumps(manifest,indent=2,sort_keys=True).encode('utf-8')
    if len(metadata)+len(RESTORE.encode())+len(README.encode())+2048 > reserve:
        raise ValueError('ZIP volume metadata exceeds reserved space')
    paths = []
    with path.open('rb') as source:
        for index, chunk in enumerate(chunks,1):
            volume = path.with_name(path.stem+f'.volume{index:04}-of-{count:04}.zip')
            staged = volume.with_suffix('.zip.tmp')
            digest = hashlib.sha256()
            try:
                with zipfile.ZipFile(staged,'w',zipfile.ZIP_STORED) as archive:
                    # Fixed timestamps make retries byte-for-byte reproducible.
                    def info(name):
                        return zipfile.ZipInfo(name, date_time=(1980,1,1,0,0,0))
                    archive.writestr(info('manifest.json'),metadata)
                    archive.writestr(info('README.txt'),README)
                    archive.writestr(info('restore_snapshot.py'),RESTORE)
                    with archive.open(info(chunk['name']),'w',force_zip64=True) as target:
                        remaining = chunk['size']
                        while remaining:
                            block = source.read(min(1024*1024,remaining))
                            if not block:
                                raise ValueError('Snapshot truncated while writing volumes')
                            target.write(block)
                            digest.update(block)
                            remaining -= len(block)
                if digest.hexdigest() != chunk['sha256']:
                    raise ValueError('Snapshot changed while writing volumes')
                if staged.stat().st_size >= max_bytes:
                    raise ValueError('ZIP volume exceeds strict size ceiling')
                with zipfile.ZipFile(staged) as archive:
                    if archive.testzip() is not None:
                        raise ValueError('ZIP volume CRC validation failed')
                staged.replace(volume)
                paths.append(volume)
            finally:
                staged.unlink(missing_ok=True)
        if source.read(1) or path.stat().st_size != size:
            raise ValueError('Snapshot grew while writing volumes')
    return manifest, paths


def upload_volumes(client, path, state_path, max_bytes=DEFAULT_MAX_BYTES):
    # Import lazily to reuse the established Drive adapter without an import cycle.
    from .drive_report import checksums
    from .weekly_report import save_state
    state_path = Path(state_path)
    manifest, volumes = build_volumes(path,max_bytes)
    state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else dict(
        manifest=manifest,folder=client.folder,account=client.account,parts=[])
    if state['manifest'] != manifest or state['folder'] != client.folder or state['account'] != client.account:
        raise ValueError('Snapshot volume source, ceiling or destination changed')
    if len(state['parts']) > len(volumes):
        raise ValueError('Unexpected extra ZIP volumes in saved state')
    save_state(state_path,state)
    for index, volume in enumerate(volumes):
        sha, md5 = checksums(volume)
        expected = dict(filename=volume.name,sha256=sha,md5=md5,size=volume.stat().st_size)
        if index == len(state['parts']):
            state['parts'].append(dict(**expected,file_id=None))
            save_state(state_path,state)
        entry = state['parts'][index]
        if any(entry.get(k)!=v for k,v in expected.items()):
            raise ValueError('Saved ZIP volume changed')
        if not entry['file_id']:
            entry['file_id'] = client.generate_id()
            save_state(state_path,state)
        remote = client.existing(entry['file_id'])
        if remote is None:
            remote = client.upload(entry['file_id'],volume,sha)
        if (remote.get('id') != entry['file_id'] or remote.get('trashed')
            or remote.get('name') != volume.name or remote.get('md5Checksum') != md5
            or int(remote.get('size',-1)) != expected['size']
            or client.folder not in remote.get('parents',[])
            or remote.get('mimeType') not in ('application/zip','application/x-zip','application/x-zip-compressed')):
            raise ValueError('Drive ZIP volume verification failed')
        entry.update(status='uploaded',web_view_link=remote.get('webViewLink'))
        save_state(state_path,state)
    state['status'] = 'uploaded'
    save_state(state_path,state)
    return state
