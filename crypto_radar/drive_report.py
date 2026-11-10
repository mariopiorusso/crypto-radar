"""Upload one timestamped weekly ZIP to an explicitly configured Drive folder."""
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

from .config import ROOT
from .operational import process_lock
from .weekly_report import build_archive, save_state
from .alerts.email import send_alert

API = "https://www.googleapis.com/drive/v3"
FIELDS = "id,name,parents,mimeType,md5Checksum,size,webViewLink,trashed"
MAX_PART_BYTES = 200_000_000  # Decimal MB, including the hard 200 MB ceiling.


def upload_parts(client, path, state_path):
    """Split a ZIP byte-for-byte and upload recoverably; never replace the ZIP."""
    path, state_path = Path(path), Path(state_path)
    sha, _ = checksums(path)
    state = json.loads(state_path.read_text()) if state_path.exists() else dict(
        sha256=sha, size=path.stat().st_size, folder=client.folder,
        account=client.account, parts=[])
    if (state['sha256'] != sha or state['size'] != path.stat().st_size
            or state['folder'] != client.folder or state['account'] != client.account):
        raise ValueError('Multipart source or destination changed')
    save_state(state_path, state)
    with path.open('rb') as source:
        index = 0
        while data := source.read(MAX_PART_BYTES):
            part = path.with_name(path.name + f'.part{index + 1:03}')
            digest = hashlib.sha256(data).hexdigest()
            md5 = hashlib.md5(data).hexdigest()
            if not part.exists() or checksums(part)[0] != digest:
                staged = part.with_suffix(part.suffix + '.tmp')
                staged.write_bytes(data)
                staged.replace(part)
            if index == len(state['parts']):
                state['parts'].append(dict(filename=part.name, sha256=digest,
                    md5=md5, size=len(data), file_id=None))
                save_state(state_path, state)
            entry = state['parts'][index]
            if (entry['filename'], entry['sha256'], entry['md5'], entry['size']) != (
                    part.name, digest, md5, len(data)):
                raise ValueError('Multipart state mismatch')
            if not entry['file_id']:
                entry['file_id'] = client.generate_id()
                save_state(state_path, state)
            remote = client.existing(entry['file_id'])
            if remote is None:
                remote = client.upload(entry['file_id'], part, digest)
            if (remote.get('trashed') or remote.get('id') != entry['file_id']
                    or remote.get('md5Checksum') != md5 or int(remote.get('size', -1)) != len(data)
                    or remote.get('name') != part.name or client.folder not in remote.get('parents', [])
                    or remote.get('mimeType') not in (
                        'application/octet-stream', 'application/zip', 'application/x-zip',
                        'application/x-zip-compressed')):
                raise ValueError('Drive part verification failed')
            entry.update(status='uploaded', web_view_link=remote.get('webViewLink'))
            save_state(state_path, state)
            index += 1
    if index != len(state['parts']):
        raise ValueError('Unexpected extra parts in state')
    state['status'] = 'uploaded'
    save_state(state_path, state)
    return state


def checksums(path):
    sha, md5 = hashlib.sha256(), hashlib.md5()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
            md5.update(chunk)
    return sha.hexdigest(), md5.hexdigest()


class DriveClient:
    def __init__(self):
        required = ("GOOGLE_DRIVE_CLIENT_ID", "GOOGLE_DRIVE_CLIENT_SECRET", "GOOGLE_DRIVE_REFRESH_TOKEN",
                    "GOOGLE_DRIVE_FOLDER_ID", "GOOGLE_DRIVE_ACCOUNT")
        if any(not os.getenv(key, "").strip() for key in required):
            raise ValueError("Configure Google Drive OAuth credentials, folder ID and account before uploading")
        self.folder = os.environ["GOOGLE_DRIVE_FOLDER_ID"].strip()
        self.account = os.environ["GOOGLE_DRIVE_ACCOUNT"].strip().lower()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", self.folder):
            raise ValueError("GOOGLE_DRIVE_FOLDER_ID must be the ID from a /folders/ link")
        self.session = requests.Session()

    def __enter__(self):
        try:
            token = self.session.post("https://oauth2.googleapis.com/token", data={
                "client_id": os.environ["GOOGLE_DRIVE_CLIENT_ID"],
                "client_secret": os.environ["GOOGLE_DRIVE_CLIENT_SECRET"],
                "refresh_token": os.environ["GOOGLE_DRIVE_REFRESH_TOKEN"],
                "grant_type": "refresh_token"}, timeout=30)
            token.raise_for_status()
            self.session.headers["Authorization"] = "Bearer " + token.json()["access_token"]
            about = self.get("about", {"fields": "user(emailAddress)"})
            if about["user"]["emailAddress"].lower() != self.account:
                raise ValueError("Authorized Google Drive account does not match GOOGLE_DRIVE_ACCOUNT")
            folder = self.get("files/" + self.folder, {"fields": "mimeType,trashed,capabilities(canAddChildren)", "supportsAllDrives": "true"})
            if folder.get("trashed") or folder["mimeType"] != "application/vnd.google-apps.folder" or not folder.get("capabilities", {}).get("canAddChildren"):
                raise ValueError("Destination must be an accessible writable Drive folder")
            return self
        except BaseException:
            self.session.close()
            raise

    def __exit__(self, *args):
        self.session.close()

    def get(self, resource, params):
        result = self.session.get(API + "/" + resource, params=params, timeout=30)
        result.raise_for_status()
        return result.json()

    def generate_id(self):
        return self.get("files/generateIds", {"count": 1, "space": "drive", "type": "files"})["ids"][0]

    def existing(self, file_id):
        response = self.session.get(API + "/files/" + file_id,
            params={"fields": FIELDS, "supportsAllDrives": "true"}, timeout=30)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    def upload(self, file_id, path, sha256):
        mime = 'application/zip' if path.suffix == '.zip' else 'application/octet-stream'
        response = self.session.post("https://www.googleapis.com/upload/drive/v3/files",
            params={"uploadType": "resumable", "fields": FIELDS, "supportsAllDrives": "true"},
            headers={"X-Upload-Content-Type": mime, "X-Upload-Content-Length": str(path.stat().st_size)},
            json={"id": file_id, "name": path.name, "mimeType": mime, "parents": [self.folder],
                  "description": "Crypto Radar weekly snapshot. SHA-256: " + sha256}, timeout=30)
        response.raise_for_status()
        location = response.headers["Location"]
        parsed = urlparse(location)
        if parsed.scheme != "https" or parsed.hostname != "www.googleapis.com":
            raise ValueError("Unexpected Google upload endpoint")
        with path.open("rb") as stream:
            result = self.session.put(location, data=stream,
                headers={"Content-Type": mime, "Content-Length": str(path.stat().st_size)}, timeout=(30, 600))
        result.raise_for_status()
        return result.json()


def run_drive_report(root=ROOT, dry_run=False, now=None, notify=False, analysis=False, history_days=84,
                     zip_volumes=False):
    root = Path(root)
    now = now or datetime.now(timezone.utc)
    year, week, _ = now.astimezone().date().isocalendar()
    directory = root / "data" / "weekly-reports"
    directory.mkdir(parents=True, exist_ok=True)
    prefix = 'crypto-radar-analysis' if analysis else 'crypto-radar'
    def build():
        if analysis:
            from .analysis_snapshot import export_snapshot
            destination = directory / f"{prefix}-{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%S%fZ')}.db"
            result = export_snapshot(root / 'data/crypto_radar.db', destination,
                                     history_days=history_days, zipped=not zip_volumes, as_of=now)
            if zip_volumes:
                import zipfile
                archive = destination.with_suffix('.zip')
                with zipfile.ZipFile(archive,'x',zipfile.ZIP_DEFLATED,compresslevel=6) as zipped:
                    zipped.write(destination,destination.name)
                return archive
            return Path(result['zip_path'])
        return build_archive(root, directory / f"crypto-radar-{now.strftime('%Y-%m-%dT%H%M%SZ')}.zip")
    if dry_run:
        path = build()
        if zip_volumes:
            from .snapshot_volumes import build_volumes
            build_volumes(path)
        return path
    with process_lock(directory / "weekly-report"), DriveClient() as client:
        state_path = directory / f"{prefix}-{year}-W{week:02}.drive.json"
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else None
        if state:
            if analysis and state.get('history_days') != history_days:
                raise ValueError('History window changed; review this week\'s saved analysis state')
            if state["folder"] != client.folder or state["account"] != client.account:
                raise ValueError("Destination changed since this week's archive was prepared; review saved state")
            filename = state["filename"]
            if Path(filename).name != filename:
                raise ValueError("Invalid stored archive filename")
            path = directory / filename
        else:
            path = build()
            sha256, md5 = checksums(path)
            state = {"filename": path.name, "sha256": sha256, "md5": md5, "size": path.stat().st_size,
                     "folder": client.folder, "account": client.account, "file_id": None, "status": "ready"}
            if analysis:
                state.update(artifact_kind='analysis', history_days=history_days)
            save_state(state_path, state)
        if zip_volumes:
            if checksums(path) != (state['sha256'],state['md5']):
                raise ValueError('Local archive changed')
            from .snapshot_volumes import upload_volumes
            volumes = upload_volumes(client,path,state_path.with_suffix('.volumes.json'))
            state.update(status='uploaded',archive_format='zip-volumes-v1',parts=volumes['parts'])
            save_state(state_path,state)
            if notify and state.get('notification') != 'sent':
                if state.get('notification') == 'dispatching':
                    raise RuntimeError('Confirmation delivery uncertain; inspect mailbox before retrying')
                state['notification'] = 'dispatching'
                save_state(state_path,state)
                links = '\n'.join(p['filename']+': '+(p.get('web_view_link') or
                    'https://drive.google.com/file/d/'+p['file_id']+'/view') for p in volumes['parts'])
                send_alert(f"Snapshot uploaded in {len(volumes['parts'])} ZIP volumes, each strictly below 200 MB.\n"
                    f"{links}\n\nExtract all volumes into one folder and run python restore_snapshot.py.\n"
                    f"Original ZIP SHA-256: {state['sha256']}",subject='Crypto Radar weekly snapshot confirmed')
                state['notification'] = 'sent'
                save_state(state_path,state)
            return path
        if analysis and path.stat().st_size > MAX_PART_BYTES:
            if checksums(path) != (state['sha256'], state['md5']):
                raise ValueError('Local archive changed')
            parts = upload_parts(client, path, state_path.with_suffix('.parts.json'))
            state.update(status='uploaded', parts=parts['parts'])
            save_state(state_path, state)
            if notify and state.get('notification') != 'sent':
                if state.get('notification') == 'dispatching':
                    raise RuntimeError('Confirmation delivery uncertain; inspect mailbox before retrying')
                state['notification'] = 'dispatching'
                save_state(state_path, state)
                links = '\n'.join(p['filename'] + ': ' + (p.get('web_view_link') or
                    'https://drive.google.com/file/d/' + p['file_id'] + '/view') for p in parts['parts'])
                send_alert(f"Compact analysis snapshot uploaded in {len(parts['parts'])} parts (max 200 MB).\n"
                           f"{links}\n\nConcatenate parts in numerical order to restore {path.name}.\n"
                           f"ZIP SHA-256: {state['sha256']}",
                           subject='Crypto Radar weekly snapshot confirmed')
                state['notification'] = 'sent'
                save_state(state_path, state)
            return path
        if not state.get('file_id'):
            state['file_id'] = client.generate_id()
            save_state(state_path, state)
        if checksums(path) != (state["sha256"], state["md5"]):
            raise ValueError("Local archive changed; refusing to upload different content")
        # Pre-generated ID allows recovery after an upload succeeds but its response is lost.
        remote = client.existing(state["file_id"])
        if remote is None:
            remote = client.upload(state["file_id"], path, state["sha256"])
        if (remote.get("trashed") or remote.get("id") != state["file_id"]
                or remote.get("md5Checksum") != state["md5"] or int(remote.get("size", -1)) != state["size"]
                or remote.get("name") != path.name or client.folder not in remote.get("parents", [])
                or remote.get("mimeType") != "application/zip"):
            raise ValueError("Drive file verification failed")
        state.update(status="uploaded", web_view_link=remote.get("webViewLink"))
        save_state(state_path, state)
        if notify and state.get('notification') != 'sent':
            if state.get('notification') == 'dispatching':
                raise RuntimeError('Confirmation delivery uncertain; inspect mailbox before retrying')
            state['notification'] = 'dispatching'
            save_state(state_path, state)
            link = 'https://drive.google.com/file/d/' + state['file_id'] + '/view'
            send_alert(f"Crypto Radar snapshot created and uploaded successfully.\n\n"
                       f"File: {path.name}\nSize: {state['size']} bytes\n"
                       f"SHA-256: {state['sha256']}\nGoogle Drive: {link}\n\n"
                       "SQLite snapshot passed its integrity check; uploaded size and checksum verified.",
                       subject='Crypto Radar weekly snapshot confirmed')
            state['notification'] = 'sent'
            save_state(state_path, state)
        return path
