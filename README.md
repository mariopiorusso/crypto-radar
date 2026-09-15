# Crypto Radar V1

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

Install Python 3.12+ from python.org and Git.

Open PowerShell in the cloned repo:

```powershell
py -3.12 -m venv .venv
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
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
- 24h volume change relative to recent observations
- price movement penalty if the coin has apparently already pumped
- liquidity/market-cap safety filters

Only candidates above `ai.min_pre_score` are investigated with AI.

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

## Phase 2

After V1 has run reliably:

1. Add exchange-native data (Binance/Coinbase/Kraken where appropriate).
2. Add compliant Reddit/community APIs.
3. Add labelled on-chain/whale provider.
4. Add social velocity source.
5. Backtest thresholds and calibrate the score.
6. Add dashboard.
