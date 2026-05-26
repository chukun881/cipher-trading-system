"""
portfolio.py — Holdings and trade lifecycle management.

Manages the full lifecycle of stock holdings: opening positions with
ATR-based stops/targets, tracking unrealized P&L, closing trades with
realised P&L, trailing stop updates, and trade journal analytics.

Depends on:
    - db.py (TradingDB)
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, datetime
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import TradingDB

# ======================================================================
# Helpers
# ======================================================================

def _today() -> str:
    """Return today's date as YYYY-MM-DD."""
    return date.today().isoformat()


def _get_atr_settings(db: TradingDB) -> tuple[float, float]:
    """Return (atr_stop_mult, atr_target_mult) from DB settings."""
    atr_stop_mult = float(db.get_setting("atr_stop_mult", "1.5"))
    atr_target_mult = float(db.get_setting("atr_target_mult", "2.5"))
    return atr_stop_mult, atr_target_mult


def _get_alert_profit_pct(db: TradingDB) -> float:
    """Return alert_profit_pct from DB settings."""
    return float(db.get_setting("alert_profit_pct", "10.0"))


# ======================================================================
# 1. open_position
# ======================================================================
def open_position(
    ticker: str,
    buy_price: float,
    volume: int,
    buy_date: str | None = None,
    db: TradingDB | None = None,
    notes: str = "",
) -> dict[str, Any]:
    """Open a new holding with ATR-calculated stop-loss and target.

    Args:
        ticker: Stock symbol (e.g. 'AAPL').
        buy_price: Entry price per share.
        volume: Number of shares.
        buy_date: YYYY-MM-DD string. Defaults to today.
        db: TradingDB instance. Created if not provided.
        notes: Optional notes.

    Returns:
        Dict with holding_id, ticker, buy_price, volume, stop_loss, target.

    Raises:
        ValueError: On invalid inputs or duplicate open position.
    """
    own_db = db is None
    if own_db:
        db = TradingDB()

    try:
        ticker = ticker.upper().strip()
        buy_date = buy_date or _today()

        # --- Validation ---
        if buy_price <= 0:
            raise ValueError(f"buy_price must be positive, got {buy_price}")
        if volume <= 0:
            raise ValueError(f"volume must be positive, got {volume}")

        existing = db.get_holding(ticker)
        if existing is not None:
            raise ValueError(
                f"Open position already exists for {ticker} "
                f"(id={existing['id']}, bought at {existing['buy_price']})"
            )

        # --- Calculate stop-loss and target ---
        atr_stop_mult, atr_target_mult = _get_atr_settings(db)

        latest_ind = db.get_latest_indicators(ticker)
        atr_14 = latest_ind.get("atr_14") if latest_ind else None

        if atr_14 is not None:
            stop_loss = round(buy_price - (atr_14 * atr_stop_mult), 2)
            target = round(buy_price + (atr_14 * atr_target_mult), 2)
        else:
            # Fallback: -8% stop, +20% target
            stop_loss = round(buy_price * 0.92, 2)
            target = round(buy_price * 1.20, 2)

        holding_id = db.add_holding(
            ticker=ticker,
            buy_price=buy_price,
            volume=volume,
            buy_date=buy_date,
            stop_loss=stop_loss,
            target=target,
            notes=notes,
        )

        return {
            "holding_id": holding_id,
            "ticker": ticker,
            "buy_price": buy_price,
            "volume": volume,
            "stop_loss": stop_loss,
            "target": target,
        }
    finally:
        if own_db:
            db.close()


# ======================================================================
# 2. close_position
# ======================================================================
def close_position(
    holding_id: int,
    sell_price: float | None = None,
    sell_date: str | None = None,
    db: TradingDB | None = None,
    notes: str = "",
) -> dict[str, Any]:
    """Close a holding and record it as a completed trade.

    Args:
        holding_id: The holdings row id.
        sell_price: Exit price per share. If None, uses latest close from OHLCV.
        sell_date: YYYY-MM-DD string. Defaults to today.
        db: TradingDB instance. Created if not provided.
        notes: Optional closing notes (appended to existing holding notes).

    Returns:
        Dict with ticker, buy_price, sell_price, volume, pnl, pnl_pct, hold_days.

    Raises:
        ValueError: If holding not found or sell_price cannot be resolved.
    """
    own_db = db is None
    if own_db:
        db = TradingDB()

    try:
        sell_date = sell_date or _today()

        # Fetch the holding
        cur = db.conn.execute("SELECT * FROM holdings WHERE id=?", (holding_id,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"Holding id {holding_id} not found")
        h = dict(row)

        # Resolve sell_price
        if sell_price is None:
            ohlcv = db.get_ohlcv(h["ticker"], days=1)
            if not ohlcv:
                raise ValueError(
                    f"Cannot resolve sell_price for {h['ticker']}: no OHLCV data"
                )
            sell_price = ohlcv[-1]["close"]

        # Calculate P&L
        pnl = round((sell_price - h["buy_price"]) * h["volume"], 2)
        pnl_pct = round(
            ((sell_price - h["buy_price"]) / h["buy_price"]) * 100, 2
            if h["buy_price"] else 0.0
        )

        # Calculate hold days
        try:
            buy_dt = datetime.strptime(h["buy_date"], "%Y-%m-%d")
            sell_dt = datetime.strptime(sell_date, "%Y-%m-%d")
            hold_days = (sell_dt - buy_dt).days
        except ValueError:
            hold_days = 0

        # Update notes if provided
        if notes:
            existing_notes = h.get("notes", "") or ""
            updated_notes = f"{existing_notes} | close: {notes}".strip(" |")
            db.update_holding(holding_id, notes=updated_notes)

        # Close in DB (moves to trades table)
        db.close_holding(holding_id, sell_price, sell_date)

        return {
            "ticker": h["ticker"],
            "buy_price": h["buy_price"],
            "sell_price": sell_price,
            "volume": h["volume"],
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "hold_days": hold_days,
        }
    finally:
        if own_db:
            db.close()


# ======================================================================
# 3. get_portfolio_status
# ======================================================================
def get_portfolio_status(db: TradingDB | None = None) -> dict[str, Any]:
    """Return comprehensive portfolio status with unrealized P&L.

    Args:
        db: TradingDB instance. Created if not provided.

    Returns:
        Dict with holdings list (enriched with current_price, unrealized P&L,
        status flags), and portfolio-level totals.
    """
    own_db = db is None
    if own_db:
        db = TradingDB()

    try:
        alert_profit_pct = _get_alert_profit_pct(db)
        holdings_raw = db.get_holdings()
        holdings: list[dict[str, Any]] = []

        total_value = 0.0
        total_cost = 0.0

        for h in holdings_raw:
            # Get latest close
            ohlcv = db.get_ohlcv(h["ticker"], days=1)
            current_price = ohlcv[-1]["close"] if ohlcv else h["buy_price"]

            buy_price = h["buy_price"]
            volume = h["volume"]
            cost = buy_price * volume
            market_val = current_price * volume
            unrealized_pnl = round(market_val - cost, 2)
            unrealized_pnl_pct = round(
                ((current_price - buy_price) / buy_price) * 100, 2
                if buy_price else 0.0
            )

            # Calculate days held
            try:
                buy_dt = datetime.strptime(h["buy_date"], "%Y-%m-%d")
                hold_days = (date.today() - buy_dt.date()).days
            except ValueError:
                hold_days = 0

            # Determine status
            stop_loss = h.get("stop_loss")
            target = h.get("target")
            status = "normal"

            if stop_loss is not None and current_price <= stop_loss * 1.02 and current_price > stop_loss:
                status = "stop_loss_risk"
            elif stop_loss is not None and current_price <= stop_loss:
                status = "stop_loss_risk"
            if target is not None and current_price >= target * 0.98 and current_price < target:
                status = "target_close"
            if target is not None and current_price >= target:
                status = "target_close"
            if unrealized_pnl_pct >= alert_profit_pct:
                status = "profit_alert"

            # More precise priority: stop_loss_risk > target_close > profit_alert > normal
            # Re-evaluate with priority
            if stop_loss is not None:
                stop_threshold = stop_loss * 1.02  # within 2% above stop
                if current_price <= stop_threshold:
                    status = "stop_loss_risk"
                elif target is not None and current_price >= target * 0.98:
                    status = "target_close"
                elif unrealized_pnl_pct >= alert_profit_pct:
                    status = "profit_alert"
                else:
                    status = "normal"
            else:
                if target is not None and current_price >= target * 0.98:
                    status = "target_close"
                elif unrealized_pnl_pct >= alert_profit_pct:
                    status = "profit_alert"
                else:
                    status = "normal"

            total_value += market_val
            total_cost += cost

            holdings.append({
                "id": h["id"],
                "ticker": h["ticker"],
                "buy_price": buy_price,
                "volume": volume,
                "buy_date": h["buy_date"],
                "stop_loss": stop_loss,
                "target": target,
                "current_price": round(current_price, 2),
                "unrealized_pnl": unrealized_pnl,
                "unrealized_pnl_pct": unrealized_pnl_pct,
                "days_held": hold_days,
                "status": status,
            })

        total_unrealized_pnl = round(total_value - total_cost, 2)
        total_unrealized_pnl_pct = round(
            ((total_value - total_cost) / total_cost) * 100, 2
        ) if total_cost else 0.0

        return {
            "holdings": holdings,
            "total_holdings": len(holdings),
            "total_value": round(total_value, 2),
            "total_cost": round(total_cost, 2),
            "total_unrealized_pnl": total_unrealized_pnl,
            "total_unrealized_pnl_pct": total_unrealized_pnl_pct,
        }
    finally:
        if own_db:
            db.close()


# ======================================================================
# 4. get_trade_journal
# ======================================================================
def get_trade_journal(
    db: TradingDB | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    ticker: str | None = None,
) -> dict[str, Any]:
    """Return trade history with analytics.

    Args:
        db: TradingDB instance. Created if not provided.
        start_date: Optional start date filter (YYYY-MM-DD).
        end_date: Optional end date filter (YYYY-MM-DD).
        ticker: Optional ticker filter.

    Returns:
        Dict with 'trades' list and 'summary' analytics.
    """
    own_db = db is None
    if own_db:
        db = TradingDB()

    try:
        trades = db.get_trades(ticker=ticker, start_date=start_date, end_date=end_date)

        if not trades:
            return {
                "trades": [],
                "summary": {
                    "total_trades": 0,
                    "wins": 0,
                    "losses": 0,
                    "win_rate": 0.0,
                    "total_pnl": 0.0,
                    "avg_pnl": 0.0,
                    "avg_win": 0.0,
                    "avg_loss": 0.0,
                    "best_trade": None,
                    "worst_trade": None,
                    "avg_hold_days": 0.0,
                    "profit_factor": 0.0,
                },
            }

        wins = [t for t in trades if t["pnl"] and t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] and t["pnl"] < 0]

        total_pnl = sum(t["pnl"] for t in trades if t["pnl"])
        avg_pnl = total_pnl / len(trades)

        avg_win = (sum(t["pnl"] for t in wins) / len(wins)) if wins else 0.0
        avg_loss = (sum(t["pnl"] for t in losses) / len(losses)) if losses else 0.0

        # Best and worst trade
        pnl_trades = [t for t in trades if t["pnl"] is not None]
        best_trade = max(pnl_trades, key=lambda t: t["pnl"]) if pnl_trades else None
        worst_trade = min(pnl_trades, key=lambda t: t["pnl"]) if pnl_trades else None

        # Average hold days
        hold_days_list = []
        for t in trades:
            try:
                buy_dt = datetime.strptime(t["buy_date"], "%Y-%m-%d")
                sell_dt = datetime.strptime(t["sell_date"], "%Y-%m-%d")
                hold_days_list.append((sell_dt - buy_dt).days)
            except (ValueError, KeyError):
                pass
        avg_hold_days = (sum(hold_days_list) / len(hold_days_list)) if hold_days_list else 0.0

        # Profit factor
        gross_wins = sum(t["pnl"] for t in wins)
        gross_losses = abs(sum(t["pnl"] for t in losses))
        profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else float("inf") if gross_wins > 0 else 0.0

        summary = {
            "total_trades": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(trades) * 100, 2),
            "total_pnl": round(total_pnl, 2),
            "avg_pnl": round(avg_pnl, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "best_trade": best_trade,
            "worst_trade": worst_trade,
            "avg_hold_days": round(avg_hold_days, 1),
            "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else "inf",
        }

        return {"trades": trades, "summary": summary}
    finally:
        if own_db:
            db.close()


# ======================================================================
# 5. update_stops
# ======================================================================
def update_stops(
    ticker: str,
    db: TradingDB | None = None,
) -> dict[str, Any]:
    """Recalculate stop-loss and target for an open position based on latest ATR.

    Stop-loss only trails upward (never down). Target only moves upward (never down).

    Args:
        ticker: Stock symbol.
        db: TradingDB instance. Created if not provided.

    Returns:
        Dict with ticker, old/new stop, old/new target, updated flag.
    """
    own_db = db is None
    if own_db:
        db = TradingDB()

    try:
        ticker = ticker.upper().strip()
        holding = db.get_holding(ticker)
        if holding is None:
            raise ValueError(f"No open position for {ticker}")

        # Get current price
        ohlcv = db.get_ohlcv(ticker, days=1)
        if not ohlcv:
            raise ValueError(f"No OHLCV data for {ticker}")
        current_price = ohlcv[-1]["close"]

        # Get latest ATR
        latest_ind = db.get_latest_indicators(ticker)
        if not latest_ind or latest_ind.get("atr_14") is None:
            return {
                "ticker": ticker,
                "old_stop": holding.get("stop_loss"),
                "new_stop": holding.get("stop_loss"),
                "old_target": holding.get("target"),
                "new_target": holding.get("target"),
                "updated": False,
                "reason": "No ATR data available",
            }

        atr_14 = latest_ind["atr_14"]
        atr_stop_mult, atr_target_mult = _get_atr_settings(db)

        new_stop = round(current_price - (atr_14 * atr_stop_mult), 2)
        new_target = round(current_price + (atr_14 * atr_target_mult), 2)

        old_stop = holding.get("stop_loss")
        old_target = holding.get("target")

        updated = False
        update_fields: dict[str, Any] = {}

        # Stop-loss only moves UP (trailing)
        if old_stop is None or new_stop > old_stop:
            update_fields["stop_loss"] = new_stop
            updated = True
        else:
            new_stop = old_stop

        # Target only moves UP
        if old_target is None or new_target > old_target:
            update_fields["target"] = new_target
            updated = True
        else:
            new_target = old_target

        if updated:
            db.update_holding(holding["id"], **update_fields)

        return {
            "ticker": ticker,
            "old_stop": old_stop,
            "new_stop": new_stop,
            "old_target": old_target,
            "new_target": new_target,
            "updated": updated,
        }
    finally:
        if own_db:
            db.close()


# ======================================================================
# 6. batch_update_stops
# ======================================================================
def batch_update_stops(db: TradingDB | None = None) -> list[dict[str, Any]]:
    """Run update_stops for all open holdings.

    Args:
        db: TradingDB instance. Created if not provided.

    Returns:
        List of update_stops result dicts.
    """
    own_db = db is None
    if own_db:
        db = TradingDB()

    try:
        holdings = db.get_holdings()
        results: list[dict[str, Any]] = []

        for h in holdings:
            try:
                result = update_stops(h["ticker"], db=db)
                results.append(result)
            except Exception as e:
                results.append({
                    "ticker": h["ticker"],
                    "updated": False,
                    "error": str(e),
                })

        return results
    finally:
        if own_db:
            db.close()


# ======================================================================
# Self-test
# ======================================================================
if __name__ == "__main__":
    import tempfile

    print("=" * 60)
    print("portfolio.py — Self-test")
    print("=" * 60)

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_path = tmp.name
    tmp.close()

    print(f"\nTest DB: {tmp_path}")
    db = TradingDB(db_path=tmp_path)

    try:
        # --- Step 1: Add test ticker ---
        print("\n--- Step 1: Add AAPL to watchlist ---")
        db.add_ticker("AAPL", "test")
        print("  AAPL added.")

        # --- Step 2: Fetch data ---
        print("\n--- Step 2: Fetch AAPL data ---")
        from data_fetcher import fetch_and_store
        ok = fetch_and_store("AAPL", db=db, period="3mo")
        print(f"  Fetch result: {'OK' if ok else 'FAILED'}")
        if not ok:
            print("  Cannot continue without data. Aborting.")
            db.close()
            os.unlink(tmp_path)
            sys.exit(1)

        # --- Step 3: Run alpha engine ---
        print("\n--- Step 3: Run alpha engine ---")
        from alpha_engine import analyze_ticker
        result = analyze_ticker("AAPL", db=db)
        print(f"  Signal: {result['signal']}")
        print(f"  ATR-14: {result['indicators'].get('atr_14')}")
        print(f"  Close: {result['indicators'].get('close')}")

        # --- Step 4: Open position ---
        print("\n--- Step 4: Open AAPL position (buy=290, volume=50) ---")
        pos = open_position("AAPL", buy_price=290.0, volume=50, db=db, notes="test buy")
        print(f"  Position opened:")
        print(f"    holding_id: {pos['holding_id']}")
        print(f"    ticker: {pos['ticker']}")
        print(f"    buy_price: {pos['buy_price']}")
        print(f"    volume: {pos['volume']}")
        print(f"    stop_loss: {pos['stop_loss']}")
        print(f"    target: {pos['target']}")
        assert pos["holding_id"] is not None
        assert pos["stop_loss"] < pos["buy_price"]
        assert pos["target"] > pos["buy_price"]

        # Test duplicate rejection
        print("\n  Testing duplicate rejection...")
        try:
            open_position("AAPL", buy_price=295.0, volume=10, db=db)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            print(f"  Correctly rejected: {e}")

        # Test invalid inputs
        print("  Testing invalid inputs...")
        try:
            open_position("MSFT", buy_price=-10, volume=50, db=db)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            print(f"  Correctly rejected negative price: {e}")

        try:
            open_position("MSFT", buy_price=100, volume=-5, db=db)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            print(f"  Correctly rejected negative volume: {e}")

        # --- Step 5: Get portfolio status ---
        print("\n--- Step 5: Get portfolio status ---")
        status = get_portfolio_status(db=db)
        print(f"  Total holdings: {status['total_holdings']}")
        print(f"  Total value: {status['total_value']}")
        print(f"  Total cost: {status['total_cost']}")
        print(f"  Unrealized P&L: {status['total_unrealized_pnl']}")
        print(f"  Unrealized P&L%: {status['total_unrealized_pnl_pct']}%")
        for h in status["holdings"]:
            print(f"    {h['ticker']}: buy={h['buy_price']}, current={h['current_price']}, "
                  f"P&L={h['unrealized_pnl']} ({h['unrealized_pnl_pct']}%), "
                  f"status={h['status']}")
        assert status["total_holdings"] == 1

        # --- Step 6: Test update_stops ---
        print("\n--- Step 6: Test update_stops ---")
        upd = update_stops("AAPL", db=db)
        print(f"  Old stop: {upd['old_stop']}, New stop: {upd['new_stop']}")
        print(f"  Old target: {upd['old_target']}, New target: {upd['new_target']}")
        print(f"  Updated: {upd['updated']}")

        # --- Step 7: Batch update stops ---
        print("\n--- Step 7: Batch update stops ---")
        batch = batch_update_stops(db=db)
        for b in batch:
            print(f"  {b['ticker']}: updated={b.get('updated', False)}")
        assert len(batch) >= 1

        # --- Step 8: Close position at 310 ---
        print("\n--- Step 8: Close AAPL at 310 ---")
        close_result = close_position(pos["holding_id"], sell_price=310.0, db=db, notes="test close")
        print(f"  Ticker: {close_result['ticker']}")
        print(f"  Buy: {close_result['buy_price']}, Sell: {close_result['sell_price']}")
        print(f"  Volume: {close_result['volume']}")
        print(f"  P&L: ${close_result['pnl']} ({close_result['pnl_pct']}%)")
        print(f"  Hold days: {close_result['hold_days']}")
        assert close_result["pnl"] == 1000.0  # (310 - 290) * 50
        assert close_result["pnl_pct"] == 6.9

        # Verify no holdings remain
        empty_status = get_portfolio_status(db=db)
        assert empty_status["total_holdings"] == 0
        print("  Holdings after close: 0 ✓")

        # --- Step 9: Get trade journal ---
        print("\n--- Step 9: Get trade journal ---")
        journal = get_trade_journal(db=db)
        print(f"  Total trades: {journal['summary']['total_trades']}")
        print(f"  Wins: {journal['summary']['wins']}")
        print(f"  Losses: {journal['summary']['losses']}")
        print(f"  Win rate: {journal['summary']['win_rate']}%")
        print(f"  Total P&L: ${journal['summary']['total_pnl']}")
        print(f"  Avg P&L: ${journal['summary']['avg_pnl']}")
        print(f"  Avg win: ${journal['summary']['avg_win']}")
        print(f"  Avg loss: ${journal['summary']['avg_loss']}")
        print(f"  Best trade: {journal['summary']['best_trade']}")
        print(f"  Worst trade: {journal['summary']['worst_trade']}")
        print(f"  Avg hold days: {journal['summary']['avg_hold_days']}")
        print(f"  Profit factor: {journal['summary']['profit_factor']}")
        assert journal["summary"]["total_trades"] == 1
        assert journal["summary"]["wins"] == 1

        # --- Step 10: Test close non-existent holding ---
        print("\n--- Step 10: Edge cases ---")
        try:
            close_position(99999, db=db)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            print(f"  Correctly rejected non-existent holding: {e}")

        # Test opening without ATR (ticker not analyzed)
        print("  Testing fallback stop/target (no indicators)...")
        db.add_ticker("TSLA", "test")
        pos2 = open_position("TSLA", buy_price=250.0, volume=20, db=db)
        print(f"    stop_loss: {pos2['stop_loss']} (expected: {250.0 * 0.92})")
        print(f"    target: {pos2['target']} (expected: {250.0 * 1.20})")
        assert pos2["stop_loss"] == 250.0 * 0.92
        assert pos2["target"] == 250.0 * 1.20
        # Clean up TSLA
        db.close_holding(pos2["holding_id"], sell_price=260.0, sell_date=_today())

        print("\n" + "=" * 60)
        print("✅ All self-tests passed!")
        print("=" * 60)

    finally:
        db.close()
        os.unlink(tmp_path)
        print(f"\nTest DB removed: {tmp_path}")
