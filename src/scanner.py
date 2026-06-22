"""
StochRSI Weekly Scanner — GitHub Actions Edition (v4)
Timeframe : Weekly (1wk candles)
Schedule  : Every Friday 8:00 PM IST (14:30 UTC) + any manual run

Replicates Pine Script:
    rsi1 = rsi(src, lengthRSI)
    k    = sma(stoch(rsi1, rsi1, rsi1, lengthStoch), smoothK)

Data sources:
  yfinance   — equities + indices (interval='1wk', period='2y')
  CoinGecko  — crypto (days=365 → auto weekly granularity)
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

# Weekly fetch settings
YF_INTERVAL = "1wk"
YF_PERIOD   = "2y"    # 2 years of weekly bars = ~104 candles, well above minimum 36


# ══════════════════════════════════════════════════════════════════════════════
# WATCHLISTS
# ══════════════════════════════════════════════════════════════════════════════

# ── Indian Indices ─────────────────────────────────────────────────────────────
INDIAN_INDICES = [
    "^NSEI",          # Nifty 50
    "^NSEBANK",       # Bank Nifty
    "^CNXSC",         # Nifty SmallCap 250
    "^NSEMDCP50",     # Nifty MidCap 150
    "^CNXINFRA",      # Nifty Infrastructure
    "^CNXIT",         # Nifty IT
    "^CNXPHARMA",     # Nifty Pharma
    "^CNXAUTO",       # Nifty Auto
    "^CNXPSUBANK",    # Nifty PSU Bank
    "^CNXFMCG",       # Nifty FMCG
    "^CNXENERGY",     # Nifty Energy
    "^CNXHEALTH",     # Nifty Healthcare
    "^BSESN",         # BSE Sensex
    "GC=F",           # Gold (USD/oz)
    "SI=F",           # Silver (USD/oz)
    "GOLDBEES.NS",    # Gold/INR proxy (Nippon Gold BeES ETF)
    "INFRABEES.NS",   # Nifty 500 Multicap Infra proxy (Nippon Infra BeES ETF)
]

# ── Major World Indices ────────────────────────────────────────────────────────
WORLD_INDICES = [
    "^GSPC",          # S&P 500
    "^NDX",           # Nasdaq 100
    "^DJI",           # Dow Jones Industrial
    "^FTSE",          # FTSE 100 (UK)
    "^GDAXI",         # DAX 40 (Germany)
    "^FCHI",          # CAC 40 (France)
    "^N225",          # Nikkei 225 (Japan)
    "^HSI",           # Hang Seng (HK)
    "000001.SS",      # Shanghai Composite
    "^KS11",          # KOSPI (South Korea)
    "^AXJO",          # ASX 200 (Australia)
    "^STI",           # Straits Times (Singapore)
    "^TWII",          # Taiwan Weighted
    "^MXX",           # IPC Mexico
    "^BVSP",          # Bovespa (Brazil)
    "^AEX",           # AEX (Netherlands)
    "^SSMI",          # SMI (Switzerland)
    "FTSEMIB.MI",     # FTSE MIB (Italy)
    "^IBEX",          # IBEX 35 (Spain)
]

# ── US Indices ─────────────────────────────────────────────────────────────────
US_INDICES = [
    "^RUT",           # Russell 2000
    "^RUI",           # Russell 1000
    "^RUA",           # Russell 3000
    "^MID",           # S&P MidCap 400
    "^SML",           # S&P SmallCap 600
    "^IXIC",          # Nasdaq Composite
    "^NYA",           # NYSE Composite
    "^XAX",           # NYSE American Composite
    "^DJT",           # Dow Jones Transport
    "^DJU",           # Dow Jones Utilities
    "^VIX",           # CBOE Volatility Index
    "^W5000",         # Wilshire 5000
    "^OEX",           # S&P 100
    "^XND",           # Nasdaq 100 Equal Weight
    "^SOX",           # Philadelphia Semiconductor
]

# ── Nifty 100 Stocks (NSE) ─────────────────────────────────────────────────────
NSE_STOCKS = [
    "RELIANCE.NS",    "TCS.NS",         "HDFCBANK.NS",    "BHARTIARTL.NS",
    "ICICIBANK.NS",   "INFOSYS.NS",     "SBIN.NS",        "HINDUNILVR.NS",
    "ITC.NS",         "LT.NS",          "BAJFINANCE.NS",  "HCLTECH.NS",
    "MARUTI.NS",      "SUNPHARMA.NS",   "KOTAKBANK.NS",   "AXISBANK.NS",
    "TITAN.NS",       "ASIANPAINT.NS",  "NESTLEIND.NS",   "WIPRO.NS",
    "ULTRACEMCO.NS",  "POWERGRID.NS",   "NTPC.NS",        "BAJAJFINSV.NS",
    "TECHM.NS",       "M&M.NS",         "TATAMOTORS.NS",  "ADANIENT.NS",
    "ADANIPORTS.NS",  "JSWSTEEL.NS",    "TATASTEEL.NS",   "ONGC.NS",
    "COALINDIA.NS",   "HINDALCO.NS",    "GRASIM.NS",      "CIPLA.NS",
    "DRREDDY.NS",     "DIVISLAB.NS",    "EICHERMOT.NS",   "BRITANNIA.NS",
    "HDFCLIFE.NS",    "SBILIFE.NS",     "ICICIPRULI.NS",  "ICICIGI.NS",
    "HEROMOTOCO.NS",  "BPCL.NS",        "IOC.NS",         "TATACONSUM.NS",
    "APOLLOHOSP.NS",  "BAJAJ-AUTO.NS",  "VEDL.NS",        "INDUSINDBK.NS",
    "SHRIRAMFIN.NS",  "ZOMATO.NS",      "PAYTM.NS",       "NYKAA.NS",
    "POLICYBZR.NS",   "DMART.NS",       "SIEMENS.NS",     "ABB.NS",
    "HAVELLS.NS",     "PIDILITIND.NS",  "BOSCHLTD.NS",    "MUTHOOTFIN.NS",
    "CHOLAFIN.NS",    "PFC.NS",         "RECLTD.NS",      "IRCTC.NS",
    "IRFC.NS",        "HAL.NS",         "BEL.NS",         "BHEL.NS",
    "GAIL.NS",        "TRENT.NS",       "DABUR.NS",       "GODREJCP.NS",
    "MARICO.NS",      "COLPAL.NS",      "BERGEPAINT.NS",  "MPHASIS.NS",
    "LTIM.NS",        "PERSISTENT.NS",  "COFORGE.NS",     "OBEROIRLTY.NS",
    "DLF.NS",         "LODHA.NS",       "ADANIGREEN.NS",  "ADANIPOWER.NS",
    "TATAPOWER.NS",   "NHPC.NS",        "SJVN.NS",        "ZYDUSLIFE.NS",
    "TORNTPHARM.NS",  "LUPIN.NS",       "AUROPHARMA.NS",  "MANKIND.NS",
]

# ── Nasdaq 100 Stocks (US) ─────────────────────────────────────────────────────
NASDAQ_100 = [
    "AAPL",  "MSFT",  "NVDA",  "AMZN",  "META",  "GOOGL", "GOOG",  "TSLA",
    "AVGO",  "COST",  "NFLX",  "ASML",  "AMD",   "PEP",   "QCOM",  "AMAT",
    "CSCO",  "TXN",   "INTU",  "AMGN",  "BKNG",  "MU",    "ISRG",  "HON",
    "LRCX",  "CMCSA", "PANW",  "ADP",   "VRTX",  "SBUX",  "MELI",  "KLAC",
    "REGN",  "CDNS",  "SNPS",  "MAR",   "MDLZ",  "ORLY",  "CSX",   "ABNB",
    "MNST",  "PYPL",  "FTNT",  "MRVL",  "ADSK",  "PCAR",  "WDAY",  "BIIB",
    "CTAS",  "DXCM",  "EXC",   "FAST",  "GEHC",  "GILD",  "IDXX",  "ILMN",
    "KDP",   "KHC",   "MCHP",  "MRNA",  "ODFL",  "ON",    "PAYX",  "ROP",
    "ROST",  "TEAM",  "TTD",   "TTWO",  "VRSK",  "WBD",   "ZS",    "CRWD",
    "ENPH",  "FANG",  "LULU",  "CEG",   "DDOG",  "GFS",   "SMCI",  "ARM",
    "DASH",  "CDW",   "FSLR",  "NXPI",  "ZM",    "ALGN",  "DLTR",  "EBAY",
    "INTC",  "RIVN",  "LCID",
]

# ── Crypto (CoinGecko IDs) ─────────────────────────────────────────────────────
CRYPTO_IDS = [
    "bitcoin",    "ethereum",    "binancecoin",  "solana",
    "ripple",     "cardano",     "dogecoin",     "avalanche-2",
    "chainlink",  "polkadot",
]

# ── Display Labels ─────────────────────────────────────────────────────────────
TICKER_LABELS = {
    "^NSEI":       "Nifty 50",          "^NSEBANK":    "Bank Nifty",
    "^CNXSC":      "Nifty SmallCap 250","^NSEMDCP50":  "Nifty MidCap 150",
    "^CNXINFRA":   "Nifty Infra",       "^CNXIT":      "Nifty IT",
    "^CNXPHARMA":  "Nifty Pharma",      "^CNXAUTO":    "Nifty Auto",
    "^CNXPSUBANK": "Nifty PSU Bank",    "^CNXFMCG":    "Nifty FMCG",
    "^CNXENERGY":  "Nifty Energy",      "^CNXHEALTH":  "Nifty Healthcare",
    "^BSESN":      "BSE Sensex",        "GC=F":        "Gold (USD/oz)",
    "SI=F":        "Silver (USD/oz)",   "GOLDBEES.NS": "Gold/INR (GoldBees)",
    "INFRABEES.NS":"Infra (InfraBees)",
    "^GSPC":       "S&P 500",           "^NDX":        "Nasdaq 100",
    "^DJI":        "Dow Jones",         "^FTSE":       "FTSE 100",
    "^GDAXI":      "DAX 40",            "^FCHI":       "CAC 40",
    "^N225":       "Nikkei 225",        "^HSI":        "Hang Seng",
    "000001.SS":   "Shanghai Composite","^KS11":       "KOSPI",
    "^AXJO":       "ASX 200",           "^STI":        "Straits Times",
    "^TWII":       "Taiwan Weighted",   "^MXX":        "IPC Mexico",
    "^BVSP":       "Bovespa",           "^AEX":        "AEX",
    "^SSMI":       "SMI",               "FTSEMIB.MI":  "FTSE MIB",
    "^IBEX":       "IBEX 35",
    "^RUT":        "Russell 2000",      "^RUI":        "Russell 1000",
    "^RUA":        "Russell 3000",      "^MID":        "S&P MidCap 400",
    "^SML":        "S&P SmallCap 600",  "^IXIC":       "Nasdaq Composite",
    "^NYA":        "NYSE Composite",    "^XAX":        "NYSE American",
    "^DJT":        "DJ Transport",      "^DJU":        "DJ Utilities",
    "^VIX":        "VIX",               "^W5000":      "Wilshire 5000",
    "^OEX":        "S&P 100",           "^XND":        "Nasdaq 100 EW",
    "^SOX":        "Philadelphia Semi",
}


# ══════════════════════════════════════════════════════════════════════════════
# STOCH RSI LOGIC  (Pine Script exact replication)
# ══════════════════════════════════════════════════════════════════════════════

def rma(series: pd.Series, length: int) -> pd.Series:
    """Wilder's RMA — seeds from SMA of first `length` bars."""
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
    delta = close.diff()
    up    = rma(delta.clip(lower=0), length)
    down  = rma((-delta).clip(lower=0), length)
    rsi   = np.where(down == 0, 100.0,
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
    Returns latest Stoch RSI K (0–100) or None if insufficient bars.
    Pine Script: k = sma(stoch(rsi(src,14), rsi(src,14), rsi(src,14), 14), 3)
    Minimum bars needed: 14 + 14 + 3 + 5 = 36 weekly candles (~9 months).
    With period='2y' (~104 weekly bars) we have comfortable headroom.
    """
    if len(close) < rsi_length + stoch_length + smooth_k + 5:
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


# ══════════════════════════════════════════════════════════════════════════════
# DATA FETCHERS
# ══════════════════════════════════════════════════════════════════════════════

def fetch_yfinance(tickers: list, label: str) -> dict:
    """
    Fetch WEEKLY OHLCV via Ticker.history(interval='1wk', period='2y').
    Returns {ticker: K_value}.
    """
    results = {}
    log.info(f"── {label}: {len(tickers)} tickers  [Weekly / 2y]")
    for ticker in tickers:
        try:
            df = yf.Ticker(ticker).history(
                period=YF_PERIOD,
                interval=YF_INTERVAL,
                auto_adjust=True,
            )
            if df is None or df.empty:
                log.warning(f"  ⚠ {ticker}: no data")
                continue
            if len(df) < 36:
                log.warning(f"  ⚠ {ticker}: only {len(df)} weekly bars (need 36+)")
                continue
            close = df["Close"].squeeze().dropna()
            k     = calc_stoch_rsi_k(close)
            if k is None:
                log.warning(f"  ⚠ {ticker}: K computation failed")
                continue
            results[ticker] = k
            if k <= STOCH_RSI_THRESHOLD:
                log.info(f"  🔔 {ticker}: K = {k}  ← TRIGGERED")
        except Exception as e:
            log.error(f"  ✗ {ticker}: {e}")
    log.info(f"  ✅ {label} done — {len(results)}/{len(tickers)} ok")
    return results


def fetch_crypto() -> dict:
    """
    CoinGecko: days=365 auto-selects weekly OHLC granularity.
    Returns {SYMBOL: K_value}.
    """
    cg      = CoinGeckoAPI()
    results = {}
    log.info(f"── Crypto: {len(CRYPTO_IDS)} coins  [Weekly via CoinGecko 365d]")
    for coin_id in CRYPTO_IDS:
        try:
            time.sleep(1.2)   # free tier: max 30 req/min
            ohlc = cg.get_coin_ohlc_by_id(id=coin_id, vs_currency="usd", days=365)
            if not ohlc or len(ohlc) < 36:
                log.warning(f"  ⚠ {coin_id}: only {len(ohlc) if ohlc else 0} bars")
                continue
            df    = pd.DataFrame(ohlc, columns=["ts","open","high","low","close"])
            df["ts"] = pd.to_datetime(df["ts"], unit="ms")
            close = df.set_index("ts").sort_index()["close"].dropna()
            k     = calc_stoch_rsi_k(close)
            if k is None:
                log.warning(f"  ⚠ {coin_id}: K computation failed")
                continue
            sym          = coin_id.upper().replace("-2","").replace("-","")
            results[sym] = k
            if k <= STOCH_RSI_THRESHOLD:
                log.info(f"  🔔 {sym}: K = {k}  ← TRIGGERED")
        except Exception as e:
            log.error(f"  ✗ {coin_id}: {e}")
    log.info(f"  ✅ Crypto done — {len(results)}/{len(CRYPTO_IDS)} ok")
    return results


# ══════════════════════════════════════════════════════════════════════════════
# TELEGRAM
# ══════════════════════════════════════════════════════════════════════════════

def send_telegram(message: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("❌ Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        return False
    url  = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        resp = requests.post(url, data=data, timeout=15)
        if resp.status_code == 200:
            log.info("✅ Telegram sent")
            return True
        log.error(f"Telegram {resp.status_code}: {resp.text}")
        return False
    except Exception as e:
        log.error(f"Telegram failed: {e}")
        return False


def get_label(sym: str) -> str:
    return TICKER_LABELS.get(sym, sym.replace(".NS","").replace("^",""))


def build_message(triggered: dict, total_scanned: int, run_type: str) -> str:
    now         = datetime.utcnow().strftime("%d %b %Y")
    crypto_syms = {c.upper().replace("-2","").replace("-","") for c in CRYPTO_IDS}

    sections = {
        "🇮🇳 Indian Indices":  {},
        "🌍 World Indices":    {},
        "🇺🇸 US Indices":      {},
        "🇮🇳 NSE Stocks":      {},
        "🇺🇸 Nasdaq 100":      {},
        "🪙 Crypto":           {},
    }

    for sym, k in triggered.items():
        if sym in set(INDIAN_INDICES):
            sections["🇮🇳 Indian Indices"][sym] = k
        elif sym in set(WORLD_INDICES):
            sections["🌍 World Indices"][sym] = k
        elif sym in set(US_INDICES):
            sections["🇺🇸 US Indices"][sym] = k
        elif sym in crypto_syms:
            sections["🪙 Crypto"][sym] = k
        elif sym.endswith(".NS"):
            sections["🇮🇳 NSE Stocks"][sym] = k
        else:
            sections["🇺🇸 Nasdaq 100"][sym] = k

    trigger_icon = "🔔 Weekly" if run_type == "scheduled" else "🔍 Manual"
    lines = [
        f"📊 <b>StochRSI Weekly Scanner</b>  |  {now} (UTC)",
        f"{trigger_icon}  |  K ≤ <b>{int(STOCH_RSI_THRESHOLD)}</b>  "
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
            lines.append(f"  {icon} {get_label(sym)}  →  K = <b>{k}</b>")
        lines.append("")

    if not any_hit:
        lines.append("✅ <b>No triggers this week.</b>")
        lines.append("All instruments above threshold — no oversold setups on weekly TF.")
        lines.append("")

    lines += [
        "─────────────────────────",
        "🟢 K ≤ 5  →  Deeply oversold (weekly)",
        "🟡 K 5–10  →  Oversold zone (weekly)",
        "💡 <i>Weekly signals = higher conviction. Confirm before entry.</i>",
    ]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def main():
    # Detect if this is a scheduled Friday run or a manual trigger
    run_type = "manual"
    if datetime.utcnow().weekday() == 4:   # 4 = Friday
        run_type = "scheduled"

    total = (len(INDIAN_INDICES) + len(WORLD_INDICES) + len(US_INDICES)
             + len(NSE_STOCKS) + len(NASDAQ_100) + len(CRYPTO_IDS))

    log.info("=" * 60)
    log.info("  StochRSI Weekly Scanner v4")
    log.info(f"  Run type   : {run_type.upper()}")
    log.info(f"  Threshold  : K ≤ {STOCH_RSI_THRESHOLD}")
    log.info(f"  Interval   : {YF_INTERVAL}  |  Period: {YF_PERIOD}")
    log.info(f"  Instruments: {total} total")
    log.info(f"  yfinance   : {yf.__version__}")
    log.info("=" * 60)

    all_k: dict = {}
    all_k.update(fetch_yfinance(INDIAN_INDICES, "Indian Indices"))
    all_k.update(fetch_yfinance(WORLD_INDICES,  "World Indices"))
    all_k.update(fetch_yfinance(US_INDICES,     "US Indices"))
    all_k.update(fetch_yfinance(NSE_STOCKS,     "NSE Stocks"))
    all_k.update(fetch_yfinance(NASDAQ_100,     "Nasdaq 100"))
    all_k.update(fetch_crypto())

    triggered = {s: k for s, k in all_k.items() if k <= STOCH_RSI_THRESHOLD}

    log.info("=" * 60)
    log.info(f"  Scanned   : {len(all_k)}")
    log.info(f"  Triggered : {len(triggered)}")
    log.info("=" * 60)

    send_telegram(build_message(triggered, total_scanned=len(all_k), run_type=run_type))


if __name__ == "__main__":
    main()
