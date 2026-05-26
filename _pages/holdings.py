"""
pages/holdings.py — Holdings management page.

Open/close positions, track unrealized P&L, update stops,
and review recent closings.
"""

from __future__ import annotations

import sys
import os
from datetime import date, datetime
from typing import Any

import streamlit as st

# Ensure local imports work regardless of cwd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import TradingDB
from portfolio import (
    open_position,
    close_position,
    get_portfolio_status,
    batch_update_stops,
    update_stops,
)


# ======================================================================
# Cached DB singleton
# ======================================================================
# ======================================================================
# Helpers
# ======================================================================

def _fmt_currency(value: float | None) -> str:
    """Format a number as USD with 2 decimal places."""
    if value is None:
        return "—"
    return f"${value:,.2f}"


def _fmt_pct(value: float | None) -> str:
    """Format a number as percentage with 2 decimal places."""
    if value is None:
        return "—"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def _status_badge(status: str) -> str:
    """Return a colour-coded status emoji + label."""
    mapping = {
        "stop_loss_risk": "🔴 Stop Risk",
        "target_close": "🟡 Target Close",
        "profit_alert": "🟢 Profit Alert",
        "normal": "⚪ Normal",
    }
    return mapping.get(status, f"⚪ {status}")


# ======================================================================
# Section 1: Open New Position
# ======================================================================
def _section_open_position(db: TradingDB) -> None:
    st.subheader("➕ Open New Position")

    # Get active watchlist tickers for the dropdown
    active_tickers = db.get_active_tickers()

    with st.form("open_position_form"):
        col_ticker1, col_ticker2 = st.columns(2)

        with col_ticker1:
            if active_tickers:
                ticker_selection = st.selectbox(
                    "Ticker",
                    options=[""] + active_tickers,
                    index=0,
                    help="Select from watchlist or type below",
                )
            else:
                ticker_selection = ""
                st.info("No active watchlist tickers. Type a ticker below.")

        with col_ticker2:
            ticker_manual = st.text_input(
                "Or enter ticker manually",
                placeholder="e.g. AAPL",
            )

        col_price, col_vol = st.columns(2)
        with col_price:
            buy_price = st.number_input(
                "Buy Price ($)",
                min_value=0.01,
                step=0.01,
                format="%.2f",
            )
        with col_vol:
            volume = st.number_input(
                "Shares",
                min_value=0.0001,
                step=0.0001,
                value=1.0,
                format="%.4f",
            )

        col_date, col_notes = st.columns(2)
        with col_date:
            buy_date = st.date_input("Buy Date", value=date.today())
        with col_notes:
            notes = st.text_input("Notes (optional)", placeholder="e.g. Earnings play")

        submitted = st.form_submit_button("📂 Open Position", use_container_width=True)

    if submitted:
        ticker = (ticker_manual.strip().upper()
                  if ticker_manual.strip()
                  else (ticker_selection.strip().upper() if ticker_selection else ""))

        if not ticker:
            st.error("Please select or enter a ticker symbol.")
            return

        try:
            with st.spinner(f"Opening position for **{ticker}**..."):
                result = open_position(
                    ticker=ticker,
                    buy_price=float(buy_price),
                    volume=float(volume),
                    buy_date=str(buy_date),
                    db=db,
                    notes=notes,
                )

            st.success(f"Position opened for **{result['ticker']}** ✅")

            # Show calculated stop-loss and target
            c1, c2, c3 = st.columns(3)
            c1.metric("Buy Price", _fmt_currency(result["buy_price"]))
            c2.metric("Stop-Loss", _fmt_currency(result["stop_loss"]),
                       delta=_fmt_pct((result["stop_loss"] / result["buy_price"] - 1) * 100))
            c3.metric("Target", _fmt_currency(result["target"]),
                       delta=_fmt_pct((result["target"] / result["buy_price"] - 1) * 100))

            st.info(
                f"**{result['ticker']}** — {result['volume']} shares @ {_fmt_currency(result['buy_price'])}  \n"
                f"Holding ID: {result['holding_id']}  \n"
                f"Stop: {_fmt_currency(result['stop_loss'])} · Target: {_fmt_currency(result['target'])}"
            )
            st.rerun()

        except ValueError as e:
            st.error(f"Cannot open position: {e}")
        except Exception as e:
            st.error(f"Unexpected error: {e}")


# ======================================================================
# Section 2: Portfolio Summary + Current Holdings
# ======================================================================
def _section_current_holdings(db: TradingDB) -> None:
    st.subheader("📊 Current Holdings")

    with st.spinner("Loading portfolio..."):
        portfolio = get_portfolio_status(db=db)

    holdings = portfolio.get("holdings", [])

    if not holdings:
        st.info("No open positions. Open one above to get started!")
        return

    # ── Portfolio summary metrics ──
    total_value = portfolio["total_value"]
    total_cost = portfolio["total_cost"]
    total_pnl = portfolio["total_unrealized_pnl"]
    total_pnl_pct = portfolio["total_unrealized_pnl_pct"]
    num_positions = portfolio["total_holdings"]

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Positions", str(num_positions))
    m2.metric("Total Cost", _fmt_currency(total_cost))
    m3.metric("Market Value", _fmt_currency(total_value))
    pnl_color = "normal" if total_pnl >= 0 else "inverse"
    m4.metric("Unrealized P&L", _fmt_currency(total_pnl),
               delta=_fmt_pct(total_pnl_pct), delta_color=pnl_color)

    st.markdown("---")

    # ── Holdings table ──
    st.markdown("#### Holdings Detail")

    # Build display data
    display_rows = []
    for h in holdings:
        pnl_str = f"${h['unrealized_pnl']:+,.2f}"
        pnl_pct_str = f"{h['unrealized_pnl_pct']:+.2f}%"

        display_rows.append({
            "Ticker": h["ticker"],
            "Buy Price": _fmt_currency(h["buy_price"]),
            "Current": _fmt_currency(h["current_price"]),
            "Shares": h["volume"],
            "Buy Date": h["buy_date"],
            "Days": h["days_held"],
            "Stop-Loss": _fmt_currency(h["stop_loss"]),
            "Target": _fmt_currency(h["target"]),
            "P&L ($)": pnl_str,
            "P&L (%)": pnl_pct_str,
            "Status": _status_badge(h["status"]),
        })

    # Display as dataframe for readability
    import pandas as pd
    df = pd.DataFrame(display_rows)
    st.dataframe(df, use_container_width=True, hide_index=True)

    # ── Actions per holding ──
    st.markdown("#### Actions")
    for h in holdings:
        cols = st.columns([1, 1, 1, 1, 2])
        with cols[0]:
            st.markdown(f"**{h['ticker']}**")
        with cols[1]:
            if st.button("✖ Close", key=f"close_btn_{h['id']}"):
                st.session_state.closing_holding_id = h["id"]
                st.rerun()
        with cols[2]:
            if st.button("🔄 Update Stop", key=f"update_stop_{h['id']}"):
                try:
                    result = update_stops(h["ticker"], db=db)
                    if result.get("updated"):
                        st.success(
                            f"**{h['ticker']}** stop trailed: "
                            f"{_fmt_currency(result['old_stop'])} → {_fmt_currency(result['new_stop'])}"
                        )
                    else:
                        st.info(f"**{h['ticker']}** stop unchanged ({_fmt_currency(result['new_stop'])})")
                except Exception as e:
                    st.error(f"Failed to update stop for {h['ticker']}: {e}")
        with cols[3]:
            if st.button("🗑️ Delete", key=f"delete_btn_{h['id']}"):
                st.session_state.deleting_holding_id = h["id"]
                st.rerun()

    # ── Delete confirmation ──
    if st.session_state.get("deleting_holding_id"):
        hid = st.session_state.deleting_holding_id
        holding = None
        for h in holdings:
            if h["id"] == hid:
                holding = h
                break
        if holding:
            st.warning(f"⚠️ Delete **{holding['ticker']}** ({holding['volume']} shares @ {holding['buy_price']})? This cannot be undone.")
            c_del, c_cancel, _ = st.columns([1, 1, 2])
            if c_del.button("✅ Confirm Delete", key=f"confirm_del_{hid}"):
                try:
                    db.conn.execute("DELETE FROM holdings WHERE id=?", (hid,))
                    db.conn.commit()
                    st.session_state.deleting_holding_id = None
                    st.success("Holding deleted.")
                    st.rerun()
                except Exception as e:
                    st.session_state.deleting_holding_id = None
                    st.error(f"Delete failed: {e}")
                    st.rerun()
            if c_cancel.button("❌ Cancel", key=f"cancel_del_{hid}"):
                st.session_state.deleting_holding_id = None
                st.rerun()
        else:
            # Holding no longer exists — reset state
            st.session_state.deleting_holding_id = None
            st.rerun()


# ======================================================================
# Section 3: Close Position Dialog
# ======================================================================
def _section_close_position(db: TradingDB) -> None:
    """Show the close-position form if a holding is selected for closing."""
    if st.session_state.closing_holding_id is None:
        return

    holding_id = st.session_state.closing_holding_id

    # Fetch holding info
    holdings_raw = db.get_holdings()
    holding = next((h for h in holdings_raw if h["id"] == holding_id), None)

    if holding is None:
        st.error("Holding not found. It may have already been closed.")
        st.session_state.closing_holding_id = None
        return

    ticker = holding["ticker"]
    buy_price = holding["buy_price"]
    volume = holding["volume"]

    # Get current price for default
    ohlcv = db.get_ohlcv(ticker, days=1)
    current_price = ohlcv[-1]["close"] if ohlcv else buy_price

    st.markdown("---")
    st.subheader(f"✖ Close Position — {ticker}")

    c1, c2, c3 = st.columns(3)
    c1.metric("Buy Price", _fmt_currency(buy_price))
    c2.metric("Current Price", _fmt_currency(current_price))
    c3.metric("Shares", str(volume))

    with st.form("close_position_form"):
        col_sell_price, col_sell_date = st.columns(2)
        with col_sell_price:
            sell_price = st.number_input(
                "Sell Price ($)",
                min_value=0.01,
                value=float(current_price),
                step=0.01,
                format="%.2f",
            )
        with col_sell_date:
            sell_date = st.date_input("Sell Date", value=date.today())

        close_notes = st.text_input("Notes (optional)", placeholder="e.g. Stop hit, target reached")

        col_confirm, col_cancel = st.columns(2)
        with col_confirm:
            confirmed = st.form_submit_button("✅ Confirm Close", use_container_width=True)
        with col_cancel:
            cancelled = st.form_submit_button("❌ Cancel", use_container_width=True)

    if cancelled:
        st.session_state.closing_holding_id = None
        st.rerun()

    if confirmed:
        try:
            with st.spinner(f"Closing position for **{ticker}**..."):
                result = close_position(
                    holding_id=holding_id,
                    sell_price=float(sell_price),
                    sell_date=str(sell_date),
                    db=db,
                    notes=close_notes,
                )

            pnl = result["pnl"]
            pnl_color = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"

            st.success(f"{pnl_color} Position closed — **{result['ticker']}**")
            r1, r2, r3, r4 = st.columns(4)
            r1.metric("Buy", _fmt_currency(result["buy_price"]))
            r2.metric("Sell", _fmt_currency(result["sell_price"]))
            r3.metric("P&L", _fmt_currency(pnl), delta=_fmt_pct(result["pnl_pct"]))
            r4.metric("Hold Days", str(result["hold_days"]))

            st.session_state.closing_holding_id = None
            st.rerun()

        except ValueError as e:
            st.error(f"Cannot close position: {e}")
        except Exception as e:
            st.error(f"Unexpected error: {e}")


# ======================================================================
# Section 4: Batch Update Stops
# ======================================================================
def _section_update_stops(db: TradingDB) -> None:
    st.markdown("---")
    st.subheader("🔄 Update Stops")

    col_btn, col_info = st.columns([1, 3])
    with col_btn:
        if st.button("🔄 Update All Stops", use_container_width=True):
            with st.spinner("Updating stops for all positions..."):
                results = batch_update_stops(db=db)

            if not results:
                st.info("No open positions to update.")
                return

            trailed = [r for r in results if r.get("updated")]
            unchanged = [r for r in results if not r.get("updated")]
            errors = [r for r in results if r.get("error")]

            if trailed:
                st.success(f"**{len(trailed)}** position(s) stop trailed up:")
                for r in trailed:
                    st.markdown(
                        f"- **{r['ticker']}**: "
                        f"stop {_fmt_currency(r['old_stop'])} → {_fmt_currency(r['new_stop'])} · "
                        f"target {_fmt_currency(r['old_target'])} → {_fmt_currency(r['new_target'])}"
                    )

            if unchanged:
                st.info(f"**{len(unchanged)}** position(s) unchanged:")
                for r in unchanged:
                    reason = r.get("reason", "stop/target already optimal")
                    st.markdown(f"- **{r['ticker']}**: {reason}")

            if errors:
                st.error(f"**{len(errors)}** error(s):")
                for r in errors:
                    st.markdown(f"- **{r.get('ticker', '?')}**: {r['error']}")

    with col_info:
        st.caption(
            "Trails stop-loss upward based on latest ATR. "
            "Stops never move down. Targets only increase."
        )


# ======================================================================
# Section 5: Recent Closings
# ======================================================================
def _section_recent_closings(db: TradingDB) -> None:
    st.markdown("---")
    st.subheader("📜 Recent Closings")

    trades = db.get_trades()

    if not trades:
        st.info("No closed trades yet.")
        return

    # Show last 5
    recent = trades[:5]

    import pandas as pd

    rows = []
    for t in recent:
        pnl = t.get("pnl", 0) or 0
        pnl_emoji = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"

        # Calculate hold days
        try:
            buy_dt = datetime.strptime(t["buy_date"], "%Y-%m-%d")
            sell_dt = datetime.strptime(t["sell_date"], "%Y-%m-%d")
            hold_days = (sell_dt - buy_dt).days
        except (ValueError, KeyError):
            hold_days = "—"

        rows.append({
            "Ticker": t["ticker"],
            "Buy → Sell": f"{_fmt_currency(t['buy_price'])} → {_fmt_currency(t['sell_price'])}",
            "Shares": t.get("volume", "—"),
            "P&L": f"{pnl_emoji} {_fmt_currency(pnl)}",
            "P&L %": _fmt_pct(t.get("pnl_pct")),
            "Hold Days": hold_days,
            "Sell Date": t.get("sell_date", "—"),
        })

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)


# ======================================================================
# Main page entry point
# ======================================================================
def show(db: TradingDB = None) -> None:
    """Render the Holdings page."""
    if db is None:
        db = TradingDB()
    # Initialise session state for close dialog
    if "closing_holding_id" not in st.session_state:
        st.session_state.closing_holding_id = None
    if "deleting_holding_id" not in st.session_state:
        st.session_state.deleting_holding_id = None

    st.title("💼 Holdings Management")

    _section_open_position(db)

    st.markdown("---")
    _section_current_holdings(db)

    # Close dialog (shown when a holding is selected)
    _section_close_position(db)

    _section_update_stops(db)

    _section_recent_closings(db)
