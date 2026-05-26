"""
watchlist_manager.py — Watchlist lifecycle management for the swing trading system.

Manages adding, dropping, reactivating, stale detection, and cleanup
of watchlist tickers. Integrates with data_fetcher for OHLCV data and
alpha_engine for indicator computation and signal generation.

Depends on:
    - db.py            (TradingDB)
    - data_fetcher.py  (fetch_stock_data, store_stock_data)
    - alpha_engine.py  (compute_indicators, check_stale, analyze_ticker)
"""

from __future__ import annotations

import os
import sys
import time
from datetime import date, datetime, timedelta
from typing import Any, Optional

import pandas as pd
import yfinance as yf

# Ensure local imports work regardless of cwd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import TradingDB
from data_fetcher import fetch_stock_data, store_stock_data
from alpha_engine import compute_indicators, check_stale


# ======================================================================
# 1. add_ticker
# ======================================================================
def add_ticker(
    ticker: str,
    reason: str = "",
    db: TradingDB | None = None,
    fetch_data: bool = True,
) -> dict[str, Any]:
    """Add a ticker to the watchlist with status='active'.

    If the ticker already exists with status='dropped', it is reactivated.
    If it already exists with status='active', returns info without re-adding.

    Args:
        ticker: Stock symbol (e.g. 'AAPL').
        reason: Reason for adding.
        db: TradingDB instance. Created if not provided.
        fetch_data: If True, fetch 1y OHLCV data and compute indicators.

    Returns:
        Dict with ticker, status, date_added, reason, data_fetched, indicators_computed.
    """
    own_db = db is None
    if own_db:
        db = TradingDB()

    try:
        ticker = ticker.upper().strip()
        today = date.today().isoformat()

        # Check existing status
        all_entries = db.get_all_tickers()
        existing = next((e for e in all_entries if e["ticker"] == ticker), None)

        if existing and existing["status"] == "active":
            return {
                "ticker": ticker,
                "status": "active",
                "date_added": existing.get("date_added", today),
                "reason": existing.get("reason", ""),
                "data_fetched": False,
                "indicators_computed": False,
            }

        data_fetched = False
        indicators_computed = False

        # Fetch and compute if requested
        if fetch_data:
            try:
                df = fetch_stock_data(ticker, period="1y")
                if not df.empty:
                    store_stock_data(db, ticker, df)
                    data_fetched = True

                    # Compute indicators
                    ohlcv = db.get_ohlcv(ticker)
                    if ohlcv:
                        pdf = pd.DataFrame(ohlcv)
                        pdf = compute_indicators(pdf)
                        if not pdf.empty:
                            indicator_rows = []
                            for _, row in pdf.iterrows():
                                d: dict[str, Any] = {"date": str(row["date"])[:10]}
                                for col in ("rsi_14", "bb_lower", "bb_mid", "bb_upper", "atr_14", "sma_200", "bb_width"):
                                    if col in pdf.columns:
                                        val = row[col]
                                        d[col] = float(val) if pd.notna(val) else None
                                indicator_rows.append(d)
                            db.upsert_indicators(ticker, indicator_rows)
                            indicators_computed = True
            except Exception as e:
                print(f"  [WARN] Data fetch/indicator computation failed for {ticker}: {e}")

        # Add or reactivate
        if existing and existing["status"] == "dropped":
            db.reactivate_ticker(ticker)
            status = "active"
        else:
            db.add_ticker(ticker, reason=reason)
            status = "active"

        # Refresh entry after add/reactivate
        updated = next((e for e in db.get_all_tickers() if e["ticker"] == ticker), None)

        return {
            "ticker": ticker,
            "status": status,
            "date_added": updated.get("date_added", today) if updated else today,
            "reason": reason or (updated.get("reason", "") if updated else ""),
            "data_fetched": data_fetched,
            "indicators_computed": indicators_computed,
        }
    finally:
        if own_db:
            db.close()


# ======================================================================
# 2. add_tickers_batch
# ======================================================================
def add_tickers_batch(
    tickers: list[str],
    reason: str = "",
    db: TradingDB | None = None,
) -> dict[str, Any]:
    """Add multiple tickers to the watchlist at once.

    Includes a 1-2 second delay between each to avoid rate limiting.

    Args:
        tickers: List of ticker symbols.
        reason: Reason for adding (applied to all).
        db: TradingDB instance. Created if not provided.

    Returns:
        Dict with 'added', 'skipped', and 'errors' lists.
    """
    own_db = db is None
    if own_db:
        db = TradingDB()

    try:
        added: list[dict[str, Any]] = []
        skipped: list[str] = []
        errors: list[dict[str, str]] = []

        for i, t in enumerate(tickers):
            try:
                result = add_ticker(t, reason=reason, db=db, fetch_data=True)
                if result.get("data_fetched") or result.get("status") == "active":
                    # Check if it was skipped (already active, no data fetched)
                    all_entries = db.get_all_tickers()
                    existing = next((e for e in all_entries if e["ticker"] == t.upper().strip()), None)
                    if existing and existing["status"] == "active" and not result.get("data_fetched"):
                        skipped.append(t.upper().strip())
                    else:
                        added.append(result)
                else:
                    # Added but data not fetched — still counts as added
                    added.append(result)
            except Exception as e:
                errors.append({"ticker": t.upper().strip(), "error": str(e)})

            # Rate limit delay (skip after last)
            if i < len(tickers) - 1:
                time.sleep(1.5)

        return {"added": added, "skipped": skipped, "errors": errors}
    finally:
        if own_db:
            db.close()


# ======================================================================
# 3. drop_ticker
# ======================================================================
def drop_ticker(
    ticker: str,
    reason: str = "",
    db: TradingDB | None = None,
) -> dict[str, Any]:
    """Set a ticker's status to 'dropped'.

    Does NOT delete historical data (OHLCV, indicators stay in DB).
    If the ticker has an open holding, warns but allows the drop.

    Args:
        ticker: Stock symbol.
        reason: Reason for dropping. Auto-populated as 'Manual drop' if empty.
        db: TradingDB instance. Created if not provided.

    Returns:
        Dict with ticker, status, date_dropped, reason.
    """
    own_db = db is None
    if own_db:
        db = TradingDB()

    try:
        ticker = ticker.upper().strip()
        today = date.today().isoformat()

        if not reason:
            reason = "Manual drop"

        # Check if ticker exists in watchlist
        all_entries = db.get_all_tickers()
        existing = next((e for e in all_entries if e["ticker"] == ticker), None)

        if not existing:
            return {
                "ticker": ticker,
                "status": "not_found",
                "date_dropped": None,
                "reason": f"Ticker {ticker} not found in watchlist",
            }

        if existing["status"] == "dropped":
            return {
                "ticker": ticker,
                "status": "dropped",
                "date_dropped": existing.get("date_dropped", today),
                "reason": f"Ticker {ticker} is already dropped",
            }

        # Warn if open holding exists
        holding = db.get_holding(ticker)
        if holding:
            print(f"  [WARN] {ticker} has an open holding (id={holding['id']}). Dropping anyway — user may have sold externally.")

        db.remove_ticker(ticker)

        return {
            "ticker": ticker,
            "status": "dropped",
            "date_dropped": today,
            "reason": reason,
        }
    finally:
        if own_db:
            db.close()


# ======================================================================
# 4. reactivate_ticker
# ======================================================================
def reactivate_ticker(
    ticker: str,
    db: TradingDB | None = None,
    fetch_data: bool = True,
) -> dict[str, Any]:
    """Reactivate a dropped ticker back to 'active'.

    If fetch_data=True, checks for stale data (latest date > 5 days ago)
    and refreshes if needed, then recomputes indicators.

    Args:
        ticker: Stock symbol.
        db: TradingDB instance. Created if not provided.
        fetch_data: If True, refresh stale data and recompute indicators.

    Returns:
        Dict with ticker, status, data_refreshed, indicators_computed.
    """
    own_db = db is None
    if own_db:
        db = TradingDB()

    try:
        ticker = ticker.upper().strip()

        # Check if ticker exists and is dropped
        all_entries = db.get_all_tickers()
        existing = next((e for e in all_entries if e["ticker"] == ticker), None)

        if not existing:
            return {
                "ticker": ticker,
                "status": "not_found",
                "data_refreshed": False,
                "indicators_computed": False,
            }

        if existing["status"] == "active":
            return {
                "ticker": ticker,
                "status": "active",
                "data_refreshed": False,
                "indicators_computed": False,
            }

        # Reactivate
        db.reactivate_ticker(ticker)

        data_refreshed = False
        indicators_computed = False

        if fetch_data:
            try:
                # Check if existing data is stale
                latest = db.get_latest_date(ticker)
                needs_refresh = True

                if latest:
                    latest_dt = datetime.strptime(latest[:10], "%Y-%m-%d").date()
                    days_behind = (date.today() - latest_dt).days
                    if days_behind <= 5:
                        needs_refresh = False

                if needs_refresh:
                    df = fetch_stock_data(ticker, period="1y")
                    if not df.empty:
                        store_stock_data(db, ticker, df)
                        data_refreshed = True

                # Recompute indicators
                ohlcv = db.get_ohlcv(ticker)
                if ohlcv:
                    pdf = pd.DataFrame(ohlcv)
                    pdf = compute_indicators(pdf)
                    if not pdf.empty:
                        indicator_rows = []
                        for _, row in pdf.iterrows():
                            d: dict[str, Any] = {"date": str(row["date"])[:10]}
                            for col in ("rsi_14", "bb_lower", "bb_mid", "bb_upper", "atr_14", "sma_200", "bb_width"):
                                if col in pdf.columns:
                                    val = row[col]
                                    d[col] = float(val) if pd.notna(val) else None
                            indicator_rows.append(d)
                        db.upsert_indicators(ticker, indicator_rows)
                        indicators_computed = True

            except Exception as e:
                print(f"  [WARN] Data refresh failed for {ticker}: {e}")

        return {
            "ticker": ticker,
            "status": "active",
            "data_refreshed": data_refreshed,
            "indicators_computed": indicators_computed,
        }
    finally:
        if own_db:
            db.close()


# ======================================================================
# 5. check_stale_all
# ======================================================================
def check_stale_all(db: TradingDB | None = None) -> dict[str, Any]:
    """Check all active tickers for stale status and auto-drop qualifying ones.

    Uses alpha_engine.check_stale() for each ticker. Only drops tickers
    where should_drop=True. Skips tickers with open holdings.

    Args:
        db: TradingDB instance. Created if not provided.

    Returns:
        Dict with 'checked', 'stale', 'dropped', 'healthy' lists.
    """
    own_db = db is None
    if own_db:
        db = TradingDB()

    try:
        tickers = db.get_active_tickers()
        stale: list[dict[str, Any]] = []
        dropped: list[dict[str, Any]] = []
        healthy: list[str] = []

        for ticker in tickers:
            stale_result = check_stale(ticker, db)

            if stale_result.get("should_drop"):
                # Build stale info
                reasons = []
                if stale_result.get("is_stale"):
                    reasons.append("stale")
                if stale_result.get("below_sma200"):
                    reasons.append("below_sma200")

                stale_info = {
                    "ticker": ticker,
                    "stale_days": stale_result.get("stale_days", 0),
                    "below_sma200": stale_result.get("below_sma200", False),
                    "reason": ", ".join(reasons) if reasons else "unknown",
                }

                # Check for open holding — skip drop if holding exists
                holding = db.get_holding(ticker)
                if holding:
                    stale_info["reason"] += " [SKIPPED: has open holding]"
                    stale.append(stale_info)
                else:
                    # Auto-drop
                    reason_str = f"Auto-dropped: {', '.join(reasons)}"
                    db.remove_ticker(ticker)
                    dropped.append({
                        "ticker": ticker,
                        "reason": reason_str,
                    })
            else:
                healthy.append(ticker)

        return {
            "checked": len(tickers),
            "stale": stale,
            "dropped": dropped,
            "healthy": healthy,
        }
    finally:
        if own_db:
            db.close()


# ======================================================================
# 6. get_watchlist_status
# ======================================================================
def get_watchlist_status(db: TradingDB | None = None) -> dict[str, Any]:
    """Return comprehensive watchlist overview with latest indicator values.

    Args:
        db: TradingDB instance. Created if not provided.

    Returns:
        Dict with 'active' list (enriched with indicators), 'dropped' list,
        'total_active', 'total_dropped', and 'data_status'.
    """
    own_db = db is None
    if own_db:
        db = TradingDB()

    try:
        all_entries = db.get_all_tickers()
        today = date.today()

        active: list[dict[str, Any]] = []
        dropped: list[dict[str, Any]] = []
        stale_count = 0
        no_data_count = 0

        for entry in all_entries:
            if entry["status"] == "active":
                # Enrich with latest data info
                latest_date = db.get_latest_date(entry["ticker"])
                days_behind = None
                latest_rsi = None
                latest_signal = None
                days_active = None

                if entry.get("date_added"):
                    try:
                        added_dt = datetime.strptime(entry["date_added"], "%Y-%m-%d").date()
                        days_active = len(pd.bdate_range(added_dt, today))
                    except ValueError:
                        pass

                if latest_date:
                    try:
                        latest_dt = datetime.strptime(latest_date[:10], "%Y-%m-%d").date()
                        days_behind = len(pd.bdate_range(latest_dt + timedelta(days=1), today))
                    except ValueError:
                        pass

                # Get latest indicators
                latest_ind = db.get_latest_indicators(entry["ticker"])
                if latest_ind:
                    latest_rsi = latest_ind.get("rsi_14")

                # Get latest signal
                sigs = db.get_signals(ticker=entry["ticker"])
                if sigs:
                    latest_signal = sigs[0].get("signal_type")

                if latest_date is None:
                    no_data_count += 1
                elif days_behind is not None and days_behind > 5:
                    stale_count += 1

                active.append({
                    "ticker": entry["ticker"],
                    "date_added": entry.get("date_added"),
                    "reason": entry.get("reason", ""),
                    "latest_date": latest_date,
                    "days_behind": days_behind,
                    "latest_rsi": latest_rsi,
                    "latest_signal": latest_signal,
                    "days_active": days_active,
                })

            elif entry["status"] == "dropped":
                dropped.append({
                    "ticker": entry["ticker"],
                    "date_added": entry.get("date_added"),
                    "date_dropped": entry.get("date_dropped"),
                    "reason": entry.get("reason", ""),
                })

        # Determine overall data status
        total_active = len(active)
        if total_active == 0:
            data_status = "no_data"
        elif no_data_count == total_active:
            data_status = "no_data"
        elif stale_count > total_active / 2:
            data_status = "stale"
        else:
            data_status = "fresh"

        return {
            "active": active,
            "dropped": dropped,
            "total_active": total_active,
            "total_dropped": len(dropped),
            "data_status": data_status,
        }
    finally:
        if own_db:
            db.close()


# ======================================================================
# 7. search_ticker
# ======================================================================
def search_ticker(query: str) -> dict[str, Any]:
    """Validate if a ticker exists on yfinance and return basic info.

    Args:
        query: Ticker symbol to look up.

    Returns:
        Dict with ticker, name, price, sector, market_cap, valid.
    """
    ticker = query.upper().strip()

    try:
        info = yf.Ticker(ticker).info

        # If yfinance returns an empty or minimal info dict, consider it invalid
        if not info or (not info.get("regularMarketPrice") and not info.get("currentPrice")):
            return {
                "ticker": ticker,
                "name": None,
                "price": None,
                "sector": None,
                "market_cap": None,
                "valid": False,
            }

        price = info.get("regularMarketPrice") or info.get("currentPrice")
        name = info.get("shortName") or info.get("longName")
        sector = info.get("sector")
        market_cap = info.get("marketCap")

        return {
            "ticker": ticker,
            "name": name,
            "price": round(price, 2) if price else None,
            "sector": sector,
            "market_cap": market_cap,
            "valid": True,
        }
    except Exception as e:
        print(f"  [WARN] search_ticker failed for {ticker}: {e}")
        return {
            "ticker": ticker,
            "name": None,
            "price": None,
            "sector": None,
            "market_cap": None,
            "valid": False,
        }


# ======================================================================
# 8. cleanup_dropped_data
# ======================================================================
def cleanup_dropped_data(
    db: TradingDB | None = None,
    older_than_days: int = 90,
) -> dict[str, Any]:
    """Delete OHLCV and indicator data for dropped tickers older than N days.

    Keeps the watchlist entry for history but frees storage.

    Args:
        db: TradingDB instance. Created if not provided.
        older_than_days: Minimum days since drop before cleanup.

    Returns:
        Dict with 'cleaned' list of tickers and 'rows_deleted' count.
    """
    own_db = db is None
    if own_db:
        db = TradingDB()

    try:
        all_entries = db.get_all_tickers()
        today = date.today()
        cutoff = today - timedelta(days=older_than_days)
        cleaned: list[str] = []
        rows_deleted = 0

        for entry in all_entries:
            if entry["status"] != "dropped":
                continue

            date_dropped = entry.get("date_dropped")
            if not date_dropped:
                continue

            try:
                drop_dt = datetime.strptime(date_dropped, "%Y-%m-%d").date()
            except ValueError:
                continue

            if drop_dt >= cutoff:
                continue

            ticker = entry["ticker"]

            # Delete OHLCV data
            cur = db.conn.execute("DELETE FROM ohlcv WHERE ticker=?", (ticker,))
            rows_deleted += cur.rowcount

            # Delete indicator data
            cur = db.conn.execute("DELETE FROM daily_indicators WHERE ticker=?", (ticker,))
            rows_deleted += cur.rowcount

            db.conn.commit()
            cleaned.append(ticker)

        return {"cleaned": cleaned, "rows_deleted": rows_deleted}
    finally:
        if own_db:
            db.close()


# ======================================================================
# Self-test
# ======================================================================
if __name__ == "__main__":
    import tempfile

    print("=" * 60)
    print("watchlist_manager.py — Self-test")
    print("=" * 60)

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_path = tmp.name
    tmp.close()

    print(f"\nTest DB: {tmp_path}")
    db = TradingDB(db_path=tmp_path)

    try:
        # --- Test 1: search_ticker (valid + invalid) ---
        print("\n--- Test 1: search_ticker ---")
        aapl_info = search_ticker("AAPL")
        print(f"  AAPL: valid={aapl_info['valid']}, name={aapl_info.get('name')}, "
              f"price={aapl_info.get('price')}, sector={aapl_info.get('sector')}")
        # Don't assert valid=True as network may be unavailable
        if aapl_info["valid"]:
            assert aapl_info["price"] is not None

        bad_info = search_ticker("INVALIDTICKERXYZ")
        print(f"  INVALIDTICKERXYZ: valid={bad_info['valid']}")
        assert bad_info["valid"] is False

        # --- Test 2: add_ticker ---
        print("\n--- Test 2: add_ticker (AAPL) ---")
        result = add_ticker("AAPL", reason="test add", db=db, fetch_data=True)
        print(f"  Result: {result}")
        assert result["ticker"] == "AAPL"
        assert result["status"] == "active"
        # Data fetch may fail in tests without network — don't assert data_fetched

        # Test adding duplicate (should skip)
        result2 = add_ticker("AAPL", reason="duplicate test", db=db, fetch_data=False)
        print(f"  Duplicate: status={result2['status']}, data_fetched={result2['data_fetched']}")
        assert result2["status"] == "active"
        assert result2["data_fetched"] is False

        # Test case normalization
        result3 = add_ticker("  aapl  ", reason="case test", db=db, fetch_data=False)
        assert result3["ticker"] == "AAPL"

        # --- Test 3: get_watchlist_status ---
        print("\n--- Test 3: get_watchlist_status ---")
        status = get_watchlist_status(db=db)
        print(f"  Active: {status['total_active']}")
        print(f"  Dropped: {status['total_dropped']}")
        print(f"  Data status: {status['data_status']}")
        assert status["total_active"] >= 1
        for a in status["active"]:
            print(f"    {a['ticker']}: added={a['date_added']}, days_active={a['days_active']}, "
                  f"latest_date={a['latest_date']}, rsi={a['latest_rsi']}")

        # --- Test 4: drop_ticker ---
        print("\n--- Test 4: drop_ticker (AAPL) ---")
        drop_result = drop_ticker("AAPL", reason="test drop", db=db)
        print(f"  Result: {drop_result}")
        assert drop_result["status"] == "dropped"
        assert drop_result["date_dropped"] is not None

        # Verify it's dropped
        status2 = get_watchlist_status(db=db)
        assert status2["total_active"] == 0
        assert status2["total_dropped"] == 1

        # Test dropping non-existent
        drop_bad = drop_ticker("NONEXISTENT", db=db)
        print(f"  Non-existent drop: status={drop_bad['status']}")
        assert drop_bad["status"] == "not_found"

        # --- Test 5: reactivate_ticker ---
        print("\n--- Test 5: reactivate_ticker (AAPL) ---")
        react_result = reactivate_ticker("AAPL", db=db, fetch_data=True)
        print(f"  Result: {react_result}")
        assert react_result["status"] == "active"

        # Verify it's active again
        status3 = get_watchlist_status(db=db)
        assert status3["total_active"] == 1

        # --- Test 6: check_stale_all ---
        print("\n--- Test 6: check_stale_all ---")
        stale_result = check_stale_all(db=db)
        print(f"  Checked: {stale_result['checked']}")
        print(f"  Stale: {len(stale_result['stale'])}")
        print(f"  Dropped: {len(stale_result['dropped'])}")
        print(f"  Healthy: {stale_result['healthy']}")
        assert stale_result["checked"] >= 1

        # --- Test 7: cleanup_dropped_data ---
        print("\n--- Test 7: cleanup_dropped_data ---")
        # Drop AAPL first so we have something to clean up
        drop_ticker("AAPL", db=db)

        # Manually set date_dropped to > 90 days ago to test cleanup
        old_date = (date.today() - timedelta(days=100)).isoformat()
        db.conn.execute(
            "UPDATE watchlist SET date_dropped=? WHERE ticker=?",
            (old_date, "AAPL"),
        )
        db.conn.commit()

        cleanup_result = cleanup_dropped_data(db=db, older_than_days=90)
        print(f"  Cleaned: {cleanup_result['cleaned']}")
        print(f"  Rows deleted: {cleanup_result['rows_deleted']}")
        assert "AAPL" in cleanup_result["cleaned"]

        # Watchlist entry should still exist
        all_entries = db.get_all_tickers()
        assert any(e["ticker"] == "AAPL" for e in all_entries)

        print("\n" + "=" * 60)
        print("✅ All self-tests passed!")
        print("=" * 60)

    finally:
        db.close()
        os.unlink(tmp_path)
        print(f"\nTest DB removed: {tmp_path}")
