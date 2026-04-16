"""
=============================================================
  STRATEGY MODULE
  Multi-Timeframe EMA Confluence System
=============================================================
  Logic per timeframe:

  ── 15m (TREND FILTER) ───────────────────────────────────
    BUY  : Price > EMA200  AND  EMA50 > EMA200  (uptrend)
    SELL : Price < EMA200  AND  EMA50 < EMA200  (downtrend)

  ── 5m (SETUP FILTER) ────────────────────────────────────
    BUY  : EMA9 > EMA21  AND  price has pulled back
           into the EMA9/EMA21 zone (not extended)
    SELL : EMA9 < EMA21  AND  pullback into zone

  ── 1m (ENTRY TRIGGER) ───────────────────────────────────
    BUY  : Current candle is bullish (close > open)
           AND body >= MIN_CANDLE_BODY_RATIO of full range
           AND close > previous candle close  (momentum)
    SELL : Mirror of above for bearish

  ALL THREE must agree → signal fires.

  Additional confluence filters (applied after 3-TF alignment):
    • RSI not in extreme zone against signal direction
    • EMA9 and EMA21 separated by ≥ MIN_EMA_SEPARATION_PIPS
=============================================================
"""

from typing import Literal, Optional
import pandas as pd

from config import (
    PULLBACK_TOLERANCE,
    MIN_CANDLE_BODY_RATIO,
    RSI_OVERBOUGHT,
    RSI_OVERSOLD,
    MIN_EMA_SEPARATION_PIPS,
    PIP_VALUE,
)
from data_fetcher import latest, prev, ema_slope
from logger import get_logger

log = get_logger("Strategy")

SignalType = Literal["BUY", "SELL"]


# ─── HELPER ──────────────────────────────────────────────────────────────────

def _ema_sep_pips(symbol: str, ema9: float, ema21: float) -> float:
    pip = PIP_VALUE.get(symbol, 0.0001)
    return abs(ema9 - ema21) / pip


# ─── 15M: TREND FILTER ───────────────────────────────────────────────────────

def check_trend_15m(df: pd.DataFrame, symbol: str) -> Optional[SignalType]:
    """
    Determine the macro trend direction from the 15-minute chart.

    Rules:
      BUY  → price > EMA200  AND  EMA50 > EMA200
      SELL → price < EMA200  AND  EMA50 < EMA200

    Additional: EMA50 slope must agree with direction (rising for BUY,
    falling for SELL) to avoid ranging markets.
    """
    price  = latest(df, "Close")
    ema50  = latest(df, "EMA50")
    ema200 = latest(df, "EMA200")
    slope50 = ema_slope(df, "EMA50", lookback=5)

    log.debug(
        f"[{symbol}] 15m | price={price:.5f} EMA50={ema50:.5f} "
        f"EMA200={ema200:.5f} slope50={slope50:.8f}"
    )

    if price > ema200 and ema50 > ema200 and slope50 >= 0:
        log.debug(f"[{symbol}] 15m trend → BUY")
        return "BUY"

    if price < ema200 and ema50 < ema200 and slope50 <= 0:
        log.debug(f"[{symbol}] 15m trend → SELL")
        return "SELL"

    log.debug(f"[{symbol}] 15m trend → NEUTRAL (no clear trend)")
    return None


# ─── 5M: SETUP FILTER ────────────────────────────────────────────────────────

def check_setup_5m(
    df: pd.DataFrame,
    trend: SignalType,
    symbol: str,
) -> bool:
    """
    Confirm a pullback setup exists on the 5-minute chart.

    BUY setup:
      EMA9 > EMA21 (short-term momentum bullish)
      Price has pulled back into the EMA9/EMA21 band (not extended above)

    SELL setup:
      EMA9 < EMA21 (short-term momentum bearish)
      Price has pulled back into the EMA9/EMA21 band (not extended below)

    Also checks that EMA9 and EMA21 are sufficiently separated.
    """
    price = latest(df, "Close")
    ema9  = latest(df, "EMA9")
    ema21 = latest(df, "EMA21")

    sep_pips = _ema_sep_pips(symbol, ema9, ema21)
    log.debug(
        f"[{symbol}] 5m | price={price:.5f} EMA9={ema9:.5f} "
        f"EMA21={ema21:.5f} sep={sep_pips:.1f} pips"
    )

    if sep_pips < MIN_EMA_SEPARATION_PIPS:
        log.debug(f"[{symbol}] 5m: EMA9/21 too close ({sep_pips:.1f} pips) — no setup")
        return False

    zone_high = max(ema9, ema21) * (1.0 + PULLBACK_TOLERANCE)
    zone_low  = min(ema9, ema21) * (1.0 - PULLBACK_TOLERANCE)

    if trend == "BUY":
        momentum_ok = ema9 > ema21
        pullback_ok = zone_low <= price <= zone_high
        result = momentum_ok and pullback_ok
        log.debug(
            f"[{symbol}] 5m BUY setup: "
            f"momentum={momentum_ok} pullback={pullback_ok} → {result}"
        )
        return result

    if trend == "SELL":
        momentum_ok = ema9 < ema21
        pullback_ok = zone_low <= price <= zone_high
        result = momentum_ok and pullback_ok
        log.debug(
            f"[{symbol}] 5m SELL setup: "
            f"momentum={momentum_ok} pullback={pullback_ok} → {result}"
        )
        return result

    return False


# ─── 1M: ENTRY TRIGGER ───────────────────────────────────────────────────────

def check_entry_1m(
    df: pd.DataFrame,
    trend: SignalType,
    symbol: str,
) -> bool:
    """
    Confirm an entry candle on the 1-minute chart.

    BUY:
      • Current candle is bullish (close > open)
      • Candle body ≥ MIN_CANDLE_BODY_RATIO of full high-low range
      • Close > previous candle close (momentum confirms)
      • EMA9 > EMA21 on 1m for final alignment

    SELL:
      Mirror conditions.
    """
    if len(df) < 3:
        log.debug(f"[{symbol}] 1m: Not enough bars for entry check.")
        return False

    last_open  = latest(df, "Open")
    last_close = latest(df, "Close")
    last_high  = latest(df, "High")
    last_low   = latest(df, "Low")
    prev_close = prev(df, "Close", 1)

    full_range  = last_high - last_low
    candle_body = abs(last_close - last_open)
    body_ratio  = (candle_body / full_range) if full_range > 0 else 0.0

    ema9_1m  = latest(df, "EMA9")
    ema21_1m = latest(df, "EMA21")

    log.debug(
        f"[{symbol}] 1m | open={last_open:.5f} close={last_close:.5f} "
        f"body_ratio={body_ratio:.2f} EMA9={ema9_1m:.5f} EMA21={ema21_1m:.5f}"
    )

    if trend == "BUY":
        bullish_candle  = last_close > last_open
        strong_body     = body_ratio >= MIN_CANDLE_BODY_RATIO
        momentum_up     = last_close > prev_close
        ema_aligned_1m  = ema9_1m > ema21_1m
        result = bullish_candle and strong_body and momentum_up and ema_aligned_1m
        log.debug(
            f"[{symbol}] 1m BUY entry: "
            f"bullish={bullish_candle} strong={strong_body} "
            f"momentum={momentum_up} ema_1m={ema_aligned_1m} → {result}"
        )
        return result

    if trend == "SELL":
        bearish_candle  = last_close < last_open
        strong_body     = body_ratio >= MIN_CANDLE_BODY_RATIO
        momentum_down   = last_close < prev_close
        ema_aligned_1m  = ema9_1m < ema21_1m
        result = bearish_candle and strong_body and momentum_down and ema_aligned_1m
        log.debug(
            f"[{symbol}] 1m SELL entry: "
            f"bearish={bearish_candle} strong={strong_body} "
            f"momentum={momentum_down} ema_1m={ema_aligned_1m} → {result}"
        )
        return result

    return False


# ─── RSI CONFLUENCE FILTER ───────────────────────────────────────────────────

def check_rsi_filter(df_1m: pd.DataFrame, trend: SignalType, symbol: str) -> bool:
    """
    Reject signals when RSI is in an extreme zone that opposes momentum.

    BUY  → reject if RSI > RSI_OVERBOUGHT (already stretched up)
    SELL → reject if RSI < RSI_OVERSOLD   (already stretched down)
    """
    rsi = latest(df_1m, "RSI14")
    log.debug(f"[{symbol}] RSI14={rsi:.1f}")

    if trend == "BUY"  and rsi > RSI_OVERBOUGHT:
        log.debug(f"[{symbol}] RSI filter REJECTED BUY: RSI={rsi:.1f} > {RSI_OVERBOUGHT}")
        return False

    if trend == "SELL" and rsi < RSI_OVERSOLD:
        log.debug(f"[{symbol}] RSI filter REJECTED SELL: RSI={rsi:.1f} < {RSI_OVERSOLD}")
        return False

    return True


# ─── MASTER SIGNAL EVALUATION ────────────────────────────────────────────────

def evaluate_signal(
    df_15m: pd.DataFrame,
    df_5m:  pd.DataFrame,
    df_1m:  pd.DataFrame,
    symbol: str,
) -> Optional[SignalType]:
    """
    Run the full 3-timeframe confluence check.

    Returns 'BUY', 'SELL', or None.
    All three timeframes must agree AND RSI must confirm.
    """
    log.info(f"[{symbol}] ── Evaluating signal ──")

    # Step 1 — 15m Trend Filter
    trend = check_trend_15m(df_15m, symbol)
    if trend is None:
        log.info(f"[{symbol}] ✗ No 15m trend — skipping.")
        return None

    # Step 2 — 5m Setup Filter
    setup = check_setup_5m(df_5m, trend, symbol)
    if not setup:
        log.info(f"[{symbol}] ✗ No 5m setup for {trend} — skipping.")
        return None

    # Step 3 — 1m Entry Trigger
    entry = check_entry_1m(df_1m, trend, symbol)
    if not entry:
        log.info(f"[{symbol}] ✗ No 1m entry confirmation for {trend} — skipping.")
        return None

    # Step 4 — RSI Confluence Filter
    rsi_ok = check_rsi_filter(df_1m, trend, symbol)
    if not rsi_ok:
        log.info(f"[{symbol}] ✗ RSI filter rejected {trend} signal.")
        return None

    log.info(
        f"[{symbol}] ✅ ALL TIMEFRAMES ALIGNED → {trend} signal confirmed!"
    )
    return trend


# ─── SIGNAL METADATA ─────────────────────────────────────────────────────────

def get_signal_metadata(
    df_15m: pd.DataFrame,
    df_5m:  pd.DataFrame,
    df_1m:  pd.DataFrame,
) -> dict:
    """
    Extract key indicator values for use in alert messages.
    """
    return {
        "price":   latest(df_1m, "Close"),
        "ema9":    latest(df_1m, "EMA9"),
        "ema21":   latest(df_1m, "EMA21"),
        "ema50":   latest(df_15m, "EMA50"),
        "ema200":  latest(df_15m, "EMA200"),
        "rsi":     latest(df_1m, "RSI14"),
        "atr":     latest(df_1m, "ATR14"),
        "bb_upper": latest(df_1m, "BB_upper"),
        "bb_lower": latest(df_1m, "BB_lower"),
    }
