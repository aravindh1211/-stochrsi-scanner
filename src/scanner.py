"""
StochRSI Scanner — GitHub Actions Edition (v5)
Timeframe : Weekly (1wk candles) for everything
Schedule  : Every Friday 8:00 PM IST (14:30 UTC) + any manual run

Two tracks, both computed on the WEEKLY timeframe:

  1) WEEKLY TRACK  (sent every Friday)
     - All indices (Indian / World / US)
     - Only the assets actually held in the portfolio (from the
       consolidated holdings report)

  2) MONTHLY TRACK  (sent once, on the last Friday of the month)
     - Every other NSE / Nasdaq / crypto asset that used to be
       scanned every week is now only *checked* every week, with
       hits accumulated in a small state file. Once a month — on
       the last Friday — anything that dipped under the threshold
       at any point in that month is summarized in a single message.

Replicates Pine Script:
    rsi1 = rsi(src, lengthRSI)
    k    = sma(stoch(rsi1, rsi1, rsi1, lengthStoch), smoothK)

Data sources:
  yfinance   — equities + indices (interval='1wk', period='2y')
  CoinGecko  — crypto (days=365 → auto weekly granularity)
"""

import os
import json
import time
import logging
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from pycoingecko import CoinGeckoAPI
from datetime import datetime, timedelta

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
YF_PERIOD   = "2y"    # 2 years of weekly bars = ~104 candles, well above minimum 36


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
# Pulled directly from the consolidated holdings report (direct-equity /
# ETF / crypto lines that have a tradeable ticker; mutual funds and debt
# funds without a listed ticker are not scanned).
HOLDINGS_NSE_STOCKS = [
    "HDFCBANK.NS",    # HDFC Bank
    "ICICIBANK.NS",   # ICICI Bank
    "RECLTD.NS",      # REC Ltd
    "CIPLA.NS",       # Cipla
    "RELIANCE.NS",    # Reliance Industries
    "TCS.NS",         # Tata Consultancy Services
    "ONGC.NS",        # ONGC
    "BEL.NS",         # Bharat Electronics
    "ASHOKLEY.NS",    # Ashok Leyland
    "NTPC.NS",        # NTPC
    "IRFC.NS",        # Indian Railway Finance Corp
]

HOLDINGS_US_STOCKS = [
    "VOO",            # Vanguard S&P 500 ETF
    "GOOGL",          # Alphabet Inc Class A
    "EEM",            # iShares MSCI Emerging Markets ETF
    "VTWO",           # Vanguard Russell 2000 ETF
    "NVDA",           # NVIDIA Corporation
    "AMZN",           # Amazon.com Inc
    "MRK",            # Merck & Co Inc
    "MSFT",           # Microsoft Corporation
    "IYH",            # iShares US Healthcare ETF
    "META",           # Meta Platforms Inc Class A
    "NFLX",           # Netflix Inc
    "V",              # Visa Inc
    "ABBV",           # AbbVie Inc
    "BRK-B",          # Berkshire Hathaway Inc Class B
    "TSLA",           # Tesla Inc
    "AAPL",           # Apple Inc
    "ACN",            # Accenture PLC
    "JNJ",            # Johnson & Johnson
]

HOLDINGS_CRYPTO = [
    "bitcoin",        # BTC
    "ethereum",       # ETH
    "ripple",         # XRP
]

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

# ── Crypto (CoinGecko IDs) — full universe ─────────────────────────────────────
CRYPTO_IDS_ALL = [
    "bitcoin",    "ethereum",    "binancecoin",  "solana",
    "ripple",     "cardano",     "dogecoin",     "avalanche-2",
    "chainlink",  "polkadot",
]

# ── Monthly-only universe = full universe minus what's already in holdings ────
MONTHLY_NSE_STOCKS    = [t for t in NSE_STOCKS_ALL if t not in HOLDINGS_NSE_STOCKS]
MONTHLY_NASDAQ_STOCKS = [t for t in NASDAQ_100_ALL if t not in HOLDINGS_US_STOCKS]
MONTHLY_CRYPTO_IDS    = [c for c in CRYPTO_IDS_ALL if c not in HOLDINGS_CRYPTO]

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
}

CRYPTO_LABELS = {
    "BITCOIN": "Bitcoin (BTC)",       "ETHEREUM": "Ethereum (ETH)",
    "BINANCECOIN": "BNB (BNB)",       "SOLANA": "Solana (SOL)",
    "RIPPLE": "XRP (XRP)",            "CARDANO": "Cardano (ADA)",
    "DOGECOIN": "Dogecoin (DOGE)",    "AVALANCHE": "Avalanche (AVAX)",
    "CHAINLINK": "Chainlink (LINK)",  "POLKADOT": "Polkadot (DOT)",
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


def fetch_crypto(crypto_ids: list, label: str = "Crypto") -> dict:
    """
    CoinGecko: days=365 auto-selects weekly OHLC granularity.
    Returns {SYMBOL: K_value}.
    """
    cg      = CoinGeckoAPI()
    results = {}
    log.info(f"── {label}: {len(crypto_ids)} coins  [Weekly via CoinGecko 365d]")
    for coin_id in crypto_ids:
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
    log.info(f"  ✅ {label} done — {len(results)}/{len(crypto_ids)} ok")
    return results


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


def build_message(triggered: dict, total_scanned: int, run_type: str) -> str:
    now = datetime.utcnow().strftime("%d %b %Y")

    sections = {
        "🇮🇳 Indian Indices":    {},
        "🌍 World Indices":      {},
        "🇺🇸 US Indices":        {},
        "💼 Portfolio Holdings": {},
    }

    for sym, k in triggered.items():
        if sym in set(INDIAN_INDICES):
            sections["🇮🇳 Indian Indices"][sym] = k
        elif sym in set(WORLD_INDICES):
            sections["🌍 World Indices"][sym] = k
        elif sym in set(US_INDICES):
            sections["🇺🇸 US Indices"][sym] = k
        else:
            # everything else in this run is a portfolio holding
            sections["💼 Portfolio Holdings"][sym] = k

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
        f"🟡 K 5–{int(STOCH_RSI_THRESHOLD)}  →  Oversold zone (weekly)",
        "💡 <i>Weekly signals = higher conviction. Confirm before entry.</i>",
    ]
    return "\n".join(lines)


def build_monthly_message(hits: dict, month_key: str, total_universe: int) -> str:
    month_label = datetime.strptime(month_key, "%Y-%m").strftime("%B %Y")
    lines = [
        f"🗓️ <b>Monthly Watchlist Scan — {month_label}</b>",
        f"K ≤ <b>{int(STOCH_RSI_THRESHOLD)}</b> at any point this month  "
        f"|  Universe: <b>{total_universe}</b> non-portfolio instruments",
        "",
    ]

    if not hits:
        lines.append("✅ <b>No oversold dips this month.</b>")
        lines.append("Nothing outside your portfolio crossed the threshold in any weekly check.")
    else:
        lines.append(f"<b>🔔 {len(hits)} instrument(s) dipped under K = {int(STOCH_RSI_THRESHOLD)} this month</b>")
        for sym, info in sorted(hits.items(), key=lambda x: x[1]["k"]):
            icon = "🟢" if info["k"] <= 5 else "🟡"
            lines.append(
                f"  {icon} {get_label(sym)}  →  lowest K = <b>{info['k']}</b>  "
                f"(seen {info['date']})"
            )

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
    weekly_total = (len(INDIAN_INDICES) + len(WORLD_INDICES) + len(US_INDICES)
                     + len(HOLDINGS_NSE_STOCKS) + len(HOLDINGS_US_STOCKS)
                     + len(HOLDINGS_CRYPTO))

    log.info("=" * 60)
    log.info("  StochRSI Scanner v5")
    log.info(f"  Run type   : {run_type.upper()}")
    log.info(f"  Threshold  : K ≤ {STOCH_RSI_THRESHOLD}")
    log.info(f"  Interval   : {YF_INTERVAL}  |  Period: {YF_PERIOD}")
    log.info(f"  Weekly instruments: {weekly_total} (indices + holdings)")
    log.info(f"  yfinance   : {yf.__version__}")
    log.info("=" * 60)

    weekly_k: dict = {}
    weekly_k.update(fetch_yfinance(INDIAN_INDICES, "Indian Indices"))
    weekly_k.update(fetch_yfinance(WORLD_INDICES,  "World Indices"))
    weekly_k.update(fetch_yfinance(US_INDICES,     "US Indices"))
    weekly_k.update(fetch_yfinance(HOLDINGS_NSE_STOCKS, "Portfolio — NSE Stocks"))
    weekly_k.update(fetch_yfinance(HOLDINGS_US_STOCKS,  "Portfolio — US Stocks"))
    weekly_k.update(fetch_crypto(HOLDINGS_CRYPTO, "Portfolio — Crypto"))

    weekly_triggered = {s: k for s, k in weekly_k.items() if k <= STOCH_RSI_THRESHOLD}

    log.info("=" * 60)
    log.info(f"  Weekly scanned   : {len(weekly_k)}")
    log.info(f"  Weekly triggered : {len(weekly_triggered)}")
    log.info("=" * 60)

    send_telegram(build_message(weekly_triggered, total_scanned=len(weekly_k), run_type=run_type))

    # ── 2) MONTHLY TRACK — everything else, checked weekly, reported monthly ──
    month_key = today.strftime("%Y-%m")
    state = load_state(month_key)

    monthly_total = (len(MONTHLY_NSE_STOCKS) + len(MONTHLY_NASDAQ_STOCKS)
                      + len(MONTHLY_CRYPTO_IDS))
    log.info(f"  Monthly watchlist universe: {monthly_total} instruments (checked weekly)")

    monthly_k: dict = {}
    monthly_k.update(fetch_yfinance(MONTHLY_NSE_STOCKS, "Monthly Watch — NSE Stocks"))
    monthly_k.update(fetch_yfinance(MONTHLY_NASDAQ_STOCKS, "Monthly Watch — Nasdaq Stocks"))
    monthly_k.update(fetch_crypto(MONTHLY_CRYPTO_IDS, "Monthly Watch — Crypto"))

    today_str = today.strftime("%d %b")
    for sym, k in monthly_k.items():
        if k <= STOCH_RSI_THRESHOLD:
            existing = state["hits"].get(sym)
            if existing is None or k < existing["k"]:
                state["hits"][sym] = {"k": k, "date": today_str}

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
