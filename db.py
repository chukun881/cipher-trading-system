"""
db.py — SQLite database layer for the swing trading signal system.

All storage for watchlist, holdings, trades, OHLCV, indicators,
signals, settings, and run logging lives here.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Default path
# ---------------------------------------------------------------------------
_DEFAULT_DB_DIR = "/home/chukungaryyew/Documents/trading"
_DEFAULT_DB_PATH = os.path.join(_DEFAULT_DB_DIR, "trading.db")

# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS watchlist (
    ticker      TEXT PRIMARY KEY,
    date_added  TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'active',
    reason      TEXT DEFAULT '',
    date_dropped TEXT
);

CREATE TABLE IF NOT EXISTS holdings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker     TEXT NOT NULL,
    buy_price  REAL NOT NULL,
    volume     INTEGER NOT NULL,
    buy_date   TEXT NOT NULL,
    stop_loss  REAL,
    target     REAL,
    notes      TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS trades (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker     TEXT NOT NULL,
    buy_price  REAL NOT NULL,
    sell_price REAL NOT NULL,
    volume     INTEGER NOT NULL,
    buy_date   TEXT NOT NULL,
    sell_date  TEXT NOT NULL,
    pnl        REAL,
    pnl_pct    REAL,
    notes      TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS ohlcv (
    ticker TEXT NOT NULL,
    date   TEXT NOT NULL,
    open   REAL,
    high   REAL,
    low    REAL,
    close  REAL,
    volume INTEGER,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS daily_indicators (
    ticker   TEXT NOT NULL,
    date     TEXT NOT NULL,
    rsi_14   REAL,
    bb_lower REAL,
    bb_mid   REAL,
    bb_upper REAL,
    atr_14   REAL,
    sma_200  REAL,
    bb_width REAL,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS signals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker      TEXT NOT NULL,
    date        TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    rsi         REAL,
    close       REAL,
    bb_lower    REAL,
    bb_upper    REAL,
    atr_14      REAL,
    notes       TEXT DEFAULT '',
    UNIQUE(ticker, date)
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date         TEXT NOT NULL,
    run_time         TEXT NOT NULL,
    status           TEXT NOT NULL,
    tickers_analyzed INTEGER DEFAULT 0,
    signals_generated INTEGER DEFAULT 0,
    errors           TEXT DEFAULT '',
    duration_seconds REAL DEFAULT 0
);
"""

_DEFAULT_SETTINGS: dict[str, str] = {
    "rsi_period": "14",
    "rsi_oversold": "30",
    "rsi_overbought": "70",
    "bb_period": "20",
    "bb_std": "2.0",
    "atr_period": "14",
    "atr_stop_mult": "1.5",
    "atr_target_mult": "2.5",
    "alert_profit_pct": "10.0",
    "stale_days": "10",
    "sma_period": "200",
    "data_lookback_days": "365",
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "schedule_time": "",
}


class TradingDB:
    """Thin wrapper around an SQLite database for the trading system."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or _DEFAULT_DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._connect()
        self._create_tables()
        self.init_default_settings()

    # ------------------------------------------------------------------
    # Connection helpers
    # ------------------------------------------------------------------
    def _connect(self) -> None:
        self._conn = sqlite3.connect(
            self.db_path, check_same_thread=False, timeout=15
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=DELETE")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")

    def _create_tables(self) -> None:
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()
        # Migration: ensure unique constraint on signals(ticker, date)
        self._ensure_signals_unique()

    def _ensure_signals_unique(self) -> None:
        """Add UNIQUE(ticker, date) to signals table if missing.

        SQLite doesn't support ALTER TABLE ADD CONSTRAINT, so we recreate
        the table if the unique index doesn't exist.
        """
        cur = self._conn.execute("PRAGMA index_list(signals)")
        has_unique = any(r[2] for r in cur.fetchall() if r[1] and "ticker" in str(r[1]).lower())
        if not has_unique:
            # Check if the column schema already has the constraint (fresh DB)
            cur2 = self._conn.execute("SELECT sql FROM sqlite_master WHERE name='signals'")
            schema = cur2.fetchone()
            if schema and "UNIQUE(ticker, date)" in schema[0]:
                return  # Already has it (new DB)
            # Migrate: recreate table with unique constraint
            self._conn.execute("ALTER TABLE signals RENAME TO signals_old")
            self._conn.execute("""CREATE TABLE signals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker      TEXT NOT NULL,
                date        TEXT NOT NULL,
                signal_type TEXT NOT NULL,
                rsi         REAL,
                close       REAL,
                bb_lower    REAL,
                bb_upper    REAL,
                atr_14      REAL,
                notes       TEXT DEFAULT '',
                UNIQUE(ticker, date)
            )""")
            # Keep only the latest row per (ticker, date) from old table
            self._conn.execute("""INSERT OR IGNORE INTO signals
                (ticker, date, signal_type, rsi, close, bb_lower, bb_upper, atr_14, notes)
                SELECT ticker, date, signal_type, rsi, close, bb_lower, bb_upper, atr_14, notes
                FROM signals_old
                WHERE id IN (SELECT MAX(id) FROM signals_old GROUP BY ticker, date)
            """)
            self._conn.execute("DROP TABLE signals_old")
            self._conn.commit()
            print("  [MIGRATION] Added UNIQUE(ticker, date) to signals table")

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._connect()
        return self._conn  # type: ignore[return-value]

    # ==================================================================
    # WATCHLIST
    # ==================================================================
    def add_ticker(self, ticker: str, reason: str = "") -> None:
        """Add a ticker to the watchlist (or reactivate if dropped)."""
        ticker = ticker.upper().strip()
        today = date.today().isoformat()
        self.conn.execute(
            """
            INSERT INTO watchlist (ticker, date_added, status, reason)
            VALUES (?, ?, 'active', ?)
            ON CONFLICT(ticker) DO UPDATE SET
                status='active', reason=excluded.reason, date_dropped=NULL
            """,
            (ticker, today, reason),
        )
        self.conn.commit()

    def remove_ticker(self, ticker: str) -> None:
        """Mark a ticker as dropped."""
        ticker = ticker.upper().strip()
        today = date.today().isoformat()
        self.conn.execute(
            "UPDATE watchlist SET status='dropped', date_dropped=? WHERE ticker=?",
            (today, ticker),
        )
        self.conn.commit()

    def get_active_tickers(self) -> list[str]:
        """Return list of active ticker symbols."""
        cur = self.conn.execute(
            "SELECT ticker FROM watchlist WHERE status='active' ORDER BY ticker"
        )
        return [row["ticker"] for row in cur.fetchall()]

    def get_all_tickers(self) -> list[dict[str, Any]]:
        """Return all watchlist entries with status info."""
        cur = self.conn.execute(
            "SELECT * FROM watchlist ORDER BY status, ticker"
        )
        return [dict(row) for row in cur.fetchall()]

    def reactivate_ticker(self, ticker: str) -> None:
        """Reactivate a dropped ticker."""
        ticker = ticker.upper().strip()
        self.conn.execute(
            "UPDATE watchlist SET status='active', date_dropped=NULL WHERE ticker=?",
            (ticker,),
        )
        self.conn.commit()

    # ==================================================================
    # HOLDINGS
    # ==================================================================
    def add_holding(
        self,
        ticker: str,
        buy_price: float,
        volume: int,
        buy_date: str,
        stop_loss: float,
        target: float,
        notes: str = "",
    ) -> int:
        """Open a new holding. Returns the holding id."""
        cur = self.conn.execute(
            """
            INSERT INTO holdings (ticker, buy_price, volume, buy_date, stop_loss, target, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (ticker.upper().strip(), buy_price, volume, buy_date, stop_loss, target, notes),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def close_holding(
        self, holding_id: int, sell_price: float, sell_date: str
    ) -> None:
        """Close a holding and record it as a completed trade."""
        cur = self.conn.execute(
            "SELECT * FROM holdings WHERE id=?", (holding_id,)
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"Holding id {holding_id} not found")
        h = dict(row)
        pnl = (sell_price - h["buy_price"]) * h["volume"]
        pnl_pct = (
            ((sell_price - h["buy_price"]) / h["buy_price"]) * 100
            if h["buy_price"]
            else 0.0
        )
        self.conn.execute(
            """
            INSERT INTO trades (ticker, buy_price, sell_price, volume,
                                buy_date, sell_date, pnl, pnl_pct, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                h["ticker"],
                h["buy_price"],
                sell_price,
                h["volume"],
                h["buy_date"],
                sell_date,
                pnl,
                pnl_pct,
                h.get("notes", ""),
            ),
        )
        self.conn.execute("DELETE FROM holdings WHERE id=?", (holding_id,))
        self.conn.commit()

    def get_holdings(self) -> list[dict[str, Any]]:
        """Return all open holdings."""
        cur = self.conn.execute("SELECT * FROM holdings ORDER BY ticker")
        return [dict(row) for row in cur.fetchall()]

    def get_holding(self, ticker: str) -> Optional[dict[str, Any]]:
        """Return the holding for a specific ticker, or None."""
        cur = self.conn.execute(
            "SELECT * FROM holdings WHERE ticker=?", (ticker.upper().strip(),)
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def update_holding(self, holding_id: int, **kwargs: Any) -> None:
        """Update arbitrary fields on a holding."""
        if not kwargs:
            return
        sets = ", ".join(f"{k}=?" for k in kwargs)
        vals = list(kwargs.values()) + [holding_id]
        self.conn.execute(
            f"UPDATE holdings SET {sets} WHERE id=?", vals
        )
        self.conn.commit()

    # ==================================================================
    # TRADES
    # ==================================================================
    def get_trades(
        self,
        ticker: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Query closed trades with optional filters."""
        clauses: list[str] = []
        params: list[Any] = []
        if ticker:
            clauses.append("ticker=?")
            params.append(ticker.upper().strip())
        if start_date:
            clauses.append("sell_date>=?")
            params.append(start_date)
        if end_date:
            clauses.append("sell_date<=?")
            params.append(end_date)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        cur = self.conn.execute(
            f"SELECT * FROM trades{where} ORDER BY sell_date DESC", params
        )
        return [dict(row) for row in cur.fetchall()]

    def get_trade_stats(self) -> dict[str, Any]:
        """Return aggregate trade statistics."""
        cur = self.conn.execute(
            """
            SELECT
                COUNT(*)            AS total_trades,
                SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) AS wins,
                SUM(pnl)            AS total_pnl,
                AVG(pnl)            AS avg_pnl
            FROM trades
            """
        )
        row = cur.fetchone()
        if row is None or row["total_trades"] == 0:
            return {
                "total_trades": 0,
                "wins": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "avg_pnl": 0.0,
            }
        total = row["total_trades"]
        wins = row["wins"] or 0
        return {
            "total_trades": total,
            "wins": wins,
            "win_rate": round(wins / total * 100, 2),
            "total_pnl": round(row["total_pnl"] or 0, 2),
            "avg_pnl": round(row["avg_pnl"] or 0, 2),
        }

    # ==================================================================
    # OHLCV
    # ==================================================================
    def upsert_ohlcv(self, ticker: str, data: list[dict[str, Any]]) -> None:
        """Insert or update OHLCV rows for a ticker.

        *data* is a list of dicts with keys:
        date, open, high, low, close, volume.
        """
        ticker = ticker.upper().strip()
        rows = [
            (ticker, d["date"], d["open"], d["high"], d["low"], d["close"], d["volume"])
            for d in data
        ]
        self.conn.executemany(
            """
            INSERT INTO ohlcv (ticker, date, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, date) DO UPDATE SET
                open=excluded.open, high=excluded.high, low=excluded.low,
                close=excluded.close, volume=excluded.volume
            """,
            rows,
        )
        self.conn.commit()

    def get_ohlcv(
        self, ticker: str, days: Optional[int] = None
    ) -> list[dict[str, Any]]:
        """Get OHLCV data for a ticker, optionally limited to last N days."""
        ticker = ticker.upper().strip()
        if days is not None:
            cur = self.conn.execute(
                """
                SELECT * FROM ohlcv
                WHERE ticker=?
                ORDER BY date DESC LIMIT ?
                """,
                (ticker, days),
            )
            # Return in chronological order
            return [dict(r) for r in reversed(cur.fetchall())]
        cur = self.conn.execute(
            "SELECT * FROM ohlcv WHERE ticker=? ORDER BY date", (ticker,)
        )
        return [dict(r) for r in cur.fetchall()]

    def get_latest_date(self, ticker: str) -> Optional[str]:
        """Return the most recent date for which we have OHLCV data."""
        cur = self.conn.execute(
            "SELECT MAX(date) AS d FROM ohlcv WHERE ticker=?",
            (ticker.upper().strip(),),
        )
        row = cur.fetchone()
        return row["d"] if row and row["d"] else None

    # ==================================================================
    # INDICATORS
    # ==================================================================
    def upsert_indicators(
        self, ticker: str, data: list[dict[str, Any]]
    ) -> None:
        """Insert or update daily indicator rows for a ticker."""
        ticker = ticker.upper().strip()
        rows = [
            (
                ticker,
                d["date"],
                d.get("rsi_14"),
                d.get("bb_lower"),
                d.get("bb_mid"),
                d.get("bb_upper"),
                d.get("atr_14"),
                d.get("sma_200"),
                d.get("bb_width"),
            )
            for d in data
        ]
        self.conn.executemany(
            """
            INSERT INTO daily_indicators
                (ticker, date, rsi_14, bb_lower, bb_mid, bb_upper, atr_14, sma_200, bb_width)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, date) DO UPDATE SET
                rsi_14=excluded.rsi_14, bb_lower=excluded.bb_lower,
                bb_mid=excluded.bb_mid, bb_upper=excluded.bb_upper,
                atr_14=excluded.atr_14, sma_200=excluded.sma_200,
                bb_width=excluded.bb_width
            """,
            rows,
        )
        self.conn.commit()

    def get_indicators(
        self, ticker: str, days: Optional[int] = None
    ) -> list[dict[str, Any]]:
        """Get indicator data for a ticker."""
        ticker = ticker.upper().strip()
        if days is not None:
            cur = self.conn.execute(
                """
                SELECT * FROM daily_indicators
                WHERE ticker=? ORDER BY date DESC LIMIT ?
                """,
                (ticker, days),
            )
            return [dict(r) for r in reversed(cur.fetchall())]
        cur = self.conn.execute(
            "SELECT * FROM daily_indicators WHERE ticker=? ORDER BY date",
            (ticker,),
        )
        return [dict(r) for r in cur.fetchall()]

    def get_latest_indicators(self, ticker: str) -> Optional[dict[str, Any]]:
        """Return the most recent indicator row for a ticker."""
        cur = self.conn.execute(
            "SELECT * FROM daily_indicators WHERE ticker=? ORDER BY date DESC LIMIT 1",
            (ticker.upper().strip(),),
        )
        row = cur.fetchone()
        return dict(row) if row else None

    # ==================================================================
    # SIGNALS
    # ==================================================================
    def save_signals(self, signals_list: list[dict[str, Any]]) -> None:
        """Batch upsert signals. Each ticker gets one signal per date (latest wins)."""
        if not signals_list:
            return
        rows = [
            (
                s["ticker"],
                s["date"],
                s["signal_type"],
                s.get("rsi"),
                s.get("close"),
                s.get("bb_lower"),
                s.get("bb_upper"),
                s.get("atr_14"),
                s.get("notes", ""),
            )
            for s in signals_list
        ]
        # Use INSERT OR REPLACE to handle any duplicate (ticker, date)
        self.conn.executemany(
            """
            INSERT OR REPLACE INTO signals
                (ticker, date, signal_type, rsi, close, bb_lower, bb_upper, atr_14, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self.conn.commit()

    def get_signals(
        self,
        date: Optional[str] = None,
        ticker: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Query signals with optional filters."""
        clauses: list[str] = []
        params: list[Any] = []
        if date:
            clauses.append("date=?")
            params.append(date)
        if ticker:
            clauses.append("ticker=?")
            params.append(ticker.upper().strip())
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        cur = self.conn.execute(
            f"SELECT * FROM signals{where} ORDER BY date DESC, ticker", params
        )
        return [dict(r) for r in cur.fetchall()]

    # ==================================================================
    # SETTINGS
    # ==================================================================
    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Get a single setting value."""
        cur = self.conn.execute(
            "SELECT value FROM settings WHERE key=?", (key,)
        )
        row = cur.fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        """Set a setting value (upsert)."""
        self.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()

    def get_all_settings(self) -> dict[str, str]:
        """Return all settings as a dict."""
        cur = self.conn.execute("SELECT key, value FROM settings")
        return {row["key"]: row["value"] for row in cur.fetchall()}

    def init_default_settings(self) -> None:
        """Seed default settings if they don't already exist."""
        for k, v in _DEFAULT_SETTINGS.items():
            self.conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (k, v),
            )
        self.conn.commit()

    # ==================================================================
    # RUN LOG
    # ==================================================================
    def log_run(
        self,
        status: str,
        tickers_analyzed: int,
        signals_generated: int,
        errors: str = "",
        duration: float = 0,
    ) -> int:
        """Record an analysis run. Returns the run id."""
        now = datetime.now()
        cur = self.conn.execute(
            """
            INSERT INTO run_log
                (run_date, run_time, status, tickers_analyzed, signals_generated, errors, duration_seconds)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now.strftime("%Y-%m-%d"),
                now.strftime("%H:%M:%S"),
                status,
                tickers_analyzed,
                signals_generated,
                errors,
                duration,
            ),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_recent_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return the most recent run log entries."""
        cur = self.conn.execute(
            "SELECT * FROM run_log ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in cur.fetchall()]


# ======================================================================
# Self-test
# ======================================================================
if __name__ == "__main__":
    import tempfile

    # Use a temp DB so we don't pollute the real one
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_path = tmp.name
    tmp.close()

    print(f"Creating test DB at {tmp_path}")
    db = TradingDB(db_path=tmp_path)

    # --- Watchlist ---
    print("\n--- Watchlist ---")
    db.add_ticker("AAPL", "earnings play")
    db.add_ticker("MSFT", "breakout setup")
    db.add_ticker("GOOGL", "sector rotation")
    print("Active tickers:", db.get_active_tickers())
    assert db.get_active_tickers() == ["AAPL", "GOOGL", "MSFT"]

    db.remove_ticker("GOOGL")
    print("After dropping GOOGL:", db.get_active_tickers())
    assert db.get_active_tickers() == ["AAPL", "MSFT"]

    db.reactivate_ticker("GOOGL")
    print("After reactivating GOOGL:", db.get_active_tickers())
    assert db.get_active_tickers() == ["AAPL", "GOOGL", "MSFT"]

    all_tickers = db.get_all_tickers()
    print("All tickers:", len(all_tickers))
    assert len(all_tickers) == 3

    # --- Holdings & Trades ---
    print("\n--- Holdings & Trades ---")
    hid = db.add_holding("AAPL", 150.0, 100, "2025-01-15", 142.0, 165.0, "test buy")
    print(f"Added holding id={hid}")
    holdings = db.get_holdings()
    assert len(holdings) == 1
    assert holdings[0]["ticker"] == "AAPL"

    h = db.get_holding("AAPL")
    assert h is not None and h["buy_price"] == 150.0

    db.update_holding(hid, stop_loss=144.0)
    h2 = db.get_holding("AAPL")
    assert h2 is not None and h2["stop_loss"] == 144.0

    db.close_holding(hid, sell_price=160.0, sell_date="2025-02-01")
    assert len(db.get_holdings()) == 0

    trades = db.get_trades()
    assert len(trades) == 1
    assert trades[0]["pnl"] == 1000.0  # (160-150)*100

    stats = db.get_trade_stats()
    print("Trade stats:", stats)
    assert stats["total_trades"] == 1
    assert stats["win_rate"] == 100.0

    # --- OHLCV ---
    print("\n--- OHLCV ---")
    ohlcv_data = [
        {"date": "2025-01-13", "open": 148, "high": 152, "low": 147, "close": 150, "volume": 50000},
        {"date": "2025-01-14", "open": 150, "high": 153, "low": 149, "close": 151, "volume": 45000},
        {"date": "2025-01-15", "open": 151, "high": 155, "low": 150, "close": 154, "volume": 60000},
    ]
    db.upsert_ohlcv("AAPL", ohlcv_data)
    fetched = db.get_ohlcv("AAPL")
    assert len(fetched) == 3
    assert fetched[0]["date"] == "2025-01-13"

    latest = db.get_latest_date("AAPL")
    assert latest == "2025-01-15"

    last2 = db.get_ohlcv("AAPL", days=2)
    assert len(last2) == 2

    # Upsert again — should update, not duplicate
    db.upsert_ohlcv("AAPL", [{"date": "2025-01-15", "open": 151, "high": 156, "low": 150, "close": 155, "volume": 65000}])
    assert len(db.get_ohlcv("AAPL")) == 3

    # --- Indicators ---
    print("\n--- Indicators ---")
    ind_data = [
        {"date": "2025-01-13", "rsi_14": 35.2, "bb_lower": 145.0, "bb_mid": 150.0, "bb_upper": 155.0, "atr_14": 3.5, "sma_200": 148.0, "bb_width": 10.0},
        {"date": "2025-01-14", "rsi_14": 38.1, "bb_lower": 146.0, "bb_mid": 151.0, "bb_upper": 156.0, "atr_14": 3.4, "sma_200": 148.5, "bb_width": 10.0},
        {"date": "2025-01-15", "rsi_14": 42.0, "bb_lower": 147.0, "bb_mid": 152.0, "bb_upper": 157.0, "atr_14": 3.6, "sma_200": 149.0, "bb_width": 10.0},
    ]
    db.upsert_indicators("AAPL", ind_data)
    fetched_ind = db.get_indicators("AAPL")
    assert len(fetched_ind) == 3

    latest_ind = db.get_latest_indicators("AAPL")
    assert latest_ind is not None and latest_ind["rsi_14"] == 42.0

    # --- Signals ---
    print("\n--- Signals ---")
    db.save_signals([
        {"ticker": "AAPL", "date": "2025-01-15", "signal_type": "BUY", "rsi": 42.0, "close": 154.0, "bb_lower": 147.0, "bb_upper": 157.0, "atr_14": 3.6, "notes": "oversold bounce"},
        {"ticker": "MSFT", "date": "2025-01-15", "signal_type": "HOLD", "rsi": 55.0, "close": 300.0, "bb_lower": 290.0, "bb_upper": 310.0, "atr_14": 5.0, "notes": ""},
    ])
    sigs = db.get_signals(date="2025-01-15")
    assert len(sigs) == 2
    aapl_sigs = db.get_signals(ticker="AAPL")
    assert len(aapl_sigs) >= 1

    # --- Settings ---
    print("\n--- Settings ---")
    assert db.get_setting("rsi_period") == "14"
    assert db.get_setting("nonexistent", "fallback") == "fallback"
    db.set_setting("rsi_period", "21")
    assert db.get_setting("rsi_period") == "21"
    all_set = db.get_all_settings()
    assert "bb_std" in all_set

    # --- Run Log ---
    print("\n--- Run Log ---")
    db.log_run("success", 3, 2, duration=1.23)
    db.log_run("partial", 2, 1, errors="GOOGL timeout", duration=0.8)
    runs = db.get_recent_runs(5)
    assert len(runs) == 2
    assert runs[0]["status"] == "partial"  # most recent first

    # --- Edge cases ---
    print("\n--- Edge Cases ---")
    # Duplicate ticker insert (should upsert gracefully)
    db.add_ticker("AAPL", "updated reason")
    assert len(db.get_active_tickers()) == 3

    # get_holding for non-existent ticker
    assert db.get_holding("NONEXISTENT") is None

    # get_trades with filters
    filtered = db.get_trades(ticker="AAPL", start_date="2025-01-01", end_date="2025-12-31")
    assert len(filtered) == 1

    # empty signals list
    db.save_signals([])  # should not error

    # get_ohlcv for unknown ticker
    assert db.get_ohlcv("UNKNOWN") == []

    # get_latest_indicators for unknown ticker
    assert db.get_latest_indicators("UNKNOWN") is None

    db.close()

    # Clean up
    os.unlink(tmp_path)
    print("\n✅ All tests passed!")
