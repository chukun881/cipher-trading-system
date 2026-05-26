"""
data_fetcher.py — Fetches daily OHLCV data from yfinance and stores it in SQLite.

Uses the TradingDB class from db.py for all database operations.
"""

from __future__ import annotations

import random
import time
from datetime import date, datetime, timedelta
from typing import Any, Optional

import pandas as pd
import yfinance as yf

from db import TradingDB


def fetch_with_retry(ticker: str, period: str = "1y", max_retries: int = 3) -> pd.DataFrame:
    """Fetch daily OHLCV data with exponential backoff retry.

    Args:
        ticker: Stock symbol (e.g. 'AAPL').
        period: yfinance period string — '1y', '6mo', '5d', etc.
        max_retries: Maximum number of attempts (default 3).

    Returns:
        Raw yfinance DataFrame (not yet cleaned), or empty DataFrame on failure.
    """
    for attempt in range(max_retries):
        try:
            df = yf.Ticker(ticker).history(period=period, auto_adjust=True)
            if not df.empty:
                return df
            else:
                print(f"  Empty data for {ticker} (attempt {attempt + 1}/{max_retries})")
                if attempt < max_retries - 1:
                    delay = 2 ** (attempt + 1)
                    print(f"    Retrying in {delay}s...")
                    time.sleep(delay)
        except Exception as e:
            print(f"  [ERROR] Fetch {ticker} failed (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                delay = 2 ** (attempt + 1)
                print(f"    Retrying in {delay}s...")
                time.sleep(delay)
    return pd.DataFrame()


def _clean_yf_df(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Clean a raw yfinance DataFrame into standardised OHLCV format.

    Returns a DataFrame with columns: date, open, high, low, close, volume.
    """
    df = df.reset_index()

    # Identify the date column
    if "Date" in df.columns:
        date_col = df["Date"]
    elif "Datetime" in df.columns:
        date_col = df["Datetime"]
    else:
        print(f"  [WARN] No Date/Datetime column found for {ticker}")
        return pd.DataFrame()

    # Rename Date column to 'date' first (before adding a new column)
    if "Date" in df.columns:
        df = df.rename(columns={"Date": "date"})
    elif "Datetime" in df.columns:
        df = df.rename(columns={"Datetime": "date"})
    else:
        print(f"  [WARN] No Date/Datetime column found for {ticker}")
        return pd.DataFrame()

    # Convert to ISO date string (YYYY-MM-DD), stripping timezone info
    dates = pd.to_datetime(df["date"])
    if dates.dt.tz is not None:
        dates = dates.dt.tz_convert("UTC").dt.tz_localize(None)
    df["date"] = dates.dt.strftime("%Y-%m-%d")

    # Rename remaining columns to lowercase
    col_map = {col: col.lower() for col in df.columns if col != col.lower()}
    df = df.rename(columns=col_map)

    # Select and order the columns we need
    available = [c for c in ["date", "open", "high", "low", "close", "volume"] if c in df.columns]
    df = df[available]

    # Drop rows where close is NaN
    df = df.dropna(subset=["close"])

    return df


def fetch_stock_data(ticker: str, period: str = "1y") -> pd.DataFrame:
    """Fetch daily OHLCV data for a single ticker from yfinance.

    Uses exponential backoff retry (up to 3 attempts: 2s, 4s, 8s).

    Args:
        ticker: Stock symbol (e.g. 'AAPL').
        period: yfinance period string — '1y', '6mo', '5d', etc.

    Returns:
        DataFrame with columns: date, open, high, low, close, volume.
        Empty DataFrame on failure.
    """
    raw = fetch_with_retry(ticker, period=period)
    if raw.empty:
        return pd.DataFrame()
    return _clean_yf_df(raw, ticker)


def fetch_all_active(db: TradingDB) -> dict[str, pd.DataFrame]:
    """Fetch data for all active tickers in the watchlist.

    Args:
        db: TradingDB instance.

    Returns:
        Dict mapping ticker symbol to its DataFrame (successful fetches only).
    """
    tickers = db.get_active_tickers()
    total = len(tickers)
    results: dict[str, pd.DataFrame] = {}

    if total == 0:
        print("No active tickers to fetch.")
        return results

    for i, ticker in enumerate(tickers, start=1):
        print(f"Fetching {ticker}... ({i}/{total})")
        df = fetch_stock_data(ticker)
        if not df.empty:
            results[ticker] = df
        time.sleep(random.uniform(1.0, 2.0))

    print(f"Fetched data for {len(results)}/{total} tickers.")
    return results


def store_stock_data(db: TradingDB, ticker: str, df: pd.DataFrame) -> None:
    """Upsert a DataFrame of OHLCV data into the database.

    Args:
        db: TradingDB instance.
        ticker: Stock symbol.
        df: DataFrame with columns: date, open, high, low, close, volume.
    """
    if df.empty:
        return

    # Ensure all values are plain Python types (not numpy/pandas types)
    data = []
    for _, row in df.iterrows():
        data.append({
            "date": str(row["date"])[:10],
            "open": float(row["open"]) if pd.notna(row["open"]) else None,
            "high": float(row["high"]) if pd.notna(row["high"]) else None,
            "low": float(row["low"]) if pd.notna(row["low"]) else None,
            "close": float(row["close"]) if pd.notna(row["close"]) else None,
            "volume": int(row["volume"]) if pd.notna(row["volume"]) else None,
        })
    db.upsert_ohlcv(ticker, data)


def fetch_and_store(ticker: str, db: TradingDB = None, period: str = "1y") -> bool:
    """Convenience: fetch data for one ticker and store it.

    Args:
        ticker: Stock symbol.
        db: Optional TradingDB instance (created if not provided).
        period: yfinance period string.

    Returns:
        True if data was fetched and stored successfully, False otherwise.
    """
    own_db = db is None
    if own_db:
        db = TradingDB()

    try:
        df = fetch_stock_data(ticker, period=period)
        if df.empty:
            return False
        store_stock_data(db, ticker, df)
        return True
    except Exception as e:
        print(f"  [ERROR] fetch_and_store failed for {ticker}: {e}")
        return False
    finally:
        if own_db:
            db.close()


def fetch_and_store_all(db: TradingDB = None, period: str = "1y") -> dict[str, Any]:
    """Fetch and store data for all active tickers.

    Args:
        db: Optional TradingDB instance (created if not provided).
        period: yfinance period string.

    Returns:
        Summary dict: {'success': [...], 'failed': [...], 'total': N}
    """
    own_db = db is None
    if own_db:
        db = TradingDB()

    try:
        tickers = db.get_active_tickers()
        total = len(tickers)
        success: list[str] = []
        failed: list[str] = []

        for i, ticker in enumerate(tickers, start=1):
            print(f"Fetching {ticker}... ({i}/{total})")
            stored = False
            for retry in range(3):  # up to 3 attempts (initial + 2 retries)
                try:
                    df = fetch_stock_data(ticker, period=period)
                    if df.empty:
                        if retry < 2:
                            print(f"    Retry {retry + 1}/2 in 3s...")
                            time.sleep(3)
                            continue
                        failed.append(ticker)
                    else:
                        store_stock_data(db, ticker, df)
                        success.append(ticker)
                        stored = True
                    break
                except Exception as e:
                    print(f"  [ERROR] {ticker}: {e}")
                    if retry < 2:
                        print(f"    Retry {retry + 1}/2 in 3s...")
                        time.sleep(3)
                    else:
                        failed.append(ticker)
            if not stored and ticker not in failed:
                failed.append(ticker)
            time.sleep(random.uniform(1.0, 2.0))

        print(f"Done: {len(success)} success, {len(failed)} failed, {total} total")
        return {"success": success, "failed": failed, "total": total}
    finally:
        if own_db:
            db.close()


def get_latest_data_status(db: TradingDB = None) -> list[dict[str, Any]]:
    """Check data freshness for all active tickers.

    Args:
        db: Optional TradingDB instance (created if not provided).

    Returns:
        List of dicts: [{'ticker': 'AAPL', 'latest_date': '2026-05-21', 'days_behind': 0}, ...]
    """
    own_db = db is None
    if own_db:
        db = TradingDB()

    try:
        tickers = db.get_active_tickers()
        status: list[dict[str, Any]] = []
        today = date.today()

        for ticker in tickers:
            latest = db.get_latest_date(ticker)
            if latest:
                # Ensure date is YYYY-MM-DD only
                latest = latest[:10]
                latest_dt = datetime.strptime(latest, "%Y-%m-%d").date()
                # Use business days for a more accurate "behind" count
                days_behind = len(pd.bdate_range(latest_dt + timedelta(days=1), today))
                status.append({
                    "ticker": ticker,
                    "latest_date": latest,
                    "days_behind": days_behind,
                })
            else:
                status.append({
                    "ticker": ticker,
                    "latest_date": None,
                    "days_behind": None,
                })

        return status
    finally:
        if own_db:
            db.close()


def update_ticker(ticker: str, db: TradingDB = None) -> bool:
    """Incremental update: fetch only new data since the last stored date.

    If no existing data is found, falls back to a full 1-year fetch.

    Args:
        ticker: Stock symbol.
        db: Optional TradingDB instance (created if not provided).

    Returns:
        True if successful, False otherwise.
    """
    own_db = db is None
    if own_db:
        db = TradingDB()

    try:
        ticker = ticker.upper().strip()
        latest = db.get_latest_date(ticker)

        if latest:
            # Incremental: fetch from last stored date to now
            start_date = datetime.strptime(latest, "%Y-%m-%d").date()
            today = date.today()

            if start_date >= today:
                print(f"  {ticker}: data is already up to date ({latest})")
                return True

            # yfinance period: use start/end parameters
            print(f"  Updating {ticker}: {start_date} → {today}")
            # Fetch incremental data with retry
            raw = pd.DataFrame()
            for attempt in range(3):
                try:
                    raw = yf.Ticker(ticker).history(
                        start=start_date.isoformat(),
                        end=(today + timedelta(days=1)).isoformat(),
                        auto_adjust=True,
                    )
                    break
                except Exception as e:
                    print(f"  [ERROR] Failed to update {ticker} (attempt {attempt + 1}/3): {e}")
                    if attempt < 2:
                        time.sleep(2 ** (attempt + 1))
                    else:
                        return False

            if raw.empty:
                print(f"  [WARN] No new data for {ticker}")
                return True  # Not an error — just no new data available

            df = _clean_yf_df(raw, ticker)
            if df.empty:
                print(f"  [WARN] Cleaned DataFrame empty for {ticker}")
                return True

            # Skip the first row if it duplicates the last stored date
            if not df.empty and str(df.iloc[0]["date"]) == str(latest):
                df = df.iloc[1:]

            if df.empty:
                print(f"  {ticker}: no new rows after dedup")
                return True

            store_stock_data(db, ticker, df)
            print(f"  {ticker}: stored {len(df)} new rows")
            return True
        else:
            # No existing data — full fetch
            print(f"  {ticker}: no existing data, fetching 1y...")
            return fetch_and_store(ticker, db=db, period="1y")
    except Exception as e:
        print(f"  [ERROR] update_ticker failed for {ticker}: {e}")
        return False
    finally:
        if own_db:
            db.close()


def update_all(db: TradingDB = None) -> dict[str, Any]:
    """Incremental update for all active tickers.

    For each ticker, only fetches data since the last stored date.
    Falls back to a full 1-year fetch if no existing data.

    Args:
        db: Optional TradingDB instance (created if not provided).

    Returns:
        Summary dict: {'success': [...], 'failed': [...], 'skipped': [...], 'total': N}
    """
    own_db = db is None
    if own_db:
        db = TradingDB()

    try:
        tickers = db.get_active_tickers()
        total = len(tickers)
        success: list[str] = []
        failed: list[str] = []
        skipped: list[str] = []

        if total == 0:
            print("No active tickers to update.")
            return {"success": success, "failed": failed, "skipped": skipped, "total": 0}

        for i, ticker in enumerate(tickers, start=1):
            print(f"Updating {ticker}... ({i}/{total})")
            try:
                result = update_ticker(ticker, db=db)
                if result:
                    success.append(ticker)
                else:
                    failed.append(ticker)
            except Exception as e:
                print(f"  [ERROR] {ticker}: {e}")
                failed.append(ticker)

        print(f"\nUpdate complete: {len(success)} updated, {len(failed)} failed, {total} total")
        return {"success": success, "failed": failed, "skipped": skipped, "total": total}

    finally:
        if own_db:
            db.close()


# ======================================================================
# Self-test
# ======================================================================
if __name__ == "__main__":
    import os
    import tempfile

    # Use a temp DB so we don't pollute the real one
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_path = tmp.name
    tmp.close()

    print(f"Creating test DB at {tmp_path}")
    db = TradingDB(db_path=tmp_path)

    # --- Test 1: fetch_stock_data ---
    print("\n--- Test 1: fetch_stock_data ---")
    df = fetch_stock_data("AAPL", period="5d")
    print(f"AAPL rows (5d): {len(df)}")
    if not df.empty:
        print(f"Columns: {list(df.columns)}")
        print(f"Date range: {df['date'].iloc[0]} to {df['date'].iloc[-1]}")
        assert "date" in df.columns
        assert "close" in df.columns
        assert len(df) > 0
        # Verify date format is ISO
        first_date = df["date"].iloc[0]
        assert len(first_date) == 10 and "-" in first_date
    else:
        print("  [WARN] No data returned (market may be closed / network issue)")

    # --- Test 2: store_stock_data ---
    print("\n--- Test 2: store_stock_data ---")
    db.add_ticker("AAPL", "test")
    if not df.empty:
        store_stock_data(db, "AAPL", df)
        stored = db.get_ohlcv("AAPL")
        print(f"Stored {len(stored)} rows for AAPL")
        assert len(stored) > 0
    else:
        print("  [SKIP] No data to store")

    # --- Test 3: get_latest_data_status ---
    print("\n--- Test 3: get_latest_data_status ---")
    status = get_latest_data_status(db=db)
    for s in status:
        print(f"  {s['ticker']}: latest={s['latest_date']}, behind={s['days_behind']} days")
    assert len(status) == 1
    assert status[0]["ticker"] == "AAPL"

    # --- Test 4: update_ticker (incremental) ---
    print("\n--- Test 4: update_ticker (incremental) ---")
    result = update_ticker("AAPL", db=db)
    print(f"  update_ticker result: {result}")
    assert result is True

    # --- Test 5: fetch_and_store ---
    print("\n--- Test 5: fetch_and_store ---")
    db.add_ticker("MSFT", "test")
    ok = fetch_and_store("MSFT", db=db, period="5d")
    print(f"  fetch_and_store MSFT: {ok}")
    if ok:
        msft_data = db.get_ohlcv("MSFT")
        assert len(msft_data) > 0
        print(f"  MSFT stored {len(msft_data)} rows")

    # --- Test 6: fetch_and_store_all ---
    print("\n--- Test 6: fetch_and_store_all ---")
    summary = fetch_and_store_all(db=db, period="5d")
    print(f"  Summary: {summary}")

    # --- Test 7: fetch_all_active ---
    print("\n--- Test 7: fetch_all_active ---")
    all_data = fetch_all_active(db)
    print(f"  Fetched: {list(all_data.keys())}")

    # --- Test 8: nonexistent ticker ---
    print("\n--- Test 8: nonexistent ticker ---")
    bad_df = fetch_stock_data("NOTREALTICKER99999")
    assert bad_df.empty
    print("  Correctly returned empty DataFrame for bad ticker")

    # Cleanup
    db.close()
    os.unlink(tmp_path)
    print("\n✅ All tests passed!")

