"""
pages/strategy.py — Strategy page with View Mode and Edit Mode.

Displays current strategy parameters in a clean, educational format
and allows editing with validation.
"""

from __future__ import annotations

import sys
import os
from typing import Any

import streamlit as st

# Ensure local imports work regardless of cwd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import TradingDB

# ======================================================================
# Cached DB singleton
# ======================================================================
# ======================================================================
# Default values (must match db.py)
# ======================================================================
DEFAULTS: dict[str, Any] = {
    "rsi_period": 14,
    "rsi_oversold": 30,
    "rsi_overbought": 70,
    "bb_period": 20,
    "bb_std": 2.0,
    "atr_period": 14,
    "atr_stop_mult": 1.5,
    "atr_target_mult": 2.5,
    "alert_profit_pct": 10.0,
    "stale_days": 10,
    "sma_period": 200,
    "data_lookback_days": 365,
}

SETTINGS_LABELS: dict[str, str] = {
    "rsi_period": "RSI Period",
    "rsi_oversold": "RSI Oversold Threshold",
    "rsi_overbought": "RSI Overbought Threshold",
    "bb_period": "Bollinger Bands Period",
    "bb_std": "Bollinger Bands Std Dev",
    "atr_period": "ATR Period",
    "atr_stop_mult": "Stop-Loss Multiplier (×ATR)",
    "atr_target_mult": "Take-Profit Multiplier (×ATR)",
    "alert_profit_pct": "Profit Alert %",
    "stale_days": "Stale Detection Days",
    "sma_period": "SMA Filter Period",
    "data_lookback_days": "Data Lookback Days",
}


# ======================================================================
# Helpers
# ======================================================================
def _get(key: str, db: TradingDB) -> Any:
    """Fetch a setting from DB, falling back to DEFAULTS."""
    raw = db.get_setting(key)
    if raw is None:
        return DEFAULTS[key]
    # Coerce type to match default
    default = DEFAULTS[key]
    if isinstance(default, int):
        return int(float(raw))
    if isinstance(default, float):
        return float(raw)
    return raw


def _section_strategy_overview(db: TradingDB) -> None:
    """Render the formatted strategy overview."""
    rsi_period = _get("rsi_period", db)
    rsi_oversold = _get("rsi_oversold", db)
    rsi_overbought = _get("rsi_overbought", db)
    bb_period = _get("bb_period", db)
    bb_std = _get("bb_std", db)
    atr_period = _get("atr_period", db)
    atr_stop = _get("atr_stop_mult", db)
    atr_target = _get("atr_target_mult", db)
    alert_pct = _get("alert_profit_pct", db)
    stale_days = _get("stale_days", db)
    sma_period = _get("sma_period", db)
    lookback = _get("data_lookback_days", db)

    st.markdown("### 📊 Current Strategy Configuration")
    st.markdown("---")

    # Signal Generation
    st.markdown(
        f"""
**📊 Signal Generation**

&nbsp;&nbsp;&nbsp;• RSI Period: **{rsi_period}**
&nbsp;&nbsp;&nbsp;• RSI Oversold Threshold: **{rsi_oversold}** *(BUY signal trigger)*
&nbsp;&nbsp;&nbsp;• RSI Overbought Threshold: **{rsi_overbought}** *(SELL signal trigger)*
&nbsp;&nbsp;&nbsp;• Bollinger Bands Period: **{bb_period}**
&nbsp;&nbsp;&nbsp;• Bollinger Bands Std Dev: **{bb_std}**
"""
    )

    # Risk Management
    st.markdown(
        f"""
**📐 Risk Management**

&nbsp;&nbsp;&nbsp;• ATR Period: **{atr_period}**
&nbsp;&nbsp;&nbsp;• Stop-Loss: Entry − (ATR × **{atr_stop}**)
&nbsp;&nbsp;&nbsp;• Take-Profit: Entry + (ATR × **{atr_target}**)
&nbsp;&nbsp;&nbsp;• Profit Alert: **+{alert_pct}%** unrealized
"""
    )

    # Watchlist Management
    st.markdown(
        f"""
**🗑️ Watchlist Management**

&nbsp;&nbsp;&nbsp;• Stale Detection: **{stale_days} days**
&nbsp;&nbsp;&nbsp;• Stale Criteria: RSI 40–60 + price in BB middle zone
&nbsp;&nbsp;&nbsp;• SMA Filter: **{sma_period}-day** *(structural downtrend check)*
&nbsp;&nbsp;&nbsp;• Data Lookback: **{lookback} days**
"""
    )


def _section_signal_logic() -> None:
    """Render the educational signal logic expander."""
    with st.expander("📖 How Signals Work"):
        st.markdown(
            """
### Signal Generation Logic

**🟢 BUY Signal**
RSI drops below the oversold threshold **AND** price touches or breaks below the Bollinger Band lower band.
> Oversold + cheap = buy opportunity. The market has overreacted to the downside.

**🔴 SELL Signal**
RSI rises above the overbought threshold **AND** price touches or breaks above the Bollinger Band upper band.
> Overbought + expensive = sell opportunity. The market has overreacted to the upside.

**⚪ HOLD Signal**
Everything else — no clear signal detected. Stay patient.

---

### Visual Overview

```
BB Upper ═══════════════════ 🔴 SELL zone
                            (RSI > 70 + price ≥ BB Upper)

BB Mid   ─ ─ ─ ─ ─ ─ ─ ─ ─ ⚪ Neutral / HOLD zone

BB Lower ═══════════════════ 🟢 BUY zone
                            (RSI < 30 + price ≤ BB Lower)
```

---

### 📐 ATR-Based Stops — Adaptive to Volatility

Stop-loss and take-profit levels use **ATR (Average True Range)** to adapt
to each stock's volatility:

- **Volatile stocks** → wider stops (avoid getting stopped out by noise)
- **Calm stocks** → tighter stops (capitalise on smaller moves)

The **reward-to-risk ratio** is determined by your multipliers:
`Target Multiplier ÷ Stop-Loss Multiplier`. A ratio ≥ 2:1 is generally recommended.

---

### 🗑️ Stale Detection

Tickers that haven't generated a signal for **N days** are flagged as stale
when they also meet these criteria:

- RSI between 40–60 (neutral momentum)
- Price trading within the Bollinger Band middle zone (no extremes)

Stale tickers are candidates for removal — they tie up attention without
producing actionable signals.

---

### 📉 SMA Structural Filter

The **200-day SMA** (configurable) acts as a structural filter. Stocks trading
well below this moving average may be in a structural downtrend, signalling
caution even when oversold signals appear.
"""
        )


# ======================================================================
# View Mode
# ======================================================================
def _view_mode(db: TradingDB) -> None:
    st.title("📊 Strategy")

    _section_strategy_overview(db)

    st.markdown("---")
    _section_signal_logic()


# ======================================================================
# Edit Mode
# ======================================================================
def _edit_mode(db: TradingDB) -> None:
    st.title("✏️ Edit Strategy")

    with st.form("strategy_edit_form"):
        st.markdown("### 📊 Signal Parameters")
        c1, c2, c3 = st.columns(3)
        with c1:
            rsi_period = st.number_input(
                "RSI Period", min_value=5, max_value=50,
                value=_get("rsi_period", db), step=1,
            )
        with c2:
            rsi_oversold = st.number_input(
                "RSI Oversold", min_value=10, max_value=45,
                value=_get("rsi_oversold", db), step=1,
            )
        with c3:
            rsi_overbought = st.number_input(
                "RSI Overbought", min_value=55, max_value=90,
                value=_get("rsi_overbought", db), step=1,
            )

        c4, c5 = st.columns(2)
        with c4:
            bb_period = st.number_input(
                "Bollinger Bands Period", min_value=5, max_value=50,
                value=_get("bb_period", db), step=1,
            )
        with c5:
            bb_std = st.number_input(
                "Bollinger Bands Std Dev", min_value=0.5, max_value=4.0,
                value=_get("bb_std", db), step=0.1, format="%.1f",
            )

        st.markdown("---")
        st.markdown("### 📐 Risk Management")
        c6, c7, c8, c9 = st.columns(4)
        with c6:
            atr_period = st.number_input(
                "ATR Period", min_value=5, max_value=50,
                value=_get("atr_period", db), step=1,
            )
        with c7:
            atr_stop = st.number_input(
                "Stop-Loss Multiplier (×ATR)", min_value=0.5, max_value=5.0,
                value=_get("atr_stop_mult", db), step=0.1, format="%.1f",
            )
        with c8:
            atr_target = st.number_input(
                "Take-Profit Multiplier (×ATR)", min_value=1.0, max_value=10.0,
                value=_get("atr_target_mult", db), step=0.1, format="%.1f",
            )
        with c9:
            alert_pct = st.number_input(
                "Profit Alert %", min_value=5.0, max_value=50.0,
                value=_get("alert_profit_pct", db), step=1.0, format="%.1f",
            )

        st.markdown("---")
        st.markdown("### 🗑️ Watchlist Management")
        c10, c11, c12 = st.columns(3)
        with c10:
            stale_days = st.number_input(
                "Stale Days", min_value=3, max_value=30,
                value=_get("stale_days", db), step=1,
            )
        with c11:
            sma_period = st.number_input(
                "SMA Period", min_value=50, max_value=300,
                value=_get("sma_period", db), step=1,
            )
        with c12:
            lookback = st.number_input(
                "Data Lookback Days", min_value=30, max_value=730,
                value=_get("data_lookback_days", db), step=1,
            )

        st.markdown("---")

        col_save, col_reset = st.columns(2)
        with col_save:
            save_clicked = st.form_submit_button("💾 Save Changes", use_container_width=True, type="primary")
        with col_reset:
            reset_clicked = st.form_submit_button("🔄 Reset to Defaults", use_container_width=True)

    # --- Handle form submissions (outside form) ---
    if save_clicked:
        errors = []
        warnings = []

        # Validation
        if rsi_oversold >= rsi_overbought:
            errors.append("RSI Oversold must be **less than** RSI Overbought.")
        if atr_target <= atr_stop:
            errors.append("Take-Profit Multiplier should be **greater than** Stop-Loss Multiplier.")
        if bb_std < 1.0:
            warnings.append("BB Std Dev < 1.0 is unusually narrow — may generate excessive signals.")
        if bb_std > 3.0:
            warnings.append("BB Std Dev > 3.0 is unusually wide — may generate very few signals.")
        if rsi_oversold > 35:
            warnings.append(f"RSI Oversold at {int(rsi_oversold)} is higher than typical (≤30). May generate frequent BUY signals.")
        if rsi_overbought < 65:
            warnings.append(f"RSI Overbought at {int(rsi_overbought)} is lower than typical (≥70). May generate frequent SELL signals.")
        if atr_stop > 3.0:
            warnings.append(f"Stop-Loss multiplier ({atr_stop:.1f}) is wide. Ensure position sizing accounts for the larger risk.")

        if errors:
            for err in errors:
                st.error(err)
            return

        for w in warnings:
            st.warning(w)

        # Save all values
        updates = {
            "rsi_period": int(rsi_period),
            "rsi_oversold": int(rsi_oversold),
            "rsi_overbought": int(rsi_overbought),
            "bb_period": int(bb_period),
            "bb_std": float(bb_std),
            "atr_period": int(atr_period),
            "atr_stop_mult": float(atr_stop),
            "atr_target_mult": float(atr_target),
            "alert_profit_pct": float(alert_pct),
            "stale_days": int(stale_days),
            "sma_period": int(sma_period),
            "data_lookback_days": int(lookback),
        }
        for key, val in updates.items():
            db.set_setting(key, str(val))

        st.success("✅ Strategy parameters saved successfully!")
        # Switch back to view mode
        st.session_state["strategy_mode"] = "view"
        st.rerun()

    if reset_clicked:
        for key, val in DEFAULTS.items():
            db.set_setting(key, str(val))
        st.success("✅ All parameters reset to defaults!")
        st.session_state["strategy_mode"] = "view"
        st.rerun()


# ======================================================================
# Main page entry point
# ======================================================================
def show(db: TradingDB = None) -> None:
    """Render the Strategy page."""
    if db is None:
        db = TradingDB()

    # Mode toggle
    if "strategy_mode" not in st.session_state:
        st.session_state["strategy_mode"] = "view"

    mode = st.radio(
        "Mode",
        ["👁 View Mode", "✏️ Edit Mode"],
        index=0 if st.session_state["strategy_mode"] == "view" else 1,
        horizontal=True,
        key="strategy_mode_radio",
    )

    # Sync session state with radio selection
    if mode == "👁 View Mode":
        st.session_state["strategy_mode"] = "view"
    else:
        st.session_state["strategy_mode"] = "edit"

    st.markdown("---")

    if st.session_state["strategy_mode"] == "view":
        _view_mode(db)
    else:
        _edit_mode(db)
