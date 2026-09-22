import json
import io
import os
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import closing
from datetime import date
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import patch

from crypto_radar.weekly_report import MEMBERS, build_archive, run_report


class WeeklyReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for member in MEMBERS:
            (self.root / member).parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.root / MEMBERS[0])) as conn:
            conn.execute('CREATE TABLE evidence(value TEXT)')
            conn.execute("INSERT INTO evidence VALUES ('preserved')")
            conn.commit()
        (self.root / MEMBERS[1]).write_text('currency: usd\n')
        (self.root / MEMBERS[2]).write_text('# scoring source\n')
        (self.root / '.env').write_text('SECRET=must-not-be-included')

    def test_archive_contains_exact_files_and_valid_database(self):
        path = build_archive(self.root, self.root / 'report.zip')
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(set(archive.namelist()), set(MEMBERS))
            self.assertEqual(archive.read(MEMBERS[1]), (self.root / MEMBERS[1]).read_bytes())
            db = self.root / 'snapshot.db'
            db.write_bytes(archive.read(MEMBERS[0]))
        with closing(sqlite3.connect(db)) as conn:
            self.assertEqual(conn.execute('PRAGMA quick_check').fetchone()[0], 'ok')
            self.assertEqual(conn.execute('SELECT value FROM evidence').fetchone()[0], 'preserved')

    def test_dry_run_never_sends(self):
        with patch('crypto_radar.weekly_report.send_alert') as send:
            path = run_report(self.root, dry_run=True)
            self.assertTrue(path.exists())
            send.assert_not_called()

    def test_weekly_email_zip_attachment_mime_type(self):
        env = {'SMTP_HOST': '127.0.0.1', 'SMTP_PORT': '1025', 'SMTP_SECURITY': 'local',
               'EMAIL_FROM': 'from@example.invalid', 'EMAIL_TO': 'to@example.invalid'}
        with patch.dict(os.environ, env, clear=True), \
                patch('crypto_radar.alerts.email.smtplib.SMTP') as smtp, \
                patch('socket.socket.connect', side_effect=AssertionError('Network forbidden')):
            client = smtp.return_value.__enter__.return_value
            client.send_message.return_value = {}
            archive_path = run_report(self.root, today=date(2026, 9, 21))
            client.send_message.assert_called_once()
            message = client.send_message.call_args.args[0]
            parsed = BytesParser(policy=policy.default).parsebytes(message.as_bytes())
        attachments = list(parsed.iter_attachments())
        self.assertEqual(len(attachments), 1)
        attachment = attachments[0]
        self.assertTrue(attachment.get_filename().endswith('.zip'))
        self.assertEqual(attachment.get_filename(), archive_path.name)
        self.assertEqual(attachment.get_content_type(), 'application/zip')
        self.assertEqual(attachment['Content-Type'], 'application/zip')
        self.assertEqual(attachment.get_payload(decode=True), archive_path.read_bytes())
        with zipfile.ZipFile(io.BytesIO(attachment.get_payload(decode=True))) as archive:
            self.assertEqual(set(archive.namelist()), set(MEMBERS))

    def test_split_archive_and_weekly_dedup(self):
        with patch('crypto_radar.weekly_report.send_alert') as send:
            path = run_report(self.root, today=date(2026,9,21), part_bytes=200)
            parts = [call.kwargs['attachments'][0][1] for call in send.call_args_list]
            self.assertGreater(len(parts),1)
            self.assertEqual(b''.join(parts), path.read_bytes())
            count=send.call_count
            run_report(self.root, today=date(2026,9,21))
            self.assertEqual(send.call_count,count)

    def test_uncertain_send_does_not_retry(self):
        with patch('crypto_radar.weekly_report.send_alert',side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):
                run_report(self.root,today=date(2026,9,21))
        with patch('crypto_radar.weekly_report.send_alert') as send:
            with self.assertRaisesRegex(RuntimeError,'uncertain'):
                run_report(self.root,today=date(2026,9,21))
            send.assert_not_called()
