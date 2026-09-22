"""Email a consistent SQLite snapshot and the two requested source/config files."""
import argparse
import hashlib
import json
import logging
import math
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from .config import ROOT
from .alerts.email import send_alert
from .operational import process_lock, setup_logging

PART_BYTES = 12_000_000  # Under Gmail's limit after MIME/base64 overhead.
MEMBERS = ("data/crypto_radar.db", "config.yaml", "crypto_radar/intelligence/statistical.py")
log = logging.getLogger(__name__)


def build_archive(root, destination):
    root, destination = Path(root), Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Never open a missing source in create mode or copy an active SQLite file directly.
    with tempfile.TemporaryDirectory(dir=destination.parent) as temp:
        snapshot = Path(temp) / "crypto_radar.db"
        with closing(sqlite3.connect((root / MEMBERS[0]).resolve().as_uri() + "?mode=ro", uri=True, timeout=30)) as source:
            with closing(sqlite3.connect(snapshot)) as target:
                source.backup(target, pages=256, sleep=0.05)
                if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise RuntimeError("Database snapshot integrity check failed")
        temporary_zip = Path(temp) / "report.zip"
        with zipfile.ZipFile(temporary_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            archive.write(snapshot, MEMBERS[0])
            for member in MEMBERS[1:]:
                archive.write(root / member, member)
        # Windows TemporaryDirectory uses a private ACL. Copy into the report
        # directory first so the final file inherits access for Local Service.
        staged = destination.with_suffix(".zip.tmp")
        shutil.copyfile(temporary_zip, staged)
        staged.replace(destination)
    return destination


def save_state(path, state):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temporary.replace(path)


def run_report(root=ROOT, dry_run=False, today=None, part_bytes=PART_BYTES):
    root = Path(root)
    year, week, _ = (today or datetime.now().date()).isocalendar()
    directory = root / "data" / "weekly-reports"
    directory.mkdir(parents=True, exist_ok=True)
    prefix = f"crypto-radar-{year}-W{week:02}"
    archive_path = directory / (prefix + ".zip")
    state_path = directory / (prefix + ".json")
    with process_lock(directory / "weekly-report"):
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else None
        if state and state["status"] == "sent":
            log.info("Weekly report already sent for %s", prefix)
            return archive_path
        if state and any(p["status"] == "dispatching" for p in state["parts"]):
            raise RuntimeError("A previous delivery is uncertain; inspect the inbox before manually retrying")
        if not state:
            build_archive(root, archive_path)
            digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
            count = max(1, math.ceil(archive_path.stat().st_size / part_bytes))
            state = {"status": "ready", "sha256": digest, "part_bytes": part_bytes,
                     "parts": [{"status": "pending"} for _ in range(count)]}
            if not dry_run:
                save_state(state_path, state)
        elif hashlib.sha256(archive_path.read_bytes()).hexdigest() != state["sha256"]:
            raise RuntimeError("Saved weekly archive changed; refusing to mix archive parts")
        log.info("Archive ready: %s (%s bytes, %s parts)", archive_path.name, archive_path.stat().st_size, len(state["parts"]))
        if dry_run:
            return archive_path
        count = len(state["parts"])
        with archive_path.open("rb") as stream:
            for index, part in enumerate(state["parts"], 1):
                data = stream.read(state["part_bytes"])
                if part["status"] == "sent":
                    continue
                filename = archive_path.name if count == 1 else archive_path.name + f".part{index:03}"
                body = "Weekly Crypto Radar snapshot. ZIP contents:\n" + "\n".join(MEMBERS)
                body += f"\n\nZIP SHA-256: {state['sha256']}\n"
                if count > 1:
                    body += (f"\nPart {index} of {count}. Save every part in the same directory.\n"
                             "In PowerShell, run this command to reconstruct the ZIP:\n"
                             f"python -c \"from pathlib import Path; files=sorted(Path('.').glob('{archive_path.name}.part*')); "
                             f"assert len(files)=={count}; out=open('{archive_path.name}','wb'); "
                             "[out.write(p.read_bytes()) for p in files]; out.close()\"\n")
                # Persist before dispatch so task restarts cannot blindly duplicate a send.
                part["status"] = "dispatching"
                save_state(state_path, state)
                send_alert(body, subject=f"Crypto Radar weekly files — {year}-W{week:02} ({index}/{count})",
                           attachments=[(filename, data)])
                part["status"] = "sent"
                save_state(state_path, state)
        state["status"] = "sent"
        save_state(state_path, state)
        log.info("Weekly report accepted by relay for %s", prefix)
        return archive_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Build and inspect the ZIP without sending email")
    parser.add_argument("--drive", action="store_true", help="Upload one timestamped ZIP to Google Drive instead of emailing")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    setup_logging({"path": str(ROOT / "logs/weekly-report.log"), "max_bytes": 1000000, "backup_count": 3})
    try:
        if args.drive:
            from .drive_report import run_drive_report
            archive = run_drive_report(dry_run=args.dry_run)
            log.info("Drive report %s: %s", "prepared" if args.dry_run else "uploaded and verified", archive.name)
        else:
            run_report(dry_run=args.dry_run)
    except Exception as exc:
        log.error("Weekly report failed: %s", type(exc).__name__)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
