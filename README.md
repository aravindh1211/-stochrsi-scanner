# 📊 StochRSI Weekly Scanner — GitHub Actions

Scans **NSE stocks, US stocks, Global Indices, and Crypto** every Friday at **8:00 PM IST**
(after both NSE and US markets have had a chance to close out the week).
Runs a **two-layer screen** — a valuation filter first, then a timing filter — and
sends a **Telegram alert** listing only what passes both. Runs 100% free on GitHub Actions.

---

## 🧠 How It Works (v7 — two-layer screen)

Every instrument is screened **in this order**: **Universe → Layer 1 (Valuation) →
Layer 2 (Timing)**. An instrument that fails Layer 1 never even reaches Layer 2 —
no wasted stochastic computation, and it simply doesn't appear in the output.

### Layer 1 — Valuation (`src/valuation.py`)

Reduces false positives from names that are "oversold" but still historically
expensive. An instrument is **cheap** when either:

- current trailing PE sits in the **bottom 30th percentile** of its own
  **5-year trailing-PE history** (reconstructed from quarterly EPS + weekly
  price), **OR**
- **PEG ratio < 1** — used for growth names (NVDA, META, ...) where PE alone is
  misleading, and as the fallback whenever a clean 5y PE history can't be
  reconstructed

**Indices and crypto are exempt** (`^`-prefixed and `-USD` tickers) — there's no
PE for a basket or a coin, so these always pass Layer 1 straight through.

**Data-availability reality check:** yfinance's free quarterly-financials data is
often thin — especially for NSE names — nowhere near a full 5 years in many
cases. Rather than silently dropping instruments we can't get clean history for,
the filter falls back to PEG, and if even that's unavailable, it **passes the
instrument through** (`method: "no_data_passthrough"`) rather than excluding it
by default. Every valuation decision — percentile, PEG, or passthrough — is
logged and shown alongside the hit in Telegram, so it's auditable, not hidden.

### Layer 2 — Timing (weekly Stoch RSI %K/%D crossover)

Only computed for instruments that passed Layer 1. Uses **slower settings**
than before — **(21, 5, 5)** instead of the old default (14, 3, 3) — specifically
to cut down whipsaw crossovers on the weekly timeframe:

| Setting | Old (v6) | New (v7) |
|---|---|---|
| RSI length | 14 | **21** |
| Stochastic length | 14 | **21** |
| %K smoothing | 3 | **5** |
| %D smoothing (new) | — | **5** |

**Trigger definition:** fires only on a **confirmed weekly close** —
```
%K crosses above %D   (prev_k <= prev_d  AND  curr_k > curr_d)
AND
both %K and %D are below STOCH_RSI_THRESHOLD (default 20)
```
A mere touch under the threshold with no crossover does **not** fire. K rising
while still below D does **not** fire either — the actual cross must happen on
this bar. The scanner also drops the most recent weekly bar if that week hasn't
finished yet (`drop_incomplete_last_bar()`), so "confirmed on weekly close"
really means closed, not a mid-week snapshot.

Replicates:
```
rsi1 = rsi(src, 21)
k    = sma(stoch(rsi1, rsi1, rsi1, 21), 5)
d    = sma(k, 5)
```

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
🔔 Weekly  |  %K/%D cross-up below 20, confirmed on weekly close
Universe: 141  |  Passed Layer 1 (valuation): 34  |  Layer 2 triggers: 2

💼 Portfolio Holdings
  🟢 Uno Minda (UNOMINDA.NS)  →  K: 14.2→18.6  D: 16.1→17.9  ↑
       PE 22.4 = 24th pct of own 260-wk history
  🟡 Booking Holdings (BKNG)  →  K: 12.1→19.3  D: 15.0→18.1  ↑
       PEG 0.87 (insufficient 5y PE history)

─────────────────────────
🟢 Current K ≤ 5  →  Turning up from deeply oversold
🟡 Current K 5–20  →  Turning up from oversold zone
💡 Layer 1 (cheap) + Layer 2 (confirmed cross) — higher conviction, still confirm before entry.
```

Note the funnel: **141** instruments in the universe, only **34** were "cheap"
enough to pass Layer 1, and of those only **2** also confirmed a weekly %K/%D
cross-up — that's the whole point of running valuation first.

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

## 🔧 Changing the Alert Threshold / Stoch RSI Settings

**Option A — Permanent change:**
Go to repo **Settings → Secrets and variables → Actions → Variables** and set any of:

| Variable | Default | Meaning |
|---|---|---|
| `STOCH_RSI_THRESHOLD` | `20` | Both %K and %D must be below this to trigger |
| `STOCH_RSI_LENGTH` | `21` | RSI lookback |
| `STOCH_LENGTH` | `21` | Stochastic lookback (applied to the RSI series) |
| `SMOOTH_K` | `5` | %K smoothing |
| `SMOOTH_D` | `5` | %D smoothing (= SMA of %K) |

**Option B — One-time manual run:**
Actions tab → Run workflow → enter a threshold in the input box before clicking Run

To adjust the **valuation filter** instead (Layer 1), edit the constants at the
top of `src/valuation.py` — `PE_PERCENTILE_CUTOFF` (default 30) and
`PEG_CUTOFF` (default 1.0).

---

## ❓ Troubleshooting

| Problem | Fix |
|---|---|
| No Telegram message received | Check token/chat ID in Secrets; confirm you clicked Start in the bot |
| Workflow not running automatically | Check Actions tab → re-enable if paused |
| Ticker showing "insufficient data" | Yahoo Finance may not support it — try removing it |
| `getUpdates` returns empty JSON | Send any message to your bot first, then retry |
| Almost nothing ever triggers | Expected — Layer 1 is a real filter, not a formality. Check the run log for `Layer 1 fail` lines to see what got screened and why |
| Many hits show `no_data_passthrough` | yfinance's free quarterly-financials data is thin for that ticker (common for NSE names) — it passed Layer 1 by default rather than being excluded; tighten `MIN_QUARTERS_FOR_PE_HISTORY` in `valuation.py` if you'd rather be stricter |
| Run takes much longer than before | Expected — Layer 1 adds 1-3 yfinance calls per non-exempt ticker. Workflow timeout is set to 45 min to accommodate this |
| "message is too long" in logs | Should no longer happen — results are chunked into multiple Telegram messages. If it still does, lower `TELEGRAM_CHAR_LIMIT` in the script |
