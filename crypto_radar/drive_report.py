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

API = "https://www.googleapis.com/drive/v3"
FIELDS = "id,name,parents,mimeType,md5Checksum,size,webViewLink,trashed"


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
        response = self.session.post("https://www.googleapis.com/upload/drive/v3/files",
            params={"uploadType": "resumable", "fields": FIELDS, "supportsAllDrives": "true"},
            headers={"X-Upload-Content-Type": "application/zip", "X-Upload-Content-Length": str(path.stat().st_size)},
            json={"id": file_id, "name": path.name, "mimeType": "application/zip", "parents": [self.folder],
                  "description": "Crypto Radar weekly snapshot. SHA-256: " + sha256}, timeout=30)
        response.raise_for_status()
        location = response.headers["Location"]
        parsed = urlparse(location)
        if parsed.scheme != "https" or parsed.hostname != "www.googleapis.com":
            raise ValueError("Unexpected Google upload endpoint")
        with path.open("rb") as stream:
            result = self.session.put(location, data=stream,
                headers={"Content-Type": "application/zip", "Content-Length": str(path.stat().st_size)}, timeout=(30, 600))
        result.raise_for_status()
        return result.json()


def run_drive_report(root=ROOT, dry_run=False, now=None):
    root = Path(root)
    now = now or datetime.now(timezone.utc)
    year, week, _ = now.astimezone().date().isocalendar()
    directory = root / "data" / "weekly-reports"
    directory.mkdir(parents=True, exist_ok=True)
    if dry_run:
        return build_archive(root, directory / f"crypto-radar-{now.strftime('%Y-%m-%dT%H%M%SZ')}.zip")
    with process_lock(directory / "weekly-report"), DriveClient() as client:
        state_path = directory / f"crypto-radar-{year}-W{week:02}.drive.json"
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else None
        if state:
            if state["folder"] != client.folder or state["account"] != client.account:
                raise ValueError("Destination changed since this week's archive was prepared; review saved state")
            filename = state["filename"]
            if Path(filename).name != filename:
                raise ValueError("Invalid stored archive filename")
            path = directory / filename
        else:
            path = build_archive(root, directory / f"crypto-radar-{now.strftime('%Y-%m-%dT%H%M%SZ')}.zip")
            sha256, md5 = checksums(path)
            state = {"filename": path.name, "sha256": sha256, "md5": md5, "size": path.stat().st_size,
                     "folder": client.folder, "account": client.account, "file_id": client.generate_id(), "status": "ready"}
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
        return path
