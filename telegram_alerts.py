"""
=============================================================
  TELEGRAM ALERTS MODULE
  Real-time push notifications for all trading events
=============================================================
  Events covered:
    • Bot startup / shutdown
    • Signal detected (all 3 TFs aligned)
    • Trade executed
    • Trade closed (TP hit / SL hit)
    • Error / reconnect
    • Daily summary
=============================================================
"""

import time
import threading
import requests
from datetime import datetime
from typing import Optional
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, BOT_NAME, BOT_VERSION
from logger import get_logger

log = get_logger("Telegram")

# ─── INTERNAL HELPERS ────────────────────────────────────────────────────────

_SEND_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
_send_lock = threading.Lock()   # Prevent concurrent floods
_RETRY_LIMIT = 3
_RETRY_DELAY = 2.0


def _label(symbol: str) -> str:
    return symbol.replace("=X", "")


def _now_str() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")


def _send_raw(text: str, parse_mode: str = "HTML", retry: int = 0) -> bool:
    """
    Low-level Telegram API call with exponential-backoff retry.
    Thread-safe via lock.
    """
    with _send_lock:
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        try:
            resp = requests.post(_SEND_URL, json=payload, timeout=10)
            if resp.status_code == 200:
                log.debug("Telegram message delivered.")
                return True

            # Telegram rate-limit → retry after Retry-After header
            if resp.status_code == 429 and retry < _RETRY_LIMIT:
                wait = float(resp.json().get("parameters", {}).get("retry_after", _RETRY_DELAY * (retry + 1)))
                log.warning(f"Telegram rate-limited. Retrying after {wait}s…")
                time.sleep(wait)
                return _send_raw(text, parse_mode, retry + 1)

            log.error(f"Telegram HTTP {resp.status_code}: {resp.text[:200]}")
            return False

        except requests.exceptions.Timeout:
            if retry < _RETRY_LIMIT:
                time.sleep(_RETRY_DELAY * (retry + 1))
                return _send_raw(text, parse_mode, retry + 1)
            log.error("Telegram timeout after retries.")
            return False
        except Exception as exc:
            log.error(f"Telegram send error: {exc}")
            return False


def _send_async(text: str) -> None:
    """Fire-and-forget Telegram message on a daemon thread."""
    t = threading.Thread(target=_send_raw, args=(text,), daemon=True)
    t.start()


# ─── PUBLIC ALERT FUNCTIONS ──────────────────────────────────────────────────

def alert_bot_started() -> None:
    msg = (
        f"🤖 <b>{BOT_NAME} v{BOT_VERSION} — STARTED</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 Time: {_now_str()}\n"
        f"📊 Symbols: EURUSD · GBPUSD · USDJPY\n"
        f"⏱ Timeframes: 1m / 5m / 15m\n"
        f"📐 Strategy: Multi-Timeframe EMA\n"
        f"🔁 Loop: Every 60 seconds\n"
        f"✅ Bot is live and monitoring markets."
    )
    _send_async(msg)


def alert_bot_stopped(reason: str = "Manual stop") -> None:
    msg = (
        f"🛑 <b>{BOT_NAME} — STOPPED</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 Time: {_now_str()}\n"
        f"📝 Reason: {reason}"
    )
    _send_raw(msg)   # Synchronous — must arrive before process dies


def alert_signal(symbol: str, signal: str, price: float,
                 ema9: float, ema21: float, ema50: float, ema200: float,
                 rsi: Optional[float] = None) -> None:
    direction = "🟢 BUY" if signal == "BUY" else "🔴 SELL"
    rsi_str = f"\n📊 RSI (14): {rsi:.1f}" if rsi is not None else ""
    msg = (
        f"📊 <b>SIGNAL ALERT</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💱 Pair: <b>{_label(symbol)}</b>\n"
        f"⏱ Timeframe: 1m / 5m / 15m (aligned)\n"
        f"📈 Signal: {direction}\n"
        f"💵 Price: <b>{price:.5f}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📉 EMA  9: {ema9:.5f}\n"
        f"📉 EMA 21: {ema21:.5f}\n"
        f"📉 EMA 50: {ema50:.5f}\n"
        f"📉 EMA200: {ema200:.5f}"
        f"{rsi_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 {_now_str()}"
    )
    _send_async(msg)


def alert_trade_executed(symbol: str, signal: str, entry: float,
                         sl: float, tp: float, lot: float,
                         position_id: str) -> None:
    direction = "🟢 BUY" if signal == "BUY" else "🔴 SELL"
    sl_pips = abs(entry - sl) / (0.01 if "JPY" in symbol else 0.0001)
    tp_pips = abs(tp - entry) / (0.01 if "JPY" in symbol else 0.0001)
    rr = tp_pips / sl_pips if sl_pips > 0 else 0
    msg = (
        f"🚀 <b>TRADE EXECUTED</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💱 Pair: <b>{_label(symbol)}</b>\n"
        f"📈 Type: {direction}\n"
        f"💵 Entry:  <b>{entry:.5f}</b>\n"
        f"🛑 SL:     {sl:.5f}  ({sl_pips:.0f} pips)\n"
        f"🎯 TP:     {tp:.5f}  ({tp_pips:.0f} pips)\n"
        f"⚖️  RR:     1 : {rr:.1f}\n"
        f"📦 Lots:   {lot}\n"
        f"🔑 ID:     <code>{position_id}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 {_now_str()}"
    )
    _send_async(msg)


def alert_trade_closed(symbol: str, result: str, pips: float,
                       entry: float, close_price: float,
                       position_id: str) -> None:
    is_win  = pips > 0
    emoji   = "✅" if is_win else "❌"
    sign    = "+" if pips >= 0 else ""
    color_tag = "🟢" if is_win else "🔴"
    msg = (
        f"{emoji} <b>TRADE RESULT</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💱 Pair:   <b>{_label(symbol)}</b>\n"
        f"📝 Result: {color_tag} <b>{result}</b>\n"
        f"💰 P/L:    <b>{sign}{pips:.1f} pips</b>\n"
        f"📥 Entry:  {entry:.5f}\n"
        f"📤 Close:  {close_price:.5f}\n"
        f"🔑 ID:     <code>{position_id}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 {_now_str()}"
    )
    _send_async(msg)


def alert_error(context: str, error: str) -> None:
    msg = (
        f"⚠️ <b>BOT ERROR</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 Context: {context}\n"
        f"💬 Error:   {str(error)[:300]}\n"
        f"🕐 {_now_str()}"
    )
    _send_async(msg)


def alert_reconnecting(attempt: int, max_attempts: int) -> None:
    msg = (
        f"🔄 <b>RECONNECTING</b> ({attempt}/{max_attempts})\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"MetaAPI connection lost. Attempting reconnect…\n"
        f"🕐 {_now_str()}"
    )
    _send_async(msg)


def alert_daily_summary(
    total_trades: int,
    wins: int,
    losses: int,
    total_pips: float,
    symbols_traded: list,
) -> None:
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0
    sign = "+" if total_pips >= 0 else ""
    result_emoji = "📈" if total_pips >= 0 else "📉"
    msg = (
        f"📋 <b>DAILY SUMMARY</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 Date: {datetime.utcnow().strftime('%Y-%m-%d')}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Total Trades:  {total_trades}\n"
        f"✅ Wins:          {wins}\n"
        f"❌ Losses:        {losses}\n"
        f"🎯 Win Rate:      {win_rate:.1f}%\n"
        f"{result_emoji} Total Pips:    <b>{sign}{total_pips:.1f}</b>\n"
        f"💱 Pairs Traded: {', '.join(symbols_traded) if symbols_traded else 'None'}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 {BOT_NAME} v{BOT_VERSION}"
    )
    _send_async(msg)


def alert_market_closed(symbol: str) -> None:
    msg = (
        f"🌙 <b>MARKET CLOSED</b>\n"
        f"💱 {_label(symbol)} — No data available (weekend or holiday)\n"
        f"🕐 {_now_str()}"
    )
    _send_async(msg)


def alert_no_signal(symbol: str, reason: str) -> None:
    """Optional — only sent for verbose debug mode."""
    pass   # Silent by default; enable for testing


def alert_max_daily_loss() -> None:
    msg = (
        f"🚨 <b>DAILY LOSS LIMIT HIT</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Bot has paused trading for the rest of the day.\n"
        f"Max daily loss threshold reached.\n"
        f"🕐 {_now_str()}"
    )
    _send_raw(msg)
