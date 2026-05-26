"""
dashboard.py — Main entry point for the Cipher Trading System Streamlit dashboard.

Multi-page app with sidebar navigation. Each page is implemented as a
module under the _pages/ package.

Uses a single shared DB connection to avoid "database is locked" errors.
"""

import streamlit as st
from db import TradingDB


@st.cache_resource
def _get_shared_db() -> TradingDB:
    """Single shared DB connection for all pages."""
    return TradingDB()


st.set_page_config(
    page_title="Cipher Trading System",
    page_icon="🔐",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Shared DB ───────────────────────────────────────────────────────
db = _get_shared_db()

# ── Sidebar navigation ──────────────────────────────────────────────
st.sidebar.title("🔐 Cipher Trading")
st.sidebar.markdown("---")

page = st.sidebar.radio(
    "Navigate",
    [
        "📋 Watchlist",
        "💼 Holdings",
        "🎯 Signals",
        "📊 Strategy",
        "📒 Trade Journal",
        "⚙️ Settings",
    ],
)

# ── Page routing — pass shared DB to each page ─────────────────────
if page == "📋 Watchlist":
    from _pages.watchlist import show
    show(db)
elif page == "💼 Holdings":
    from _pages.holdings import show
    show(db)
elif page == "🎯 Signals":
    from _pages.signals import show
    show(db)
elif page == "📊 Strategy":
    from _pages.strategy import show
    show(db)
elif page == "📒 Trade Journal":
    from _pages.journal import show
    show(db)
elif page == "⚙️ Settings":
    from _pages.settings import show
    show(db)
