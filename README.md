# 🔐 Cipher Trading System

A personal swing trading signal scanner for US equities. Runs daily, tracks your watchlist, and alerts you when entry or exit conditions align.

---

## What This System Does

Cipher is a **daily signal scanner** with three jobs:

1. **Track your watchlist** — fetches latest price data for every ticker you add
2. **Generate signals** — tells you which stocks are oversold (potential buy) or overbought (potential sell)
3. **Manage positions** — calculates stop losses, profit targets, and trailing stops for your open holdings

It does **not** execute trades. It does **not** screen the entire market. It does **not** predict direction. It tells you what the indicators say right now — you decide and act.

---

## Who This Is For

**Recommended trader profile:**

| | |
|---|---|
| **Style** | Swing trading (hold days to weeks) |
| **Market** | US equities (NYSE / NASDAQ) |
| **Markets hours** | 9:30 PM – 4:00 AM MYT (9:30 AM – 4:00 PM ET) |
| **Data source** | Yahoo Finance via yfinance |
| **Analysis type** | Technical — price action + indicators |
| **Role of system** | Scanner + tracker. You make all trading decisions |

This is **not** for day traders, quant traders, or anyone looking for automated execution. It's for traders who want a disciplined daily routine: screen once, add to watchlist, let the system monitor, act on signals.

---

## Strategy Overview

### Entry Signal — BUY

A BUY signal fires when **both** conditions are true:

| Condition | Threshold | Why |
|-----------|-----------|-----|
| RSI(14) | < 30 | Stock is oversold — selling pressure exhausted |
| Close | ≤ Bollinger Band Lower | Price is at an extreme low relative to recent range |

Two independent confirmations required. Not just oversold on RSI. Not just at the lower band. Both must align.

**Trend context** is shown in the signal notes:
- ✅ **"above SMA200"** — pullback in a long-term uptrend (higher confidence)
- ⚠️ **"BELOW SMA200"** — oversold in a downtrend (falling knife risk, proceed with caution)

You decide whether to act on the signal.

### Exit Signal — SELL

A SELL signal fires when **both** conditions are true:

| Condition | Threshold | Why |
|-----------|-----------|-----|
| RSI(14) | > 70 | Stock is overbought — buying pressure exhausted |
| Close | ≥ Bollinger Band Upper | Price is at an extreme high relative to recent range |

In practice, most swing trade exits happen via the **trailing stop** or **profit target** rather than a SELL signal. The SELL signal is a secondary exit tool.

### Risk Management — ATR-Based

When you open a position, the system calculates:

| | Formula | Purpose |
|---|---------|---------|
| **Stop Loss** | Entry − 1.5 × ATR(14) | Maximum acceptable loss |
| **Profit Target** | Entry + 2.5 × ATR(14) | Realistic profit target |
| **Trailing Stop** | Moves up only, never down | Locks in profit as price rises |

ATR (Average True Range) measures volatility. ATR-based stops adapt to each stock's personality — volatile stocks get wider stops, calm stocks get tighter ones.

### Stale Ticker Cleanup

Stocks that sit in the "boring middle" get auto-removed after **15 trading days** of:
- RSI between 40–60 (no momentum)
- Price in the middle of the Bollinger Bands (no direction)

OR if the price falls below the 200-day SMA (long-term downtrend).

Auto-dropped tickers can be reactivated anytime from the Dropped Tickers section.

---

## Recommended Workflow

### Step 1: Find Stocks on TradingView

Use TradingView's stock screener to find candidates. The system does not screen for you — it only tracks what you give it.

**Primary screener — approaching oversold:**

| Filter | Value | Why |
|--------|-------|-----|
| RSI(14) | < 40 | Getting close to oversold territory |
| Bollinger %B | < 0.3 | Price in lower portion of bands |
| Average Volume | > 500K | Liquid enough to exit cleanly |
| Price | > $10 | Avoid penny stocks |
| Market Cap | > $1B | Established companies |

**Secondary screener — trending pullbacks:**

| Filter | Value | Why |
|--------|-------|-----|
| Price vs SMA(200) | Above | Long-term uptrend confirmed |
| RSI(14) | 30–40 | Pullback in an uptrend |

Save these as presets in TradingView. Scan periodically — once or twice a week is enough.

### Step 2: Add to Watchlist

On the dashboard (**📋 Watchlist** tab):

- **Single add:** Search ticker → verify name/price → Add
- **Batch add:** Paste comma-separated tickers → Add All

Historical data (1 year) is fetched automatically on add.

### Step 3: Let the System Run

The system runs automatically every weekday at **5:30 PM MYT** (after US market close). You'll receive a Telegram message with:

- Holdings alerts (stop loss hit, profit target reached)
- BUY signals (if any)
- SELL signals (if any)
- Summary (total analyzed, counts)

You can also click **▶ Run Analysis** manually anytime on the Signals tab.

### Step 4: Act on Signals

When you see a BUY signal you like:

1. Check the signal notes — above or below SMA(200)?
2. Verify on TradingView — check the chart, volume, recent news
3. If you decide to buy → open your broker, execute the trade
4. Log the position on the dashboard (**💼 Holdings** tab):
   - Enter ticker, buy price, number of shares
   - System auto-calculates stop loss and target
5. Monitor — the system tracks trailing stops and alerts you

### Step 5: Close Positions

When to sell:
- **Trailing stop hit** → system alerts you on Telegram
- **Profit target reached** → system alerts you
- **Your own analysis** → you decide to exit early

Close the position on the dashboard (**💼 Holdings** tab). P&L is recorded in the Trade Journal.

---

## Dashboard Guide

Access at: **http://127.0.0.1:8501**

### 📋 Watchlist

| Feature | Description |
|---------|-------------|
| Add ticker | Search by symbol, verify name/price/sector |
| Batch add | Paste multiple tickers at once |
| Active table | Shows RSI, signal, days active, data freshness per ticker |
| Drop ticker | Remove from active monitoring |
| Dropped tickers | View and reactivate previously dropped tickers |
| Data status | Overall data freshness indicator |

### 💼 Holdings

| Feature | Description |
|---------|-------------|
| Open position | Log a new buy with price, shares, optional notes |
| Close position | Record the sell, system calculates P&L |
| Trailing stop | Automatically updated each analysis run |
| Fractional shares | Supported (e.g., 1.4722 shares) |

### 🎯 Signals

| Feature | Description |
|---------|-------------|
| Run Analysis | Fetch data + analyze all tickers + update stops + check stale |
| Today's Signals | BUY / SELL / HOLD for the current run |
| Signal History | View signals from previous dates |
| Holdings Alerts | Stop loss / profit target alerts for open positions |
| Send to Telegram | Forward the current report to your Telegram |
| Run History | Log of all analysis runs with timing and results |

### 📊 Strategy

View and edit all signal parameters:

| Parameter | Default | What it does |
|-----------|---------|--------------|
| RSI Period | 14 | Lookback period for RSI calculation |
| RSI Oversold | 30 | RSI threshold for BUY signal |
| RSI Overbought | 70 | RSI threshold for SELL signal |
| BB Period | 20 | Lookback for Bollinger Bands |
| BB Std Dev | 2.0 | Standard deviation for bands |
| ATR Period | 14 | Lookback for Average True Range |
| ATR Stop Multiplier | 1.5 | Stop loss = entry − (multiplier × ATR) |
| ATR Target Multiplier | 2.5 | Target = entry + (multiplier × ATR) |
| SMA Period | 200 | Lookback for trend filter |
| Stale Days | 15 | Trading days in dead zone before auto-drop |
| Alert Profit % | 10 | Unrealized profit % to trigger alert |

### 📒 Trade Journal

Interactive charts for closed trades:

- Cumulative P&L over time
- P&L distribution (winners vs losers)
- P&L by ticker
- Monthly win rate
- Filterable by date range and ticker

### ⚙️ Settings

- Telegram bot configuration
- Data management (clear cache, export database)
- System info and diagnostics

---

## Telegram Alerts

The bot sends a daily report after each analysis run:

```
📊 Daily Signal Report — 2026-05-26

⚠️ HOLDINGS ALERT
━━━━━━━━━━━━━━━━
🟢 AAPL: Profit alert! +13.3% unrealized

🎯 WATCHLIST SIGNALS
━━━━━━━━━━━━━━━━
🟢 BUY: BILI (RSI 27, near BB lower $17.56)
🟢 BUY: EDU (RSI 27, near BB lower $47.35)
⚪ HOLD: 135 tickers

📈 Summary: 140 analyzed | 2 buy | 0 sell | 135 hold | 0 dropped
```

Bot: `@cipher_00_bot` (private — only your chat ID receives messages)

---

## Technical Architecture

```
/home/chukungaryyew/Documents/trading/
├── db.py                   ← SQLite schema + CRUD (8 tables)
├── data_fetcher.py         ← yfinance fetch + incremental updates
├── alpha_engine.py         ← Indicators + signal generation + stale detection
├── portfolio.py            ← Holdings lifecycle, P&L, trailing stops
├── watchlist_manager.py    ← Add / drop / reactivate / search
├── telegram_reporter.py    ← Report formatting + Telegram delivery
├── dashboard.py            ← Streamlit main entry
├── _pages/                 ← Dashboard page modules
│   ├── watchlist.py
│   ├── holdings.py
│   ├── signals.py
│   ├── strategy.py
│   ├── journal.py
│   └── settings.py
├── trading.db              ← SQLite database
└── .cache/                 ← Dashboard chart cache
```

### Key Technical Details

| | |
|---|---|
| **Storage** | SQLite — single file, no server |
| **Data source** | yfinance (Yahoo Finance) |
| **Incremental updates** | Only fetches new trading days, not full history |
| **Indicators** | pandas_ta — RSI, BB, ATR, SMA |
| **Signals** | Upsert — one signal per ticker per date (latest wins) |
| **DB mode** | DELETE journal (avoids corruption with Streamlit threading) |
| **Shared connection** | Single DB connection across all dashboard pages |
| **No LLM dependency** | System runs standalone Python, no AI needed for execution |

### Daily Automated Flow

```
5:30 PM MYT (Mon–Fri)
    │
    ▼
Incremental data fetch (only new days)
    │
    ▼
Compute indicators for all tickers
    │
    ▼
Generate BUY / SELL / HOLD signals
    │
    ▼
Update trailing stops on open positions
    │
    ▼
Check for stale tickers → auto-drop
    │
    ▼
Format + send Telegram report
```

---

## Important Notes

### This System Does NOT
- Execute trades automatically
- Screen the entire market for new stocks
- Predict future price movements
- Replace your own chart analysis and judgment
- Account for fundamentals, earnings, news, or macro events

### Before Trading a Signal
1. Check the chart on TradingView
2. Verify volume (is there a volume spike on the down day?)
3. Check for upcoming earnings or news events
4. Consider position size relative to your total portfolio
5. Always use the stop loss — no exceptions

### Risk Disclaimer
This is a personal trading tool, not financial advice. All trading involves risk. Past signals do not guarantee future results. Use at your own risk. Always do your own research before entering any trade.

---

## Quick Reference

| Action | Where |
|--------|-------|
| Add stocks to track | 📋 Watchlist → Add / Batch Add |
| Run daily analysis | 🎯 Signals → Run Analysis (or wait for 5:30 PM auto-run) |
| Check signals | 🎯 Signals → Today's Signals |
| Log a buy | 💼 Holdings → Open Position |
| Log a sell | 💼 Holdings → Close Position |
| Review past trades | 📒 Trade Journal |
| Adjust strategy params | 📊 Strategy → Edit mode |
| Find new stocks | TradingView screener → then add to watchlist |

---

*Built with Python, SQLite, Streamlit, yfinance, and pandas_ta.*
*🔐 Cipher Trading System*
