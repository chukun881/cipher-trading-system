"""
pages/settings.py — Settings page for the Cipher Trading System.

Telegram config, schedule, data management, system info, and danger zone.
"""

from __future__ import annotations

import os
import platform
import sqlite3
import sys
from datetime import datetime

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import TradingDB


# ======================================================================
# Cached DB singleton
# ======================================================================
# ======================================================================
# Session state defaults
# ======================================================================
def _init_state() -> None:
    defaults = {
        "tg_test_result": None,
        "tg_test_ok": False,
        "refresh_progress": None,
        "refresh_done": False,
        "refresh_summary": None,
        "incremental_done": False,
        "incremental_summary": None,
        "cleanup_done": False,
        "cleanup_result": None,
        "danger_confirm_reset": False,
        "danger_confirm_signals": False,
        "danger_confirm_trades": False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


# ======================================================================
# Section 1: Telegram Configuration
# ======================================================================
def _section_telegram(db: TradingDB) -> None:
    st.subheader("📨 Telegram Configuration")

    token = db.get_setting("telegram_bot_token", "")
    chat_id = db.get_setting("telegram_chat_id", "")

    # Status indicator
    if token and chat_id:
        st.success("✅ Connected")
    else:
        st.warning("❌ Not configured")

    # Hint for current token
    if token:
        masked = "*" * (len(token) - 4) + token[-4:]
        st.caption(f"Current token: {masked}")
    if chat_id:
        st.caption(f"Current chat ID: {chat_id}")

    with st.form("telegram_form"):
        new_token = st.text_input("Bot Token", type="password", placeholder="Enter Telegram bot token")
        new_chat_id = st.text_input("Chat ID", placeholder="Enter Telegram chat ID")
        submitted = st.form_submit_button("💾 Save Telegram Config", type="primary")

        if submitted:
            if new_token:
                db.set_setting("telegram_bot_token", new_token)
            if new_chat_id:
                db.set_setting("telegram_chat_id", new_chat_id)
            st.success("Telegram configuration saved.")
            st.rerun()

    # Test connection
    st.markdown("---")
    if st.button("🔧 Test Connection", use_container_width=True):
        with st.spinner("Sending test message…"):
            from telegram_reporter import send_test_message
            ok = send_test_message(db=db)
        if ok:
            st.success("Test message sent ✅")
        else:
            st.error("Failed to send test message. Check your token and chat ID.")


# ======================================================================
# Section 2: Schedule Configuration
# ======================================================================
def _section_schedule(db: TradingDB) -> None:
    st.subheader("⏰ Schedule Configuration")

    current_schedule = db.get_setting("schedule_time", "")

    if current_schedule:
        st.info(f"Current schedule: **{current_schedule}** daily")
    else:
        st.info("No schedule configured.")

    st.caption("All times in **Asia/Kuala_Lumpur (UTC+8)**")
    st.caption(
        "⚠️ Schedule only works when the computer is on and the dashboard is running. "
        "For 24/7 scheduled runs, deploy to a server."
    )

    col_set, col_clear = st.columns(2)

    with col_set:
        schedule_input = st.text_input(
            "Schedule Time (HH:MM, 24h format)",
            placeholder="09:30",
            key="schedule_time_input",
        )
        if st.button("📅 Set Schedule", use_container_width=True):
            if schedule_input:
                # Validate format
                try:
                    parts = schedule_input.strip().split(":")
                    if len(parts) != 2:
                        raise ValueError
                    h, m = int(parts[0]), int(parts[1])
                    if not (0 <= h <= 23 and 0 <= m <= 59):
                        raise ValueError
                    formatted = f"{h:02d}:{m:02d}"
                    db.set_setting("schedule_time", formatted)
                    st.success(f"Schedule set to **{formatted}** daily.")
                    st.rerun()
                except (ValueError, IndexError):
                    st.error("Invalid time format. Use HH:MM (e.g., 09:30).")
            else:
                st.warning("Enter a time first.")

    with col_clear:
        if st.button("🗑️ Clear Schedule", use_container_width=True):
            db.set_setting("schedule_time", "")
            st.success("Schedule cleared.")
            st.rerun()


# ======================================================================
# Section 3: Data Management
# ======================================================================
def _section_data_management(db: TradingDB) -> None:
    st.subheader("🗄️ Data Management")

    # Data stats
    _show_data_stats(db)

    st.markdown("---")

    # Refresh All Data
    col_refresh, col_incremental = st.columns(2)

    with col_refresh:
        if st.button("🔄 Refresh All Data", use_container_width=True, type="primary"):
            tickers = db.get_active_tickers()
            total = len(tickers)
            if total == 0:
                st.warning("No active tickers to refresh.")
            else:
                from data_fetcher import fetch_and_store_all
                with st.spinner(f"Refreshing {total} ticker(s)… This may take a while."):
                    result = fetch_and_store_all(db=db)
                success = len(result.get("success", []))
                failed = result.get("failed", [])
                st.session_state.refresh_done = True
                st.session_state.refresh_summary = result
                if failed:
                    st.success(f"Refresh complete: **{success}** succeeded, **{len(failed)}** failed.")
                    st.warning(f"Failed tickers: {', '.join(failed)}")
                else:
                    st.success(f"Refresh complete: **{success}** ticker(s) updated successfully.")

    with col_incremental:
        if st.button("⚡ Update Incremental", use_container_width=True):
            tickers = db.get_active_tickers()
            total = len(tickers)
            if total == 0:
                st.warning("No active tickers to update.")
            else:
                from data_fetcher import update_ticker
                success_count = 0
                fail_count = 0
                progress = st.progress(0, text=f"Updating 0/{total}…")
                for i, ticker in enumerate(tickers):
                    progress.progress(
                        (i + 1) / total,
                        text=f"Updating {ticker} ({i + 1}/{total})…",
                    )
                    try:
                        ok = update_ticker(ticker, db=db)
                        if ok:
                            success_count += 1
                        else:
                            fail_count += 1
                    except Exception:
                        fail_count += 1
                progress.empty()
                st.session_state.incremental_done = True
                st.session_state.incremental_summary = {
                    "success": success_count,
                    "failed": fail_count,
                }
                st.success(
                    f"Incremental update complete: **{success_count}** updated, **{fail_count}** failed."
                )

    st.markdown("---")

    # Clean up dropped data
    if st.button("🧹 Clean Up Dropped Data (>90 days)", use_container_width=True):
        from watchlist_manager import cleanup_dropped_data
        with st.spinner("Cleaning up old data for dropped tickers…"):
            result = cleanup_dropped_data(db=db)
        cleaned = result.get("cleaned", [])
        rows = result.get("rows_deleted", 0)
        st.session_state.cleanup_done = True
        st.session_state.cleanup_result = result
        if cleaned:
            st.success(
                f"Cleaned up **{len(cleaned)}** dropped ticker(s), **{rows}** rows removed."
            )
        else:
            st.info("No dropped tickers older than 90 days to clean up.")


def _show_data_stats(db: TradingDB) -> None:
    """Display data statistics from the database."""
    try:
        conn = db.conn

        # Total tickers with data
        ticker_count = conn.execute(
            "SELECT COUNT(DISTINCT ticker) FROM ohlcv"
        ).fetchone()[0]

        # Total OHLCV rows
        ohlcv_rows = conn.execute("SELECT COUNT(*) FROM ohlcv").fetchone()[0]

        # Latest data date
        latest = conn.execute(
            "SELECT MAX(date) FROM ohlcv"
        ).fetchone()[0]
        latest_str = latest if latest else "—"

        # Active tickers
        active = len(db.get_active_tickers())

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Active Tickers", active)
        c2.metric("Tickers with Data", ticker_count)
        c3.metric("Total OHLCV Rows", f"{ohlcv_rows:,}")
        c4.metric("Latest Data Date", latest_str)
    except Exception:
        st.caption("Unable to load data stats.")


# ======================================================================
# Section 4: System Information
# ======================================================================
def _section_system_info(db: TradingDB) -> None:
    st.subheader("ℹ️ System Information")

    # Database info
    db_path = db.db_path
    db_size = "—"
    if os.path.exists(db_path):
        size_bytes = os.path.getsize(db_path)
        if size_bytes >= 1_048_576:
            db_size = f"{size_bytes / 1_048_576:.1f} MB"
        elif size_bytes >= 1_024:
            db_size = f"{size_bytes / 1_024:.1f} KB"
        else:
            db_size = f"{size_bytes} B"

    # Table info
    table_info = []
    try:
        conn = db.conn
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        for (table_name,) in tables:
            count = conn.execute(f"SELECT COUNT(*) FROM [{table_name}]").fetchone()[0]
            table_info.append((table_name, count))
    except Exception:
        table_info = [("—", 0)]

    # Module versions
    module_versions = {}
    for mod_name in ["yfinance", "pandas", "pandas_ta", "streamlit", "plotly"]:
        try:
            mod = __import__(mod_name)
            module_versions[mod_name] = getattr(mod, "__version__", "installed")
        except ImportError:
            module_versions[mod_name] = "not installed"

    # Last run
    recent_runs = db.get_recent_runs(limit=1)
    last_run = "—"
    if recent_runs:
        r = recent_runs[0]
        last_run = f"{r.get('run_date', '—')} {r.get('run_time', '')}"

    # Display
    col_left, col_right = st.columns(2)

    with col_left:
        st.markdown("##### Database")
        st.text(f"Path:    {db_path}")
        st.text(f"Size:    {db_size}")
        st.text(f"Tables:  {len(table_info)}")
        for name, count in table_info:
            st.text(f"  • {name}: {count:,} rows")

    with col_right:
        st.markdown("##### Environment")
        st.text(f"Python:        {platform.python_version()}")
        for mod, ver in module_versions.items():
            st.text(f"{mod + ':':<15}{ver}")
        st.text(f"Trading Dir:   {os.path.dirname(db_path)}")
        st.text(f"Last Run:      {last_run}")
        st.text(f"Platform:      {platform.system()} {platform.release()}")


# ======================================================================
# Section 5: Danger Zone
# ======================================================================
def _section_danger_zone(db: TradingDB) -> None:
    with st.expander("🚨 Danger Zone", expanded=False):
        st.error("⚠️ These actions are destructive and cannot be undone. Proceed with caution.")
        st.markdown("---")

        # Confirmation checkbox — shared requirement
        confirm = st.checkbox(
            "☑️ I understand this cannot be undone",
            key="danger_confirm",
            value=False,
        )

        st.markdown("---")

        col1, col2, col3 = st.columns(3)

        # Reset All Settings
        with col1:
            if st.button(
                "🔄 Reset All Settings",
                disabled=not confirm,
                use_container_width=True,
            ):
                from db import _DEFAULT_SETTINGS
                for k, v in _DEFAULT_SETTINGS.items():
                    db.set_setting(k, v)
                st.success("All settings reset to defaults.")
                st.rerun()

        # Clear All Signals
        with col2:
            if st.button(
                "🗑️ Clear All Signals",
                disabled=not confirm,
                use_container_width=True,
            ):
                db.conn.execute("DELETE FROM signals")
                db.conn.commit()
                st.success("All signals cleared.")
                st.rerun()

        # Clear All Trade History
        with col3:
            if st.button(
                "🗑️ Clear All Trades",
                disabled=not confirm,
                use_container_width=True,
            ):
                db.conn.execute("DELETE FROM trades")
                db.conn.commit()
                st.success("All trade history cleared.")
                st.rerun()

        st.markdown("---")

        # Export Database
        if os.path.exists(db.db_path):
            with open(db.db_path, "rb") as f:
                st.download_button(
                    "📥 Export Database",
                    data=f.read(),
                    file_name=f"trading_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db",
                    mime="application/x-sqlite3",
                    use_container_width=True,
                )
        else:
            st.warning("Database file not found for export.")


# ======================================================================
# Main page entry point
# ======================================================================
def show(db: TradingDB = None) -> None:
    """Render the Settings page."""
    _init_state()
    if db is None:
        db = TradingDB()

    st.title("⚙️ Settings")

    _section_telegram(db)

    st.markdown("---")
    _section_schedule(db)

    st.markdown("---")
    _section_data_management(db)

    st.markdown("---")
    _section_system_info(db)

    st.markdown("---")
    _section_danger_zone(db)
