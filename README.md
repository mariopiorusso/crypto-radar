# Crypto Radar V1.1

Experimental early-warning research engine for crypto market anomalies.

## What V1 does

- Polls CoinGecko market data for liquid coins.
- Stores observations in local SQLite.
- Builds rolling per-coin baselines from your own history.
- Detects unusual volume/price behaviour.
- Fetches recent Google News RSS results only for shortlisted candidates.
- Sends shortlisted evidence to OpenAI for structured interpretation.
- Stores AI assessments and later price outcomes.
- Can send Telegram alerts for high scores.
- Does **not** trade automatically.

V1 deliberately uses public/API/RSS sources rather than bypassing social-media anti-bot controls.
Reddit/X and labelled whale intelligence are Phase 2 collectors.

## Windows 11 quick start

### 1. Install prerequisites

Install Python 3.14 from python.org and Git, or keep the existing tested virtual environment.

Open PowerShell in the cloned repo:

```powershell
py -3.14 -m venv .venv
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-lock.txt
```

### 2. Configure secrets

```powershell
Copy-Item .env.example .env
notepad .env
```

At minimum set:

```text
OPENAI_API_KEY=...
```

`OPENAI_MODEL` is configurable. If the configured model is not enabled for your API account,
replace it with a model shown in your OpenAI API account.

CoinGecko can run keyless for prototyping. If you have a Demo key, add it as
`COINGECKO_API_KEY`.

### 3. Test one scan

```powershell
python -m crypto_radar.main --once
```

### 4. Run continuously

```powershell
python -m crypto_radar.main
```

Default interval is five minutes. Stop with Ctrl+C.

### 5. Git

The `.gitignore` excludes `.env`, SQLite data and logs:

```powershell
git add .
git commit -m "Initial Crypto Radar V1"
git push
```

## Optional Telegram alerts

Create a Telegram bot with BotFather, obtain its bot token and your chat ID, then set:

```text
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

If these are blank, alerts are printed to the console only.

## Run automatically after Windows reboot

First verify the continuous process works. Then open **Task Scheduler** and create a task:

- Trigger: At startup
- Run whether user is logged on or not
- Program: `C:\path\to\repo\.venv\Scripts\python.exe`
- Arguments: `-m crypto_radar.main`
- Start in: `C:\path\to\repo`
- Restart on failure

Using Task Scheduler avoids relying on an open PowerShell window.

## How scoring works

The local detector is intentionally simple in V1:

- volume / market-cap anomaly
- deviation from the coin's own stored volume baseline
- rolling 24h volume level relative to recent observations (not interval trading volume)
- price movement penalty if the coin has apparently already pumped
- liquidity/market-cap safety filters

Only candidates above `ai.min_pre_score` are investigated with AI.

Stablecoins are excluded before scoring. The original scoring weights remain unchanged;
an asset can still reach the default 45-point threshold without sufficient history.
The score is experimental and is not a calibrated probability.

The AI receives numeric evidence plus recent news headlines and must return JSON containing:

- surge_score (0-100)
- stage
- catalyst_strength
- manipulation_risk
- confidence
- thesis
- risks

This is an experimental signal, not a prediction or investment recommendation.

## Database

`data/crypto_radar.db` contains:

- `market_observations`
- `assessments`
- `outcomes`

Outcome prices at +1h/+3h/+6h/+12h/+24h are filled opportunistically on later scans.

## V1.1 configuration and operations

The default AI budget is **50 attempts per UTC day**, with at most 8 per scan.
Configure `ai.daily_call_budget` and `ai.max_candidates_per_scan` in `config.yaml`.
Zero disables calls at that budget boundary. Failed/interrupted attempts consume budget;
SDK automatic retries are disabled. The budget is a call limit, not a dollar limit.
Output is capped at `ai.max_output_tokens`; input dossiers include baseline evidence.

Candidate cooldown is 60 minutes. During cooldown a preliminary-score increase of
at least 10 points over the latest attempt permits re-analysis. Both values are configurable.
Every fetched coin gets a `candidate_evaluations` row, including rejected, excluded,
disabled-AI, budget-limited and cooldown-suppressed decisions. No AI-generated surrogate
score is inserted when AI is disabled. These evaluations and market history support later
control-group analysis; this release does not implement a statistical research report.

Stablecoins are maintained centrally in `crypto_radar/filters.py` by CoinGecko ID.
Set `filters.stablecoin_ids` to replace the default list. Add newly issued stablecoins
as needed; this is a maintained list, not automatic classification. Their observations
are retained. Currency must remain USD because the liquidity thresholds are in USD.

Each AI attempt stores its exact request, supplied market/history/news dossier,
configuration hash, scoring/prompt versions, requested and returned model, raw response,
and usage when available. Strict validation rejects invalid output. Reproduction means
reconstructing inputs and decisions, not guaranteeing identical model output.
Secrets and exception bodies are not logged or stored.

### Independent email and Telegram alerts

Enable channels independently using `alerts.email_enabled` and `alerts.telegram_enabled`.
Telegram credentials retain their existing environment names. For email, add to `.env`:

```text
SMTP_HOST=smtp.your-provider.example
SMTP_PORT=587
SMTP_SECURITY=starttls
SMTP_USERNAME=your-smtp-user
SMTP_PASSWORD=your-smtp-password-or-app-password
EMAIL_FROM=radar@your-domain.example
EMAIL_TO=you@your-domain.example
```

Use `SMTP_SECURITY=ssl` and port 465 for implicit TLS if required by your provider.
Remote SMTP requires encryption. Username/password are optional for providers that
permit authenticated network relays without SMTP login. `EMAIL_TO` accepts comma-separated
recipients. Credentials belong only in the environment or untracked `.env`.

For a local relay use `SMTP_HOST=127.0.0.1`, `SMTP_PORT=1025` and
`SMTP_SECURITY=local`, leaving `SMTP_USERNAME` and `SMTP_PASSWORD` blank.
Unencrypted submission is accepted only for literal loopback IP addresses.
The relay needs its own outbound delivery route; localhost acceptance does not
mean the recipient's provider has delivered the message to an inbox.

#### Local Gmail relay

The local relay runs as a separate Python process using `aiosmtpd`. It binds only
to `127.0.0.1`, accepts only the configured sender/recipients, and forwards to
`smtp.gmail.com:587` over verified TLS. Gmail credentials are loaded from `.env`;
they are never placed in command-line arguments or another credentials file.
The previously downloaded E-MailRelay package is unused.

Configure `GMAIL_USERNAME` with the sending Gmail address, `GMAIL_APP_PASSWORD`
with a Google app password, and `EMAIL_FROM` with the same sending address.
Keep `SMTP_HOST=127.0.0.1`, `SMTP_PORT=1025`, `SMTP_SECURITY=local`, and both
`SMTP_USERNAME` and `SMTP_PASSWORD` blank. `EMAIL_TO` can be changed independently.
Enable `alerts.email_enabled` in `config.yaml` when ready to use email.

Create an app password through your Google account with 2-Step Verification enabled:
https://support.google.com/accounts/answer/185833 . Do not use your normal Gmail password.
After saving `.env`, run `./start_mail_relay.ps1` to start the relay in the background.
It validates settings before starting; logs are in `logs/mail-relay.log`.
Restart the relay after changing its sender, password, port or recipient list.
The launcher does not start the scanner or send a test email, and no auto-start task
is installed. You can stop the relay using its reported process ID.

The relay acknowledges a message only after Gmail accepts it. It has no disk queue
and does not retry ambiguous deliveries; errors propagate to the scanner's persisted
alert-delivery state. Gmail acceptance does not guarantee inbox placement.

Alert history is persisted **per channel**. The first qualifying assessment alerts;
another alert within the episode requires a 10-point Surge Score increase above the
highest prior alert attempt that may have been delivered. An episode resets after
24 hours of observed below-threshold scans. Gaps greater than twice the scan interval
reset the below-threshold timer, so downtime never establishes a reset.

Delivery errors do not stop the other channel or the scan. Uncertain/partial deliveries
are recorded as `delivery_unknown` and are not blindly retried, to avoid duplicates.
When neither channel is configured, alerts fall back to the console with the same
deduplication. Enabling email without required connection/address settings logs an error.

### Outcome semantics

New outcomes are anchored to the stored starting-price observation timestamp, with
assessment completion time recorded separately. The earliest stored valid price at or
after the target is used. `timing_error_seconds` records lateness; `within_tolerance`
means at most `outcomes.tolerance_seconds` (default 600), not an exact-time price.
Unavailable measurements become `missing`; later recovery becomes `late`.
Late returns remain stored but should be excluded from standard horizon comparisons.
Provider update timestamps are retained separately from local observation time.

For a clean V1.1 cohort, filter `measurement_version='1.1'` and
`timing_status='within_tolerance'`. Legacy records retain their old target timestamps
and measurement label; their missing original dossiers cannot be reconstructed.
Coins leaving the fetched universe can have missing outcomes. Repeated assessments
are correlated observations, so later statistical analysis must account for episodes.

### Migration, logging and tests

Before upgrading a running installation, stop the old scanner. The new scanner uses
an OS-backed single-instance lock per database; the old V1 did not honor this lock.
The migration first makes a SQLite backup named
`data/crypto_radar.db.before-v1.1-<UTC timestamp>.bak`, then adds schema changes in one
transaction. Existing observations, assessments and outcomes are retained unchanged.
Migrations are versioned and repeatable. Do not run old V1 against the upgraded database.

Migration only (no collection, AI, or alert delivery):

```powershell
.\.venv\Scripts\python.exe -m crypto_radar.main --migrate-only
```

Run one manual scan from the repository root:

```powershell
.\.venv\Scripts\python.exe -m crypto_radar.main --once
```

This manual scan uses live providers and can send alerts if configured and eligible.
The wrapper scripts resolve their working directory and no longer install dependencies
on every invocation. Install dependencies explicitly before first use.

Each scan logs fetched/excluded coins, candidates, cooldown suppressions, AI calls,
alerts, errors, and duration. Summaries are persisted in `scan_runs`; rotating logs
default to three 5 MB backups. Interrupted AI attempts keep their budget reservation.

Offline tests (temporary databases, mocked AI/SMTP/Telegram, no real messages):

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

The dependency lock records the tested Windows/Python 3.14.7 environment. Python 3.12
has not been tested for this release. Keep the existing interpreter unless a verified
compatibility issue requires a change.

## Phase 2

After V1 has run reliably:

1. Add exchange-native data (Binance/Coinbase/Kraken where appropriate).
2. Add compliant Reddit/community APIs.
3. Add labelled on-chain/whale provider.
4. Add social velocity source.
5. Backtest thresholds and calibrate the score.
6. Add dashboard.
