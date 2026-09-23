# Privacy Policy

Last updated: 23 September 2026

## About this policy

This policy describes the current personal-use Crypto Radar application and its
optional Google Drive reporting integration.

## Information processed

Crypto Radar stores cryptocurrency market observations, news evidence, signal
features, AI assessments, outcome measurements and operational records in a local
SQLite database and log files.

When Google Drive reporting is enabled, the application processes the authorized
Google account's email address, the configured folder ID, report file IDs and file
metadata needed to upload and verify reports. Google OAuth client credentials and
refresh tokens are stored locally in environment variables or the local `.env`
file. They are not included in report ZIPs.

Configured alert-recipient addresses and notification credentials are also stored
locally. The application does not use Gmail inbox access for weekly Drive uploads.

## How information is used

Information is used to operate the research engine, evaluate its signals, deliver
configured alerts and upload weekly reports. Google account information is used
to confirm that reports go to the intended account and folder.

The Drive integration currently requests the full Google Drive OAuth scope. Its
implemented reporting workflow checks account and folder metadata and creates or
verifies its report ZIPs; it does not download unrelated Drive documents for
analysis.

## Third-party processing

When AI analysis is enabled, selected market, news and available signal evidence
is sent to OpenAI. Configured email or Telegram providers process alert messages
and delivery information. Google stores uploaded report ZIPs, containing the
research database snapshot, application configuration and a scoring source file.

Crypto Radar does not sell personal information or use Google account information
for advertising. Third-party providers process information under their own
applicable policies.

## Storage, access and retention

Local databases, logs, credentials and report archives reside on the computer
running Crypto Radar. Logs rotate according to configuration. Research data,
archives and uploaded reports are retained until the operator removes them;
research records are not automatically deleted on a fixed schedule.

Access depends on the computer's permissions and the Google Drive folder's sharing
settings. The application does not create public sharing links. Reports may contain
research evidence and operational metadata, so folder access should be configured
accordingly.

## Revoking access and deleting information

You can revoke Crypto Radar's Google authorization through your Google Account's
third-party connections settings. Revoking authorization stops future authorized
Drive operations but does not delete reports already uploaded.

The operator can remove uploaded reports from Google Drive and delete local
research records, archives or credentials separately. Stop the relevant processes
before removing files they are using.

## Changes and contact

This policy may be updated when the application's data handling changes.
For questions or deletion requests, contact cryptoradar128@gmail.com.
