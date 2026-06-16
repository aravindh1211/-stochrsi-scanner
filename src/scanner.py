"""
StochRSI Daily Scanner — GitHub Actions Edition (v2)
Replicates Pine Script:
    rsi1 = rsi(src, lengthRSI)
    k    = sma(stoch(rsi1, rsi1, rsi1, lengthStoch), smoothK)

Fix v2: switched from yf.download() to Ticker.history() for reliable
single-level column access across all yfinance versions.
"""

import os
import time
import logging
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from pycoingecko import CoinGeckoAPI
from datetime import datetime

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN  = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID    = os.environ.get("TELEGRAM_CHAT_ID", "")
STOCH_RSI_THRESHOLD = float(os.environ.get("STOCH_RSI_THRESHOLD", "10"))

# ── Watchlists — edit freely ───────────────────────────────────────────────────
# NSE stocks: always suffix with .NS
NSE_STOCKS = [
    "RELIANCE.NS", "TCS.NS",        "INFY.NS",       "HDFCBANK.NS",   "ICICIBANK.NS",
    "SBIN.NS",     "WIPRO.NS",      "AXISBANK.NS",   "KOTAKBANK.NS",  "LT.NS",
    "BAJFINANCE.NS","MARUTI.NS",    "TITAN.NS",      "SUNPHARMA.NS",  "ULTRACEMCO.NS",
    "NESTLEIND.NS", "ADANIENT.NS",  "ADANIPORTS.NS", "POWERGRID.NS",  "NTPC.NS",
    "HINDUNILVR.NS","ITC.NS",       "ASIANPAINT.NS", "BAJAJFINSV.NS", "TECHM.NS",
]

# US stocks: plain ticker
US_STOCKS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA",
    "META", "TSLA", "NFLX",  "AMD",  "INTC",
    "ORCL", "CRM",  "QCOM",  "AVGO", "TSM",
]

# Indices & commodities: Yahoo Finance codes
GLOBAL_INDICES = [
    "^NSEI",     # Nifty 50
    "^BSESN",    # BSE Sensex
    "^NSEBANK",  # Bank Nifty
    "^GSPC",     # S&P 500
    "^IXIC",     # NASDAQ
    "^DJI",      # Dow Jones
    "^FTSE",     # FTSE 100
    "^N225",     # Nikkei 225
    "^HSI",      # Hang Seng
    "GC=F",      # Gold Futures
    "CL=F",      # Crude Oil
    "DX-Y.NYB",  # US Dollar Index
]

# Crypto: CoinGecko coin IDs (lowercase)
CRYPTO_IDS = [
    "bitcoin",   "ethereum",  "binancecoin", "solana",
    "ripple",    "cardano",   "dogecoin",    "avalanche-2",
    "chainlink", "polkadot",
]

# ── Stoch RSI Core Logic ───────────────────────────────────────────────────────

def rma(series: pd.Series, length: int) -> pd.Series:
    """Wilder's RMA — seeds from SMA then applies exponential smoothing."""
    alpha  = 1.0 / length
    result = np.full(len(series), np.nan)
    if len(series) < length:
        return pd.Series(result, index=series.index)
    result[length - 1] = series.iloc[:length].mean()
    for i in range(length, len(series)):
        result[i] = alpha * series.iloc[i] + (1 - alpha) * result[i - 1]
    return pd.Series(result, index=series.index)


def calc_rsi(close: pd.Series, length: int = 14) -> pd.Series:
    """RSI using Wilder's RMA — matches Pine Script rsi()."""
    delta  = close.diff()
    up     = rma(delta.clip(lower=0), length)
    down   = rma((-delta).clip(lower=0), length)
    rsi    = np.where(down == 0, 100.0,
             np.where(up   == 0,   0.0,
                      100.0 - (100.0 / (1.0 + up / down))))
    return pd.Series(rsi, index=close.index)


def calc_stoch_rsi_k(
    close:        pd.Series,
    rsi_length:   int = 14,
    stoch_length: int = 14,
    smooth_k:     int = 3,
) -> float | None:
    """
    Returns latest Stoch RSI K (0–100) or None if data insufficient.
    Pine Script: k = sma(stoch(rsi(src,14), rsi(src,14), rsi(src,14), 14), 3)
    """
    min_bars = rsi_length + stoch_length + smooth_k + 5
    if len(close) < min_bars:
        log.warning(f"  Only {len(close)} bars, need {min_bars}")
        return None

    rsi_vals  = calc_rsi(close, rsi_length)
    hi        = rsi_vals.rolling(stoch_length).max()
    lo        = rsi_vals.rolling(stoch_length).min()
    denom     = (hi - lo).replace(0, np.nan)
    stoch_raw = ((rsi_vals - lo) / denom) * 100
    k_series  = stoch_raw.rolling(smooth_k).mean().dropna()

    if len(k_series) == 0:
        return None
    return round(float(k_series.values[-1]), 2)


# ── Data Fetchers ──────────────────────────────────────────────────────────────

def fetch_yfinance(tickers: list, label: str) -> dict:
    """
    Fetch daily OHLCV via Ticker.history() — single-level columns guaranteed.
    Returns {ticker: K_value} for all successfully fetched tickers.
    """
    results = {}
    log.info(f"── {label}: scanning {len(tickers)} tickers")

    for ticker in tickers:
        try:
            t  = yf.Ticker(ticker)
            df = t.history(period="120d", interval="1d", auto_adjust=True)

            if df is None or df.empty:
                log.warning(f"  ⚠ {ticker}: no data returned")
                continue

            if len(df) < 40:
                log.warning(f"  ⚠ {ticker}: only {len(df)} bars (need 40+)")
                continue

            # Ticker.history() always returns plain column names: Open High Low Close Volume
            close = df["Close"].squeeze().dropna()
            k     = calc_stoch_rsi_k(close)

            if k is None:
                log.warning(f"  ⚠ {ticker}: could not compute K")
                continue

            results[ticker] = k
            flag = " ← 🔔 TRIGGERED" if k <= STOCH_RSI_THRESHOLD else ""
            log.info(f"  {ticker}: K = {k}{flag}")

        except Exception as e:
            log.error(f"  ✗ {ticker}: {e}")

    log.info(f"  ✅ {label} done — {len(results)}/{len(tickers)} fetched")
    return results


def fetch_crypto() -> dict:
    """Fetch 90-day daily OHLC from CoinGecko. Returns {SYMBOL: K_value}."""
    cg      = CoinGeckoAPI()
    results = {}
    log.info(f"── Crypto: scanning {len(CRYPTO_IDS)} coins")

    for coin_id in CRYPTO_IDS:
        try:
            time.sleep(1.2)   # CoinGecko free tier: max 30 req/min
            ohlc = cg.get_coin_ohlc_by_id(id=coin_id, vs_currency="usd", days=90)

            if not ohlc or len(ohlc) < 40:
                log.warning(f"  ⚠ {coin_id}: insufficient data ({len(ohlc) if ohlc else 0} bars)")
                continue

            df_ohlc           = pd.DataFrame(ohlc, columns=["ts","open","high","low","close"])
            df_ohlc["ts"]     = pd.to_datetime(df_ohlc["ts"], unit="ms")
            df_ohlc           = df_ohlc.set_index("ts").sort_index()
            close             = df_ohlc["close"].dropna()

            k = calc_stoch_rsi_k(close)
            if k is None:
                log.warning(f"  ⚠ {coin_id}: could not compute K")
                continue

            symbol          = coin_id.upper().replace("-2", "").replace("-", "")
            results[symbol] = k
            flag            = " ← 🔔 TRIGGERED" if k <= STOCH_RSI_THRESHOLD else ""
            log.info(f"  {symbol}: K = {k}{flag}")

        except Exception as e:
            log.error(f"  ✗ {coin_id}: {e}")

    log.info(f"  ✅ Crypto done — {len(results)}/{len(CRYPTO_IDS)} fetched")
    return results


# ── Telegram ───────────────────────────────────────────────────────────────────

def send_telegram(message: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("❌ Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        return False
    url  = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        resp = requests.post(url, data=data, timeout=15)
        if resp.status_code == 200:
            log.info("✅ Telegram message sent")
            return True
        log.error(f"Telegram error {resp.status_code}: {resp.text}")
        return False
    except Exception as e:
        log.error(f"Telegram send failed: {e}")
        return False


def build_message(triggered: dict, total_scanned: int) -> str:
    now         = datetime.utcnow().strftime("%d %b %Y")
    crypto_syms = {c.upper().replace("-2","").replace("-","") for c in CRYPTO_IDS}

    sections = {
        "🇮🇳 NSE Stocks":    {},
        "🇺🇸 US Stocks":     {},
        "📈 Global Indices": {},
        "🪙 Crypto":         {},
    }
    for sym, k in triggered.items():
        if sym.endswith(".NS"):
            sections["🇮🇳 NSE Stocks"][sym] = k
        elif sym in crypto_syms:
            sections["🪙 Crypto"][sym] = k
        elif sym.startswith("^") or "=F" in sym or sym == "DX-Y.NYB":
            sections["📈 Global Indices"][sym] = k
        else:
            sections["🇺🇸 US Stocks"][sym] = k

    lines = [
        f"📊 <b>StochRSI Daily Scanner</b>  |  {now} (UTC)",
        f"🔔 Stoch RSI K ≤ <b>{int(STOCH_RSI_THRESHOLD)}</b>  "
        f"|  Scanned: <b>{total_scanned}</b> instruments",
        "",
    ]

    any_hit = False
    for section, items in sections.items():
        if not items:
            continue
        any_hit = True
        lines.append(f"<b>{section}</b>")
        for sym, k in sorted(items.items(), key=lambda x: x[1]):
            icon = "🟢" if k <= 5 else "🟡"
            lines.append(f"  {icon} {sym.replace('.NS','')}  →  K = <b>{k}</b>")
        lines.append("")

    if not any_hit:
        lines.append("✅ <b>No triggers today.</b>")
        lines.append("All instruments above threshold — no oversold setups found.")
        lines.append("")

    lines += [
        "─────────────────────────",
        "🟢 K ≤ 5  →  Deeply oversold",
        "🟡 K 5–10  →  Oversold zone",
        "💡 Use as an <i>accumulation signal</i>, not a standalone entry.",
    ]
    return "\n".join(lines)


# ── Entry Point ────────────────────────────────────────────────────────────────

def main():
    log.info("=" * 55)
    log.info("  StochRSI Daily Scanner v2 — Starting")
    log.info(f"  Threshold : K ≤ {STOCH_RSI_THRESHOLD}")
    log.info(f"  yfinance  : {yf.__version__}")
    log.info("=" * 55)

    all_k: dict = {}
    all_k.update(fetch_yfinance(NSE_STOCKS,     "NSE Stocks"))
    all_k.update(fetch_yfinance(US_STOCKS,      "US Stocks"))
    all_k.update(fetch_yfinance(GLOBAL_INDICES, "Global Indices"))
    all_k.update(fetch_crypto())

    triggered = {sym: k for sym, k in all_k.items() if k <= STOCH_RSI_THRESHOLD}

    log.info("=" * 55)
    log.info(f"  Total scanned : {len(all_k)}")
    log.info(f"  Triggered     : {len(triggered)}")
    log.info("=" * 55)

    message = build_message(triggered, total_scanned=len(all_k))
    send_telegram(message)


if __name__ == "__main__":
    main()
