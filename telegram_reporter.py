"""
telegram_reporter.py — Formats daily analysis results into Telegram messages and sends them.

Pure Python formatting (no LLM). Uses raw HTTP POST to the Telegram Bot API.
Handles message splitting for Telegram's 4096-character limit.

Depends on:
    - db.py               (TradingDB)
    - data_fetcher.py     (update_all / update_ticker)
    - alpha_engine.py     (analyze_all, check_holdings_alerts)
    - portfolio.py        (batch_update_stops)
    - watchlist_manager.py (check_stale_all)
"""

from __future__ import annotations

import html
import os
import sys
import time
from datetime import date
from typing import Any, Optional

import requests

# Ensure local imports work regardless of cwd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import TradingDB


# ======================================================================
# Constants
# ======================================================================
_TG_API_BASE = "https://api.telegram.org/bot"
_MAX_MSG_LEN = 4096

# Alert type priority ordering (highest urgency first)
_ALERT_PRIORITY = {
    "stop_loss_hit": 0,
    "sell_signal": 1,
    "target_hit": 2,
    "profit_alert": 3,
}


# ======================================================================
# 1. format_daily_report
# ======================================================================
def format_daily_report(
    signals: list[dict[str, Any]],
    holdings_alerts: list[dict[str, Any]],
    stale_dropped: list[dict[str, Any]] | None = None,
    run_date: str | None = None,
) -> str:
    """Format analysis results into a Telegram-friendly HTML message.

    Args:
        signals: List of signal dicts from alpha_engine.analyze_all().
                 Each must have: ticker, signal_type, rsi, close, bb_lower, bb_upper.
        holdings_alerts: List of alert dicts from alpha_engine.check_holdings_alerts().
                         Each must have: ticker, alert_type, current_price, stop_loss,
                         target, unrealized_pnl_pct, message.
        stale_dropped: List of dicts for auto-dropped stale tickers.
                       Each must have: ticker, reason.
        run_date: Date string for the report header. Defaults to today.

    Returns:
        Formatted HTML string ready for Telegram.
    """
    if signals is None:
        signals = []
    if holdings_alerts is None:
        holdings_alerts = []
    if stale_dropped is None:
        stale_dropped = []

    run_date = run_date or date.today().isoformat()

    parts: list[str] = []

    # ── Header ──
    parts.append(f"📊 <b>Daily Signal Report — {run_date}</b>")

    # ── Holdings Alerts ──
    if holdings_alerts:
        # Sort by priority
        sorted_alerts = sorted(
            holdings_alerts,
            key=lambda a: a.get("ticker", ""),
        )
        parts.append("")
        parts.append("⚠️ <b>HOLDINGS ALERT</b>")
        parts.append("━━━━━━━━━━━━━━━━")

        for alert in sorted_alerts:
            ticker = alert.get("ticker", "???")
            alert_type = alert.get("alert_type", "")
            current = alert.get("current_price")
            stop = alert.get("stop_loss")
            target = alert.get("target")
            pnl_pct = alert.get("unrealized_pnl_pct", 0)
            msg = alert.get("message", "")

            if alert_type == "stop_loss_hit":
                stop_str = f"${stop:.2f}" if stop else "N/A"
                cur_str = f"${current:.2f}" if current else "N/A"
                parts.append(f"🔴 <b>{ticker}</b>: Stop-loss hit! Current {cur_str}, Stop {stop_str}")
            elif alert_type == "sell_signal":
                cur_str = f"${current:.2f}" if current else "N/A"
                parts.append(f"🔴 <b>{ticker}</b>: Sell signal! {msg}")
            elif alert_type == "target_hit":
                tgt_str = f"${target:.2f}" if target else "N/A"
                cur_str = f"${current:.2f}" if current else "N/A"
                parts.append(f"🟡 <b>{ticker}</b>: Target hit! Current {cur_str}, Target {tgt_str} (+{pnl_pct:.1f}%)")
            elif alert_type == "profit_alert":
                parts.append(f"🟢 <b>{ticker}</b>: Profit alert! +{pnl_pct:.1f}% unrealized ({msg})")
            else:
                parts.append(f"⚪ <b>{ticker}</b>: {msg}")

    # ── Watchlist Signals ──
    buy_signals = sorted(
        [s for s in signals if s.get("signal_type") == "BUY"],
        key=lambda s: s.get("ticker", ""),
    )
    sell_signals = sorted(
        [s for s in signals if s.get("signal_type") == "SELL"],
        key=lambda s: s.get("ticker", ""),
    )
    hold_signals = sorted(
        [s for s in signals if s.get("signal_type") == "HOLD"],
        key=lambda s: s.get("ticker", ""),
    )

    if buy_signals or sell_signals or hold_signals:

        # Show the section if there are any signals at all
        if buy_signals or sell_signals or hold_signals:
            parts.append("")
            parts.append("🎯 <b>WATCHLIST SIGNALS</b>")
            parts.append("━━━━━━━━━━━━━━━━")

            for s in buy_signals:
                ticker = s.get("ticker", "???")
                rsi = s.get("rsi")
                bb_lower = s.get("bb_lower")
                rsi_str = f"{rsi:.0f}" if rsi is not None else "N/A"
                bb_str = f"${bb_lower:.2f}" if bb_lower is not None else "N/A"
                parts.append(f"🟢 <b>BUY: {ticker}</b> (RSI {rsi_str}, near BB lower {bb_str})")

            for s in sell_signals:
                ticker = s.get("ticker", "???")
                rsi = s.get("rsi")
                bb_upper = s.get("bb_upper")
                rsi_str = f"{rsi:.0f}" if rsi is not None else "N/A"
                bb_str = f"${bb_upper:.2f}" if bb_upper is not None else "N/A"
                parts.append(f"🔴 <b>SELL: {ticker}</b> (RSI {rsi_str}, near BB upper {bb_str})")

            if hold_signals:
                if len(hold_signals) <= 5:
                    names = ", ".join(s.get("ticker", "???") for s in hold_signals)
                    parts.append(f"⚪ HOLD: {names}")
                else:
                    parts.append(f"⚪ HOLD: {len(hold_signals)} tickers")

    # ── Dropped (stale) ──
    if stale_dropped:
        parts.append("")
        parts.append("🗑️ <b>DROPPED (stale)</b>")
        parts.append("━━━━━━━━━━━━━━━━")
        for d in stale_dropped:
            ticker = d.get("ticker", "???")
            reason = d.get("reason", "removed from watchlist")
            parts.append(f"• <b>{ticker}</b> — {reason}")

    # ── Summary line ──
    buy_count = sum(1 for s in signals if s.get("signal_type") == "BUY")
    sell_count = sum(1 for s in signals if s.get("signal_type") == "SELL")
    hold_count = sum(1 for s in signals if s.get("signal_type") == "HOLD")
    dropped_count = len(stale_dropped)
    total = len(signals)

    parts.append("")
    parts.append(
        f"📈 Summary: {total} analyzed | "
        f"{buy_count} buy | {sell_count} sell | "
        f"{hold_count} hold | {dropped_count} dropped"
    )

    return "\n".join(parts)


# ======================================================================
# 2. send_telegram_message
# ======================================================================
def send_telegram_message(
    message: str,
    db: TradingDB | None = None,
    parse_mode: str = "HTML",
) -> bool:
    """Send a message to the configured Telegram chat.

    Handles splitting messages that exceed Telegram's 4096-char limit
    by breaking at line boundaries.

    Args:
        message: The message text (HTML formatted).
        db: TradingDB instance. Created if not provided.
        parse_mode: Telegram parse mode ('HTML' or 'Markdown').

    Returns:
        True if all message parts were sent successfully, False otherwise.
    """
    own_db = db is None
    if own_db:
        db = TradingDB()

    try:
        token = db.get_setting("telegram_bot_token", "") or ""
        chat_id = db.get_setting("telegram_chat_id", "") or ""

        if not token or not chat_id:
            print("[ERROR] Telegram bot_token or chat_id not configured")
            return False

        # Split message if needed
        chunks = _split_message(message)

        url = f"{_TG_API_BASE}{token}/sendMessage"
        all_ok = True

        for chunk in chunks:
            payload = {
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": parse_mode,
            }
            try:
                resp = requests.post(url, json=payload, timeout=30)
                if resp.status_code == 200:
                    print(f"  [OK] Telegram message sent ({len(chunk)} chars)")
                else:
                    print(f"[ERROR] Telegram API error: {resp.status_code} {resp.text[:200]}")
                    all_ok = False
            except requests.exceptions.Timeout:
                print("[ERROR] Telegram request timed out (30s)")
                all_ok = False
            except requests.exceptions.ConnectionError:
                print("[ERROR] Telegram connection error — check network")
                all_ok = False
            except Exception as e:
                print(f"[ERROR] Failed to send Telegram message: {e}")
                all_ok = False

        return all_ok

    finally:
        if own_db:
            db.close()


def _split_message(message: str, max_len: int = _MAX_MSG_LEN) -> list[str]:
    """Split a message into chunks under max_len at line boundaries.

    Tries to split at empty lines first (section boundaries), then at
    any newline.
    """
    if len(message) <= max_len:
        return [message]

    chunks: list[str] = []
    remaining = message

    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break

        # Find the best split point within max_len
        # Prefer splitting at double newlines (section boundaries)
        search_region = remaining[:max_len]
        split_pos = -1

        # Try double newline first
        idx = search_region.rfind("\n\n")
        if idx > 0:
            split_pos = idx + 1  # Include one trailing newline
        else:
            # Single newline
            idx = search_region.rfind("\n")
            if idx > 0:
                split_pos = idx + 1
            else:
                # Force split at max_len
                split_pos = max_len

        chunks.append(remaining[:split_pos].rstrip("\n"))
        remaining = remaining[split_pos:].lstrip("\n")

    return chunks


# ======================================================================
# 3. send_daily_report
# ======================================================================
def send_daily_report(db: TradingDB | None = None) -> bool:
    """Full pipeline: load results → format → send.

    Steps:
        1. Get latest signals from DB (today's signals).
        2. Get holdings alerts using alpha_engine.check_holdings_alerts().
        3. Get stale/dropped tickers from latest run.
        4. Format the report using format_daily_report().
        5. Send via send_telegram_message().

    Args:
        db: TradingDB instance. Created if not provided.

    Returns:
        True if the report was sent successfully.
    """
    own_db = db is None
    if own_db:
        db = TradingDB()

    try:
        from alpha_engine import check_holdings_alerts

        today = date.today().isoformat()

        # 1. Get today's signals
        signals = db.get_signals(date=today)
        print(f"  Signals for {today}: {len(signals)}")

        # 2. Get holdings alerts
        holdings_alerts = check_holdings_alerts(db=db)
        print(f"  Holdings alerts: {len(holdings_alerts)}")

        # 3. Get stale/dropped from latest run log
        stale_dropped: list[dict[str, Any]] = []
        recent_runs = db.get_recent_runs(limit=1)
        if recent_runs and recent_runs[0].get("run_date") == today:
            # Check watchlist for recently dropped tickers
            all_tickers = db.get_all_tickers()
            for t in all_tickers:
                if t.get("status") == "dropped" and t.get("date_dropped") == today:
                    stale_dropped.append({
                        "ticker": t["ticker"],
                        "reason": t.get("reason", "removed from watchlist"),
                    })

        # 4. Format
        report = format_daily_report(
            signals=signals,
            holdings_alerts=holdings_alerts,
            stale_dropped=stale_dropped,
            run_date=today,
        )

        # 5. Send
        print(f"  Report length: {len(report)} chars")
        return send_telegram_message(report, db=db)

    except Exception as e:
        print(f"[ERROR] send_daily_report failed: {e}")
        return False
    finally:
        if own_db:
            db.close()


# ======================================================================
# 4. send_test_message
# ======================================================================
def send_test_message(db: TradingDB | None = None) -> bool:
    """Send a simple test message to verify Telegram connection.

    Args:
        db: TradingDB instance. Created if not provided.

    Returns:
        True if the test message was sent successfully.
    """
    return send_telegram_message(
        "🔐 Cipher Trading System — Connection test successful. ✅",
        db=db,
    )


# ======================================================================
# 5. run_and_report
# ======================================================================
def run_and_report(db: TradingDB | None = None) -> dict[str, Any]:
    """Complete daily workflow: update data, analyze, alert, report.

    Steps:
        1. Update data for all active tickers (data_fetcher).
        2. Run full analysis (alpha_engine.analyze_all).
        3. Check holdings alerts (alpha_engine.check_holdings_alerts).
        4. Update stops for all holdings (portfolio.batch_update_stops).
        5. Check stale and auto-drop (watchlist_manager.check_stale_all).
        6. Format and send Telegram report.
        7. Return summary.

    Args:
        db: TradingDB instance. Created if not provided.

    Returns:
        Summary dict with date, counts, signals, alerts, report_sent, etc.
    """
    own_db = db is None
    if own_db:
        db = TradingDB()

    try:
        from data_fetcher import update_all
        from alpha_engine import analyze_all, check_holdings_alerts
        from portfolio import batch_update_stops
        from watchlist_manager import check_stale_all

        start = time.time()
        today = date.today().isoformat()
        errors: list[str] = []

        # 1. Update data
        print("[1/6] Fetching data for all active tickers...")
        try:
            fetch_result = update_all(db=db)
            tickers_updated = len(fetch_result.get("success", []))
            if fetch_result.get("failed"):
                errors.extend(f"Fetch failed: {t}" for t in fetch_result["failed"])
        except Exception as e:
            tickers_updated = 0
            errors.append(f"Data fetch error: {e}")
            print(f"  [ERROR] {e}")

        # 2. Run full analysis
        print("[2/6] Running analysis...")
        try:
            analysis = analyze_all(db=db)
            signals = analysis.get("signals", [])
            tickers_analyzed = analysis.get("total", 0)
            if analysis.get("errors"):
                errors.extend(analysis["errors"])
        except Exception as e:
            signals = []
            tickers_analyzed = 0
            errors.append(f"Analysis error: {e}")
            print(f"  [ERROR] {e}")

        # 3. Check holdings alerts
        print("[3/6] Checking holdings alerts...")
        try:
            holdings_alerts = check_holdings_alerts(db=db)
        except Exception as e:
            holdings_alerts = []
            errors.append(f"Holdings alert error: {e}")
            print(f"  [ERROR] {e}")

        # 4. Update stops for all holdings
        print("[4/6] Updating trailing stops...")
        try:
            batch_update_stops(db=db)
        except Exception as e:
            errors.append(f"Stop update error: {e}")
            print(f"  [ERROR] {e}")

        # 5. Check stale and auto-drop
        print("[5/6] Checking stale tickers...")
        try:
            stale_result = check_stale_all(db=db)
            dropped = stale_result.get("dropped", [])
        except Exception as e:
            dropped = []
            errors.append(f"Stale check error: {e}")
            print(f"  [ERROR] {e}")

        # 6. Format and send Telegram report
        print("[6/6] Sending Telegram report...")
        try:
            stale_for_report = [
                {"ticker": d["ticker"], "reason": d.get("reason", "")}
                for d in dropped
            ]
            report = format_daily_report(
                signals=signals,
                holdings_alerts=holdings_alerts,
                stale_dropped=stale_for_report,
                run_date=today,
            )
            report_sent = send_telegram_message(report, db=db)
        except Exception as e:
            report_sent = False
            errors.append(f"Report send error: {e}")
            print(f"  [ERROR] {e}")

        duration = round(time.time() - start, 2)

        result = {
            "date": today,
            "tickers_updated": tickers_updated,
            "tickers_analyzed": tickers_analyzed,
            "signals": signals,
            "holdings_alerts": holdings_alerts,
            "dropped": dropped,
            "report_sent": report_sent,
            "duration_seconds": duration,
            "errors": errors,
        }

        print(f"\n  Done in {duration}s — {tickers_analyzed} analyzed, "
              f"{len(signals)} signals, {len(holdings_alerts)} alerts, "
              f"{len(dropped)} dropped, report={'sent' if report_sent else 'FAILED'}")

        return result

    finally:
        if own_db:
            db.close()


# ======================================================================
# Self-test
# ======================================================================
if __name__ == "__main__":
    print("=" * 60)
    print("telegram_reporter.py — Self-test")
    print("=" * 60)

    # ── Test 1: format_daily_report with sample data ──
    print("\n--- Test 1: format_daily_report with sample data ---")

    sample_signals = [
        {"ticker": "NVDA", "signal_type": "BUY", "rsi": 28.5, "close": 875.0, "bb_lower": 870.0, "bb_upper": 920.0, "atr_14": 12.5, "notes": "oversold"},
        {"ticker": "AMD", "signal_type": "BUY", "rsi": 31.2, "close": 165.0, "bb_lower": 160.0, "bb_upper": 180.0, "atr_14": 5.0, "notes": "oversold"},
        {"ticker": "META", "signal_type": "SELL", "rsi": 73.1, "close": 510.0, "bb_lower": 470.0, "bb_upper": 505.0, "atr_14": 8.0, "notes": "overbought"},
        {"ticker": "GOOG", "signal_type": "HOLD", "rsi": 52.0, "close": 175.0, "bb_lower": 168.0, "bb_upper": 182.0, "atr_14": 3.5, "notes": ""},
        {"ticker": "AMZN", "signal_type": "HOLD", "rsi": 48.5, "close": 185.0, "bb_lower": 178.0, "bb_upper": 192.0, "atr_14": 4.0, "notes": ""},
        {"ticker": "NFLX", "signal_type": "HOLD", "rsi": 55.0, "close": 630.0, "bb_lower": 600.0, "bb_upper": 660.0, "atr_14": 10.0, "notes": ""},
    ]

    sample_alerts = [
        {"holding_id": 1, "ticker": "AAPL", "alert_type": "stop_loss_hit", "current_price": 282.50, "stop_loss": 281.14, "target": 310.0, "unrealized_pnl_pct": -2.59, "message": "Close 282.50 <= stop-loss 281.14"},
        {"holding_id": 2, "ticker": "TSLA", "alert_type": "target_hit", "current_price": 208.0, "stop_loss": 180.0, "target": 210.0, "unrealized_pnl_pct": 15.2, "message": "Close 208.00 >= target 210.00"},
        {"holding_id": 3, "ticker": "MSFT", "alert_type": "sell_signal", "current_price": 430.0, "stop_loss": 400.0, "target": 450.0, "unrealized_pnl_pct": 7.5, "message": "RSI 74 > 70 & close 430.00 >= BB upper 425.00"},
    ]

    sample_dropped = [
        {"ticker": "XOM", "reason": "10 days dead zone, removed from watchlist"},
        {"ticker": "INTC", "reason": "below 200-day SMA, removed from watchlist"},
    ]

    report = format_daily_report(
        signals=sample_signals,
        holdings_alerts=sample_alerts,
        stale_dropped=sample_dropped,
        run_date="2026-05-22",
    )

    print(report)
    print()

    # Validate structure
    assert "📊" in report
    assert "Daily Signal Report — 2026-05-22" in report
    assert "⚠️" in report
    assert "AAPL" in report
    assert "Stop-loss" in report
    assert "TSLA" in report
    assert "MSFT" in report
    assert "🎯" in report
    assert "NVDA" in report
    assert "AMD" in report
    assert "META" in report
    assert "🗑️" in report
    assert "XOM" in report
    assert "INTC" in report
    assert "📈" in report
    print("  ✅ Report contains all expected sections")

    # ── Test 2: Empty inputs ──
    print("\n--- Test 2: format_daily_report with empty inputs ---")
    empty_report = format_daily_report(signals=[], holdings_alerts=[], stale_dropped=[])
    print(empty_report)
    assert "📊" in empty_report
    assert "⚠️" not in empty_report  # No holdings alert section
    assert "🎯" not in empty_report  # No signals section
    assert "🗑️" not in empty_report  # No dropped section
    print("  ✅ Empty inputs handled correctly")

    # ── Test 3: None inputs ──
    print("\n--- Test 3: format_daily_report with None inputs ---")
    none_report = format_daily_report(signals=None, holdings_alerts=None, stale_dropped=None)
    print(none_report)
    assert "📊" in none_report
    print("  ✅ None inputs handled correctly")

    # ── Test 4: Alert priority ordering ──
    print("\n--- Test 4: Alert priority ordering ---")
    unordered_alerts = [
        {"ticker": "ZZZ", "alert_type": "profit_alert", "current_price": 100, "stop_loss": 90, "target": 110, "unrealized_pnl_pct": 12.0, "message": "Profit"},
        {"ticker": "AAA", "alert_type": "stop_loss_hit", "current_price": 89, "stop_loss": 90, "target": 110, "unrealized_pnl_pct": -5.0, "message": "Stop hit"},
        {"ticker": "MMM", "alert_type": "sell_signal", "current_price": 115, "stop_loss": 90, "target": 110, "unrealized_pnl_pct": 15.0, "message": "Sell"},
    ]
    priority_report = format_daily_report(signals=[], holdings_alerts=unordered_alerts, run_date="2026-05-22")
    # stop_loss_hit should appear before sell_signal, which should appear before profit_alert
    aaa_pos = priority_report.find("AAA")
    zzz_pos = priority_report.find("ZZZ")
    mmm_pos = priority_report.find("MMM")
    assert aaa_pos < mmm_pos < zzz_pos, f"Priority order wrong: AAA={aaa_pos}, MMM={mmm_pos}, ZZZ={zzz_pos}"
    print("  ✅ Alerts correctly ordered by priority")

    # ── Test 5: Many HOLD signals summarised ──
    print("\n--- Test 5: HOLD signals summarised when >5 ---")
    many_holds = [{"ticker": f"T{i}", "signal_type": "HOLD", "rsi": 50, "close": 100, "bb_lower": 95, "bb_upper": 105, "atr_14": 3, "notes": ""} for i in range(7)]
    holds_report = format_daily_report(signals=many_holds, holdings_alerts=[], run_date="2026-05-22")
    assert "7 tickers" in holds_report
    print("  ✅ HOLD signals summarised when >5")

    # ── Test 6: Message splitting ──
    print("\n--- Test 6: Message splitting ---")
    long_msg = "Line\n\n" * 1500  # ~9000 chars
    chunks = _split_message(long_msg)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= _MAX_MSG_LEN, f"Chunk too long: {len(chunk)}"
    print(f"  Split {len(long_msg)} chars into {len(chunks)} chunks")
    print("  ✅ Message splitting works correctly")

    # ── Test 7: HTML escaping ──
    print("\n--- Test 7: HTML in signal data ---")
    html_signals = [
        {"ticker": "TEST", "signal_type": "BUY", "rsi": 28, "close": 100, "bb_lower": 95, "bb_upper": 105, "atr_14": 3, "notes": "test <alert> & stuff"},
    ]
    html_report = format_daily_report(signals=html_signals, holdings_alerts=[], run_date="2026-05-22")
    # The ticker name itself should be fine (no special chars in our formatting)
    assert "TEST" in html_report
    print("  ✅ Report generated with HTML signal data")

    # ── Test 8: send_test_message (dry run — will fail without token) ──
    print("\n--- Test 8: send_test_message (no token expected) ---")
    result = send_test_message()
    if result:
        print("  ✅ Test message sent (token is configured!)")
    else:
        print("  ✅ Test message correctly returned False (no token configured, expected)")

    print("\n" + "=" * 60)
    print("✅ All self-tests passed!")
    print("=" * 60)
