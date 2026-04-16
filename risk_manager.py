"""
=============================================================
  RISK MANAGER MODULE
  Stop Loss / Take Profit / Pip Calculation
=============================================================
  Handles:
    • Pip value per instrument (Forex / JPY / Gold / Crypto)
    • Fixed SL & TP calculation in price terms
    • Pip profit/loss calculation on trade close
    • Daily PnL tracking
    • Max daily loss guard
=============================================================
"""

from datetime import date
from dataclasses import dataclass, field
from typing import Optional
from config import (
    PIP_VALUE,
    SL_PIPS,
    TP_PIPS,
    LOT_SIZES,
    MAX_DAILY_LOSS_PIPS,
    DAILY_PROFIT_TARGET_PIPS,
    SLIPPAGE_PIPS,
    YAHOO_TO_BASE,
    BROKER_SUFFIX,
)
from logger import get_logger

log = get_logger("RiskManager")


# ─── PIP UTILITIES ───────────────────────────────────────────────────────────

def pip_size(symbol: str) -> float:
    """Return the pip size (price movement per pip) for a given symbol."""
    return PIP_VALUE.get(symbol, 0.0001)


def price_to_pips(symbol: str, price_diff: float) -> float:
    """Convert an absolute price difference into pips."""
    pip = pip_size(symbol)
    return round(price_diff / pip, 2)


def pips_to_price(symbol: str, pips: float) -> float:
    """Convert pips into an absolute price movement."""
    pip = pip_size(symbol)
    return round(pips * pip, 6)


def lot_size(symbol: str) -> float:
    """Return the configured lot size for this instrument."""
    return LOT_SIZES.get(symbol, 0.01)


# ─── SL / TP CALCULATION ─────────────────────────────────────────────────────

@dataclass
class TradeParams:
    symbol:      str
    signal:      str           # "BUY" or "SELL"
    entry:       float
    sl:          float
    tp:          float
    sl_pips:     int
    tp_pips:     int
    lot:         float
    mt_symbol:   str           # Broker symbol name (e.g. "EURUSD.m")
    rr_ratio:    float         # Risk : Reward


def calculate_trade_params(symbol: str, signal: str, entry_price: float) -> TradeParams:
    """
    Compute Stop Loss and Take Profit prices from fixed pip distances.

    For BUY:
        SL = entry - sl_distance
        TP = entry + tp_distance

    For SELL:
        SL = entry + sl_distance
        TP = entry - tp_distance

    Automatically handles:
        • Forex (non-JPY) : pip = 0.0001
        • JPY pairs       : pip = 0.01
        • Gold (XAUUSD)   : pip = 0.10
        • Bitcoin         : pip = 1.00
    """
    sl_p = SL_PIPS.get(symbol, 5)
    tp_p = TP_PIPS.get(symbol, 10)
    pip  = pip_size(symbol)

    sl_distance = sl_p * pip
    tp_distance = tp_p * pip

    if signal == "BUY":
        sl = entry_price - sl_distance
        tp = entry_price + tp_distance
    else:   # SELL
        sl = entry_price + sl_distance
        tp = entry_price - tp_distance

    # Determine decimal places for rounding
    if pip >= 1.0:
        decimals = 2
    elif pip >= 0.01:
        decimals = 2
    else:
        decimals = 5

    sl = round(sl, decimals)
    tp = round(tp, decimals)
    rr = tp_p / sl_p if sl_p > 0 else 0.0

    base     = YAHOO_TO_BASE.get(symbol, symbol.replace("=X", ""))
    mt_sym   = f"{base}{BROKER_SUFFIX}"

    params = TradeParams(
        symbol    = symbol,
        signal    = signal,
        entry     = entry_price,
        sl        = sl,
        tp        = tp,
        sl_pips   = sl_p,
        tp_pips   = tp_p,
        lot       = lot_size(symbol),
        mt_symbol = mt_sym,
        rr_ratio  = rr,
    )

    log.info(
        f"[{symbol}] TradeParams: {signal} "
        f"entry={entry_price} SL={sl} ({sl_p}p) TP={tp} ({tp_p}p) "
        f"RR=1:{rr:.1f} lot={params.lot} broker_symbol={mt_sym}"
    )
    return params


# ─── TRADE RESULT CALCULATION ────────────────────────────────────────────────

def calc_pnl_pips(symbol: str, signal: str, entry: float, close: float) -> float:
    """
    Return profit/loss in pips for a closed trade.
    Positive = profit, negative = loss.
    """
    if signal == "BUY":
        raw = close - entry
    else:
        raw = entry - close
    return round(raw / pip_size(symbol), 1)


def determine_close_reason(
    symbol: str,
    signal: str,
    entry:  float,
    current_price: float,
    sl:     float,
    tp:     float,
) -> Optional[str]:
    """
    Determine whether the current price has triggered SL or TP.
    Returns 'TP HIT', 'SL HIT', or None.

    Uses a slippage buffer to avoid false triggers on noisy 1m data.
    """
    pip     = pip_size(symbol)
    slip    = SLIPPAGE_PIPS * pip

    if signal == "BUY":
        if current_price >= tp - slip:
            return "TP HIT"
        if current_price <= sl + slip:
            return "SL HIT"
    else:  # SELL
        if current_price <= tp + slip:
            return "TP HIT"
        if current_price >= sl - slip:
            return "SL HIT"

    return None


# ─── DAILY PnL TRACKER ───────────────────────────────────────────────────────

class DailyPnLTracker:
    """
    Tracks intra-day profit/loss in pips across all symbols.

    Resets automatically at the start of each new trading day (UTC).
    """

    def __init__(self) -> None:
        self._date:       date  = date.today()
        self._total_pips: float = 0.0
        self._trades:     int   = 0
        self._wins:       int   = 0
        self._losses:     int   = 0
        self._symbols_traded: list = []

    def _roll_if_new_day(self) -> None:
        today = date.today()
        if today != self._date:
            log.info(
                f"[DailyPnL] New trading day. Previous: {self._date} | "
                f"Total pips={self._total_pips:+.1f} | "
                f"Trades={self._trades} W={self._wins} L={self._losses}"
            )
            self._date         = today
            self._total_pips   = 0.0
            self._trades       = 0
            self._wins         = 0
            self._losses       = 0
            self._symbols_traded = []

    def record(self, symbol: str, pips: float) -> None:
        self._roll_if_new_day()
        self._total_pips += pips
        self._trades     += 1
        if pips > 0:
            self._wins += 1
        else:
            self._losses += 1
        label = symbol.replace("=X", "").replace("-", "")
        if label not in self._symbols_traded:
            self._symbols_traded.append(label)
        log.info(
            f"[DailyPnL] Recorded {pips:+.1f} pips for {label}. "
            f"Day total: {self._total_pips:+.1f} pips over {self._trades} trades."
        )

    @property
    def total_pips(self) -> float:
        self._roll_if_new_day()
        return self._total_pips

    @property
    def is_daily_loss_breached(self) -> bool:
        self._roll_if_new_day()
        breached = self._total_pips <= MAX_DAILY_LOSS_PIPS
        if breached:
            log.warning(
                f"[DailyPnL] ⚠ Max daily loss reached: "
                f"{self._total_pips:.1f} pips (limit: {MAX_DAILY_LOSS_PIPS})"
            )
        return breached

    @property
    def is_daily_target_hit(self) -> bool:
        self._roll_if_new_day()
        return self._total_pips >= DAILY_PROFIT_TARGET_PIPS

    def summary(self) -> dict:
        self._roll_if_new_day()
        return {
            "date":           str(self._date),
            "total_pips":     self._total_pips,
            "total_trades":   self._trades,
            "wins":           self._wins,
            "losses":         self._losses,
            "symbols_traded": list(self._symbols_traded),
        }
