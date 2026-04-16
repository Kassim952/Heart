"""
=============================================================
  FOREX TRADING BOT — MAIN ENGINE
  Professional Multi-Timeframe EMA Strategy
=============================================================
  Execution flow (every 60 seconds):

  For each symbol (EURUSD.m / GBPUSD.m / USDJPY.m /
                   XAUUSD.m / BTCUSD.m):

    1. Monitor existing open trade (SL/TP check)
    2. Skip if trade already active for this symbol
    3. Fetch 15m / 5m / 1m OHLCV + indicators
    4. Evaluate 3-timeframe confluence signal
    5. Calculate SL / TP (per-instrument pip rules)
    6. Execute via MetaAPI → send Telegram alert
    7. Record in trade journal + update daily P&L

  Safety guards:
    • Max concurrent trades
    • Daily loss limit (auto-pause)
    • Auto-reconnect on MetaAPI failure
    • Rotating log files
=============================================================
"""

import os
import sys
import time
import uuid
from datetime import datetime, timezone

# ── Ensure forex_bot/ is on the Python path ─────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    SYMBOLS,
    LOOP_INTERVAL_SECONDS,
    MAX_CONCURRENT_TRADES,
    MAX_CONSECUTIVE_ERRORS,
    MAX_RECONNECT_ATTEMPTS,
    BOT_NAME,
    BOT_VERSION,
    YAHOO_TO_BASE,
    BROKER_SUFFIX,
)
from logger import log
from data_fetcher import fetch_all_timeframes
from strategy import evaluate_signal, get_signal_metadata
from risk_manager import (
    calculate_trade_params,
    calc_pnl_pips,
    determine_close_reason,
    DailyPnLTracker,
)
from meta_trader import MetaTrader
from trade_journal import TradeJournal, TradeRecord
import telegram_alerts as alerts


# ─── ACTIVE TRADE STATE ──────────────────────────────────────────────────────

def _broker_symbol(yf_symbol: str) -> str:
    base = YAHOO_TO_BASE.get(yf_symbol, yf_symbol.replace("=X", "").replace("-", ""))
    return f"{base}{BROKER_SUFFIX}"


def _display(yf_symbol: str) -> str:
    return YAHOO_TO_BASE.get(yf_symbol, yf_symbol)


# ─── BOT ENGINE ──────────────────────────────────────────────────────────────

class ForexBot:
    """
    Main trading bot orchestrator.

    State:
        active_trades  : dict[yf_symbol → TradeRecord]   currently open trades
        pnl_tracker    : DailyPnLTracker                  daily P&L accounting
        trade_journal  : TradeJournal                     CSV logging
        trader         : MetaTrader                       MT4/5 execution
    """

    def __init__(self):
        self.trader        = MetaTrader()
        self.journal       = TradeJournal()
        self.pnl           = DailyPnLTracker()
        self.active_trades: dict[str, TradeRecord] = {}
        self._consec_errors = 0

    # ── Startup ───────────────────────────────────────────────────────────────

    def _startup_banner(self) -> None:
        symbols_display = " · ".join(_broker_symbol(s) for s in SYMBOLS)
        log.info("=" * 62)
        log.info(f"  {BOT_NAME}  v{BOT_VERSION}")
        log.info("=" * 62)
        log.info(f"  Broker Symbols : {symbols_display}")
        log.info(f"  Strategy       : Multi-Timeframe EMA (1m / 5m / 15m)")
        log.info(f"  Indicators     : EMA 9/21/50/200 · ATR14 · RSI14 · BB")
        log.info(f"  Loop Interval  : {LOOP_INTERVAL_SECONDS}s")
        log.info(f"  Max Trades     : {MAX_CONCURRENT_TRADES}")
        log.info("=" * 62)

    def _connect(self) -> bool:
        for attempt in range(1, MAX_RECONNECT_ATTEMPTS + 1):
            log.info(f"[MetaAPI] Connection attempt {attempt}/{MAX_RECONNECT_ATTEMPTS}…")
            if self.trader.connect():
                return True
            alerts.alert_reconnecting(attempt, MAX_RECONNECT_ATTEMPTS)
            if attempt < MAX_RECONNECT_ATTEMPTS:
                log.warning(f"Retrying in 30 seconds…")
                time.sleep(30)
        return False

    # ── Trade Lifecycle: OPEN ─────────────────────────────────────────────────

    def _open_trade(self, symbol: str, signal: str, meta: dict) -> None:
        """Execute a new trade for the given symbol and signal."""
        if len(self.active_trades) >= MAX_CONCURRENT_TRADES:
            log.warning(
                f"[{_display(symbol)}] Max concurrent trades reached "
                f"({MAX_CONCURRENT_TRADES}). Skipping."
            )
            return

        entry_price = meta["price"]
        params      = calculate_trade_params(symbol, signal, entry_price)

        # ── Send Telegram signal alert ──
        alerts.alert_signal(
            symbol    = symbol,
            signal    = signal,
            price     = entry_price,
            ema9      = meta["ema9"],
            ema21     = meta["ema21"],
            ema50     = meta["ema50"],
            ema200    = meta["ema200"],
            rsi       = meta["rsi"],
        )

        # ── Execute order via MetaAPI ──
        position_id = self.trader.place_order(
            mt_symbol  = params.mt_symbol,
            order_type = signal,
            lots       = params.lot,
            sl         = params.sl,
            tp         = params.tp,
        )

        if not position_id:
            log.error(f"[{_display(symbol)}] Order failed — no position ID returned.")
            alerts.alert_error(f"{_display(symbol)} order placement", "No position ID returned.")
            return

        # ── Build trade record ──
        trade_id = str(uuid.uuid4())[:8].upper()
        record   = TradeRecord(
            trade_id      = trade_id,
            symbol        = symbol,
            broker_symbol = params.mt_symbol,
            signal        = signal,
            open_time     = datetime.now(tz=timezone.utc),
            entry_price   = entry_price,
            sl            = params.sl,
            tp            = params.tp,
            sl_pips       = params.sl_pips,
            tp_pips       = params.tp_pips,
            lot           = params.lot,
            rr_ratio      = params.rr_ratio,
            position_id   = position_id,
        )

        self.active_trades[symbol] = record
        self.journal.log_open(record)

        # ── Send execution alert ──
        alerts.alert_trade_executed(
            symbol      = symbol,
            signal      = signal,
            entry       = entry_price,
            sl          = params.sl,
            tp          = params.tp,
            lot         = params.lot,
            position_id = position_id,
        )

        log.info(
            f"[{_display(symbol)}] ✅ Trade OPENED | "
            f"ID={trade_id} | {signal} @ {entry_price} | "
            f"SL={params.sl} TP={params.tp} | Broker={params.mt_symbol}"
        )

    # ── Trade Lifecycle: MONITOR & CLOSE ──────────────────────────────────────

    def _monitor_trade(self, symbol: str, current_price: float) -> None:
        """Check if an open trade has hit SL or TP."""
        if symbol not in self.active_trades:
            return

        record = self.active_trades[symbol]
        reason = determine_close_reason(
            symbol        = symbol,
            signal        = record.signal,
            entry         = record.entry_price,
            current_price = current_price,
            sl            = record.sl,
            tp            = record.tp,
        )

        if reason is None:
            log.debug(
                f"[{_display(symbol)}] Trade {record.trade_id} still open. "
                f"Price={current_price:.5f} SL={record.sl} TP={record.tp}"
            )
            return

        # ── Close position ──
        pips = calc_pnl_pips(symbol, record.signal, record.entry_price, current_price)
        self.trader.close_position(record.position_id)

        record.close_time  = datetime.now(tz=timezone.utc)
        record.close_price = current_price
        record.result      = reason
        record.pnl_pips    = pips

        self.journal.log_close(record)
        self.pnl.record(symbol, pips)

        alerts.alert_trade_closed(
            symbol      = symbol,
            result      = reason,
            pips        = pips,
            entry       = record.entry_price,
            close_price = current_price,
            position_id = record.position_id,
        )

        log.info(
            f"[{_display(symbol)}] 🔒 Trade CLOSED | "
            f"ID={record.trade_id} | {reason} | "
            f"P&L={pips:+.1f} pips | DayTotal={self.pnl.total_pips:+.1f} pips"
        )

        del self.active_trades[symbol]

    # ── Per-Symbol Processing ─────────────────────────────────────────────────

    def _process_symbol(self, symbol: str) -> None:
        display = _display(symbol)
        log.info(f"[{display}] Processing…")

        # ── Fetch latest 1m price for monitoring ──
        data = fetch_all_timeframes(symbol)
        df_1m  = data.get("1m")
        df_5m  = data.get("5m")
        df_15m = data.get("15m")

        # ── Monitor open trade ──
        if symbol in self.active_trades and df_1m is not None:
            current_price = float(df_1m["Close"].iloc[-1])
            self._monitor_trade(symbol, current_price)

        # ── Skip if trade already open for this symbol ──
        if symbol in self.active_trades:
            log.info(f"[{display}] Active trade open — skipping new signal evaluation.")
            return

        # ── Validate all timeframes ──
        if df_15m is None or df_5m is None or df_1m is None:
            log.warning(f"[{display}] Incomplete data — skipping this cycle.")
            return

        # ── Evaluate signal ──
        signal = evaluate_signal(df_15m, df_5m, df_1m, symbol)
        if signal is None:
            log.info(f"[{display}] No signal this cycle.")
            return

        # ── Build metadata for alerts ──
        meta = get_signal_metadata(df_15m, df_5m, df_1m)

        # ── Open trade ──
        self._open_trade(symbol, signal, meta)

    # ── Daily Guards ─────────────────────────────────────────────────────────

    def _check_daily_guards(self) -> bool:
        """
        Returns False if trading should be paused for the day.
        """
        if self.pnl.is_daily_loss_breached:
            log.critical(
                f"⛔ Daily loss limit hit: {self.pnl.total_pips:.1f} pips. "
                f"Pausing trading for the rest of the day."
            )
            alerts.alert_max_daily_loss()
            return False

        if self.pnl.is_daily_target_hit:
            log.info(
                f"🎯 Daily profit target reached: {self.pnl.total_pips:.1f} pips. "
                f"Bot continues in monitoring mode."
            )

        return True

    # ── Main Loop ─────────────────────────────────────────────────────────────

    def run(self) -> None:
        self._startup_banner()
        alerts.alert_bot_started()

        # ── Connect to MetaAPI ──
        connected = self._connect()
        if connected:
            log.info("MetaAPI connection established.")
        else:
            log.warning(
                "MetaAPI connection could not be established. "
                "Running in simulation mode — orders will be logged but not executed."
            )

        # ── Account info snapshot ──
        try:
            info = self.trader.get_account_info()
            if info:
                log.info(
                    f"Account: Balance={info.get('balance', 'N/A')} "
                    f"Equity={info.get('equity', 'N/A')} "
                    f"Free Margin={info.get('freeMargin', 'N/A')}"
                )
        except Exception:
            pass

        self._consec_errors = 0

        while True:
            try:
                log.info("")
                log.info("─" * 60)
                log.info(f"  TICK | Active Trades: {len(self.active_trades)} | "
                         f"Day P&L: {self.pnl.total_pips:+.1f} pips")
                log.info("─" * 60)

                # ── Daily guard ──
                if not self._check_daily_guards():
                    log.info("Bot paused (daily loss limit). Sleeping 1 hour…")
                    time.sleep(3600)
                    continue

                # ── Process each symbol ──
                for symbol in SYMBOLS:
                    try:
                        self._process_symbol(symbol)
                    except Exception as sym_exc:
                        log.error(
                            f"[{_display(symbol)}] Unexpected error: {sym_exc}",
                            exc_info=True,
                        )
                        alerts.alert_error(f"Processing {_display(symbol)}", str(sym_exc))

                self._consec_errors = 0

                # ── Daily summary (every midnight UTC) ──
                if datetime.now(tz=timezone.utc).hour == 0 and \
                   datetime.now(tz=timezone.utc).minute < 1:
                    summary = self.pnl.summary()
                    alerts.alert_daily_summary(
                        total_trades   = summary["total_trades"],
                        wins           = summary["wins"],
                        losses         = summary["losses"],
                        total_pips     = summary["total_pips"],
                        symbols_traded = summary["symbols_traded"],
                    )

                log.info(f"Sleeping {LOOP_INTERVAL_SECONDS}s until next tick…")
                time.sleep(LOOP_INTERVAL_SECONDS)

            except KeyboardInterrupt:
                log.info("KeyboardInterrupt received — shutting down.")
                alerts.alert_bot_stopped("Manual keyboard interrupt")
                break

            except Exception as loop_exc:
                self._consec_errors += 1
                log.error(
                    f"Main loop error #{self._consec_errors}: {loop_exc}",
                    exc_info=True,
                )
                alerts.alert_error("Main loop", str(loop_exc))

                if self._consec_errors >= MAX_CONSECUTIVE_ERRORS:
                    log.critical(
                        f"Too many consecutive errors ({self._consec_errors}). "
                        f"Attempting MetaAPI reconnect…"
                    )
                    for attempt in range(1, MAX_RECONNECT_ATTEMPTS + 1):
                        alerts.alert_reconnecting(attempt, MAX_RECONNECT_ATTEMPTS)
                        if self.trader.reconnect(attempt):
                            log.info("Reconnect successful.")
                            self._consec_errors = 0
                            break
                        time.sleep(30)
                    else:
                        log.critical(
                            "All reconnect attempts failed. "
                            "Sleeping 10 minutes before retry…"
                        )
                        time.sleep(600)

                time.sleep(LOOP_INTERVAL_SECONDS)


# ─── ENTRYPOINT ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    bot = ForexBot()
    bot.run()
