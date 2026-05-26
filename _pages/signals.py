"""
pages/signals.py — Signals & Analysis page.

Run the full analysis pipeline, view signals, holdings alerts,
signal history, and send Telegram reports.
"""

from __future__ import annotations

import sys
import os
import time
from datetime import date, datetime, timedelta
from typing import Any

import streamlit as st
import pandas as pd

# Ensure local imports work regardless of cwd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import TradingDB


# ======================================================================
# Cached DB singleton
# ======================================================================
# ======================================================================
# Formatting helpers
# ======================================================================

def _fmt_price(value: float | None) -> str:
    if value is None:
        return "—"
    return f"${value:,.2f}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "—"
    sign = "+" if (value or 0) > 0 else ""
    return f"{sign}{value:.1f}%"


def _signal_badge(sig_type: str | None) -> str:
    if sig_type is None:
        return "⚪ —"
    s = sig_type.upper()
    if s == "BUY":
        return "🟢 BUY"
    if s == "SELL":
        return "🔴 SELL"
    return f"⚪ {sig_type}"


_ALERT_PRIORITY = {
    "stop_loss_hit": 0,
    "sell_signal": 1,
    "target_hit": 2,
    "profit_alert": 3,
}

_ALERT_EMOJI = {
    "stop_loss_hit": "🔴",
    "sell_signal": "🔴",
    "target_hit": "🟡",
    "profit_alert": "🟢",
}


# ======================================================================
# Session state defaults
# ======================================================================
def _init_state() -> None:
    defaults = {
        "analysis_run": False,
        "analysis_signals": [],
        "analysis_stale": [],
        "analysis_fetch_result": None,
        "analysis_stop_result": None,
        "analysis_stale_result": None,
        "analysis_alerts": [],
        "analysis_errors": [],
        "analysis_total": 0,
        "analysis_duration": 0.0,
        "telegram_sent": False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


# ======================================================================
# Section 1: Run Analysis
# ======================================================================
def _section_run_analysis(db: TradingDB) -> None:
    st.subheader("⚡ Run Analysis")

    # Last run info
    recent = db.get_recent_runs(limit=1)
    if recent:
        r = recent[0]
        st.caption(
            f"Last run: **{r.get('run_date', '—')}** {r.get('run_time', '')}  ·  "
            f"Tickers: {r.get('tickers_analyzed', 0)}  ·  "
            f"Signals: {r.get('signals_generated', 0)}  ·  "
            f"Status: {r.get('status', '—')}  ·  "
            f"Duration: {r.get('duration_seconds', 0):.1f}s"
        )
    else:
        st.caption("No analysis runs recorded yet.")

    if st.button("▶ Run Analysis Now", type="primary", width="stretch"):
        with st.spinner("Running full analysis pipeline…"):
            t0 = time.time()

            # Step 1: Fetch data
            st.info("📥 Fetching latest data for all tickers…")
            from data_fetcher import update_all
            fetch_result = update_all(db=db)

            # Step 2: Analyze
            st.info("🔍 Running analysis on all tickers…")
            from alpha_engine import analyze_all
            analysis = analyze_all(db=db)

            # Step 3: Update stops
            st.info("🔄 Updating trailing stops…")
            from portfolio import batch_update_stops
            stop_result = batch_update_stops(db=db)

            # Step 4: Check stale
            st.info("🕰️ Checking for stale tickers…")
            from watchlist_manager import check_stale_all
            stale_result = check_stale_all(db=db)

            # Step 5: Holdings alerts
            from alpha_engine import check_holdings_alerts
            alerts = check_holdings_alerts(db=db)

            duration = time.time() - t0

            # Store in session state
            st.session_state.analysis_run = True
            st.session_state.analysis_signals = analysis.get("signals", [])
            st.session_state.analysis_stale = analysis.get("stale", [])
            st.session_state.analysis_fetch_result = fetch_result
            st.session_state.analysis_stop_result = stop_result
            st.session_state.analysis_stale_result = stale_result
            st.session_state.analysis_alerts = alerts
            st.session_state.analysis_errors = analysis.get("errors", [])
            st.session_state.analysis_total = analysis.get("total", 0)
            st.session_state.analysis_duration = analysis.get("duration_seconds", 0)
            st.session_state.telegram_sent = False

        # Summary
        signals = st.session_state.analysis_signals
        buy_count = sum(1 for s in signals if s.get("signal_type", "").upper() == "BUY")
        sell_count = sum(1 for s in signals if s.get("signal_type", "").upper() == "SELL")
        hold_count = sum(1 for s in signals if s.get("signal_type", "").upper() == "HOLD")
        fetch_ok = len(fetch_result.get("success", []))
        fetch_fail = len(fetch_result.get("failed", []))
        stale_dropped = len(stale_result.get("dropped", []))
        stale_flagged = len(stale_result.get("stale", []))
        stops_trailed = sum(1 for r in stop_result if r.get("updated")) if stop_result else 0
        errors = st.session_state.analysis_errors

        st.success("✅ Analysis complete!")
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Tickers", st.session_state.analysis_total)
        c2.metric("🟢 BUY", buy_count)
        c3.metric("🔴 SELL", sell_count)
        c4.metric("⚪ HOLD", hold_count)
        c5.metric("Duration", f"{st.session_state.analysis_duration:.1f}s")

        # Sub-details
        if fetch_fail:
            st.warning(f"Data fetch: **{fetch_fail}** ticker(s) failed: {', '.join(fetch_result['failed'])}")
        if stale_dropped:
            st.warning(f"Dropped **{stale_dropped}** stale ticker(s): {', '.join(d['ticker'] for d in stale_result['dropped'])}")
        if stale_flagged:
            st.info(f"Flagged **{stale_flagged}** stale ticker(s) (kept due to open holdings)")
        if stops_trailed:
            st.info(f"Trailed stops for **{stops_trailed}** position(s)")
        if errors:
            st.error(f"**{len(errors)}** error(s) during analysis:")
            for e in errors:
                st.error(f"  {e}")

        st.rerun()


# ======================================================================
# Section 2: Today's Signals
# ======================================================================
def _section_today_signals(db: TradingDB) -> None:
    st.subheader("🎯 Today's Signals")

    # Use session state if fresh, else load from DB
    if st.session_state.analysis_run and st.session_state.analysis_signals:
        signals = st.session_state.analysis_signals
    else:
        today = date.today().isoformat()
        signals = db.get_signals(date=today)

    if not signals:
        st.info("No signals generated yet. Run analysis first.")
        return

    buy_signals = sorted(
        [s for s in signals if s.get("signal_type", "").upper() == "BUY"],
        key=lambda s: s.get("ticker", ""),
    )
    sell_signals = sorted(
        [s for s in signals if s.get("signal_type", "").upper() == "SELL"],
        key=lambda s: s.get("ticker", ""),
    )
    hold_signals = sorted(
        [s for s in signals if s.get("signal_type", "").upper() == "HOLD"],
        key=lambda s: s.get("ticker", ""),
    )

    # BUY signals
    if buy_signals:
        st.markdown("#### 🟢 BUY Signals")
        for s in buy_signals:
            _render_signal_card(s, "green")
    else:
        st.markdown("#### 🟢 BUY Signals")
        st.caption("None")

    # SELL signals
    if sell_signals:
        st.markdown("#### 🔴 SELL Signals")
        for s in sell_signals:
            _render_signal_card(s, "red")
    else:
        st.markdown("#### 🔴 SELL Signals")
        st.caption("None")

    # HOLD — collapsed
    st.markdown(f"#### ⚪ HOLD — {len(hold_signals)} ticker(s)")
    if hold_signals:
        with st.expander("Show HOLD signals"):
            hold_rows = []
            for s in hold_signals:
                hold_rows.append({
                    "Ticker": s.get("ticker", "—"),
                    "RSI": f"{s['rsi']:.1f}" if s.get("rsi") is not None else "—",
                    "Close": _fmt_price(s.get("close")),
                    "Notes": s.get("notes") or "—",
                })
            st.dataframe(pd.DataFrame(hold_rows), width="stretch", hide_index=True)


def _render_signal_card(s: dict[str, Any], color: str) -> None:
    """Render a single signal card with coloured border."""
    border = "green" if color == "green" else "red"
    ticker = s.get("ticker", "—")
    rsi = s.get("rsi")
    close = s.get("close")
    bb_lower = s.get("bb_lower")
    bb_upper = s.get("bb_upper")
    atr = s.get("atr_14")
    notes = s.get("notes") or ""

    cols = st.columns([1, 1, 1, 1, 1, 2])
    cols[0].markdown(f"**{ticker}**")
    cols[1].markdown(f"RSI: {rsi:.1f}" if rsi is not None else "RSI: —")
    cols[2].markdown(f"Close: {_fmt_price(close)}")
    cols[3].markdown(f"BB: {_fmt_price(bb_lower)} – {_fmt_price(bb_upper)}")
    cols[4].markdown(f"ATR: {_fmt_price(atr)}")
    cols[5].markdown(f"*{notes}*" if notes else "")


# ======================================================================
# Section 3: Holdings Alerts
# ======================================================================
def _section_holdings_alerts(db: TradingDB) -> None:
    st.subheader("🚨 Holdings Alerts")

    if st.session_state.analysis_run and st.session_state.analysis_alerts:
        alerts = st.session_state.analysis_alerts
    else:
        from alpha_engine import check_holdings_alerts
        alerts = check_holdings_alerts(db=db)

    if not alerts:
        st.info("No open positions or no alerts triggered.")
        return

    # Sort by priority
    alerts.sort(key=lambda a: a.get("ticker", ""))

    for a in alerts:
        alert_type = a.get("alert_type", "unknown")
        emoji = _ALERT_EMOJI.get(alert_type, "⚪")
        ticker = a.get("ticker", "—")
        current = a.get("current_price")
        stop = a.get("stop_loss")
        target = a.get("target")
        pnl_pct = a.get("unrealized_pnl_pct")
        msg = a.get("message", "")

        # Format the alert type label
        label_map = {
            "stop_loss_hit": "Stop-Loss Hit",
            "sell_signal": "Sell Signal Triggered",
            "target_hit": "Target Reached",
            "profit_alert": "Profit Alert (+10%)",
        }
        label = label_map.get(alert_type, alert_type)

        cols = st.columns([1, 2, 1, 1, 1, 2])
        cols[0].markdown(f"**{ticker}**")
        cols[1].markdown(f"{emoji} **{label}**")
        cols[2].markdown(f"Price: {_fmt_price(current)}")
        cols[3].markdown(f"Stop: {_fmt_price(stop)}")
        cols[4].markdown(f"P&L: {_fmt_pct(pnl_pct)}")
        cols[5].caption(msg)


# ======================================================================
# Section 4: Signal History
# ======================================================================
def _section_signal_history(db: TradingDB) -> None:
    st.subheader("📅 Signal History")

    # Date range: last 30 days
    today = date.today()
    min_date = today - timedelta(days=30)

    selected_date = st.date_input(
        "Select date",
        value=today,
        min_value=min_date,
        max_value=today,
        key="signal_history_date",
    )

    signals = db.get_signals(date=str(selected_date))

    if not signals:
        st.info(f"No signals found for **{selected_date}**.")
        return

    buy = sorted(
        [s for s in signals if s.get("signal_type", "").upper() == "BUY"],
        key=lambda s: s.get("ticker", ""),
    )
    sell = sorted(
        [s for s in signals if s.get("signal_type", "").upper() == "SELL"],
        key=lambda s: s.get("ticker", ""),
    )
    hold = sorted(
        [s for s in signals if s.get("signal_type", "").upper() == "HOLD"],
        key=lambda s: s.get("ticker", ""),
    )

    c1, c2, c3 = st.columns(3)
    c1.metric("🟢 BUY", len(buy))
    c2.metric("🔴 SELL", len(sell))
    c3.metric("⚪ HOLD", len(hold))

    # BUY
    if buy:
        st.markdown("##### 🟢 BUY")
        rows = _signals_to_rows(buy)
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    # SELL
    if sell:
        st.markdown("##### 🔴 SELL")
        rows = _signals_to_rows(sell)
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    # HOLD (collapsed)
    if hold:
        with st.expander(f"⚪ HOLD — {len(hold)} ticker(s)"):
            rows = _signals_to_rows(hold)
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def _signals_to_rows(signals: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Convert signal records to display rows."""
    rows = []
    for s in signals:
        rows.append({
            "Ticker": s.get("ticker", "—"),
            "RSI": f"{s['rsi']:.1f}" if s.get("rsi") is not None else "—",
            "Close": _fmt_price(s.get("close")),
            "BB Lower": _fmt_price(s.get("bb_lower")),
            "BB Upper": _fmt_price(s.get("bb_upper")),
            "ATR": _fmt_price(s.get("atr_14")),
            "Notes": s.get("notes") or "—",
        })
    return rows


# ======================================================================
# Section 5: Send to Telegram
# ======================================================================
def _section_telegram(db: TradingDB) -> None:
    st.subheader("📤 Telegram")

    col_send, col_test = st.columns(2)

    with col_send:
        send_disabled = not st.session_state.analysis_run
        if st.button(
            "📤 Send Report to Telegram",
            disabled=send_disabled,
            width="stretch",
        ):
            with st.spinner("Sending daily report…"):
                from telegram_reporter import send_daily_report
                ok = send_daily_report(db=db)
            if ok:
                st.success("Report sent to Telegram ✅")
                st.session_state.telegram_sent = True
            else:
                st.error("Failed to send report. Check logs and Telegram configuration.")

    with col_test:
        if st.button("🔧 Send Test Message", width="stretch"):
            with st.spinner("Sending test message…"):
                from telegram_reporter import send_test_message
                ok = send_test_message(db=db)
            if ok:
                st.success("Test message sent ✅")
            else:
                st.error("Failed to send test message. Check Telegram configuration.")

    if send_disabled:
        st.caption("Run analysis first before sending a report.")


# ======================================================================
# Section 6: Run History
# ======================================================================
def _section_run_history(db: TradingDB) -> None:
    with st.expander("📜 Run History (last 10)"):
        runs = db.get_recent_runs(limit=10)

        if not runs:
            st.info("No run history yet.")
            return

        rows = []
        for r in runs:
            rows.append({
                "Date": r.get("run_date", "—"),
                "Time": r.get("run_time", "—"),
                "Tickers": r.get("tickers_analyzed", 0),
                "Signals": r.get("signals_generated", 0),
                "Status": r.get("status", "—"),
                "Duration": f"{r.get('duration_seconds', 0):.1f}s",
            })

        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


# ======================================================================
# Main page entry point
# ======================================================================
def show(db: TradingDB = None) -> None:
    """Render the Signals page."""
    _init_state()
    if db is None:
        db = TradingDB()

    st.title("🎯 Signals & Analysis")

    _section_run_analysis(db)

    st.markdown("---")
    _section_today_signals(db)

    st.markdown("---")
    _section_holdings_alerts(db)

    st.markdown("---")
    _section_signal_history(db)

    st.markdown("---")
    _section_telegram(db)

    _section_run_history(db)
