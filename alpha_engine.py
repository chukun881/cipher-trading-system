"""
alpha_engine.py — Technical indicators and signal generation for swing trading.

Computes RSI, Bollinger Bands, ATR, SMA-200 from OHLCV data and generates
BUY / SELL / HOLD signals. Also provides stale-ticker detection and
holdings alert monitoring.

Depends on:
    - db.py          (TradingDB)
    - data_fetcher.py (optional, for self-test only)
    - pandas_ta
    - pandas
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from datetime import date
from typing import Any, Optional

import pandas as pd
import pandas_ta as ta

# Ensure local imports work regardless of cwd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import TradingDB

# ======================================================================
# Defaults
# ======================================================================
_DEFAULTS: dict[str, Any] = {
    "rsi_period": 14,
    "rsi_oversold": 30,
    "rsi_overbought": 70,
    "bb_period": 20,
    "bb_std": 2.0,
    "atr_period": 14,
    "sma_period": 200,
    "stale_days": 10,
    "alert_profit_pct": 10.0,
}


# ======================================================================
# Helpers
# ======================================================================
def _load_settings(db: TradingDB) -> dict[str, Any]:
    """Load all relevant settings from DB, falling back to defaults.

    Returns a dict with typed values (int / float).
    """
    raw = db.get_all_settings()
    out: dict[str, Any] = {}
    int_keys = [
        "rsi_period", "rsi_oversold", "rsi_overbought",
        "bb_period", "atr_period", "sma_period", "stale_days",
    ]
    float_keys = ["bb_std", "alert_profit_pct"]
    for k in int_keys:
        out[k] = int(raw.get(k, _DEFAULTS[k]))
    for k in float_keys:
        out[k] = float(raw.get(k, _DEFAULTS[k]))
    return out


def _merge_settings(settings: Optional[dict], defaults: dict) -> dict:
    """Merge a user-provided settings dict on top of defaults."""
    if settings is None:
        return dict(defaults)
    merged = dict(defaults)
    merged.update(settings)
    return merged


# ======================================================================
# 1. compute_indicators
# ======================================================================
def compute_indicators(
    df: pd.DataFrame, settings: Optional[dict] = None
) -> pd.DataFrame:
    """Compute technical indicators on an OHLCV DataFrame.

    Args:
        df: DataFrame with columns: date, open, high, low, close, volume.
        settings: Optional dict overriding indicator parameters.

    Returns:
        DataFrame with indicator columns appended. Rows where indicators
        are NaN (warm-up period) are dropped.
    """
    s = _merge_settings(settings, _DEFAULTS)

    df = df.copy()

    # Ensure required columns exist and are numeric
    for col in ("open", "high", "low", "close", "volume"):
        if col not in df.columns:
            raise ValueError(f"Missing required column: {col}")
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # --- RSI ---
    rsi_col = f"RSI_{s['rsi_period']}"
    df.ta.rsi(length=s["rsi_period"], append=True)
    if rsi_col in df.columns:
        df.rename(columns={rsi_col: "rsi_14"}, inplace=True)

    # --- Bollinger Bands ---
    bb_len = s["bb_period"]
    bb_std = s["bb_std"]
    # pandas_ta creates columns like BBL_20_2.0, BBM_20_2.0, etc.
    df.ta.bbands(length=bb_len, std=bb_std, append=True)

    bb_rename = {}
    for col in df.columns:
        if col.startswith("BBL_"):
            bb_rename[col] = "bb_lower"
        elif col.startswith("BBM_"):
            bb_rename[col] = "bb_mid"
        elif col.startswith("BBU_"):
            bb_rename[col] = "bb_upper"
        elif col.startswith("BBB_"):
            bb_rename[col] = "bb_pct"
        elif col.startswith("BBP_") or col.startswith("BBW_"):
            bb_rename[col] = "bb_width"
    df.rename(columns=bb_rename, inplace=True)

    # --- ATR ---
    atr_col = f"ATRr_{s['atr_period']}"
    df.ta.atr(length=s["atr_period"], append=True)
    if atr_col in df.columns:
        df.rename(columns={atr_col: "atr_14"}, inplace=True)

    # --- SMA ---
    sma_col = f"SMA_{s['sma_period']}"
    df.ta.sma(length=s["sma_period"], append=True)
    if sma_col in df.columns:
        df.rename(columns={sma_col: "sma_200"}, inplace=True)

    # Drop warm-up rows where any core indicator is NaN
    indicator_cols = [c for c in ("rsi_14", "bb_lower", "bb_upper", "atr_14", "sma_200") if c in df.columns]
    if indicator_cols:
        df.dropna(subset=indicator_cols, inplace=True)
    df.reset_index(drop=True, inplace=True)

    # Drop the raw BBP column if it survived rename (duplicate of bb_pct)
    dup_cols = [c for c in df.columns if c.startswith("BBP_")]
    if dup_cols:
        df.drop(columns=dup_cols, inplace=True)

    return df


# ======================================================================
# 2. generate_signal
# ======================================================================
def generate_signal(
    row: dict[str, Any], settings: Optional[dict] = None
) -> dict[str, Any]:
    """Generate a BUY / SELL / HOLD signal for a single data row.

    Args:
        row: Dict with at least: close, rsi_14, bb_lower, bb_upper.
        settings: Optional dict with rsi_oversold, rsi_overbought.

    Returns:
        {'signal_type': 'BUY'|'SELL'|'HOLD', 'notes': str}
    """
    s = _merge_settings(settings, _DEFAULTS)
    oversold = s.get("rsi_oversold", 30)
    overbought = s.get("rsi_overbought", 70)

    close = row.get("close")
    rsi = row.get("rsi_14")
    bb_lower = row.get("bb_lower")
    bb_upper = row.get("bb_upper")
    sma_200 = row.get("sma_200")

    if any(v is None for v in (close, rsi, bb_lower, bb_upper)):
        return {"signal_type": "HOLD", "notes": "Insufficient indicator data"}

    # BUY: oversold RSI + price at or below lower band
    if rsi < oversold and close <= bb_lower:
        sma_val = f"{sma_200:.2f}" if sma_200 is not None else "N/A"
        if sma_200 is not None and close > sma_200:
            return {
                "signal_type": "BUY",
                "notes": f"RSI {rsi:.1f} < {oversold} & close {close:.2f} <= BB lower {bb_lower:.2f} (above SMA200 {sma_val})",
            }
        else:
            return {
                "signal_type": "BUY",
                "notes": f"RSI {rsi:.1f} < {oversold} & close {close:.2f} <= BB lower {bb_lower:.2f} ⚠️ BELOW SMA200 ({sma_val})",
            }

    # SELL: overbought RSI + price at or above upper band
    if rsi > overbought and close >= bb_upper:
        return {
            "signal_type": "SELL",
            "notes": f"RSI {rsi:.1f} > {overbought} & close {close:.2f} >= BB upper {bb_upper:.2f}",
        }

    return {"signal_type": "HOLD", "notes": ""}


# ======================================================================
# 3. check_stale
# ======================================================================
def check_stale(
    ticker: str, db: TradingDB, settings: Optional[dict] = None
) -> dict[str, Any]:
    """Check whether a ticker is stale or in structural downtrend.

    Args:
        ticker: Stock symbol.
        db: TradingDB instance.
        settings: Optional settings dict.

    Returns:
        {'is_stale': bool, 'below_sma200': bool, 'stale_days': int, 'should_drop': bool}
    """
    s = _merge_settings(settings, _DEFAULTS)
    stale_days = s.get("stale_days", 10)

    indicators = db.get_indicators(ticker, days=stale_days)
    if not indicators:
        return {
            "is_stale": False,
            "below_sma200": False,
            "stale_days": 0,
            "should_drop": False,
        }

    # Check stale: RSI between 40-60 AND close in middle 50% of BB, for ALL N days
    all_stale = True
    for row in indicators:
        rsi = row.get("rsi_14")
        close = row.get("close")
        bb_lower = row.get("bb_lower")
        bb_upper = row.get("bb_upper")
        bb_mid = row.get("bb_mid")

        if any(v is None for v in (rsi, close, bb_lower, bb_upper, bb_mid)):
            all_stale = False
            break

        # RSI dead zone: 40–60
        if not (40 <= rsi <= 60):
            all_stale = False
            break

        # Close within middle 50% of BB width
        half_width = (bb_upper - bb_lower) / 2 * 0.5
        if not (bb_mid - half_width <= close <= bb_mid + half_width):
            all_stale = False
            break

    # Check below SMA-200 on the most recent row
    latest = indicators[-1] if indicators else None
    below_sma200 = False
    if latest and latest.get("sma_200") is not None and latest.get("close") is not None:
        below_sma200 = latest["close"] < latest["sma_200"]

    is_stale = all_stale and len(indicators) == stale_days

    return {
        "is_stale": is_stale,
        "below_sma200": below_sma200,
        "stale_days": len(indicators),
        "should_drop": is_stale or below_sma200,
    }


# ======================================================================
# 4. analyze_ticker
# ======================================================================
def analyze_ticker(
    ticker: str,
    db: Optional[TradingDB] = None,
    settings: Optional[dict] = None,
) -> dict[str, Any]:
    """Full analysis pipeline for a single ticker.

    1. Load settings (from DB if not provided).
    2. Get OHLCV data from DB.
    3. Compute indicators.
    4. Store indicators back to DB.
    5. Generate signal for the latest row.
    6. Check stale status.
    7. Return summary dict.

    Args:
        ticker: Stock symbol.
        db: Optional TradingDB instance (created if not provided).
        settings: Optional settings dict.

    Returns:
        Summary dict with keys: ticker, date, signal, indicators, stale_info.
    """
    own_db = db is None
    if own_db:
        db = TradingDB()

    try:
        ticker = ticker.upper().strip()

        # 1. Load settings
        if settings is None:
            settings = _load_settings(db)

        # 2. Get OHLCV data
        ohlcv = db.get_ohlcv(ticker)
        if not ohlcv:
            return {
                "ticker": ticker,
                "date": None,
                "signal": {"signal_type": "HOLD", "notes": "No OHLCV data"},
                "indicators": {},
                "stale_info": {"is_stale": False, "below_sma200": False, "stale_days": 0, "should_drop": False},
            }

        # 3. Compute indicators
        df = pd.DataFrame(ohlcv)
        df = compute_indicators(df, settings=settings)

        if df.empty:
            return {
                "ticker": ticker,
                "date": None,
                "signal": {"signal_type": "HOLD", "notes": "Insufficient data for indicators"},
                "indicators": {},
                "stale_info": {"is_stale": False, "below_sma200": False, "stale_days": 0, "should_drop": False},
            }

        # 4. Store ALL computed indicator rows
        indicator_rows = []
        for _, row in df.iterrows():
            d: dict[str, Any] = {"date": str(row["date"])[:10]}
            for col in ("rsi_14", "bb_lower", "bb_mid", "bb_upper", "atr_14", "sma_200", "bb_width"):
                if col in df.columns:
                    val = row[col]
                    d[col] = float(val) if pd.notna(val) else None
            indicator_rows.append(d)
        db.upsert_indicators(ticker, indicator_rows)

        # 5. Get latest row and generate signal
        latest = df.iloc[-1].to_dict()
        signal = generate_signal(latest, settings=settings)

        # Ensure latest has a close price from original data
        # (compute_indicators keeps all original columns)
        latest_close = latest.get("close")
        if latest_close is not None:
            latest["close"] = float(latest_close)

        # 6. Check stale
        stale_info = check_stale(ticker, db, settings=settings)

        # 7. Return summary
        return {
            "ticker": ticker,
            "date": str(latest.get("date", ""))[:10],
            "signal": signal,
            "indicators": {
                "rsi_14": latest.get("rsi_14"),
                "bb_lower": latest.get("bb_lower"),
                "bb_mid": latest.get("bb_mid"),
                "bb_upper": latest.get("bb_upper"),
                "atr_14": latest.get("atr_14"),
                "sma_200": latest.get("sma_200"),
                "close": latest.get("close"),
            },
            "stale_info": stale_info,
        }
    finally:
        if own_db:
            db.close()


# ======================================================================
# 5. analyze_all
# ======================================================================
def analyze_all(db: Optional[TradingDB] = None) -> dict[str, Any]:
    """Analyze all active tickers in the watchlist.

    Args:
        db: Optional TradingDB instance (created if not provided).

    Returns:
        Summary: {'signals': [...], 'stale': [...], 'errors': [...], 'total': N, 'duration_seconds': float}
    """
    own_db = db is None
    if own_db:
        db = TradingDB()

    try:
        start = time.time()
        settings = _load_settings(db)
        tickers = db.get_active_tickers()

        signals: list[dict[str, Any]] = []
        stale: list[dict[str, Any]] = []
        errors: list[str] = []
        run_date = date.today().isoformat()

        for ticker in tickers:
            try:
                print(f"  Analyzing {ticker}...")
                result = analyze_ticker(ticker, db=db, settings=settings)

                sig = result["signal"]
                # Skip if no date (no data available)
                if not result.get("date"):
                    print(f"    → SKIP (no date/data)")
                    continue
                # Build a signal record for saving
                sig_record = {
                    "ticker": ticker,
                    "date": run_date,
                    "signal_type": sig["signal_type"],
                    "rsi": result["indicators"].get("rsi_14"),
                    "close": result["indicators"].get("close"),
                    "bb_lower": result["indicators"].get("bb_lower"),
                    "bb_upper": result["indicators"].get("bb_upper"),
                    "atr_14": result["indicators"].get("atr_14"),
                    "notes": sig["notes"],
                }
                signals.append(sig_record)

                # Track stale tickers
                si = result["stale_info"]
                if si["should_drop"]:
                    stale.append({
                        "ticker": ticker,
                        **si,
                    })

                print(f"    → {sig['signal_type']}"
                      + (f" ({sig['notes']})" if sig["notes"] else "")
                      + (" [STALE]" if si["should_drop"] else ""))

            except Exception as e:
                err = f"{ticker}: {e}"
                errors.append(err)
                print(f"    → ERROR: {e}")

        # Save signals to DB
        db.save_signals(signals)

        # Update stale/dropped tickers
        for s_entry in stale:
            ticker = s_entry["ticker"]
            reasons = []
            if s_entry.get("is_stale"):
                reasons.append("stale")
            if s_entry.get("below_sma200"):
                reasons.append("below_sma200")
            reason = f"Auto-dropped: {', '.join(reasons)}"
            db.remove_ticker(ticker)
            print(f"  Dropped {ticker}: {reason}")

        duration = time.time() - start

        # Log the run
        generated_count = sum(1 for s in signals if s["signal_type"] != "HOLD")
        db.log_run(
            status="success" if not errors else "partial",
            tickers_analyzed=len(tickers),
            signals_generated=generated_count,
            errors="; ".join(errors) if errors else "",
            duration=duration,
        )

        print(f"\n  Done: {len(tickers)} tickers, {generated_count} signals, "
              f"{len(stale)} stale, {len(errors)} errors, {duration:.1f}s")

        return {
            "signals": signals,
            "stale": stale,
            "errors": errors,
            "total": len(tickers),
            "duration_seconds": round(duration, 2),
        }
    finally:
        if own_db:
            db.close()


# ======================================================================
# 6. check_holdings_alerts
# ======================================================================
def check_holdings_alerts(db: Optional[TradingDB] = None) -> list[dict[str, Any]]:
    """Check all active holdings for sell signals, stop-loss, target, and profit alerts.

    Args:
        db: Optional TradingDB instance (created if not provided).

    Returns:
        List of alert dicts, each containing:
            holding_id, ticker, alert_type, message, current_price,
            stop_loss, target, unrealized_pnl_pct
    """
    own_db = db is None
    if own_db:
        db = TradingDB()

    try:
        settings = _load_settings(db)
        holdings = db.get_holdings()
        alerts: list[dict[str, Any]] = []

        for h in holdings:
            ticker = h["ticker"]
            latest_ind = db.get_latest_indicators(ticker)
            if not latest_ind:
                continue

            rsi = latest_ind.get("rsi_14")
            bb_upper = latest_ind.get("bb_upper")
            bb_lower = latest_ind.get("bb_lower")

            # Get close price from OHLCV (indicators table doesn't store close)
            ohlcv_rows = db.get_ohlcv(ticker, days=1)
            close = ohlcv_rows[0]["close"] if ohlcv_rows else None
            if close is None:
                # Fallback: use bb_mid as proxy (shouldn't happen in practice)
                continue

            if close is None:
                continue

            buy_price = h["buy_price"]
            stop_loss = h.get("stop_loss")
            target = h.get("target")
            unrealized_pnl_pct = round(((close - buy_price) / buy_price) * 100, 2) if buy_price else 0.0

            base_alert = {
                "holding_id": h["id"],
                "ticker": ticker,
                "current_price": close,
                "stop_loss": stop_loss,
                "target": target,
                "unrealized_pnl_pct": unrealized_pnl_pct,
            }

            # 1. Sell signal: RSI > overbought AND close >= bb_upper
            if rsi is not None and bb_upper is not None:
                if rsi > settings["rsi_overbought"] and close >= bb_upper:
                    alerts.append({
                        **base_alert,
                        "alert_type": "sell_signal",
                        "message": f"RSI {rsi:.1f} > {settings['rsi_overbought']} & close {close:.2f} >= BB upper {bb_upper:.2f}",
                    })

            # 2. Stop-loss hit
            if stop_loss is not None and close <= stop_loss:
                alerts.append({
                    **base_alert,
                    "alert_type": "stop_loss_hit",
                    "message": f"Close {close:.2f} <= stop-loss {stop_loss:.2f}",
                })

            # 3. Target hit
            if target is not None and close >= target:
                alerts.append({
                    **base_alert,
                    "alert_type": "target_hit",
                    "message": f"Close {close:.2f} >= target {target:.2f}",
                })

            # 4. Profit alert
            if unrealized_pnl_pct >= settings["alert_profit_pct"]:
                alerts.append({
                    **base_alert,
                    "alert_type": "profit_alert",
                    "message": f"Unrealized P&L {unrealized_pnl_pct:.1f}% >= {settings['alert_profit_pct']}%",
                })

        return alerts
    finally:
        if own_db:
            db.close()


# ======================================================================
# Self-test
# ======================================================================
if __name__ == "__main__":
    print("=" * 60)
    print("alpha_engine.py — Self-test")
    print("=" * 60)

    # Use a temp DB to avoid polluting real data
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_path = tmp.name
    tmp.close()

    print(f"\nTest DB: {tmp_path}")
    db = TradingDB(db_path=tmp_path)

    # --- Add test tickers ---
    print("\n--- Setup: Adding test tickers ---")
    db.add_ticker("AAPL", "test")
    db.add_ticker("MSFT", "test")

    # --- Fetch data (3 months) ---
    print("\n--- Fetching data (3mo) ---")
    from data_fetcher import fetch_and_store

    for ticker in ("AAPL", "MSFT"):
        ok = fetch_and_store(ticker, db=db, period="3mo")
        print(f"  {ticker}: {'OK' if ok else 'FAILED'}")
        if not ok:
            print("  Cannot continue test without data — aborting.")
            db.close()
            os.unlink(tmp_path)
            sys.exit(1)

    # --- Test compute_indicators ---
    print("\n--- Test: compute_indicators ---")
    ohlcv = db.get_ohlcv("AAPL")
    df = pd.DataFrame(ohlcv)
    print(f"  AAPL OHLCV rows: {len(df)}")
    df = compute_indicators(df)
    print(f"  After indicators: {len(df)} rows")
    print(f"  Columns: {list(df.columns)}")
    if not df.empty:
        latest = df.iloc[-1]
        print(f"  Latest RSI: {latest.get('rsi_14', 'N/A'):.2f}")
        print(f"  Latest BB: {latest.get('bb_lower', 'N/A'):.2f} / {latest.get('bb_upper', 'N/A'):.2f}")
        print(f"  Latest ATR: {latest.get('atr_14', 'N/A'):.2f}")
        sma_val = latest.get('sma_200')
        print(f"  Latest SMA-200: {sma_val:.2f}" if pd.notna(sma_val) else "  Latest SMA-200: N/A (insufficient data)")
    assert len(df) > 0, "compute_indicators returned empty DataFrame"
    assert "rsi_14" in df.columns
    assert "bb_lower" in df.columns
    assert "bb_upper" in df.columns
    assert "atr_14" in df.columns
    # sma_200 may not exist with short data periods
    print("  ✅ compute_indicators OK")

    # --- Test generate_signal ---
    print("\n--- Test: generate_signal ---")
    row = df.iloc[-1].to_dict()
    signal = generate_signal(row)
    print(f"  AAPL signal: {signal}")
    assert signal["signal_type"] in ("BUY", "SELL", "HOLD")
    # Test synthetic BUY
    buy_signal = generate_signal({"close": 100, "rsi_14": 20, "bb_lower": 105, "bb_upper": 120})
    assert buy_signal["signal_type"] == "BUY"
    # Test synthetic SELL
    sell_signal = generate_signal({"close": 125, "rsi_14": 80, "bb_lower": 100, "bb_upper": 120})
    assert sell_signal["signal_type"] == "SELL"
    print("  ✅ generate_signal OK")

    # --- Test analyze_ticker ---
    print("\n--- Test: analyze_ticker ---")
    result = analyze_ticker("AAPL", db=db)
    print(f"  Ticker: {result['ticker']}")
    print(f"  Date: {result['date']}")
    print(f"  Signal: {result['signal']}")
    print(f"  Indicators: {result['indicators']}")
    print(f"  Stale: {result['stale_info']}")
    assert result["ticker"] == "AAPL"
    assert result["signal"]["signal_type"] in ("BUY", "SELL", "HOLD")
    assert result["indicators"]["rsi_14"] is not None
    print("  ✅ analyze_ticker OK")

    # --- Test analyze_all ---
    print("\n--- Test: analyze_all ---")
    summary = analyze_all(db=db)
    print(f"  Total: {summary['total']}")
    print(f"  Signals: {len(summary['signals'])}")
    print(f"  Stale: {len(summary['stale'])}")
    print(f"  Errors: {len(summary['errors'])}")
    print(f"  Duration: {summary['duration_seconds']}s")
    assert summary["total"] == 2
    assert len(summary["signals"]) == 2

    # Print non-HOLD signals
    print("\n--- Generated Signals ---")
    for sig in summary["signals"]:
        if sig["signal_type"] != "HOLD":
            print(f"  {sig['ticker']}: {sig['signal_type']} — {sig['notes']}")
        else:
            print(f"  {sig['ticker']}: HOLD")

    # --- Test check_holdings_alerts (no holdings, should be empty) ---
    print("\n--- Test: check_holdings_alerts (no holdings) ---")
    alerts = check_holdings_alerts(db=db)
    print(f"  Alerts: {len(alerts)}")
    assert len(alerts) == 0

    # --- Test check_holdings_alerts (with a synthetic holding) ---
    print("\n--- Test: check_holdings_alerts (with holding) ---")
    aapl_ind = db.get_latest_indicators("AAPL")
    if aapl_ind:
        # Get close from OHLCV (indicators table doesn't store close)
        aapl_ohlcv = db.get_ohlcv("AAPL", days=1)
        close_price = aapl_ohlcv[0]["close"] if aapl_ohlcv else None
        if close_price is None:
            print("  SKIP: No close price available")
        else:
            hid = db.add_holding(
                "AAPL", buy_price=close_price * 0.9, volume=10,
                buy_date="2026-01-01", stop_loss=close_price * 0.85,
                target=close_price * 0.95, notes="test holding",
            )
            alerts = check_holdings_alerts(db=db)
            print(f"  Alerts with holding: {len(alerts)}")
            for a in alerts:
                print(f"    {a['alert_type']}: {a['message']}")
            db.close_holding(hid, sell_price=close_price, sell_date="2026-05-22")

    # --- Cleanup ---
    print("\n--- Cleanup ---")
    db.close()
    os.unlink(tmp_path)
    print("Test DB removed.")
    print("\n" + "=" * 60)
    print("✅ All self-tests passed!")
    print("=" * 60)
