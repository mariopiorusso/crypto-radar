"""Archive read-only ApeWisdom health without running the scanner or sending messages."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from .validation_report import health


def build_health(path, now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('Timezone-aware health-check time required')
    asof = now.astimezone(timezone.utc).isoformat()
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True, timeout=10)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        scan = db.execute("SELECT id,completed_ts,config_json FROM scan_runs WHERE status='completed' AND completed_ts<=? ORDER BY completed_ts DESC,id DESC LIMIT 1", (asof,)).fetchone()
        if not scan:
            raise ValueError('No completed scanner run available')
        config = json.loads(scan['config_json'])
        social = config.get('v12', {}).get('social', {})
        settings = social.get('provider_options', {}).get('apewisdom', {})
        result = health(db, asof, settings)
        result['checked_at'] = asof
        result['latest_completed_scan_id'] = scan['id']
        result['latest_completed_scan_ts'] = scan['completed_ts']
        result['scanner_age_seconds'] = (now-datetime.fromisoformat(scan['completed_ts'])).total_seconds()
        result['runtime_enabled'] = bool(config.get('v12', {}).get('enabled') and social.get('enabled')
                                         and settings.get('enabled') and 'apewisdom' in social.get('providers', []))
        result['configured_assets'] = len(settings.get('asset_map', {}))
        result['permission_evidence'] = 'Operator access_confirmed flag; not independent verification.'
        return result


def save_report(result, output):
    directory = Path(output)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromisoformat(result['checked_at']).strftime('%Y%m%dT%H%M%S%fZ')
    archive = directory/f'apewisdom-health-{stamp}.json'
    payload = json.dumps(result, indent=2, sort_keys=True, allow_nan=False)+'\n'
    # Failed checks never replace the last successful report. Existing archives survive.
    with archive.open('x', encoding='utf-8') as file:
        file.write(payload)
    pending = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=directory, suffix='.tmp', delete=False) as file:
            pending = file.name
            file.write(payload)
        os.replace(pending, directory/'latest.json')
        pending = None
    finally:
        if pending:
            Path(pending).unlink(missing_ok=True)
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True)
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    try:
        report = build_health(args.db)
        archive = save_report(report, args.output_dir)
    except (sqlite3.Error, ValueError, OSError) as exc:
        parser.exit(2, f'Health check failed: {exc}\n')
    print(json.dumps({'status': report['data_status'], 'resolved_assets': report['distinct_resolved_assets'],
                      'archive': str(archive)}))


if __name__ == '__main__':
    main()
