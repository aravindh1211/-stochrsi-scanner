"""
One-off retrospective check — StochRSI Weekly Scanner (v7, two-layer)
Run manually: `python scripts/backfill_4weeks.py`

Runs the SAME two layers as the live scanner, in the same order:

  Layer 1 — Valuation (current snapshot; doesn't change per historical
            week, so it's computed once per ticker, not once per week)
  Layer 2 — Weekly Stoch RSI %K/%D crossover (21,5,5), confirmed on a
            closed weekly bar, replayed across each of the last N
            closed bars — not just the latest one — so you can see
            what WOULD have fired each of the past N Fridays even
            though the bot only started running now.

Sends the results as one or more Telegram messages. Telegram caps a
single message at 4096 characters; even with Layer 1 cutting the
universe down substantially, a wide lookback can still produce more
hits than fit in one message, so results are chunked by week (and
further within a week if even one week's hits alone exceed the limit).

Does NOT touch state/monthly_state.json or state/weekly_log.json —
this is a read-only, one-time lookback, independent of the regular
weekly/monthly cadence.
"""

import os
import sys
from datetime import datetime

# Make src/ importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import yfinance as yf  # noqa: E402
import pandas as pd  # noqa: E402

from scanner import (  # noqa: E402
    INDIAN_INDICES, WORLD_INDICES, US_INDICES,
    HOLDINGS_NSE_STOCKS, HOLDINGS_US_STOCKS, HOLDINGS_CRYPTO,
    STOCH_RSI_THRESHOLD, STOCH_RSI_LENGTH, STOCH_LENGTH, SMOOTH_K, SMOOTH_D,
    calc_stoch_rsi_kd_series, drop_incomplete_last_bar,
    get_label, send_telegram,
    YF_PERIOD, YF_INTERVAL,
)
from valuation import get_valuation_signal  # noqa: E402

LOOKBACK_WEEKS = int(os.environ.get("LOOKBACK_WEEKS", "4"))

# Telegram's hard cap is 4096 chars per message. Leave headroom for the
# per-chunk header/footer we add on top of the packed week lines.
TELEGRAM_CHAR_LIMIT = 3500


def find_kd_crossovers_over_lookback(k_series: pd.Series, d_series: pd.Series, weeks: int) -> list:
    """
    Replays the %K/%D cross-up-below-threshold condition bar-by-bar over
    the last `weeks` closed bars, using only data available up to and
    including each bar (no lookahead).
    """
    if k_series is None or d_series is None or len(k_series) < weeks + 1:
        return []

    hits = []
    for i in range(-weeks, 0):
        curr_k, prev_k = round(float(k_series.iloc[i]), 2), round(float(k_series.iloc[i - 1]), 2)
        curr_d, prev_d = round(float(d_series.iloc[i]), 2), round(float(d_series.iloc[i - 1]), 2)
        crossed_up = prev_k <= prev_d and curr_k > curr_d
        both_below = curr_k < STOCH_RSI_THRESHOLD and curr_d < STOCH_RSI_THRESHOLD
        if crossed_up and both_below:
            hits.append({
                "date": k_series.index[i].strftime("%d %b %Y"),
                "k": curr_k, "d": curr_d, "prev_k": prev_k, "prev_d": prev_d,
            })
    return hits


def scan_group(tickers: list, label: str, weeks: int) -> dict:
    """
    Layer 1 (valuation, once per ticker) -> Layer 2 (K/D crossover,
    replayed over the lookback window). Tickers failing Layer 1 are
    skipped before any stochastic work is done.
    """
    results = {}
    print(f"-- {label}: {len(tickers)} tickers")
    for ticker in tickers:
        try:
            val = get_valuation_signal(ticker)
            if not val["cheap"]:
                print(f"  X {ticker}: Layer 1 fail - {val['detail']}")
                continue

            df = yf.Ticker(ticker).history(period=YF_PERIOD, interval=YF_INTERVAL, auto_adjust=True)
            if df is None or df.empty:
                print(f"  ! {ticker}: no price data")
                continue
            df = drop_incomplete_last_bar(df)
            if len(df) < 57 + weeks:
                print(f"  ! {ticker}: only {len(df)} closed weekly bars (need {57 + weeks}+)")
                continue

            close = df["Close"].squeeze().dropna()
            k_series, d_series = calc_stoch_rsi_kd_series(
                close, STOCH_RSI_LENGTH, STOCH_LENGTH, SMOOTH_K, SMOOTH_D,
            )
            hits = find_kd_crossovers_over_lookback(k_series, d_series, weeks)
            if hits:
                for h in hits:
                    h["valuation_detail"] = val["detail"]
                results[ticker] = hits
                print(f"  * {ticker}: {len(hits)} trigger(s) in lookback  [{val['detail']}]")
        except Exception as e:
            print(f"  X {ticker}: {e}")
    return results


def build_backfill_messages(all_hits: dict, weeks: int) -> list:
    """
    Returns a list of message strings, each under TELEGRAM_CHAR_LIMIT,
    chunked by week -- a week's hits are never split across messages
    unless that single week alone exceeds the limit, in which case it's
    split further within the week.
    """
    now = datetime.utcnow().strftime("%d %b %Y")
    header = (
        f"\U0001F570\uFE0F <b>StochRSI \u2014 {weeks}-Week Retrospective Check</b>  |  run on {now} (UTC)\n"
        f"Layer 1 (valuation, current snapshot) + Layer 2 (%K/%D cross-up below "
        f"{int(STOCH_RSI_THRESHOLD)}, confirmed weekly close) replayed over the last {weeks} bars.\n\n"
    )
    footer = "\n\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n\U0001F4A1 <i>Retrospective only \u2014 not a live signal.</i>"

    if not all_hits:
        return [header + f"\u2705 <b>No triggers found in the last {weeks} weeks</b> across any tracked instrument." + footer]

    by_date: dict = {}
    for sym, hits in all_hits.items():
        for h in hits:
            by_date.setdefault(h["date"], []).append((sym, h))

    dates_sorted = sorted(by_date.keys(), key=lambda d: datetime.strptime(d, "%d %b %Y"))

    week_blocks = []
    for date in dates_sorted:
        block_lines = [f"<b>Week of {date}</b>"]
        for sym, h in sorted(by_date[date], key=lambda x: x[1]["k"]):
            icon = "\U0001F7E2" if h["k"] <= 5 else "\U0001F7E1"
            block_lines.append(
                f"  {icon} {get_label(sym)}  \u2192  K: {h['prev_k']}\u2192<b>{h['k']}</b>  "
                f"D: {h['prev_d']}\u2192{h['d']}  \u2191"
            )
            block_lines.append(f"       <i>{h['valuation_detail']}</i>")
        week_blocks.append(block_lines)

    messages = []
    current_lines: list = []
    current_len = len(header) + len(footer)

    def flush():
        nonlocal current_lines, current_len
        if current_lines:
            messages.append(header + "\n".join(current_lines) + footer)
        current_lines = []
        current_len = len(header) + len(footer)

    for block in week_blocks:
        block_text = "\n".join(block) + "\n"
        if len(block_text) > TELEGRAM_CHAR_LIMIT - current_len and not current_lines:
            sub_lines: list = []
            sub_len = len(header) + len(footer)
            for line in block:
                line_len = len(line) + 1
                if sub_len + line_len > TELEGRAM_CHAR_LIMIT and sub_lines:
                    messages.append(header + "\n".join(sub_lines) + footer)
                    sub_lines, sub_len = [], len(header) + len(footer)
                sub_lines.append(line)
                sub_len += line_len
            if sub_lines:
                messages.append(header + "\n".join(sub_lines) + footer)
            continue

        if current_len + len(block_text) > TELEGRAM_CHAR_LIMIT:
            flush()
        current_lines.append(block_text.rstrip("\n"))
        current_len += len(block_text)

    flush()

    if len(messages) > 1:
        title = f"<b>StochRSI \u2014 {weeks}-Week Retrospective Check</b>"
        messages = [
            m.replace(title, f"{title} (part {i+1}/{len(messages)})", 1)
            for i, m in enumerate(messages)
        ]

    return messages


def main():
    print(f"Running {LOOKBACK_WEEKS}-week retrospective backfill (Layer 1 + Layer 2)...")

    all_hits: dict = {}
    all_hits.update(scan_group(INDIAN_INDICES, "Indian Indices", LOOKBACK_WEEKS))
    all_hits.update(scan_group(WORLD_INDICES, "World Indices", LOOKBACK_WEEKS))
    all_hits.update(scan_group(US_INDICES, "US Indices", LOOKBACK_WEEKS))
    all_hits.update(scan_group(HOLDINGS_NSE_STOCKS, "Portfolio -- NSE Stocks", LOOKBACK_WEEKS))
    all_hits.update(scan_group(HOLDINGS_US_STOCKS, "Portfolio -- US Stocks", LOOKBACK_WEEKS))
    all_hits.update(scan_group(HOLDINGS_CRYPTO, "Portfolio -- Crypto", LOOKBACK_WEEKS))

    messages = build_backfill_messages(all_hits, LOOKBACK_WEEKS)
    print("\n" + "=" * 60)
    for m in messages:
        print(m.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", ""))
        print("-" * 60)
    print("=" * 60)
    print(f"{len(messages)} message(s) to send, lengths: {[len(m) for m in messages]}")

    if os.environ.get("SEND_TELEGRAM", "true").lower() == "true":
        for m in messages:
            send_telegram(m)
    else:
        print("(SEND_TELEGRAM=false -- messages printed above only, not sent)")


if __name__ == "__main__":
    main()
