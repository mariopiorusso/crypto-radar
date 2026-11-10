# Snapshot ZIP volumes

Use the existing summary exporter and configured Drive folder, with a strict
**200,000,000-byte ceiling per ZIP** (200 MB, also below 200 GB). The CLI option
`--zip-volumes` produces at least two valid ZIP volumes even for a smaller source:

```powershell
.\.venv\Scripts\python.exe -B -m crypto_radar.weekly_report --drive --analysis --history-days 84 --zip-volumes
```

The analysis exporter still uses SQLite's backup API, a consistent read snapshot,
relationship closure, dictionary encoding and integrity checks. No source rows
are modified or dropped to make the archive smaller. ZIP-volume mode uses Deflate
for the original summary archive, avoiding the slower LZMA stage. Each outer ZIP
stores a chunk of that already-compressed archive plus a complete manifest,
README and `restore_snapshot.py`. Every volume can be opened independently, but
the original single SQLite database requires **all volumes**.

Download one complete volume set, extract each into the same directory, and run:

```powershell
python restore_snapshot.py
```

Identical manifests/scripts may be overwritten when extracting subsequent volumes.
The restore script validates every chunk and the entire archive's SHA-256 before
publishing the original snapshot ZIP; then extract that ZIP to obtain the database.
It refuses to overwrite an existing restored archive, preserves another process's
pending restoration, and removes its own incomplete output on errors.

Volume generation streams in 1 MB buffers and reserves space for ZIP headers and
metadata, so final ZIP sizes are strictly below the cap. Fixed timestamps make
retry artifacts byte-for-byte reproducible. Persisted upload state stores a reserved
Drive ID before each request; a lost successful response is recovered by checking
that ID. Verify size, MD5, MIME, filename, parent folder and trash status before
marking a volume uploaded. Changed source, destination, ceiling or saved volume
metadata is rejected. Existing single-file and binary-chunk states remain separate.

The email-triggered worker's fixed snapshot action now also uploads ZIP volumes.
It keeps its existing full SQLite/config/scoring snapshot contract; the weekly
`--analysis` option remains the compact research-summary export. Operation keys and
destination configuration remain fixed; mail content cannot supply arbitrary
commands, paths or upload destinations.

The reusable weekly installer adds `--zip-volumes` for Drive jobs and allows eight
hours instead of one. Its default Local Service mode requires administrator access.
Use `-CurrentUser` for the explicitly approved signed-in-user mode; that job only
runs while the user is signed in. The live task was changed to this mode after
specific user approval, retaining its Monday 18:00 schedule.
Existing scheduled `--drive --analysis` commands automatically use ZIP volumes and
Deflate through the updated worker, without changing the task's account or settings.
The installed task was read back to verify the account, eight-hour limit, arguments
and next run. No account changes are made by the archive implementation itself.

Local source archives, volumes and upload-state JSON are retained for recovery.
No existing Drive files are deleted. Explicit `--notify` retains the established
notification behavior; volume creation/upload by itself sends no email.

Offline tests cover strict size caps, valid ZIP CRCs, exact restoration, retry
determinism, missing/corrupt chunks, existing-output protection, lost upload replies,
destination changes, worker retries and weekly notification idempotency.
