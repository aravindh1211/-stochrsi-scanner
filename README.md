# 📊 StochRSI Weekly Scanner — GitHub Actions

Scans **NSE stocks, US stocks, Global Indices, and Crypto** every Friday at **8:00 PM IST**
(after both NSE and US markets have had a chance to close out the week).
Sends a **Telegram alert** listing every instrument whose **weekly Stoch RSI K line was
below threshold on the prior closed bar and is now turning up** — not merely sitting
under the threshold, but actively curling upward out of an oversold reading.
Runs 100% free on GitHub Actions.

---

## 🧠 How It Works

Replicates your Pine Script logic exactly:

| Pine Script | Python |
|---|---|
| `rsi(src, 14)` | Wilder's RMA — `rma(gain/loss, 14)` |
| `stoch(rsi1, rsi1, rsi1, 14)` | Rolling min/max over RSI values |
| `sma(stoch, 3)` | 3-period rolling mean = K line |
| Alert when K turns up from oversold | Configurable via `STOCH_RSI_THRESHOLD` (default 20) |

**Trigger definition:** for each instrument, take the current closed weekly bar's K
(`curr_k`) and the prior closed weekly bar's K (`prev_k`). The instrument fires when:

```
prev_k < STOCH_RSI_THRESHOLD   AND   curr_k > prev_k
```

This intentionally fires as soon as K starts turning up while still under the
threshold — e.g. K going from 4 → 9 triggers just as much as K going from 18 → 23 —
rather than waiting only for the exact bar where it crosses above the threshold.
This means an instrument can trigger on consecutive weeks (once for each new week
it keeps rising while under threshold, or right as it crosses above it) — that's
by design, it reflects momentum building week over week.

---

## 🚀 Full Setup — Step by Step

---

### STEP 1 — Create a Telegram Bot (5 minutes)

1. Open Telegram on your phone or desktop
2. Search for **@BotFather** and open it
3. Send the message: `/newbot`
4. It will ask for a **name** — type anything, e.g. `StochRSI Alerts`
5. It will ask for a **username** — must end in `bot`, e.g. `stochrsi_aravindh_bot`
6. BotFather replies with your token — looks like:
   ```
   7123456789:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```
   → **Copy and save this. This is your `TELEGRAM_BOT_TOKEN`.**

7. Now get your **Chat ID**:
   - Click **Start** in your new bot's chat window (this activates it)
   - Open this URL in your browser (replace `YOUR_TOKEN`):
     ```
     https://api.telegram.org/botYOUR_TOKEN/getUpdates
     ```
   - Look for `"chat":{"id":XXXXXXXXX}` in the response
   - That number is your **`TELEGRAM_CHAT_ID`** (can be negative for groups)

---

### STEP 2 — Create a GitHub Repository

1. Go to [github.com](https://github.com) — sign up free if needed
2. Click **New repository** (top right `+` → New repository)
3. Settings:
   - **Repository name:** `stochrsi-scanner`
   - **Visibility:** Private ✅
   - Leave everything else default
4. Click **Create repository**

---

### STEP 3 — Upload the Files

Upload these files **exactly** in this folder structure:

```
stochrsi-scanner/               ← root of your repo
├── .github/
│   └── workflows/
│       └── scanner.yml         ← GitHub Actions trigger
├── src/
│   └── scanner.py              ← main scanner logic
└── requirements.txt            ← Python dependencies
```

**To upload:**
1. On your new repo page, click **uploading an existing file**
2. First create the folders by uploading `scanner.yml` — GitHub will ask you for the path,
   type `.github/workflows/scanner.yml` in the path field
3. Then upload `src/scanner.py` with path `src/scanner.py`
4. Then upload `requirements.txt` at the root

> **Easier alternative:** Use [GitHub Desktop](https://desktop.github.com/) — clone the repo,
> copy files into the folder, commit and push.

---

### STEP 4 — Add Telegram Secrets

Your bot token and chat ID must never be hardcoded. GitHub Secrets keeps them safe.

1. In your repo, go to **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository secret** and add these two:

   | Secret Name | Value |
   |---|---|
   | `TELEGRAM_BOT_TOKEN` | The token from BotFather (Step 1) |
   | `TELEGRAM_CHAT_ID` | Your chat ID number (Step 1) |

3. Optionally add a **variable** (not secret) for threshold:
   - Go to **Variables** tab → **New repository variable**
   - Name: `STOCH_RSI_THRESHOLD` → Value: `10`
   - (If you skip this, it defaults to 10 automatically)

---

### STEP 5 — Test It Right Now (Manual Trigger)

Don't wait until tomorrow morning — trigger it immediately:

1. In your repo, click the **Actions** tab
2. Click **StochRSI Daily Scanner** in the left sidebar
3. Click **Run workflow** → **Run workflow** (green button)
4. Watch it run — click the job to see live logs
5. Check your Telegram — message arrives within ~2 minutes ✅

---

### STEP 6 — Automatic Daily Schedule

The workflow already has the schedule set:
```yaml
- cron: "30 3 * * *"   # = 9:00 AM IST every day
```

No action needed — once the file is in your repo, GitHub runs it automatically every morning.

> ⚠️ **Note:** GitHub may pause scheduled workflows on repos with no activity for 60 days.
> To prevent this: occasionally open the repo or push a small change.
> You can also re-enable it from the Actions tab if it gets paused.

---

## 📬 Sample Telegram Message

```
📊 StochRSI Weekly Scanner  |  16 Jun 2026 (UTC)
🔔 Weekly  |  Trigger: K turning up from below 20  |  Scanned: 141 instruments

🇮🇳 Indian Indices
  🟢 Nifty PSU Bank (^CNXPSUBANK)  →  K:  8.1 → 14.3  ↑

💼 Portfolio Holdings
  🟢 Uno Minda (UNOMINDA.NS)  →  K:  3.8 → 9.1  ↑
  🟡 Booking Holdings (BKNG)  →  K:  17.2 → 21.6  ↑

🪙 Crypto
  🟡 Ethereum (ETH)  →  K:  12.5 → 18.9  ↑

─────────────────────────
🟢 Current K ≤ 5  →  Turning up from deeply oversold
🟡 Current K 5–20+  →  Turning up from oversold zone
💡 Weekly signals = higher conviction. Confirm before entry.
```

---

## ✏️ Customising Your Watchlist

The portfolio holdings tracked every week (indices are separate, hardcoded in
`src/scanner.py` since they rarely change) live in **`holdings.json`** at the repo
root — not hardcoded in the script. Two ways to edit it:

**A — Actions tab (no code editor needed):**
Go to **Actions → Update Holdings → Run workflow**, paste comma-separated tickers
into the category you want to change, and run. Prefix a ticker with `-` to remove
it (e.g. `-IRFC.NS`). Leave a field blank to leave it untouched.

**B — Edit `holdings.json` directly:**
```json
{
  "nse_stocks": ["RELIANCE.NS", "TCS.NS", ...],
  "us_stocks":  ["AAPL", "NVDA", ...],
  "crypto":     ["BTC-USD", "ETH-USD", ...]
}
```
NSE tickers must end in `.NS`, US tickers are used as-is, crypto tickers use the
**Yahoo Finance "-USD" suffix** (e.g. `BTC-USD`, `SOL-USD`) — same source as
everything else, no separate API or rate limit to worry about.

The wider Nifty 100 / Nasdaq 100 / top-10-crypto universe used by the *monthly*
non-portfolio track is still hardcoded in `src/scanner.py` (`NSE_STOCKS_ALL`,
`NASDAQ_100_ALL`, `CRYPTO_TICKERS_ALL`) — anything you add to your holdings is
automatically excluded from that universe so it isn't double-counted.

Indices tracked every week are also in `src/scanner.py` (`INDIAN_INDICES`,
`WORLD_INDICES`, `US_INDICES`) since they change far less often than holdings.

**Useful index codes:**
| Index | Code |
|---|---|
| Nifty 50 | `^NSEI` |
| Bank Nifty | `^NSEBANK` |
| BSE Sensex | `^BSESN` |
| S&P 500 | `^GSPC` |
| NASDAQ | `^IXIC` |
| Gold Futures | `GC=F` |
| Crude Oil | `CL=F` |

---

## 🕰️ Retrospective Backfill

If you're only starting the scanner now, you can still see what WOULD have
triggered on each of the last few Fridays — a one-off lookback across all
your indices, holdings, and crypto.

1. Go to **Actions → One-Off Retrospective Backfill → Run workflow**
2. Enter how many past weeks to check (default `4`)
3. Run it — you'll get **one consolidated Telegram message**, grouped by
   week, covering every trigger that would have fired

This replays the same "K was below threshold last bar, rising this bar"
condition bar-by-bar using only the data available at each historical bar
(no lookahead). It's read-only — it doesn't touch `state/monthly_state.json`
or `state/weekly_log.json`, so it won't interfere with the regular weekly
run or the monthly digest. Safe to run as many times as you like, or delete
`.github/workflows/backfill_once.yml` afterwards if you don't need it again.

---

## ⏰ Changing the Schedule

Edit `.github/workflows/scanner.yml`:
```yaml
schedule:
  - cron: "30 3 * * *"    # Current: 9:00 AM IST daily
```

Cron format: `minute  hour  day  month  weekday`

| Schedule | Cron |
|---|---|
| 7:30 AM IST daily | `0 2 * * *` |
| 9:00 AM IST, weekdays only | `30 3 * * 1-5` |
| 9:00 AM IST + 3:30 PM IST | `30 3,10 * * *` |

---

## 🆓 GitHub Actions Free Tier

| Limit | Free Allowance | Your Usage |
|---|---|---|
| Minutes/month | 2,000 min | ~5 min/day = ~150 min/month |
| Storage | 500 MB | Negligible |
| Concurrent jobs | 20 | 1 |

**You use less than 10% of the free quota.** No billing ever needed for this use case.

---

## 🔧 Changing the Alert Threshold

**Option A — Permanent change:**
Go to repo **Settings → Secrets and variables → Actions → Variables**
Update `STOCH_RSI_THRESHOLD` to any value (e.g. `15` or `20`)

**Option B — One-time manual run:**
Actions tab → Run workflow → enter a threshold in the input box before clicking Run

---

## ❓ Troubleshooting

| Problem | Fix |
|---|---|
| No Telegram message received | Check token/chat ID in Secrets; confirm you clicked Start in the bot |
| Workflow not running automatically | Check Actions tab → re-enable if paused |
| Ticker showing "insufficient data" | Yahoo Finance may not support it — try removing it |
| `getUpdates` returns empty JSON | Send any message to your bot first, then retry |
