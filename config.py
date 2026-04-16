"""
=============================================================
  FOREX TRADING BOT — CONFIGURATION
  Professional Multi-Timeframe EMA Strategy
=============================================================
  Symbols  : EURUSD.m · GBPUSD.m · USDJPY.m
             XAUUSD.m (Gold) · BTCUSD.m (Bitcoin)
  Strategy : EMA 9/21/50/200 across 1m / 5m / 15m
  Execution: MetaAPI (MetaTrader 4/5)
  Alerts   : Telegram Bot
=============================================================
"""

import os
from typing import Dict, List


# ─── BROKER SUFFIX ────────────────────────────────────────────────────────────
# Your broker appends this to every symbol (e.g. ".m" → "EURUSD.m")
BROKER_SUFFIX: str = ".m"


# ─── YAHOO FINANCE SYMBOLS → DISPLAY LABELS ──────────────────────────────────
# Keys are yfinance tickers. Values are base symbol names (suffix added automatically).

YAHOO_TO_BASE: Dict[str, str] = {
    "EURUSD=X": "EURUSD",
    "GBPUSD=X": "GBPUSD",
    "USDJPY=X": "USDJPY",
    "GC=F":     "XAUUSD",   # Gold spot via Gold Futures (most liquid)
    "BTC-USD":  "BTCUSD",   # Bitcoin / USD
}

# List of Yahoo Finance tickers to monitor
SYMBOLS: List[str] = list(YAHOO_TO_BASE.keys())


# ─── BROKER (MetaTrader) SYMBOL MAPPING ───────────────────────────────────────
# Yahoo ticker → full broker symbol name (with suffix)

def _mt_symbol(base: str) -> str:
    return f"{base}{BROKER_SUFFIX}"

META_SYMBOLS: Dict[str, str] = {
    yf: _mt_symbol(base) for yf, base in YAHOO_TO_BASE.items()
}
# e.g. {"EURUSD=X": "EURUSD.m", "GC=F": "XAUUSD.m", "BTC-USD": "BTCUSD.m"}


# ─── INSTRUMENT TYPE CLASSIFICATION ───────────────────────────────────────────

JPY_PAIRS:     set = {"USDJPY=X"}
GOLD_PAIRS:    set = {"GC=F"}
CRYPTO_PAIRS:  set = {"BTC-USD"}
FOREX_PAIRS:   set = {"EURUSD=X", "GBPUSD=X", "USDJPY=X"}


# ─── PIP / POINT VALUES ───────────────────────────────────────────────────────
# The monetary value of 1 pip for each instrument.

PIP_VALUE: Dict[str, float] = {
    "EURUSD=X": 0.0001,   # Standard forex
    "GBPUSD=X": 0.0001,
    "USDJPY=X": 0.01,     # JPY-quoted
    "GC=F":     0.10,     # Gold: 1 pip = $0.10 (price ~2000, 2 decimal places)
    "BTC-USD":  1.0,      # Bitcoin: 1 pip = $1.00
}


# ─── PER-SYMBOL SL / TP IN PIPS ───────────────────────────────────────────────
# Forex: tight (5 / 10 pips).
# Gold:  wider (200 / 400 pips ≈ $20 / $40 move).
# BTC:   much wider (500 / 1000 pips ≈ $500 / $1000 move).

SL_PIPS: Dict[str, int] = {
    "EURUSD=X": 5,
    "GBPUSD=X": 5,
    "USDJPY=X": 5,
    "GC=F":     200,
    "BTC-USD":  500,
}

TP_PIPS: Dict[str, int] = {
    "EURUSD=X": 10,
    "GBPUSD=X": 10,
    "USDJPY=X": 10,
    "GC=F":     400,
    "BTC-USD":  1000,
}


# ─── TIMEFRAMES ───────────────────────────────────────────────────────────────

TIMEFRAMES: Dict[str, Dict] = {
    "1m":  {"interval": "1m",  "period": "1d",  "min_bars": 250, "label": "1-Minute  (Entry)"},
    "5m":  {"interval": "5m",  "period": "5d",  "min_bars": 250, "label": "5-Minute  (Setup)"},
    "15m": {"interval": "15m", "period": "5d",  "min_bars": 200, "label": "15-Minute (Trend)"},
}


# ─── INDICATORS ───────────────────────────────────────────────────────────────

EMA_PERIODS:  List[int] = [9, 21, 50, 200]
ATR_PERIOD:   int   = 14
RSI_PERIOD:   int   = 14
BB_PERIOD:    int   = 20
BB_STD_DEV:   float = 2.0


# ─── STRATEGY THRESHOLDS ──────────────────────────────────────────────────────

# How close to EMA band counts as a "pullback" (fraction of price)
PULLBACK_TOLERANCE: float = 0.0025        # 0.25%

# Candle body must be ≥ this fraction of the full high-low range
MIN_CANDLE_BODY_RATIO: float = 0.35

# RSI extremes — skip signals if RSI too extreme in signal direction
RSI_OVERBOUGHT: float = 78.0
RSI_OVERSOLD:   float = 22.0

# Minimum EMA-9 vs EMA-21 separation to confirm divergence (in pips)
MIN_EMA_SEPARATION_PIPS: int = 1


# ─── RISK MANAGEMENT ──────────────────────────────────────────────────────────

# Lot sizes per instrument type
LOT_SIZES: Dict[str, float] = {
    "EURUSD=X": 0.01,
    "GBPUSD=X": 0.01,
    "USDJPY=X": 0.01,
    "GC=F":     0.01,   # Gold micro lot
    "BTC-USD":  0.001,  # Bitcoin micro
}

MAX_CONCURRENT_TRADES:    int   = 3
MAX_DAILY_LOSS_PIPS:      float = -150.0   # Aggregate across all symbols
DAILY_PROFIT_TARGET_PIPS: float = 300.0
SLIPPAGE_PIPS:            float = 2.0


# ─── EXECUTION ────────────────────────────────────────────────────────────────

LOOP_INTERVAL_SECONDS:    int = 60
RECONNECT_WAIT_SECONDS:   int = 30
MAX_RECONNECT_ATTEMPTS:   int = 5
MAX_CONSECUTIVE_ERRORS:   int = 5
META_DEPLOY_TIMEOUT:      int = 120
META_SYNC_TIMEOUT:        int = 60
META_ORDER_TIMEOUT:       int = 30


# ─── CREDENTIALS ──────────────────────────────────────────────────────────────

def _require_env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        raise EnvironmentError(
            f"Required environment variable '{key}' is not set. "
            f"Please add it to Replit Secrets."
        )
    return val


TELEGRAM_BOT_TOKEN: str = _require_env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID:   str = _require_env("TELEGRAM_CHAT_ID")
META_API_TOKEN:     str = _require_env("META_API_TOKEN")
META_ACCOUNT_ID:    str = _require_env("META_ACCOUNT_ID")


# ─── LOGGING ──────────────────────────────────────────────────────────────────

LOG_FILE:          str = "logs/forex_bot.log"
TRADE_LOG_FILE:    str = "logs/trade_journal.csv"
LOG_MAX_BYTES:     int = 10 * 1024 * 1024   # 10 MB per log file
LOG_BACKUP_COUNT:  int = 5
LOG_LEVEL_CONSOLE: str = "INFO"
LOG_LEVEL_FILE:    str = "DEBUG"


# ─── BOT IDENTITY ─────────────────────────────────────────────────────────────

BOT_VERSION: str = "2.0.0"
BOT_NAME:    str = "ForexBot Pro"
