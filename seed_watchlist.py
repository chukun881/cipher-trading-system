#!/usr/bin/env python3
"""
seed_watchlist.py — Seed 50 US swing-trading stocks into the watchlist.

Run once to populate the database with a diversified set of large/mid-cap
US equities across all major sectors.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db import TradingDB
from watchlist_manager import add_tickers_batch

# ── Curated ticker list (50 stocks, 8 sector groups) ────────────────────────

TICKERS: dict[str, list[str]] = {
    "Technology": [
        "AAPL", "MSFT", "NVDA", "AMD", "GOOG", "META",
        "CRM", "ORCL", "ADBE", "INTC", "QCOM", "MU",
    ],
    "Healthcare": [
        "JNJ", "UNH", "PFE", "LLY", "ABBV", "MRK", "TMO", "AMGN",
    ],
    "Financials": [
        "JPM", "BAC", "GS", "MS", "V", "MA", "BLK",
    ],
    "Consumer": [
        "AMZN", "TSLA", "NKE", "SBUX", "COST", "WMT", "TGT",
    ],
    "Industrials": [
        "CAT", "BA", "GE", "HON", "UNP",
    ],
    "Energy": [
        "XOM", "CVX", "SLB", "OXY",
    ],
    "Communication Services": [
        "DIS", "NFLX", "CMCSA", "TMUS",
    ],
    "Other": [
        "LIN", "SHW", "CB",
    ],
}

REASON = "Initial seed - 50 US swing candidates"

# Telegram credentials
TELEGRAM_BOT_TOKEN = "814524…G4qw"
TELEGRAM_CHAT_ID = "902084713"


def main() -> None:
    # Flatten ticker list
    all_tickers: list[str] = []
    for sector, tickers in TICKERS.items():
        all_tickers.extend(tickers)

    print(f"🔐 Seed Watchlist — {len(all_tickers)} tickers across {len(TICKERS)} sectors")
    print("=" * 60)
    for sector, tickers in TICKERS.items():
        print(f"  {sector} ({len(tickers)}): {', '.join(tickers)}")
    print("=" * 60)

    db = TradingDB()

    # Store Telegram credentials in settings
    db.set_setting("telegram_bot_token", TELEGRAM_BOT_TOKEN)
    db.set_setting("telegram_chat_id", TELEGRAM_CHAT_ID)
    print("✅ Telegram credentials stored in settings.\n")

    # Seed tickers
    print("🚀 Seeding tickers (this will take ~2 minutes)...\n")
    start = time.time()
    result = add_tickers_batch(all_tickers, reason=REASON, db=db)
    elapsed = time.time() - start

    # ── Results ──────────────────────────────────────────────────────────────
    added = result.get("added", [])
    skipped = result.get("skipped", [])
    errors = result.get("errors", [])

    print("\n" + "=" * 60)
    print("📊 SEED RESULTS")
    print("=" * 60)
    print(f"  Added:   {len(added)}")
    print(f"  Skipped: {len(skipped)}")
    print(f"  Errors:  {len(errors)}")
    print(f"  Time:    {elapsed:.1f}s")

    if added:
        print(f"\n✅ Added tickers ({len(added)}):")
        for r in added:
            t = r.get("ticker", "?")
            df = "✓" if r.get("data_fetched") else "✗"
            ic = "✓" if r.get("indicators_computed") else "✗"
            print(f"  {t:6s}  data={df}  indicators={ic}")

    if skipped:
        print(f"\n⏭️  Skipped (already active): {', '.join(skipped)}")

    if errors:
        print(f"\n❌ Failed tickers ({len(errors)}):")
        for e in errors:
            print(f"  {e.get('ticker', '?'):6s} — {e.get('error', 'unknown')}")

    # ── Verification ─────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("📋 VERIFICATION")
    print("=" * 60)

    active = db.get_active_tickers()
    print(f"  Active tickers in watchlist: {len(active)}")
    print(f"  {'Ticker':<8s} {'Latest Date':<14s} {'Rows':>6s}")
    print(f"  {'------':<8s} {'------------':<14s} {'-----':>6s}")

    for ticker in sorted(active):
        latest = db.get_latest_date(ticker) or "NO DATA"
        all_ohlcv = db.get_ohlcv(ticker)
        rows = len(all_ohlcv)
        print(f"  {ticker:<8s} {latest:<14s} {rows:>6d}")

    db.close()
    print("\n🔐 Seeding complete.")


if __name__ == "__main__":
    main()
