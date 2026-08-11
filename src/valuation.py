"""
Layer 1 — Valuation filter (PE percentile / PEG)

Screens out instruments that look "oversold" on the stochastic but are
still historically expensive. An instrument is "cheap" when either:

  - its current trailing PE sits in the bottom PE_PERCENTILE_CUTOFF
    percentile of its OWN trailing-PE history (reconstructed from
    quarterly EPS + weekly price, up to ~5 years where available), OR
  - its PEG ratio < PEG_CUTOFF (used for growth names like NVDA/META
    where PE alone is misleading, or whenever a 5y PE history can't be
    reconstructed reliably)

Not applicable to indices (^-prefixed) or crypto (-USD tickers) — there's
no PE for a basket or a coin, so these always pass Layer 1 untouched and
go straight to the stochastic timing filter.

Data-availability reality check (yfinance/Yahoo free tier): quarterly
EPS history is frequently short — especially for NSE-listed names —
often nowhere near a full 5 years. Rather than silently dropping every
instrument we can't get clean history for, get_valuation_signal() falls
back to PEG, and if even that's missing, PASSES THE INSTRUMENT THROUGH
with method="no_data_passthrough" — logged so it's auditable, not a
silent exclusion. Tighten MIN_QUARTERS_FOR_PE_HISTORY if you'd rather be
stricter (and accept more passthroughs as a result).
"""

import logging
import pandas as pd
import yfinance as yf

log = logging.getLogger(__name__)

PE_PERCENTILE_CUTOFF        = 30.0   # bottom 30th percentile of own PE history
PEG_CUTOFF                  = 1.0
MIN_QUARTERS_FOR_PE_HISTORY = 8      # need ~2y of quarterly EPS before trusting a percentile
MIN_PE_HISTORY_POINTS       = 20     # min weekly PE data points after merge, to trust the percentile


def is_valuation_exempt(ticker: str) -> bool:
    """Indices and crypto have no PE — Layer 1 doesn't apply to them."""
    return ticker.startswith("^") or ticker.endswith("-USD")


def _trailing_eps_series(ticker_obj: yf.Ticker) -> pd.Series | None:
    """
    Trailing-12m EPS time series (rolling 4-quarter sum of diluted/basic
    EPS), indexed by quarterly report date. None if too little history.
    """
    try:
        qfin = ticker_obj.quarterly_income_stmt
    except Exception:
        return None
    if qfin is None or qfin.empty:
        return None

    row = None
    for candidate in ("Diluted EPS", "Basic EPS"):
        if candidate in qfin.index:
            row = qfin.loc[candidate]
            break
    if row is None:
        return None

    row = row.dropna().sort_index()
    if len(row) < MIN_QUARTERS_FOR_PE_HISTORY:
        return None

    ttm_eps = row.rolling(4).sum().dropna()
    if len(ttm_eps) < MIN_QUARTERS_FOR_PE_HISTORY - 3:  # rolling(4) consumes 3 points
        return None
    return ttm_eps


def _pe_percentile_signal(t: yf.Ticker, trailing_pe: float) -> dict | None:
    """Attempt the PE-percentile method. Returns None if data is too thin."""
    ttm_eps = _trailing_eps_series(t)
    if ttm_eps is None:
        return None

    try:
        price = t.history(period="5y", interval="1wk", auto_adjust=True)["Close"].dropna()
    except Exception:
        return None
    if len(price) < MIN_PE_HISTORY_POINTS:
        return None

    price.index = pd.to_datetime(price.index)
    if price.index.tz is not None:
        price.index = price.index.tz_localize(None)

    eps_df = ttm_eps.to_frame("eps")
    eps_df.index = pd.to_datetime(eps_df.index)
    if eps_df.index.tz is not None:
        eps_df.index = eps_df.index.tz_localize(None)

    merged = pd.merge_asof(
        price.sort_index().to_frame("close").reset_index().rename(columns={"index": "date"}),
        eps_df.sort_index().reset_index().rename(columns={"index": "date"}),
        on="date", direction="backward",
    )
    merged = merged.dropna(subset=["eps"])
    merged = merged[merged["eps"] > 0]   # PE undefined/meaningless with negative TTM EPS
    if len(merged) < MIN_PE_HISTORY_POINTS:
        return None

    pe_hist    = merged["close"] / merged["eps"]
    percentile = float((pe_hist < trailing_pe).mean() * 100)
    cheap      = percentile <= PE_PERCENTILE_CUTOFF
    return {
        "cheap": cheap,
        "method": "pe_percentile",
        "percentile": round(percentile, 1),
        "detail": f"PE {trailing_pe:.1f} = {percentile:.0f}th pct of own {len(pe_hist)}-wk history",
    }


def get_valuation_signal(ticker: str) -> dict:
    """
    Returns:
      {
        "cheap":  bool,
        "method": "pe_percentile" | "peg" | "no_data_passthrough" | "not_applicable",
        "detail": str,   # short human-readable summary for Telegram messages
      }
    """
    if is_valuation_exempt(ticker):
        return {"cheap": True, "method": "not_applicable", "detail": "index/crypto — no PE"}

    try:
        t    = yf.Ticker(ticker)
        info = t.info or {}
    except Exception as e:
        log.warning(f"  ⚠ {ticker}: valuation info fetch failed ({e}) — passing through")
        return {"cheap": True, "method": "no_data_passthrough", "detail": f"info fetch failed: {e}"}

    trailing_pe = info.get("trailingPE")
    peg = info.get("trailingPegRatio")
    if peg is None:
        peg = info.get("pegRatio")

    if trailing_pe is not None and trailing_pe > 0:
        pe_signal = _pe_percentile_signal(t, trailing_pe)
        if pe_signal is not None:
            return pe_signal

    if peg is not None:
        cheap = peg < PEG_CUTOFF
        return {"cheap": cheap, "method": "peg", "detail": f"PEG {peg:.2f} (insufficient 5y PE history)"}

    log.warning(f"  ⚠ {ticker}: no usable PE/PEG data — passing through Layer 1")
    return {"cheap": True, "method": "no_data_passthrough", "detail": "no PE/PEG data — Layer 1 skipped"}
