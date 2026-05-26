"""
pages/watchlist.py — Fully functional Watchlist management page.

Provides ticker search, single/batch add, active watchlist table with
indicators, dropped ticker management, and data freshness overview.
"""

from __future__ import annotations

import sys
import os
from datetime import date, datetime
from typing import Any

import streamlit as st
import pandas as pd

# Ensure local imports work regardless of cwd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import TradingDB
from watchlist_manager import (
    search_ticker,
    add_ticker,
    add_tickers_batch,
    drop_ticker,
    reactivate_ticker,
    get_watchlist_status,
)
from data_fetcher import update_ticker
from alpha_engine import compute_indicators


# ======================================================================
# Cached DB singleton
# ======================================================================
# ======================================================================
# Helper: recompute indicators after data update
# ======================================================================
def _recompute_indicators(ticker: str, db: TradingDB) -> bool:
    """Recompute and store indicators for a single ticker."""
    ohlcv = db.get_ohlcv(ticker)
    if not ohlcv:
        return False
    pdf = pd.DataFrame(ohlcv)
    pdf = compute_indicators(pdf)
    if pdf.empty:
        return False
    rows: list[dict[str, Any]] = []
    for _, row in pdf.iterrows():
        d: dict[str, Any] = {"date": str(row["date"])[:10]}
        for col in ("rsi_14", "bb_lower", "bb_mid", "bb_upper", "atr_14", "sma_200", "bb_width"):
            if col in pdf.columns:
                val = row[col]
                d[col] = float(val) if pd.notna(val) else None
        rows.append(d)
    db.upsert_indicators(ticker, rows)
    return True


# ======================================================================
# Helper: format market cap for display
# ======================================================================
def _fmt_market_cap(mc: int | float | None) -> str:
    if mc is None:
        return "—"
    if mc >= 1e12:
        return f"${mc / 1e12:.2f}T"
    if mc >= 1e9:
        return f"${mc / 1e9:.2f}B"
    if mc >= 1e6:
        return f"${mc / 1e6:.2f}M"
    return f"${mc:,.0f}"


# ======================================================================
# Helper: RSI colour tag
# ======================================================================
def _rsi_label(rsi: float | None) -> str:
    if rsi is None:
        return "—"
    if rsi > 70:
        return f"🔴 {rsi:.1f}"
    if rsi < 30:
        return f"🟢 {rsi:.1f}"
    return f"{rsi:.1f}"


# ======================================================================
# Helper: signal emoji
# ======================================================================
def _signal_emoji(sig) -> str:
    import pandas as pd
    if sig is None or (isinstance(sig, float) and pd.isna(sig)):
        return "—"
    sig_upper = str(sig).upper()
    if sig_upper == "BUY":
        return "🟢 BUY"
    if sig_upper == "SELL":
        return "🔴 SELL"
    if sig_upper == "HOLD":
        return "⚪ HOLD"
    return sig


# ======================================================================
# Section 1: Add New Ticker
# ======================================================================
def _section_add_ticker(db: TradingDB) -> None:
    st.subheader("🔍 Add New Ticker")

    with st.container():
        col_input, col_search = st.columns([3, 1])
        with col_input:
            ticker_input = st.text_input(
                "Ticker Symbol",
                placeholder="e.g. AAPL",
                key="add_ticker_input",
            )
        with col_search:
            st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
            search_clicked = st.button("Search", key="search_ticker_btn", use_container_width=True)

        # Search results area (persists in session state)
        if search_clicked and ticker_input.strip():
            with st.spinner("Looking up ticker..."):
                info = search_ticker(ticker_input.strip())
            if info["valid"]:
                st.session_state["search_result"] = info
            else:
                st.session_state.pop("search_result", None)
                st.error(f"Ticker **{ticker_input.strip().upper()}** not found or invalid.")

        if "search_result" in st.session_state and st.session_state["search_result"]:
            info = st.session_state["search_result"]
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Name", info.get("name") or "—")
            c2.metric("Price", f"${info['price']}" if info.get("price") else "—")
            c3.metric("Sector", info.get("sector") or "—")
            c4.metric("Market Cap", _fmt_market_cap(info.get("market_cap")))

            reason = st.text_input("Reason (optional)", key="add_ticker_reason")
            fetch_hist = st.checkbox("Fetch historical data on add", value=True, key="add_ticker_fetch")

            if st.button("✅ Add to Watchlist", key="add_ticker_btn"):
                try:
                    with st.spinner("Adding ticker..."):
                        result = add_ticker(
                            info["ticker"],
                            reason=reason,
                            db=db,
                            fetch_data=fetch_hist,
                        )
                    if result.get("data_fetched"):
                        st.success(f"**{info['ticker']}** added with historical data ✅")
                    else:
                        st.warning(
                            f"**{info['ticker']}** added, but data fetch was skipped or failed."
                        )
                    st.session_state.pop("search_result", None)
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to add ticker: {e}")


# ======================================================================
# Section 2: Batch Add
# ======================================================================
def _section_batch_add(db: TradingDB) -> None:
    st.subheader("📝 Batch Add")

    with st.container():
        tickers_text = st.text_area(
            "Enter tickers (one per line or comma-separated)",
            placeholder="AAPL\nMSFT, GOOGL\nTSLA",
            height=100,
            key="batch_add_text",
        )

        batch_reason = st.text_input("Reason (applied to all)", key="batch_add_reason")

        if st.button("➕ Add All", key="batch_add_btn"):
            if not tickers_text.strip():
                st.warning("Enter at least one ticker.")
                return

            # Parse tickers: split by newlines and commas
            raw = tickers_text.replace(",", "\n").split("\n")
            ticker_list = [t.strip().upper() for t in raw if t.strip()]

            if not ticker_list:
                st.warning("No valid tickers found.")
                return

            progress = st.progress(0, text=f"Adding {len(ticker_list)} tickers...")
            try:
                result = add_tickers_batch(ticker_list, reason=batch_reason, db=db)
                progress.progress(100, text="Done!")

                added = result.get("added", [])
                skipped = result.get("skipped", [])
                errors = result.get("errors", [])

                if added:
                    st.success(f"Added **{len(added)}** ticker(s)")
                if skipped:
                    st.info(f"Skipped **{len(skipped)}** already-active ticker(s): {', '.join(skipped)}")
                if errors:
                    st.error(f"**{len(errors)}** error(s):")
                    for err in errors:
                        st.error(f"  {err['ticker']}: {err['error']}")

                st.rerun()
            except Exception as e:
                st.error(f"Batch add failed: {e}")


# ======================================================================
# Section 3: Active Watchlist Table
# ======================================================================
def _section_active_watchlist(db: TradingDB) -> None:
    st.subheader("📋 Active Watchlist")

    # Refresh button
    col_refresh, col_count = st.columns([1, 4])
    with col_refresh:
        if st.button("🔄 Refresh Data", key="refresh_all_btn"):
            with st.spinner("Updating all tickers..."):
                tickers = db.get_active_tickers()
                updated = 0
                for i, t in enumerate(tickers):
                    update_ticker(t, db=db)
                    _recompute_indicators(t, db)
                    updated += 1
                st.success(f"Updated **{updated}** ticker(s)")
                st.rerun()

    # Load watchlist status
    with st.spinner("Loading watchlist..."):
        status = get_watchlist_status(db=db)

    active = status.get("active", [])

    if not active:
        st.info("No active tickers in the watchlist. Add one above!")
        return

    # Sort alphabetically by ticker
    active.sort(key=lambda x: x["ticker"])

    # Build a DataFrame for display
    rows = []
    for entry in active:
        rows.append(
            {
                "Ticker": entry["ticker"],
                "Date Added": entry.get("date_added", "—"),
                "Latest Date": entry.get("latest_date", "—") or "—",
                "Days Behind": entry.get("days_behind"),
                "RSI": entry.get("latest_rsi"),
                "Signal": entry.get("latest_signal"),
                "Days Active": entry.get("days_active"),
            }
        )

    if rows:
        df = pd.DataFrame(rows)
        # Format columns for display
        display_df = df.copy()
        display_df["RSI"] = display_df["RSI"].apply(
            lambda x: f"{x:.1f}" if x is not None else "—"
        )
        display_df["Days Behind"] = display_df["Days Behind"].apply(
            lambda x: str(x) if x is not None else "—"
        )
        display_df["Days Active"] = display_df["Days Active"].apply(
            lambda x: str(x) if x is not None else "—"
        )
        display_df["Signal"] = display_df["Signal"].apply(_signal_emoji)

        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True,
        )

    # Drop ticker — dropdown instead of long button list
    st.markdown("#### Actions")
    col_drop, col_btn = st.columns([3, 1])
    with col_drop:
        ticker_options = [entry["ticker"] for entry in active]
        selected_drop = st.selectbox(
            "Select ticker to drop",
            options=ticker_options,
            key="drop_ticker_select",
            label_visibility="collapsed",
        )
    with col_btn:
        st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        if st.button("🗑️ Drop", key="drop_selected_btn", type="secondary"):
            if selected_drop:
                try:
                    result = drop_ticker(selected_drop, reason="Manual drop", db=db)
                    if result["status"] == "dropped":
                        st.success(f"Dropped **{selected_drop}**")
                        st.rerun()
                    else:
                        st.warning(result.get("reason", "Could not drop ticker."))
                except Exception as e:
                    st.error(f"Failed to drop {selected_drop}: {e}")


# ======================================================================
# Section 4: Dropped Tickers
# ======================================================================
def _section_dropped(db: TradingDB) -> None:
    with st.expander("🗑️ Dropped Tickers"):
        status = get_watchlist_status(db=db)
        dropped = status.get("dropped", [])

        if not dropped:
            st.info("No dropped tickers.")
            return

        for entry in dropped:
            ticker = entry["ticker"]
            col_info, col_action = st.columns([4, 1])
            with col_info:
                st.markdown(
                    f"**{ticker}** — "
                    f"Added: {entry.get('date_added', '—')} · "
                    f"Dropped: {entry.get('date_dropped', '—')} · "
                    f"Reason: {entry.get('reason', '—')}"
                )
            with col_action:
                if st.button(f"♻️ Reactivate", key=f"reactivate_{ticker}"):
                    try:
                        result = reactivate_ticker(ticker, db=db, fetch_data=True)
                        if result["status"] == "active":
                            st.success(f"Reactivated **{ticker}**")
                            st.rerun()
                        else:
                            st.warning(f"Could not reactivate: {result}")
                    except Exception as e:
                        st.error(f"Failed to reactivate {ticker}: {e}")


# ======================================================================
# Section 5: Data Status
# ======================================================================
def _section_data_status(db: TradingDB) -> None:
    st.markdown("---")
    st.subheader("📊 Data Status")

    status = get_watchlist_status(db=db)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Active Tickers", status.get("total_active", 0))
    c2.metric("Dropped Tickers", status.get("total_dropped", 0))

    # Data freshness with detail
    data_status = status.get("data_status", "no_data")
    freshness_map = {
        "fresh": ("🟢 Fresh", "Data is up to date (≤ 5 trading days behind)"),
        "stale": ("🟡 Stale", "More than half of tickers are > 5 trading days behind"),
        "no_data": ("🔴 No Data", "No OHLCV data available"),
    }
    label, desc = freshness_map.get(data_status, ("⚪ Unknown", ""))
    c3.metric("Data Status", label)
    c4.write("")  # empty placeholder
    st.caption(f"ℹ️ {desc}")

    # Per-ticker freshness breakdown
    if data_status == "fresh":
        behind_counts = {}
        for a in status.get("active", []):
            dbb = a.get("days_behind")
            if dbb is not None:
                bucket = "0" if dbb == 0 else "1-2" if dbb <= 2 else "3-5" if dbb <= 5 else "6+"
                behind_counts[bucket] = behind_counts.get(bucket, 0) + 1
        if behind_counts:
            detail = " | ".join(f"{k} days: {v}" for k, v in sorted(behind_counts.items()))
            st.caption(f"📊 Days behind breakdown: {detail}")

    # Last run info
    runs = db.get_recent_runs(limit=1)
    if runs:
        last_run = runs[0]
        run_date = f"{last_run.get('run_date', '—')} {last_run.get('run_time', '')}"
        st.caption(f"Last analysis run: {run_date} ({last_run.get('status', '—')})")
    else:
        st.caption("No analysis runs recorded yet.")


# ======================================================================
# Main page entry point
# ======================================================================
def show(db: TradingDB = None) -> None:
    """Render the Watchlist page."""
    if db is None:
        db = TradingDB()

    st.title("📋 Watchlist Management")

    _section_add_ticker(db)

    st.markdown("---")
    _section_batch_add(db)

    st.markdown("---")
    _section_active_watchlist(db)

    st.markdown("---")
    _section_dropped(db)

    _section_data_status(db)
