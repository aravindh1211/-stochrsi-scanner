"""
StochRSI Scanner — GitHub Actions Edition (v7)
Timeframe : Weekly (1wk candles) for everything
Schedule  : Every Friday 8:00 PM IST (14:30 UTC) + any manual run

TWO-LAYER SCREEN (v7), run in this order for every instrument —
Universe → Layer 1 (Valuation) → Layer 2 (Timing):

  LAYER 1 — Valuation (src/valuation.py)
    An instrument is "cheap" when either:
      - current trailing PE is in the bottom 30th percentile of its OWN
        5-year trailing-PE history (reconstructed from quarterly EPS +
        weekly price), OR
      - PEG ratio < 1 (used for growth names where PE alone misleads,
        or whenever 5y PE history can't be reliably reconstructed)
    Indices and crypto have no PE and are exempt — they always pass
    Layer 1 straight through to Layer 2.
    If an instrument fails Layer 1, Layer 2 is skipped for it entirely.

  LAYER 2 — Timing (Stoch RSI %K/%D crossover)
    Only evaluated for instruments that passed Layer 1. Weekly Stoch
    RSI with SLOWER settings — (21,5,5) instead of the old (14,3,3) —
    to cut down whipsaw crossovers. Fires only on a CONFIRMED weekly
    close: %K crosses above %D while both lines are below
    STOCH_RSI_THRESHOLD (default 20). A mere touch under the threshold
    with no crossover does not fire.

Two tracks, both computed on the WEEKLY timeframe:

  1) WEEKLY TRACK  (sent every Friday)
     - All indices (Indian / World / US)
     - Only the assets actually held in the portfolio (from the
       consolidated holdings report)

  2) MONTHLY TRACK  (sent once, on the last Friday of the month)
     - Every other NSE / Nasdaq / crypto asset that used to be
       scanned every week is now only *checked* every week, with
       hits accumulated in a small state file. Once a month — on
       the last Friday — anything that confirmed a cross-up (after
       passing Layer 1) at any point in that month is summarized in
       a single message.

Replicates Pine Script:
    rsi1 = rsi(src, 21)
    k    = sma(stoch(rsi1, rsi1, rsi1, 21), 5)
    d    = sma(k, 5)

Data sources:
  yfinance   — equities + indices + crypto (interval='1wk', period='2y')
              Crypto tickers use the Yahoo Finance "-USD" suffix format,
              e.g. BTC-USD, ETH-USD — same source as everything else, so
              no separate rate limit or API to babysit.
  yfinance   — Layer 1 valuation also pulls .info (trailingPE, PEG),
              quarterly_income_stmt (EPS history), and 5y weekly price
              per instrument, all from the same yfinance library.
"""

import os
import json
import logging
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta

from valuation import get_valuation_signal, is_valuation_exempt

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
STOCH_RSI_THRESHOLD = float(os.environ.get("STOCH_RSI_THRESHOLD", "20"))
STATE_FILE          = os.environ.get("STATE_FILE", "state/monthly_state.json")

# Weekly fetch settings
YF_INTERVAL = "1wk"
YF_PERIOD   = "2y"    # 2 years of weekly bars = ~104 candles, well above minimum 57

# Stoch RSI settings (v7) — slower than the old (14,3,3) to cut weekly whipsaw
STOCH_RSI_LENGTH = int(os.environ.get("STOCH_RSI_LENGTH", "21"))
STOCH_LENGTH     = int(os.environ.get("STOCH_LENGTH", "21"))
SMOOTH_K         = int(os.environ.get("SMOOTH_K", "5"))
SMOOTH_D         = int(os.environ.get("SMOOTH_D", "5"))


# ══════════════════════════════════════════════════════════════════════════════
# WATCHLISTS
# ══════════════════════════════════════════════════════════════════════════════

# ── Indian Indices (tracked WEEKLY, every run) ─────────────────────────────────
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

# ── Major World Indices (tracked WEEKLY, every run) ────────────────────────────
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

# ── US Indices (tracked WEEKLY, every run) ─────────────────────────────────────
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

# ── Portfolio holdings (tracked WEEKLY, every run) ─────────────────────────────
# Loaded from holdings.json (repo root) instead of being hardcoded here, so the
# list can be updated without touching this script — either by editing
# holdings.json directly, or via the "Update Holdings" workflow
# (.github/workflows/update-holdings.yml).
HOLDINGS_FILE = os.environ.get("HOLDINGS_FILE", "holdings.json")


def _load_holdings(path: str) -> dict:
    default = {"nse_stocks": [], "us_stocks": [], "crypto": []}
    if not os.path.exists(path):
        log.warning(f"  ⚠ Holdings file '{path}' not found — scanning 0 holdings")
        return default
    try:
        with open(path, "r") as f:
            data = json.load(f)
        for key in default:
            data.setdefault(key, [])
        return data
    except Exception as e:
        log.error(f"  ✗ Failed to load holdings file: {e}")
        return default


_holdings = _load_holdings(HOLDINGS_FILE)
HOLDINGS_NSE_STOCKS = _holdings["nse_stocks"]
HOLDINGS_US_STOCKS  = _holdings["us_stocks"]
HOLDINGS_CRYPTO     = _holdings["crypto"]

# ── Nifty 100 Stocks (NSE) — full universe ─────────────────────────────────────
NSE_STOCKS_ALL = [
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
    "ASHOKLEY.NS",
]

# ── Nasdaq 100 Stocks (US) — full universe ─────────────────────────────────────
NASDAQ_100_ALL = [
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

# ── Crypto (Yahoo Finance "-USD" tickers) — full universe ──────────────────────
CRYPTO_TICKERS_ALL = [
    "BTC-USD",   "ETH-USD",   "BNB-USD",   "SOL-USD",
    "XRP-USD",   "ADA-USD",   "DOGE-USD",  "AVAX-USD",
    "LINK-USD",  "DOT-USD",
]

# ── Monthly-only universe = full universe minus what's already in holdings ────
MONTHLY_NSE_STOCKS    = [t for t in NSE_STOCKS_ALL if t not in HOLDINGS_NSE_STOCKS]
MONTHLY_NASDAQ_STOCKS = [t for t in NASDAQ_100_ALL if t not in HOLDINGS_US_STOCKS]
MONTHLY_CRYPTO_TICKERS = [c for c in CRYPTO_TICKERS_ALL if c not in HOLDINGS_CRYPTO]

# ── Display Labels ─────────────────────────────────────────────────────────────
INDEX_LABELS = {
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

STOCK_LABELS = {
    "RELIANCE.NS": "Reliance Industries",     "TCS.NS": "Tata Consultancy Services",
    "HDFCBANK.NS": "HDFC Bank",               "BHARTIARTL.NS": "Bharti Airtel",
    "ICICIBANK.NS": "ICICI Bank",             "INFOSYS.NS": "Infosys",
    "SBIN.NS": "State Bank of India",         "HINDUNILVR.NS": "Hindustan Unilever",
    "ITC.NS": "ITC",                          "LT.NS": "Larsen & Toubro",
    "BAJFINANCE.NS": "Bajaj Finance",         "HCLTECH.NS": "HCL Technologies",
    "MARUTI.NS": "Maruti Suzuki",             "SUNPHARMA.NS": "Sun Pharma",
    "KOTAKBANK.NS": "Kotak Mahindra Bank",    "AXISBANK.NS": "Axis Bank",
    "TITAN.NS": "Titan Company",              "ASIANPAINT.NS": "Asian Paints",
    "NESTLEIND.NS": "Nestle India",           "WIPRO.NS": "Wipro",
    "ULTRACEMCO.NS": "UltraTech Cement",      "POWERGRID.NS": "Power Grid Corp",
    "NTPC.NS": "NTPC",                        "BAJAJFINSV.NS": "Bajaj Finserv",
    "TECHM.NS": "Tech Mahindra",              "M&M.NS": "Mahindra & Mahindra",
    "TATAMOTORS.NS": "Tata Motors",           "ADANIENT.NS": "Adani Enterprises",
    "ADANIPORTS.NS": "Adani Ports",           "JSWSTEEL.NS": "JSW Steel",
    "TATASTEEL.NS": "Tata Steel",             "ONGC.NS": "ONGC",
    "COALINDIA.NS": "Coal India",             "HINDALCO.NS": "Hindalco Industries",
    "GRASIM.NS": "Grasim Industries",         "CIPLA.NS": "Cipla",
    "DRREDDY.NS": "Dr Reddy's Labs",          "DIVISLAB.NS": "Divi's Laboratories",
    "EICHERMOT.NS": "Eicher Motors",          "BRITANNIA.NS": "Britannia Industries",
    "HDFCLIFE.NS": "HDFC Life Insurance",     "SBILIFE.NS": "SBI Life Insurance",
    "ICICIPRULI.NS": "ICICI Prudential Life", "ICICIGI.NS": "ICICI Lombard",
    "HEROMOTOCO.NS": "Hero MotoCorp",         "BPCL.NS": "BPCL",
    "IOC.NS": "Indian Oil Corp",              "TATACONSUM.NS": "Tata Consumer Products",
    "APOLLOHOSP.NS": "Apollo Hospitals",      "BAJAJ-AUTO.NS": "Bajaj Auto",
    "VEDL.NS": "Vedanta",                     "INDUSINDBK.NS": "IndusInd Bank",
    "SHRIRAMFIN.NS": "Shriram Finance",       "ZOMATO.NS": "Zomato (Eternal)",
    "PAYTM.NS": "Paytm (One97 Comm.)",        "NYKAA.NS": "Nykaa",
    "POLICYBZR.NS": "PB Fintech (Policybazaar)","DMART.NS": "Avenue Supermarts (DMart)",
    "SIEMENS.NS": "Siemens India",            "ABB.NS": "ABB India",
    "HAVELLS.NS": "Havells India",            "PIDILITIND.NS": "Pidilite Industries",
    "BOSCHLTD.NS": "Bosch Ltd",               "MUTHOOTFIN.NS": "Muthoot Finance",
    "CHOLAFIN.NS": "Cholamandalam Investment","PFC.NS": "Power Finance Corp",
    "RECLTD.NS": "REC Ltd",                   "IRCTC.NS": "IRCTC",
    "IRFC.NS": "Indian Railway Finance Corp", "HAL.NS": "Hindustan Aeronautics",
    "BEL.NS": "Bharat Electronics",           "BHEL.NS": "BHEL",
    "GAIL.NS": "GAIL India",                  "TRENT.NS": "Trent",
    "DABUR.NS": "Dabur India",                "GODREJCP.NS": "Godrej Consumer Products",
    "MARICO.NS": "Marico",                    "COLPAL.NS": "Colgate-Palmolive India",
    "BERGEPAINT.NS": "Berger Paints",         "MPHASIS.NS": "Mphasis",
    "LTIM.NS": "LTIMindtree",                 "PERSISTENT.NS": "Persistent Systems",
    "COFORGE.NS": "Coforge",                  "OBEROIRLTY.NS": "Oberoi Realty",
    "DLF.NS": "DLF",                          "LODHA.NS": "Macrotech Developers (Lodha)",
    "ADANIGREEN.NS": "Adani Green Energy",    "ADANIPOWER.NS": "Adani Power",
    "TATAPOWER.NS": "Tata Power",             "NHPC.NS": "NHPC",
    "SJVN.NS": "SJVN",                        "ZYDUSLIFE.NS": "Zydus Lifesciences",
    "TORNTPHARM.NS": "Torrent Pharmaceuticals","LUPIN.NS": "Lupin",
    "AUROPHARMA.NS": "Aurobindo Pharma",      "MANKIND.NS": "Mankind Pharma",
    "ASHOKLEY.NS": "Ashok Leyland",

    "AAPL": "Apple Inc",          "MSFT": "Microsoft Corporation",
    "NVDA": "NVIDIA Corporation", "AMZN": "Amazon.com Inc",
    "META": "Meta Platforms",     "GOOGL": "Alphabet Inc (Class A)",
    "GOOG": "Alphabet Inc (Class C)", "TSLA": "Tesla Inc",
    "AVGO": "Broadcom Inc",       "COST": "Costco Wholesale",
    "NFLX": "Netflix Inc",        "ASML": "ASML Holding",
    "AMD": "Advanced Micro Devices", "PEP": "PepsiCo",
    "QCOM": "Qualcomm",           "AMAT": "Applied Materials",
    "CSCO": "Cisco Systems",      "TXN": "Texas Instruments",
    "INTU": "Intuit",             "AMGN": "Amgen",
    "BKNG": "Booking Holdings",   "MU": "Micron Technology",
    "ISRG": "Intuitive Surgical", "HON": "Honeywell",
    "LRCX": "Lam Research",       "CMCSA": "Comcast",
    "PANW": "Palo Alto Networks", "ADP": "Automatic Data Processing",
    "VRTX": "Vertex Pharmaceuticals", "SBUX": "Starbucks",
    "MELI": "MercadoLibre",       "KLAC": "KLA Corp",
    "REGN": "Regeneron Pharmaceuticals", "CDNS": "Cadence Design Systems",
    "SNPS": "Synopsys",           "MAR": "Marriott International",
    "MDLZ": "Mondelez International", "ORLY": "O'Reilly Automotive",
    "CSX": "CSX Corporation",     "ABNB": "Airbnb",
    "MNST": "Monster Beverage",   "PYPL": "PayPal Holdings",
    "FTNT": "Fortinet",           "MRVL": "Marvell Technology",
    "ADSK": "Autodesk",           "PCAR": "PACCAR",
    "WDAY": "Workday",            "BIIB": "Biogen",
    "CTAS": "Cintas",             "DXCM": "Dexcom",
    "EXC": "Exelon",              "FAST": "Fastenal",
    "GEHC": "GE HealthCare",      "GILD": "Gilead Sciences",
    "IDXX": "IDEXX Laboratories", "ILMN": "Illumina",
    "KDP": "Keurig Dr Pepper",    "KHC": "Kraft Heinz",
    "MCHP": "Microchip Technology", "MRNA": "Moderna",
    "ODFL": "Old Dominion Freight Line", "ON": "ON Semiconductor",
    "PAYX": "Paychex",            "ROP": "Roper Technologies",
    "ROST": "Ross Stores",        "TEAM": "Atlassian",
    "TTD": "The Trade Desk",      "TTWO": "Take-Two Interactive",
    "VRSK": "Verisk Analytics",   "WBD": "Warner Bros Discovery",
    "ZS": "Zscaler",              "CRWD": "CrowdStrike",
    "ENPH": "Enphase Energy",     "FANG": "Diamondback Energy",
    "LULU": "Lululemon Athletica","CEG": "Constellation Energy",
    "DDOG": "Datadog",            "GFS": "GlobalFoundries",
    "SMCI": "Super Micro Computer", "ARM": "Arm Holdings",
    "DASH": "DoorDash",           "CDW": "CDW Corp",
    "FSLR": "First Solar",        "NXPI": "NXP Semiconductors",
    "ZM": "Zoom Video Communications", "ALGN": "Align Technology",
    "DLTR": "Dollar Tree",        "EBAY": "eBay",
    "INTC": "Intel Corporation",  "RIVN": "Rivian Automotive",
    "LCID": "Lucid Group",

    "VOO": "Vanguard S&P 500 ETF",             "EEM": "iShares MSCI Emerging Markets ETF",
    "VTWO": "Vanguard Russell 2000 ETF",       "MRK": "Merck & Co",
    "IYH": "iShares US Healthcare ETF",        "V": "Visa Inc",
    "ABBV": "AbbVie Inc",                      "BRK-B": "Berkshire Hathaway (Class B)",
    "ACN": "Accenture PLC",                    "JNJ": "Johnson & Johnson",
    "VEA": "Vanguard FTSE Developed Markets ETF",

    # ── Added with the Aug 2026 holdings expansion ──────────────────────────
    "JPM": "JPMorgan Chase",                   "LLY": "Eli Lilly",
    "ETN": "Eaton Corporation",                "GE": "GE Aerospace",
    "PG": "Procter & Gamble",                  "COP": "ConocoPhillips",
    "XOM": "Exxon Mobil",                      "LIN": "Linde plc",
    "ECL": "Ecolab",                           "NEE": "NextEra Energy",
    "PLD": "Prologis",                         "EQIX": "Equinix",
    "TSM": "Taiwan Semiconductor Manufacturing","NOW": "ServiceNow",
    "CRM": "Salesforce",                       "RTX": "RTX Corporation",
    "LMT": "Lockheed Martin",                  "GD": "General Dynamics",
    "CMG": "Chipotle Mexican Grill",           "HLT": "Hilton Worldwide",
    "BLK": "BlackRock",                        "CME": "CME Group",
    "UNP": "Union Pacific",                    "CP": "Canadian Pacific Kansas City",
    "HD": "Home Depot",                        "LOW": "Lowe's Companies",
    "ANET": "Arista Networks",

    "HDFCAMC.NS": "HDFC Asset Management",     "BSE.NS": "BSE Ltd",
    "INDUSTOWER.NS": "Indus Towers",           "UNOMINDA.NS": "Uno Minda",
    "MAXHEALTH.NS": "Max Healthcare Institute","LALPATHLAB.NS": "Dr Lal PathLabs",
    "METROPOLIS.NS": "Metropolis Healthcare",  "VBL.NS": "Varun Beverages",
    "POLYCAB.NS": "Polycab India",             "KNRCON.NS": "KNR Constructions",
    "SHREECEM.NS": "Shree Cement",             "JSWENERGY.NS": "JSW Energy",
    "SRF.NS": "SRF Ltd",                       "PIIND.NS": "PI Industries",
    "INDHOTEL.NS": "Indian Hotels Company",    "INDIGO.NS": "InterGlobe Aviation (IndiGo)",
    "CONCOR.NS": "Container Corp of India",    "TCI.NS": "Transport Corp of India",
    "PAGEIND.NS": "Page Industries",           "KPRMILL.NS": "KPR Mill",
    "COROMANDEL.NS": "Coromandel International","NAUKRI.NS": "Info Edge (Naukri)",
    "ETERNAL.NS": "Eternal (formerly Zomato)", "SUNTV.NS": "Sun TV Network",
    "PVRINOX.NS": "PVR INOX",
}

CRYPTO_LABELS = {
    "BTC-USD": "Bitcoin (BTC)",       "ETH-USD": "Ethereum (ETH)",
    "BNB-USD": "BNB (BNB)",           "SOL-USD": "Solana (SOL)",
    "XRP-USD": "XRP (Ripple)",        "ADA-USD": "Cardano (ADA)",
    "DOGE-USD": "Dogecoin (DOGE)",    "AVAX-USD": "Avalanche (AVAX)",
    "LINK-USD": "Chainlink (LINK)",   "DOT-USD": "Polkadot (DOT)",
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


def calc_stoch_rsi_kd_series(
    close:        pd.Series,
    rsi_length:   int = 21,
    stoch_length: int = 21,
    smooth_k:     int = 5,
    smooth_d:     int = 5,
) -> tuple[pd.Series, pd.Series] | tuple[None, None]:
    """
    Returns (K series, D series), both 0–100, or (None, None) if
    insufficient bars.

    v7 defaults are the SLOWER weekly settings (21,5,5) instead of the
    old default (14,3,3) — deliberately less twitchy, fewer whipsaw
    crossovers on the weekly timeframe.

    Pine Script equivalent:
        rsi1 = rsi(src, 21)
        k    = sma(stoch(rsi1, rsi1, rsi1, 21), 5)
        d    = sma(k, 5)

    Minimum bars needed: 21 + 21 + 5 + 5 + 5 = 57 weekly candles (~14mo).
    With period='2y' (~104 weekly bars) there's comfortable headroom.
    """
    if len(close) < rsi_length + stoch_length + smooth_k + smooth_d + 5:
        return None, None
    rsi_vals  = calc_rsi(close, rsi_length)
    hi        = rsi_vals.rolling(stoch_length).max()
    lo        = rsi_vals.rolling(stoch_length).min()
    denom     = (hi - lo).replace(0, np.nan)
    stoch_raw = ((rsi_vals - lo) / denom) * 100
    k_series  = stoch_raw.rolling(smooth_k).mean().dropna()
    d_series  = k_series.rolling(smooth_d).mean().dropna()
    if len(d_series) == 0:
        return None, None
    k_series = k_series.reindex(d_series.index)  # align K to D's (shorter) index
    return k_series, d_series


def drop_incomplete_last_bar(df: pd.DataFrame) -> pd.DataFrame:
    """
    yfinance's most recent weekly bar is often still in progress (mid-
    week). "Confirmed on weekly close" means we must not act on that
    bar — drop it if the week it represents hasn't finished yet.
    """
    if df is None or df.empty:
        return df
    last_date = pd.Timestamp(df.index[-1])
    if last_date.tzinfo is not None:
        last_date = last_date.tz_localize(None)
    now = pd.Timestamp.utcnow().tz_localize(None)
    if (now - last_date) < pd.Timedelta(days=6):
        return df.iloc[:-1]
    return df


def check_kd_bullish_crossover(
    k_series: pd.Series,
    d_series: pd.Series,
    threshold: float,
) -> dict | None:
    """
    Trigger condition (v7): %K crosses above %D — prev bar K <= D, this
    bar K > D — while BOTH lines are below `threshold` on the current
    (now-confirmed-closed) weekly bar. A mere touch under the threshold
    with no crossover does NOT fire; K rising while still under D does
    NOT fire either — the cross itself must happen this bar.

    Returns {"k","d","prev_k","prev_d"} if triggered, else None.
    """
    if k_series is None or d_series is None or len(k_series) < 2 or len(d_series) < 2:
        return None
    curr_k, prev_k = float(k_series.iloc[-1]), float(k_series.iloc[-2])
    curr_d, prev_d = float(d_series.iloc[-1]), float(d_series.iloc[-2])

    crossed_up = (prev_k <= prev_d) and (curr_k > curr_d)
    both_below = (curr_k < threshold) and (curr_d < threshold)

    if crossed_up and both_below:
        return {
            "k": round(curr_k, 2), "d": round(curr_d, 2),
            "prev_k": round(prev_k, 2), "prev_d": round(prev_d, 2),
        }
    return None


# ══════════════════════════════════════════════════════════════════════════════
# DATA FETCHERS
# ══════════════════════════════════════════════════════════════════════════════

def fetch_yfinance(tickers: list, label: str) -> dict:
    """
    Two-layer screen, run in order — Universe → Valuation (Layer 1) →
    Stochastic (Layer 2):

      Layer 1 — cheap check via get_valuation_signal(). Indices/crypto
      are exempt (no PE) and always pass through. If an instrument
      fails Layer 1 (not cheap), Layer 2 is skipped entirely for it —
      no stochastic call, no history fetch wasted.

      Layer 2 — only for instruments that passed Layer 1: weekly
      Stoch RSI %K/%D crossover, confirmed on a CLOSED weekly bar —
      %K crosses above %D while both are below STOCH_RSI_THRESHOLD.

    Returns {ticker: {"k","d","prev_k","prev_d","triggered","cheap",
                       "valuation_method","valuation_detail"}}.
    Tickers that fail Layer 1 are NOT included in the return dict at
    all (they never reach Layer 2, so there's no timing state to show).
    """
    results = {}
    log.info(f"── {label}: {len(tickers)} tickers  [Weekly / 2y]")
    for ticker in tickers:
        try:
            # ── Layer 1: Valuation ──────────────────────────────────────
            val = get_valuation_signal(ticker)
            if not val["cheap"]:
                log.info(f"  ⛔ {ticker}: Layer 1 fail — {val['detail']}")
                continue

            # ── Layer 2: Stochastic timing ──────────────────────────────
            df = yf.Ticker(ticker).history(
                period=YF_PERIOD,
                interval=YF_INTERVAL,
                auto_adjust=True,
            )
            if df is None or df.empty:
                log.warning(f"  ⚠ {ticker}: no price data")
                continue
            df = drop_incomplete_last_bar(df)
            if len(df) < 57:
                log.warning(f"  ⚠ {ticker}: only {len(df)} closed weekly bars (need 57+)")
                continue
            close = df["Close"].squeeze().dropna()
            k_series, d_series = calc_stoch_rsi_kd_series(
                close, STOCH_RSI_LENGTH, STOCH_LENGTH, SMOOTH_K, SMOOTH_D,
            )
            if k_series is None:
                log.warning(f"  ⚠ {ticker}: K/D computation failed")
                continue
            cross = check_kd_bullish_crossover(k_series, d_series, STOCH_RSI_THRESHOLD)
            results[ticker] = {
                "k": round(float(k_series.iloc[-1]), 2),
                "d": round(float(d_series.iloc[-1]), 2),
                "prev_k": round(float(k_series.iloc[-2]), 2) if len(k_series) >= 2 else None,
                "prev_d": round(float(d_series.iloc[-2]), 2) if len(d_series) >= 2 else None,
                "triggered": cross is not None,
                "cheap": True,
                "valuation_method": val["method"],
                "valuation_detail": val["detail"],
            }
            if cross is not None:
                log.info(
                    f"  🔔 {ticker}: K/D cross-up below {STOCH_RSI_THRESHOLD} "
                    f"(K {cross['prev_k']}→{cross['k']}, D {cross['prev_d']}→{cross['d']})  "
                    f"[{val['method']}: {val['detail']}]  ← TRIGGERED"
                )
        except Exception as e:
            log.error(f"  ✗ {ticker}: {e}")
    log.info(f"  ✅ {label} done — {len(results)}/{len(tickers)} passed Layer 1 and were scanned")
    return results


def fetch_crypto(crypto_tickers: list, label: str = "Crypto") -> dict:
    """
    Crypto fetched via yfinance using the "-USD" ticker suffix (e.g.
    BTC-USD, ETH-USD) — same source, same weekly bars as everything
    else in fetch_yfinance(). Layer 1 (valuation) is a no-op for crypto
    (is_valuation_exempt() catches the "-USD" suffix) — crypto always
    proceeds straight to the Layer 2 stochastic check.
    """
    return fetch_yfinance(crypto_tickers, label)


# ══════════════════════════════════════════════════════════════════════════════
# MONTHLY STATE (persisted in the repo between runs)
# ══════════════════════════════════════════════════════════════════════════════

def _empty_state(month_key: str) -> dict:
    return {"month": month_key, "hits": {}}


def load_state(month_key: str) -> dict:
    if not os.path.exists(STATE_FILE):
        return _empty_state(month_key)
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
        if state.get("month") != month_key:
            # Safety net: a prior month's state that never got cleared.
            log.warning(f"  ⚠ Stale state found for {state.get('month')}, resetting")
            return _empty_state(month_key)
        return state
    except Exception as e:
        log.error(f"  ✗ Failed to load state file: {e}")
        return _empty_state(month_key)


def save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def is_last_friday_of_month(today: datetime) -> bool:
    """True if the next Friday (7 days from now) falls in a different month."""
    next_friday = today + timedelta(days=7)
    return next_friday.month != today.month


# ══════════════════════════════════════════════════════════════════════════════
# WEEKLY LOG (persisted so the monthly digest can compile every weekly
# holdings/indices notification sent this month — see monthly_digest.py)
# ══════════════════════════════════════════════════════════════════════════════

WEEKLY_LOG_FILE = os.environ.get("WEEKLY_LOG_FILE", "state/weekly_log.json")


def append_weekly_log(today: datetime, triggered: dict, total_scanned: int) -> None:
    os.makedirs(os.path.dirname(WEEKLY_LOG_FILE) or ".", exist_ok=True)
    entries = []
    if os.path.exists(WEEKLY_LOG_FILE):
        try:
            with open(WEEKLY_LOG_FILE, "r") as f:
                entries = json.load(f)
        except Exception as e:
            log.error(f"  ✗ Failed to load weekly log, starting fresh: {e}")
            entries = []

    entries.append({
        "date": today.strftime("%Y-%m-%d"),
        "total_scanned": total_scanned,
        "triggered": {
            sym: {
                "k": info["k"], "prev_k": info["prev_k"],
                "d": info["d"], "prev_d": info["prev_d"],
                "valuation_method": info["valuation_method"],
            }
            for sym, info in triggered.items()
        },
    })

    with open(WEEKLY_LOG_FILE, "w") as f:
        json.dump(entries, f, indent=2, sort_keys=True)
    log.info(f"  📝 Weekly log updated — {len(entries)} week(s) logged so far this month")


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
    if sym in INDEX_LABELS:
        return f"{INDEX_LABELS[sym]} ({sym})"
    if sym in STOCK_LABELS:
        return f"{STOCK_LABELS[sym]} ({sym})"
    if sym in CRYPTO_LABELS:
        return CRYPTO_LABELS[sym]
    return sym.replace(".NS", "").replace("^", "")


def build_message(triggered: dict, universe_total: int, layer1_passed: int, run_type: str) -> str:
    """
    `triggered` maps sym -> {"k","d","prev_k","prev_d","valuation_method",
    "valuation_detail","triggered": True} for instruments that passed
    Layer 1 (valuation) AND fired the Layer 2 %K/%D crossover.
    """
    now = datetime.utcnow().strftime("%d %b %Y")

    sections = {
        "🇮🇳 Indian Indices":    {},
        "🌍 World Indices":      {},
        "🇺🇸 US Indices":        {},
        "💼 Portfolio Holdings": {},
    }

    for sym, info in triggered.items():
        if sym in set(INDIAN_INDICES):
            sections["🇮🇳 Indian Indices"][sym] = info
        elif sym in set(WORLD_INDICES):
            sections["🌍 World Indices"][sym] = info
        elif sym in set(US_INDICES):
            sections["🇺🇸 US Indices"][sym] = info
        else:
            # everything else in this run is a portfolio holding
            sections["💼 Portfolio Holdings"][sym] = info

    trigger_icon = "🔔 Weekly" if run_type == "scheduled" else "🔍 Manual"
    lines = [
        f"📊 <b>StochRSI Weekly Scanner</b>  |  {now} (UTC)",
        f"{trigger_icon}  |  %K/%D cross-up below <b>{int(STOCH_RSI_THRESHOLD)}</b>, "
        f"confirmed on weekly close",
        f"Universe: <b>{universe_total}</b>  |  Passed Layer 1 (valuation): "
        f"<b>{layer1_passed}</b>  |  Layer 2 triggers: <b>{len(triggered)}</b>",
        "",
    ]

    any_hit = False
    for section, items in sections.items():
        if not items:
            continue
        any_hit = True
        lines.append(f"<b>{section}</b>")
        for sym, info in sorted(items.items(), key=lambda x: x[1]["k"]):
            k, d = info["k"], info["d"]
            icon = "🟢" if k <= 5 else "🟡"
            lines.append(
                f"  {icon} {get_label(sym)}  →  K: {info['prev_k']}→<b>{k}</b>  "
                f"D: {info['prev_d']}→{d}  ↑"
            )
            lines.append(f"       <i>{info['valuation_detail']}</i>")
        lines.append("")

    if not any_hit:
        lines.append("✅ <b>No triggers this week.</b>")
        lines.append("Nothing both passed the valuation filter AND confirmed a weekly K/D cross-up.")
        lines.append("")

    lines += [
        "─────────────────────────",
        "🟢 Current K ≤ 5  →  Turning up from deeply oversold",
        f"🟡 Current K 5–{int(STOCH_RSI_THRESHOLD)}  →  Turning up from oversold zone",
        "💡 <i>Layer 1 (cheap) + Layer 2 (confirmed cross) — higher conviction, still confirm before entry.</i>",
    ]
    return "\n".join(lines)


def build_monthly_message(hits: dict, month_key: str, universe_total: int) -> str:
    month_label = datetime.strptime(month_key, "%Y-%m").strftime("%B %Y")
    lines = [
        f"🗓️ <b>Monthly Watchlist Scan — {month_label}</b>",
        f"%K/%D cross-up below <b>{int(STOCH_RSI_THRESHOLD)}</b> (weekly close, valuation-filtered) "
        f"at any point this month  |  Universe: <b>{universe_total}</b> non-portfolio instruments",
        "",
    ]

    if not hits:
        lines.append("✅ <b>No confirmed setups this month.</b>")
        lines.append("Nothing outside your portfolio both passed the valuation filter and confirmed a weekly cross-up.")
    else:
        lines.append(f"<b>🔔 {len(hits)} instrument(s) confirmed a cross-up below K/D = {int(STOCH_RSI_THRESHOLD)} this month</b>")
        for sym, info in sorted(hits.items(), key=lambda x: x[1]["k"]):
            icon = "🟢" if info["k"] <= 5 else "🟡"
            lines.append(
                f"  {icon} {get_label(sym)}  →  K: {info['prev_k']}→<b>{info['k']}</b>  "
                f"D: {info['prev_d']}→{info['d']}  (seen {info['date']})"
            )
            lines.append(f"       <i>{info['valuation_detail']}</i>")

    lines += [
        "",
        "─────────────────────────",
        "💡 <i>These are outside your current holdings — worth a look, not an action item.</i>",
    ]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def main():
    today = datetime.utcnow()

    # Detect if this is a scheduled Friday run or a manual trigger
    run_type = "manual"
    if today.weekday() == 4:   # 4 = Friday
        run_type = "scheduled"

    # ── 1) WEEKLY TRACK — indices + portfolio holdings only ───────────────────
    weekly_universe_total = (len(INDIAN_INDICES) + len(WORLD_INDICES) + len(US_INDICES)
                              + len(HOLDINGS_NSE_STOCKS) + len(HOLDINGS_US_STOCKS)
                              + len(HOLDINGS_CRYPTO))

    log.info("=" * 60)
    log.info("  StochRSI Scanner v7")
    log.info(f"  Run type   : {run_type.upper()}")
    log.info(f"  Layer 1    : PE percentile ≤ {30}th pct (own 5y history) OR PEG < 1")
    log.info(f"  Layer 2    : %K/%D cross-up below {STOCH_RSI_THRESHOLD}, confirmed on weekly close")
    log.info(f"  Stoch RSI  : ({STOCH_RSI_LENGTH},{STOCH_LENGTH},{SMOOTH_K},{SMOOTH_D})")
    log.info(f"  Interval   : {YF_INTERVAL}  |  Period: {YF_PERIOD}")
    log.info(f"  Weekly universe: {weekly_universe_total} (indices + holdings)")
    log.info(f"  yfinance   : {yf.__version__}")
    log.info("=" * 60)

    weekly_k: dict = {}
    weekly_k.update(fetch_yfinance(INDIAN_INDICES, "Indian Indices"))
    weekly_k.update(fetch_yfinance(WORLD_INDICES,  "World Indices"))
    weekly_k.update(fetch_yfinance(US_INDICES,     "US Indices"))
    weekly_k.update(fetch_yfinance(HOLDINGS_NSE_STOCKS, "Portfolio — NSE Stocks"))
    weekly_k.update(fetch_yfinance(HOLDINGS_US_STOCKS,  "Portfolio — US Stocks"))
    weekly_k.update(fetch_crypto(HOLDINGS_CRYPTO, "Portfolio — Crypto"))

    weekly_layer1_passed = len(weekly_k)
    weekly_triggered = {s: info for s, info in weekly_k.items() if info["triggered"]}

    log.info("=" * 60)
    log.info(f"  Weekly universe   : {weekly_universe_total}")
    log.info(f"  Passed Layer 1    : {weekly_layer1_passed}")
    log.info(f"  Layer 2 triggered : {len(weekly_triggered)}")
    log.info("=" * 60)

    send_telegram(build_message(
        weekly_triggered,
        universe_total=weekly_universe_total,
        layer1_passed=weekly_layer1_passed,
        run_type=run_type,
    ))

    # Log this week's holdings/indices notification so the last-day-of-month
    # digest (src/monthly_digest.py) can compile everything sent this month.
    if run_type == "scheduled":
        append_weekly_log(today, weekly_triggered, weekly_universe_total)

    # ── 2) MONTHLY TRACK — everything else, checked weekly, reported monthly ──
    month_key = today.strftime("%Y-%m")
    state = load_state(month_key)

    monthly_total = (len(MONTHLY_NSE_STOCKS) + len(MONTHLY_NASDAQ_STOCKS)
                      + len(MONTHLY_CRYPTO_TICKERS))
    log.info(f"  Monthly watchlist universe: {monthly_total} instruments (checked weekly)")

    monthly_k: dict = {}
    monthly_k.update(fetch_yfinance(MONTHLY_NSE_STOCKS, "Monthly Watch — NSE Stocks"))
    monthly_k.update(fetch_yfinance(MONTHLY_NASDAQ_STOCKS, "Monthly Watch — Nasdaq Stocks"))
    monthly_k.update(fetch_crypto(MONTHLY_CRYPTO_TICKERS, "Monthly Watch — Crypto"))

    today_str = today.strftime("%d %b")
    for sym, info in monthly_k.items():
        if info["triggered"]:
            existing = state["hits"].get(sym)
            if existing is None or info["k"] < existing["k"]:
                state["hits"][sym] = {
                    "k": info["k"], "prev_k": info["prev_k"],
                    "d": info["d"], "prev_d": info["prev_d"],
                    "valuation_detail": info["valuation_detail"],
                    "date": today_str,
                }

    save_state(state)
    log.info(f"  Monthly state updated — {len(state['hits'])} cumulative hit(s) so far this month")

    # ── 3) On the last Friday of the month, send the monthly summary ──────────
    if run_type == "scheduled" and is_last_friday_of_month(today):
        log.info("  📅 Last Friday of the month — sending monthly summary")
        send_telegram(build_monthly_message(state["hits"], month_key, monthly_total))
        # Reset for the next month
        next_month = (today.replace(day=28) + timedelta(days=4)).strftime("%Y-%m")
        save_state(_empty_state(next_month))


if __name__ == "__main__":
    main()
