"""
=============================================================
  DATA FETCHER MODULE
  Yahoo Finance OHLC + Technical Indicator Computation
=============================================================
  Indicators computed per DataFrame:
    • EMA 9, 21, 50, 200
    • ATR (14)
    • RSI (14)
    • Bollinger Bands (20, 2σ)
    • Candle body / wick metadata
=============================================================
"""

import numpy as np
import pandas as pd
import yfinance as yf
from typing import Optional

from config import (
    EMA_PERIODS, ATR_PERIOD, RSI_PERIOD,
    BB_PERIOD, BB_STD_DEV, TIMEFRAMES
)
from logger import get_logger

log = get_logger("DataFetcher")


# ─── OHLC FETCH ──────────────────────────────────────────────────────────────

def fetch_ohlc(symbol: str, interval: str, period: str) -> Optional[pd.DataFrame]:
    """
    Download OHLCV data from Yahoo Finance.

    Returns a clean DataFrame with columns:
        Open, High, Low, Close, Volume
    or None if data is unavailable / insufficient.
    """
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(
            period=period,
            interval=interval,
            auto_adjust=True,
            prepost=False,
            repair=True,
        )

        if df is None or df.empty:
            log.warning(f"[{symbol}] No data returned for interval={interval}")
            return None

        df.index = pd.to_datetime(df.index, utc=True)
        df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
        df.dropna(subset=["Close"], inplace=True)

        if len(df) == 0:
            log.warning(f"[{symbol}] DataFrame empty after dropping NaN.")
            return None

        return df

    except Exception as exc:
        log.error(f"[{symbol}] fetch_ohlc error (interval={interval}): {exc}")
        return None


# ─── INDICATOR CALCULATIONS ──────────────────────────────────────────────────

def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def _atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    """Average True Range."""
    high  = df["High"]
    low   = df["Low"]
    close = df["Close"]
    prev_close = close.shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)

    return tr.ewm(span=period, adjust=False, min_periods=period).mean()


def _rsi(series: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    """Relative Strength Index via Wilder smoothing."""
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)

    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()

    rs  = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


def _bollinger_bands(
    series: pd.Series,
    period: int   = BB_PERIOD,
    std_dev: float = BB_STD_DEV,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (upper_band, middle_band, lower_band)."""
    mid   = series.rolling(window=period, min_periods=period).mean()
    sigma = series.rolling(window=period, min_periods=period).std(ddof=0)
    upper = mid + std_dev * sigma
    lower = mid - std_dev * sigma
    return upper, mid, lower


def _candle_meta(df: pd.DataFrame) -> pd.DataFrame:
    """Append body size, upper/lower wick, and body ratio columns."""
    df["body"]       = (df["Close"] - df["Open"]).abs()
    df["full_range"] = df["High"] - df["Low"]
    df["body_ratio"] = df["body"] / df["full_range"].replace(0, np.nan)
    df["bullish"]    = df["Close"] > df["Open"]
    df["upper_wick"] = df["High"] - df[["Close", "Open"]].max(axis=1)
    df["lower_wick"] = df[["Close", "Open"]].min(axis=1) - df["Low"]
    return df


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Attach all indicators to the DataFrame in-place.
    Columns added:
        EMA9, EMA21, EMA50, EMA200
        ATR14, RSI14
        BB_upper, BB_mid, BB_lower
        body, full_range, body_ratio, bullish, upper_wick, lower_wick
    """
    close = df["Close"]

    # ── EMAs ──
    for period in EMA_PERIODS:
        df[f"EMA{period}"] = _ema(close, period)

    # ── ATR ──
    df["ATR14"] = _atr(df, ATR_PERIOD)

    # ── RSI ──
    df["RSI14"] = _rsi(close, RSI_PERIOD)

    # ── Bollinger Bands ──
    df["BB_upper"], df["BB_mid"], df["BB_lower"] = _bollinger_bands(
        close, BB_PERIOD, BB_STD_DEV
    )

    # ── Candle metadata ──
    df = _candle_meta(df)

    return df


# ─── HIGH-LEVEL ACCESSOR ─────────────────────────────────────────────────────

def get_data_with_indicators(
    symbol: str,
    timeframe_key: str,
) -> Optional[pd.DataFrame]:
    """
    Download OHLCV for `symbol` at `timeframe_key` (e.g. '1m', '5m', '15m'),
    compute all indicators, and return the enriched DataFrame.

    Returns None if data is unavailable or insufficient.
    """
    tf = TIMEFRAMES[timeframe_key]
    interval  = tf["interval"]
    period    = tf["period"]
    min_bars  = tf["min_bars"]

    df = fetch_ohlc(symbol, interval, period)
    if df is None:
        return None

    if len(df) < min_bars:
        log.warning(
            f"[{symbol}] [{timeframe_key}] Only {len(df)} bars — need {min_bars}. "
            f"Indicators may be unreliable."
        )
        # Still proceed — EMA needs only ~200 bars for EMA200

    df = compute_indicators(df)

    # Drop rows where any EMA200 is NaN (insufficient history)
    df.dropna(subset=["EMA200"], inplace=True)

    if df.empty:
        log.warning(f"[{symbol}] [{timeframe_key}] No rows after EMA200 warmup drop.")
        return None

    log.debug(
        f"[{symbol}] [{timeframe_key}] {len(df)} bars | "
        f"Close={df['Close'].iloc[-1]:.5f} | "
        f"EMA9={df['EMA9'].iloc[-1]:.5f} | "
        f"EMA200={df['EMA200'].iloc[-1]:.5f}"
    )
    return df


def fetch_all_timeframes(symbol: str) -> dict[str, Optional[pd.DataFrame]]:
    """
    Fetch 1m, 5m, and 15m DataFrames for a given symbol in one call.
    Returns a dict keyed by timeframe string.
    """
    return {
        "1m":  get_data_with_indicators(symbol, "1m"),
        "5m":  get_data_with_indicators(symbol, "5m"),
        "15m": get_data_with_indicators(symbol, "15m"),
    }


# ─── UTILITY HELPERS ─────────────────────────────────────────────────────────

def latest(df: pd.DataFrame, col: str) -> float:
    """Safely get the most recent value of a column."""
    return float(df[col].iloc[-1])


def prev(df: pd.DataFrame, col: str, n: int = 1) -> float:
    """Safely get the n-th most recent value of a column."""
    return float(df[col].iloc[-(n + 1)])


def ema_slope(df: pd.DataFrame, col: str, lookback: int = 3) -> float:
    """Approximate slope of an EMA column over last `lookback` bars."""
    if len(df) < lookback + 1:
        return 0.0
    vals = df[col].iloc[-lookback:].values
    return float(np.polyfit(range(len(vals)), vals, 1)[0])
