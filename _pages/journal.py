"""
pages/journal.py — Trade Journal page.

Interactive charts, performance summary, and full trade history
with date/ticker filters powered by Plotly.
"""

from __future__ import annotations

import sys
import os
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# Ensure local imports work regardless of cwd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import TradingDB
from portfolio import get_trade_journal


# ======================================================================
# Cached DB singleton
# ======================================================================

# ======================================================================
# Helpers
# ======================================================================

def _fmt_currency(value: float | None) -> str:
    if value is None:
        return "—"
    return f"${value:,.2f}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.1f}%"


def _pnl_color(value: float | None) -> str:
    """Return green/red hex based on P&L sign."""
    if value is None or value == 0:
        return "#888888"
    return "#00C853" if value > 0 else "#FF1744"


def _trades_to_df(trades: list[dict]) -> pd.DataFrame:
    """Convert trade list to a display-ready DataFrame."""
    if not trades:
        return pd.DataFrame()

    rows = []
    for t in trades:
        buy_dt = t.get("buy_date", "")
        sell_dt = t.get("sell_date", "")
        hold_days = None
        try:
            bd = datetime.strptime(buy_dt, "%Y-%m-%d")
            sd = datetime.strptime(sell_dt, "%Y-%m-%d")
            hold_days = (sd - bd).days
        except (ValueError, TypeError):
            pass

        rows.append({
            "ticker": t.get("ticker", ""),
            "buy_date": buy_dt,
            "sell_date": sell_dt,
            "buy_price": t.get("buy_price"),
            "sell_price": t.get("sell_price"),
            "volume": t.get("volume"),
            "pnl": t.get("pnl"),
            "pnl_pct": t.get("pnl_pct"),
            "hold_days": hold_days,
            "notes": t.get("notes", "") or "",
        })

    df = pd.DataFrame(rows)
    df.sort_values("sell_date", ascending=False, inplace=True)
    return df


# ======================================================================
# Chart builders
# ======================================================================

def _chart_cumulative_pnl(df: pd.DataFrame) -> go.Figure:
    """Equity curve: cumulative P&L over time."""
    sorted_df = df.sort_values("sell_date").copy()
    sorted_df["cum_pnl"] = sorted_df["pnl"].cumsum()

    line_color = "#00C853" if sorted_df["cum_pnl"].iloc[-1] >= 0 else "#FF1744"

    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=sorted_df["sell_date"],
        y=sorted_df["cum_pnl"],
        mode="lines+markers",
        line=dict(color=line_color, width=2.5),
        marker=dict(size=5, color=line_color),
        name="Cumulative P&L",
        customdata=sorted_df[["ticker", "pnl"]].values,
        hovertemplate=(
            "<b>%{x}</b><br>"
            "Ticker: %{customdata[0]}<br>"
            "Trade P&L: $%{customdata[1]:.2f}<br>"
            "Cumulative P&L: $%{y:.2f}<br>"
            "<extra></extra>"
        ),
        fill="tozeroy",
        fillcolor="rgba(0,200,83,0.08)" if line_color == "#00C853" else "rgba(255,23,68,0.08)",
    ))

    fig.add_hline(y=0, line_dash="dot", line_color="#666", line_width=1)

    fig.update_layout(
        title="📈 Cumulative P&L Over Time",
        xaxis_title="Date",
        yaxis_title="Cumulative P&L ($)",
        height=400,
        margin=dict(l=20, r=20, t=50, b=40),
        hovermode="x unified",
    )
    return fig


def _chart_pnl_distribution(df: pd.DataFrame) -> go.Figure:
    """Histogram of P&L per trade."""
    wins = df[df["pnl"] >= 0]["pnl"]
    losses = df[df["pnl"] < 0]["pnl"]

    fig = go.Figure()

    if len(wins) > 0:
        fig.add_trace(go.Histogram(
            x=wins,
            name="Wins",
            marker_color="#00C853",
            opacity=0.8,
        ))
    if len(losses) > 0:
        fig.add_trace(go.Histogram(
            x=losses,
            name="Losses",
            marker_color="#FF1744",
            opacity=0.8,
        ))

    fig.add_vline(x=0, line_dash="dot", line_color="#666", line_width=1)

    fig.update_layout(
        title="📊 P&L Distribution",
        xaxis_title="P&L per Trade ($)",
        yaxis_title="Number of Trades",
        barmode="overlay",
        height=400,
        margin=dict(l=20, r=20, t=50, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig


def _chart_pnl_by_ticker(df: pd.DataFrame) -> go.Figure:
    """Horizontal bar chart of total P&L by ticker."""
    ticker_pnl = df.groupby("ticker")["pnl"].sum().sort_values()

    colors = [_pnl_color(v) for v in ticker_pnl.values]

    fig = go.Figure(go.Bar(
        x=ticker_pnl.values,
        y=ticker_pnl.index,
        orientation="h",
        marker_color=colors,
        text=[f"${v:,.2f}" for v in ticker_pnl.values],
        textposition="auto",
    ))

    fig.add_vline(x=0, line_dash="dot", line_color="#666", line_width=1)

    fig.update_layout(
        title="🏷️ P&L by Ticker",
        xaxis_title="Total P&L ($)",
        yaxis_title="Ticker",
        height=max(300, 40 * len(ticker_pnl) + 80),
        margin=dict(l=20, r=20, t=50, b=40),
    )
    return fig


def _chart_win_rate_by_month(df: pd.DataFrame) -> go.Figure:
    """Monthly win rate bars with trade count overlay."""
    df_copy = df.copy()
    df_copy["month"] = df_copy["sell_date"].str[:7]  # YYYY-MM

    monthly = df_copy.groupby("month").agg(
        total=("pnl", "count"),
        wins=("pnl", lambda x: (x > 0).sum()),
    ).reset_index()
    monthly["win_rate"] = (monthly["wins"] / monthly["total"] * 100).round(1)
    monthly.sort_values("month", inplace=True)

    fig = go.Figure()

    fig.add_trace(go.Bar(
        x=monthly["month"],
        y=monthly["win_rate"],
        name="Win Rate (%)",
        marker_color="#42A5F5",
        text=[f"{v:.0f}%" for v in monthly["win_rate"]],
        textposition="auto",
        yaxis="y",
    ))

    fig.add_trace(go.Scatter(
        x=monthly["month"],
        y=monthly["total"],
        name="Trades",
        mode="lines+markers+text",
        line=dict(color="#FFA726", width=2),
        marker=dict(size=7, color="#FFA726"),
        text=monthly["total"],
        textposition="top center",
        yaxis="y2",
    ))

    fig.update_layout(
        title="📅 Win Rate by Month",
        xaxis_title="Month",
        yaxis=dict(title="Win Rate (%)", side="left", range=[0, 105]),
        yaxis2=dict(title="Number of Trades", side="right", overlaying="y", showgrid=False),
        height=400,
        margin=dict(l=20, r=60, t=50, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig


# ======================================================================
# Section renderers
# ======================================================================

def _render_summary_cards(summary: dict) -> None:
    """Row of performance metric cards."""
    col1, col2, col3, col4, col5, col6 = st.columns(6)

    total_pnl = summary.get("total_pnl", 0)
    avg_pnl = summary.get("avg_pnl", 0)
    pnl_delta_color = "normal" if total_pnl >= 0 else "inverse"

    col1.metric("Total Trades", summary.get("total_trades", 0))
    col2.metric("Win Rate", _fmt_pct(summary.get("win_rate", 0)))

    col3.metric(
        "Total P&L",
        _fmt_currency(total_pnl),
        delta=_fmt_currency(total_pnl),
        delta_color=pnl_delta_color,
    )
    col4.metric(
        "Avg P&L",
        _fmt_currency(avg_pnl),
        delta=_fmt_currency(avg_pnl),
        delta_color="normal" if avg_pnl >= 0 else "inverse",
    )

    pf = summary.get("profit_factor", 0)
    pf_str = "∞" if pf == "inf" else f"{pf:.2f}"
    col5.metric("Profit Factor", pf_str)
    col6.metric("Avg Hold Days", f"{summary.get('avg_hold_days', 0):.1f}")

    st.markdown("---")


def _render_filters(db: TradingDB) -> tuple[str | None, str | None, str | None]:
    """Date range and ticker filters. Returns (start_date, end_date, ticker)."""
    st.subheader("🔍 Filters")

    # Quick filter buttons
    q1, q2, q3, q4, q5 = st.columns(5)
    today = date.today()

    quick_ranges = {
        "This Month": (today.replace(day=1).isoformat(), today.isoformat()),
        "Last Month": (
            (today.replace(day=1) - timedelta(days=1)).replace(day=1).isoformat(),
            (today.replace(day=1) - timedelta(days=1)).isoformat(),
        ),
        "Last 3 Months": ((today - timedelta(days=90)).isoformat(), today.isoformat()),
        "This Year": (today.replace(month=1, day=1).isoformat(), today.isoformat()),
        "All Time": (None, None),
    }

    quick_selected = st.session_state.get("journal_quick", None)

    if q1.button("📅 This Month", use_container_width=True):
        st.session_state["journal_quick"] = "This Month"
        st.rerun()
    if q2.button("📅 Last Month", use_container_width=True):
        st.session_state["journal_quick"] = "Last Month"
        st.rerun()
    if q3.button("📅 Last 3 Months", use_container_width=True):
        st.session_state["journal_quick"] = "Last 3 Months"
        st.rerun()
    if q4.button("📅 This Year", use_container_width=True):
        st.session_state["journal_quick"] = "This Year"
        st.rerun()
    if q5.button("📅 All Time", use_container_width=True):
        st.session_state["journal_quick"] = "All Time"
        st.rerun()

    st.markdown("</br>", unsafe_allow_html=True)

    # Custom date range
    col_start, col_end, col_ticker = st.columns(3)

    default_start = today - timedelta(days=365)
    default_end = today

    # Override defaults if quick filter is active
    if quick_selected and quick_selected in quick_ranges:
        qs, qe = quick_ranges[quick_selected]
        default_start = datetime.strptime(qs, "%Y-%m-%d").date() if qs else today - timedelta(days=365 * 10)
        default_end = datetime.strptime(qe, "%Y-%m-%d").date() if qe else today

    start_date = col_start.date_input(
        "Start Date",
        value=st.session_state.get("journal_start", default_start),
    )
    end_date = col_end.date_input(
        "End Date",
        value=st.session_state.get("journal_end", default_end),
    )

    # Ticker dropdown
    try:
        tickers = [row["ticker"] for row in db.get_trades()]
        unique_tickers = sorted(set(tickers))
    except Exception:
        unique_tickers = []

    ticker_options = ["All Tickers"] + unique_tickers
    selected_ticker = col_ticker.selectbox(
        "Ticker",
        options=ticker_options,
        index=0,
    )

    if st.button("✅ Apply Filter", type="primary", use_container_width=True):
        st.session_state["journal_start"] = start_date
        st.session_state["journal_end"] = end_date
        st.session_state["journal_ticker"] = selected_ticker
        st.rerun()

    # Use session state or current widgets
    s_start = st.session_state.get("journal_start", start_date)
    s_end = st.session_state.get("journal_end", end_date)
    s_ticker = st.session_state.get("journal_ticker", selected_ticker)

    start_str = s_start.isoformat() if isinstance(s_start, date) else str(s_start)
    end_str = s_end.isoformat() if isinstance(s_end, date) else str(s_end)
    ticker_filter = None if s_ticker == "All Tickers" else s_ticker

    st.markdown("---")
    return start_str, end_str, ticker_filter


def _render_charts(df: pd.DataFrame) -> None:
    """All four interactive charts."""
    st.subheader("📊 Charts")

    if len(df) < 2:
        st.info("Need at least 2 trades to generate charts.")
        return

    tab1, tab2, tab3, tab4 = st.tabs([
        "📈 Cumulative P&L",
        "📊 P&L Distribution",
        "🏷️ P&L by Ticker",
        "📅 Win Rate by Month",
    ])

    with tab1:
        fig = _chart_cumulative_pnl(df)
        st.plotly_chart(fig, use_container_width=True)

    with tab2:
        fig = _chart_pnl_distribution(df)
        st.plotly_chart(fig, use_container_width=True)

    with tab3:
        fig = _chart_pnl_by_ticker(df)
        st.plotly_chart(fig, use_container_width=True)

    with tab4:
        fig = _chart_win_rate_by_month(df)
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")


def _render_trade_table(df: pd.DataFrame) -> None:
    """Sortable, color-coded trade history table."""
    st.subheader("📋 Trade History")

    if df.empty:
        st.info("No trades match the current filter.")
        return

    display_df = df.copy()
    display_df.columns = [
        "Ticker", "Buy Date", "Sell Date", "Buy Price", "Sell Price",
        "Volume", "P&L ($)", "P&L (%)", "Hold Days", "Notes",
    ]

    st.dataframe(
        display_df,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Buy Price": st.column_config.NumberColumn(format="$ %.2f"),
            "Sell Price": st.column_config.NumberColumn(format="$ %.2f"),
            "P&L ($)": st.column_config.NumberColumn(format="$ %.2f"),
            "P&L (%)": st.column_config.NumberColumn(format="%.1f %%"),
            "Volume": st.column_config.NumberColumn(format="%d"),
            "Hold Days": st.column_config.NumberColumn(format="%d"),
        },
    )

    st.markdown("---")


def _render_best_worst(summary: dict) -> None:
    """Best and worst trade highlight cards."""
    st.subheader("🏆 Best & 💀 Worst Trades")

    col_best, col_worst = st.columns(2)

    best = summary.get("best_trade")
    worst = summary.get("worst_trade")

    with col_best:
        st.markdown("#### 🏆 Best Trade")
        if best:
            st.markdown(f"**Ticker:** {best.get('ticker', '—')}")
            st.markdown(f"**P&L:** {_fmt_currency(best.get('pnl'))}  ({_fmt_pct(best.get('pnl_pct'))})")
            st.markdown(f"**Buy:** {best.get('buy_date', '—')} @ {_fmt_currency(best.get('buy_price'))}")
            st.markdown(f"**Sell:** {best.get('sell_date', '—')} @ {_fmt_currency(best.get('sell_price'))}")
        else:
            st.info("No winning trades yet.")

    with col_worst:
        st.markdown("#### 💀 Worst Trade")
        if worst:
            st.markdown(f"**Ticker:** {worst.get('ticker', '—')}")
            st.markdown(f"**P&L:** {_fmt_currency(worst.get('pnl'))}  ({_fmt_pct(worst.get('pnl_pct'))})")
            st.markdown(f"**Buy:** {worst.get('buy_date', '—')} @ {_fmt_currency(worst.get('buy_price'))}")
            st.markdown(f"**Sell:** {worst.get('sell_date', '—')} @ {_fmt_currency(worst.get('sell_price'))}")
        else:
            st.info("No losing trades yet.")


def _render_delete_trades(db, trades: list) -> None:
    """Allow deletion of closed trades from the journal."""
    if not trades:
        return

    with st.expander("🗑️ Delete Trades"):
        st.warning("⚠️ Deleted trades cannot be recovered.")

        for t in trades:
            tid = t.get("id")
            ticker = t.get("ticker", "?")
            pnl = t.get("pnl", 0)
            pnl_pct = t.get("pnl_pct", 0)
            buy_date = t.get("buy_date", "?")
            sell_date = t.get("sell_date", "?")

            cols = st.columns([3, 1])
            with cols[0]:
                pnl_emoji = "🟢" if pnl >= 0 else "🔴"
                st.markdown(
                    f"{pnl_emoji} **{ticker}** | {buy_date} → {sell_date} | "
                    f"P&L: {_fmt_currency(pnl)} ({_fmt_pct(pnl_pct)})"
                )
            with cols[1]:
                if st.button("🗑️ Delete", key=f"del_trade_{tid}"):
                    st.session_state.confirm_delete_trade = tid
                    st.rerun()

        # Confirmation dialog
        if st.session_state.get("confirm_delete_trade"):
            tid = st.session_state.confirm_delete_trade
            st.error(f"⚠️ Permanently delete trade #{tid}? This cannot be undone.")
            c_yes, c_no, _ = st.columns([1, 1, 2])
            if c_yes.button("✅ Yes, Delete", key=f"confirm_del_trade_{tid}"):
                try:
                    db.conn.execute("DELETE FROM trades WHERE id=?", (tid,))
                    db.conn.commit()
                    st.session_state.confirm_delete_trade = None
                    st.success("Trade deleted.")
                    st.rerun()
                except Exception as e:
                    st.session_state.confirm_delete_trade = None
                    st.error(f"Delete failed: {e}")
                    st.rerun()
            if c_no.button("❌ Cancel", key=f"cancel_del_trade_{tid}"):
                st.session_state.confirm_delete_trade = None
                st.rerun()


def _render_edit_trades(db, trades: list) -> None:
    """Allow editing of closed trade details."""
    if not trades:
        return

    with st.expander("✏️ Edit Trades"):
        for t in trades:
            tid = t.get("id")
            ticker = t.get("ticker", "?")

            with st.container():
                cols = st.columns([2, 2, 2, 2, 2, 1])
                with cols[0]:
                    new_ticker = st.text_input("Ticker", value=ticker, key=f"edit_ticker_{tid}", label_visibility="collapsed")
                with cols[1]:
                    new_buy_date = st.text_input("Buy Date", value=t.get("buy_date", ""), key=f"edit_buy_date_{tid}", label_visibility="collapsed")
                with cols[2]:
                    new_sell_date = st.text_input("Sell Date", value=t.get("sell_date", ""), key=f"edit_sell_date_{tid}", label_visibility="collapsed")
                with cols[3]:
                    new_buy_price = st.text_input("Buy Price", value=str(t.get("buy_price", "")), key=f"edit_buy_price_{tid}", label_visibility="collapsed")
                with cols[4]:
                    new_sell_price = st.text_input("Sell Price", value=str(t.get("sell_price", "")), key=f"edit_sell_price_{tid}", label_visibility="collapsed")
                with cols[5]:
                    if st.button("💾", key=f"save_trade_{tid}", help="Save changes"):
                        try:
                            bp = float(new_buy_price)
                            sp = float(new_sell_price)
                            vol = t.get("volume", 1)
                            pnl = (sp - bp) * vol
                            pnl_pct = ((sp - bp) / bp) * 100 if bp != 0 else 0
                            db.conn.execute(
                                "UPDATE trades SET ticker=?, buy_date=?, sell_date=?, buy_price=?, sell_price=?, pnl=?, pnl_pct=? WHERE id=?",
                                (new_ticker.upper(), new_buy_date, new_sell_date, bp, sp, pnl, pnl_pct, tid),
                            )
                            db.conn.commit()
                            st.success(f"Updated **{new_ticker.upper()}** trade #{tid}")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Update failed: {e}")

                st.markdown("<hr style='margin:2px 0 8px 0'>", unsafe_allow_html=True)


# ======================================================================
# Main entry
# ======================================================================

show_sentinel = None  # marker


def show(db: TradingDB = None) -> None:
    """Render the Trade Journal page."""
    if db is None:
        db = TradingDB()

    st.title("📒 Trade Journal")
    st.caption("Review closed trades, track performance, and analyze patterns.")

    if "confirm_delete_trade" not in st.session_state:
        st.session_state.confirm_delete_trade = None

    # --- Filters ---
    start_date, end_date, ticker = _render_filters(db)

    # --- Fetch data ---
    journal = get_trade_journal(db=db, start_date=start_date, end_date=end_date, ticker=ticker)
    summary = journal["summary"]
    trades = journal["trades"]

    # --- Empty state ---
    if not trades:
        st.info("No trades recorded yet. Close your first position to see it here.")
        return

    df = _trades_to_df(trades)

    # --- Section 1: Summary cards ---
    _render_summary_cards(summary)

    # --- Section 3: Charts ---
    _render_charts(df)

    # --- Section 4: Trade table ---
    _render_trade_table(df)

    # --- Section 5: Best & worst ---
    _render_best_worst(summary)

    # --- Section 6: Delete trades ---
    _render_delete_trades(db, trades)
    _render_edit_trades(db, trades)
