# Crypto Radar handshake worker â€” Phase 1

This is a separate worker inside `C:\crypto-radar\orchestration_worker`, following
the latest location instruction. Its snapshot subprocess reuses Crypto Radar's backup
and upload helpers with read-only access to the source database. It does not change
scanner configuration, run scanner tests, or manage scanner/relay/Drive scheduled
tasks. Its OAuth credentials, SQLite state, logs and reports are separate.
Engineering mode now permits requested repository edits, tests, commits and pushes.
Set `engineering_enabled: true` in local config to enable it (example default false).
The approved live configuration uses an independent engineering checkout at
`orchestration_worker/workspaces/crypto-radar`, configured by `engineering_repository`.
`repository` remains the live scanner source for snapshots and read-only mode.
Coding tasks run in the checkout with its own Git metadata and worker/* branches;
replies name the actual execution repository. No automatic deployment or merge into
the live scanner occurs. The checkout starts at committed HEAD; existing live staged,
unstaged and untracked work is preserved in place and is not copied. Its origin points
to the same upstream as the live repository. It does not share Git object files.
Dependencies and credentials are not copied. Tests may require a checkout-local venv.
Do not run scanners or live alerts from this checkout. Existing completed email
requests are never replayed automatically; send a new request when retrying.

The earlier live-repository Git write probe failed due to protected .git permissions.
No ACLs were removed to create this checkout. Windowless polling and the snapshot
operation continue independently of engineering checkout changes.

## Authorized snapshot action

Send `[CRADAR TASK] SNAPSHOT-002` as the subject, or include the exact line
`ACTION: SNAPSHOT` in a tagged email. Trusted sender validation still applies.
This selects a fixed Python action instead of Codex. Other requests use the configured Codex execution mode.
The action uses the repository's `.venv` and existing `build_archive` / `DriveClient`
implementations. Only its subprocess loads the existing Drive configuration from
`.env`; credentials and email text are never passed as command arguments.

It creates a timestamped ZIP containing `data/crypto_radar.db`, `config.yaml` and
`crypto_radar/intelligence/statistical.py`, using SQLite backup and quick_check.
It uploads to the configured Drive account/folder and verifies size, name, MIME type,
parent and checksum. Email cannot select commands, source paths or destinations.
The reply includes the result, ZIP path/size/hash, timestamp and Drive link.

Archives and upload state live in ignored `orchestration_worker/snapshots/`, keyed
by Gmail message ID. This does not reuse weekly report state or overwrite older
snapshots. A preallocated Drive ID supports recovery without creating another file.
The source database is opened read-only; scanner processes are not stopped.
The fixed action has a 20-minute timeout. An interrupted/failed action is not
automatically rerun; inspect its saved state before retrying. Mock/offline tests
never upload. Run snapshot tests using the repository `.venv`, which contains the
existing report dependencies.

## Architecture and boundaries

Every minute when enabled, a Windows task runs `pythonw.exe gmail_poller.py` once
without opening a console window. Logging goes only to `logs/worker.log` with rotation.
Manual authorization still uses `python.exe`. Python uses
the official Gmail API with desktop OAuth, `gmail.readonly` and `gmail.send`. Gmail polling makes
no Codex/OpenAI calls. Empty inbox: log counts and exit. A pending accepted task from
an earlier poll can still execute even if there are no newly discovered messages.

Any subject containing the exact marker `[CRADAR TASK]` is accepted from an explicitly
allowed, authenticated From address. MODE and TASK_ID fields are optional and never
grant additional permissions. Legacy HANDSHAKE task IDs retain their deduplication;
other messages receive an ID derived from their raw-message hash. Automated messages
(including this worker's replies) are rejected to prevent loops. One text/plain body
is required; attachments, HTML-only mail and oversized messages are rejected. Multipart alternatives may
include HTML, but exactly one plain-text body must exist. Gmail read/unread flags are
not changed. Search covers Inbox task mail within the configured rolling lookback.
Self-sent mail may lack authentication headers. For an allow-listed sender matching
the configured mailbox, Gmail API's SENT label is accepted as evidence of sending
from that account. Email headers cannot supply this exception.

The validated plain-text email request is passed to a fresh Codex session, together
with local repository facts. Codex answers in the reply's summary field. This is not
the existing IDE conversation: include all necessary context in each request.
Email text is passed as structured prompt data, never interpolated into a shell command.
In engineering mode, Codex can inspect/edit the repository, run tests, commit requested
changes and push to existing remotes. It uses `workspace-write`, shell/unified-exec tools,
network access for Git, and explicit write access to the repository's `.git` directory.
It does not use `danger-full-access` or a sandbox bypass. The repository must have a local
`.git` directory; linked worktrees are not supported by this configuration.

`engineering.txt` requires preserving existing changes, staging only requested work,
keeping secrets/data out of commits, and reporting actual tests and Git results. Trading,
scanner disruption, force pushes and changing worker permissions are prohibited by the
worker instructions. These are agent instructions, not OS-level per-command filters.
Workspace write access permits repository changes; it is not a confidentiality boundary.
Only trust senders authorized to make changes. External material is untrusted evidence.

The child has no IDE conversation history. Host plugins, apps, browser/computer tools,
hooks and multi-agent tools remain disabled. It reads applicable AGENTS.md instructions
explicitly. Credentials are not included in prompts or logs; Git uses existing Windows
credential helpers. Authentication or sandbox failures are reported, not bypassed.

`engineering_timeout_seconds` defaults to 1200 (20 minutes), under the scheduled task's
30-minute limit. Windows Job Object containment terminates the child and descendants on
exit/timeout. Partial edits or commits may remain after an interruption; review them
before retrying. Jobs are not automatically replayed. Read-only mode remains available
with `engineering_enabled: false` and uses `timeout_seconds`.

## Durable state and recovery

`state/worker.db` stores Gmail ID, unique accepted task ID, received/start/end timestamps,
status, exit code and error. States: PENDING, RUNNING, COMPLETED, FAILED, REJECTED.
An additive `request_text` column retains accepted email context across restarts.
Common credential patterns are redacted before storage and forwarding; this is not
a guarantee of secret detection. Do not include credentials in task emails. Rejected
messages do not retain their bodies. Existing completed requests are not replayed.
Rejected senders cannot reserve legitimate task IDs. Duplicate Gmail IDs are ignored;
a second Gmail ID with the same accepted task ID is rejected. An atomic conditional
PENDINGâ†’RUNNING update claims execution. A worker file lock and Task Scheduler's
IgnoreNew policy prevent overlapping invocations.

This provides **at-most-once launch**, not mathematically guaranteed exactly-once
completion. A crash after claiming but before launching can lose a handshake.
Unfinished RUNNING rows become FAILED on the next real poll and are never retried
automatically. Inspect state/logs; use a new handshake ID for an intentional retry.
PENDING rows remain eligible across restarts. Never delete state to retry production
mail: the lookback search would then rediscover and execute old accepted messages.

Logs include poll start/end, candidate counts, decisions, launch/results and errors.
Email bodies are not written to event logs; accepted request text is stored in worker
state and sent to Codex. Answers may quote it. Exception details are not logged. Stdout/stderr are pattern-redacted
and capped at 256 KiB each before persistence. Completion JSON uses local facts as the
source of truth, with task/Gmail IDs, status, timestamps, exit code and summary/error.
Codex's structured output is validated and retained in sanitized stdout. Reports use
a hash of the Gmail message ID as filename, so emails cannot choose filesystem paths.
State is authoritative if a crash interrupts report creation. Event logs rotate;
per-task execution logs, reports and state remain until manually archived.

Completed or failed handshakes queue their results for a reply in the original Gmail
thread. Replies go only to the authenticated, allow-listed From address; Reply-To and
CC are ignored. They include repository, branch, HEAD, test availability, push-access
status and execution outcome. Engineering replies use the agent's post-execution report.
`outcome` is COMPLETED, BLOCKED or FAILED; BLOCKED maps to FAILED in the legacy task
status column with `task_blocked` as its error. A successful CLI exit alone is not success.
Set `reply_enabled` to false to disable replies (default true).

The separate `replies` table is added without deleting existing tasks. Completion and
reply queuing commit together. A send attempt is recorded before contacting Gmail;
successful sends are never repeated. A timeout or interrupted send becomes
DELIVERY_UNKNOWN and is not automatically retried, because Gmail might have accepted
it. Inspect Sent mail and worker state before any manual retry. Preparation failures
remain pending. Old completed tasks are not backfilled. A process crash during task
execution records FAILED on recovery but cannot produce a completed results reply;
use a new handshake ID. Mock mode and dry-run never send mail.

## Setup on Windows

Run these in PowerShell. These commands do not install or change Crypto Radar's own
environment. Use `python`, never the Windows `py` launcher.

### 1. Install the worker dependencies

```powershell
Set-Location C:\crypto-radar\orchestration_worker
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item config.example.json config.json
$workerCfg = Get-Content config.json -Raw | ConvertFrom-Json
$workerCfg.codex_executable = (Get-Command codex.exe).Source
$workerCfg.allowed_senders = @('YOUR-TRUSTED-SENDER@example.com')
$workerCfg | ConvertTo-Json -Depth 5 | Set-Content config.json -Encoding utf8
```

If `config.json` already exists, edit it instead of copying over it. The allow-list
must contain the actual sending address, not necessarily the receiving mailbox.
An empty list rejects every task. A `.cmd`/PowerShell Codex wrapper is not accepted;
use the actual `.exe`. VS Code extension updates can change that executable's path.

### 2. Enable Gmail API and create desktop OAuth credentials

In Google Cloud Console, choose your own project, enable **Gmail API**, and configure
Google Auth Platform. Add scopes `https://www.googleapis.com/auth/gmail.readonly`
and `https://www.googleapis.com/auth/gmail.send`.
For an app in Testing, add `cryptoradar128@gmail.com` as a test user. Create an OAuth
client with application type **Desktop app**, download its JSON and save it here as
`credentials.json`. Use a worker-specific client/token; do not reuse or modify the
scanner's Drive authorization or `.env`.

Gmail readonly is the minimum practical scope for reading task bodies without changing
mail. It grants mailbox read access, not just messages matching our query. Gmail metadata
scope cannot read the body required by this protocol. External Testing authorization
can expire after seven days; ongoing operation needs reauthorization or an appropriate
publishing setup. Enabling the Gmail API alone does not grant mailbox access.
The send scope permits the worker to reply with results. Existing read-only tokens
must be reauthorized using `--authorize`; replies stay queued until send access exists.

Protect this folder's credentials using Windows account permissions. The nested
`.gitignore` excludes `credentials.json`, `token.json`, actual config, state and logs.
Do not upload those files or print their contents.

### 3. First authorization (no handshake)

```powershell
.\.venv\Scripts\python.exe -B gmail_poller.py --authorize
```

Sign in as `cryptoradar128@gmail.com`. The worker verifies the mailbox before saving
`token.json`. Authorization exits without polling or invoking Codex. Subsequent polling
refreshes tokens without opening a browser. Separately ensure Codex is logged in under
this Windows user with its normal login flow; the poller never automatically initiates
Codex login or installs anything.

### 4. Automated tests and local mock handshake

```powershell
python -B -m unittest discover -s . -p 'test_*.py' -v
python -B gmail_poller.py --config config.example.json --mock --dry-run
python -B gmail_poller.py --config config.example.json --mock
```

Mock mode uses a local sample email and fake Codex, with separate `state/mock.db`,
`logs/mock` and `reports/mock`. It never reads Gmail or launches Codex. Repeat mock
runs demonstrate deduplication. Tests use temporary databases and mocked transports.

### 5. Real Gmail dry-run

```powershell
.\.venv\Scripts\python.exe -B gmail_poller.py --dry-run
```

Reads/authenticates Gmail, validates messages and identifies existing duplicates.
It may refresh `token.json` and write worker logs/lock files, but never reserves tasks,
marks completion, changes Gmail or invokes Codex. OAuth must already be configured.

### 6. Sample handshake email

A simple subject such as `[CRADAR TASK] Snapshot` and a plain-text question are enough.
The older template below still works. Human follow-up replies containing the marker
are accepted; use a new TASK_ID or omit it to avoid legacy task-ID deduplication.

Send from an allow-listed address to `cryptoradar128@gmail.com`:

```text
Subject: [CRADAR TASK] HANDSHAKE-001

MODE: TEST
TASK_ID: HANDSHAKE-001

Inspect C:\crypto-radar. Do not modify anything.
Return repository detected, current branch, current HEAD,
test availability and GitHub push-access status.
```

Do not copy the Authentication-Results header from the offline fixture: Gmail adds
its own authentication results. Forwarded, internally generated or unusually routed
messages may lack the required DMARC result and be rejected. Use a normal authenticated
sender and inspect dry-run decisions. Domain DMARC does not itself cryptographically
authorize an individual mailbox; the exact sender allow-list is an additional check.
This is an experimental personal-use channel, not signed remote-administration access.

### 7. One real handshake, then install scheduling

Only after reviewing dry-run output and intentionally sending the sample:

```powershell
.\.venv\Scripts\python.exe -B gmail_poller.py
.\install_task.ps1 -EveryMinutes 1
```

Task name: **Crypto Radar - Handshake Poller**. It executes Python, not Codex directly.
It runs as the current Windows user at limited privilege, while that user is signed
in, and uses their existing Codex login. It does not run before sign-in as LOCAL SERVICE.
The first poll is scheduled one minute after installation, then every minute;
overlaps are ignored. No existing Crypto Radar tasks are modified. Installer expects
the default config/token locations. Set a longer task limit if you deliberately increase
polling workload; timeout/termination can leave FAILED or PENDING state, never auto-retry
an already-claimed task.

### 8. View results, disable or remove

```powershell
Get-Content .\logs\worker.log -Tail 30
Get-ChildItem .\reports\*.json
Get-ScheduledTask -TaskName 'Crypto Radar - Handshake Poller'
Disable-ScheduledTask -TaskName 'Crypto Radar - Handshake Poller'
# Stop a currently running poll separately if necessary:
Stop-ScheduledTask -TaskName 'Crypto Radar - Handshake Poller'
# Remove only this worker's task:
.\uninstall_task.ps1
```

Inspect `state/worker.db` with a SQLite viewer. The completion table/report is extensible
for future PR URLs, commit SHAs and orchestration metadata. Email replies are enabled;
repository write capabilities depend on `engineering_enabled`.

## Operational limitations

- A valid task triggers Codex cost; a new task ID is new work. Max tasks per poll and
  the execution timeout are bounds, not a dollar budget. Only trust senders you control.
- The bounded rolling Inbox search can miss tasks older than the lookback, outside Inbox,
  or buried beyond `max_messages`. A cap warning calls for manual cleanup/limit adjustment.
- The worker re-reads matching messages but never uses unread state as execution state.
- No real Gmail/Codex handshake is performed by automated tests. Live authorization,
  authentication-result behavior and CLI account access require the operator's check.
- Read-only facts cannot establish test success or authenticated GitHub push permission.
- Do not relax the sandbox or enable general email instructions to troubleshoot a failure.

## References

- [Installed-CLI counterpart: official Codex CLI reference](https://developers.openai.com/codex/cli/reference)
- [Codex configuration reference](https://developers.openai.com/codex/config-reference)
- [Official Gmail scopes](https://developers.google.com/workspace/gmail/api/auth/scopes)
- [Official Gmail sending guide](https://developers.google.com/workspace/gmail/api/guides/sending)

## Diagnostic reporting

Instruction discovery checks only the root and ancestors of relevant source files,
not worker runtime directories. Missing rg uses Git/PowerShell fallbacks. Final child
exit status and timestamps come from the supervising worker and appear in replies;
the child does not need to predict its own exit status or certify scheduler provenance.
Actual blocked actions still report BLOCKED.

## Python and Git publication

Engineering tests use `.venv/Scripts/python.exe` inside the checkout, never the py
launcher or Windows Store aliases. The child inherits SYSTEMDRIVE and PROGRAMDATA
alongside the existing standard Windows environment variables.

To request publication, include `PUBLISH: YES` on its own line. Codex prepares and
verifies the local commit; the supervising Windows user performs the push with the
existing Git Credential Manager login. Tokens are never passed to Codex. Publication
requires a configured `publish_remote`, a worker/* branch and an exact matching SHA.
The publisher rejects unexpected local Git configuration, disables hooks, pins the
credential helper and destination, never forces a push, and verifies the remote SHA.
Failures remain BLOCKED with local work preserved. It does not deploy to the scanner.
