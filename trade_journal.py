"""
=============================================================
  TRADE JOURNAL MODULE
  CSV-based trade logging for analysis and record keeping
=============================================================
"""

import csv
import os
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional
from config import TRADE_LOG_FILE
from logger import get_logger

log = get_logger("TradeJournal")

JOURNAL_HEADERS = [
    "trade_id",
    "symbol",
    "broker_symbol",
    "signal",
    "open_time",
    "close_time",
    "entry_price",
    "close_price",
    "sl",
    "tp",
    "sl_pips",
    "tp_pips",
    "lot",
    "result",
    "pnl_pips",
    "rr_ratio",
    "position_id",
    "duration_minutes",
]


@dataclass
class TradeRecord:
    trade_id:       str
    symbol:         str
    broker_symbol:  str
    signal:         str
    open_time:      datetime
    entry_price:    float
    sl:             float
    tp:             float
    sl_pips:        int
    tp_pips:        int
    lot:            float
    rr_ratio:       float
    position_id:    str
    close_time:     Optional[datetime] = None
    close_price:    Optional[float]    = None
    result:         Optional[str]      = None
    pnl_pips:       Optional[float]    = None

    @property
    def duration_minutes(self) -> Optional[float]:
        if self.close_time and self.open_time:
            delta = self.close_time - self.open_time
            return round(delta.total_seconds() / 60, 1)
        return None


class TradeJournal:
    def __init__(self):
        self._ensure_file()

    def _ensure_file(self):
        log_dir = os.path.dirname(TRADE_LOG_FILE)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        if not os.path.exists(TRADE_LOG_FILE):
            with open(TRADE_LOG_FILE, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=JOURNAL_HEADERS)
                writer.writeheader()
            log.info(f"Trade journal created: {TRADE_LOG_FILE}")

    def log_open(self, record: TradeRecord) -> None:
        row = {
            "trade_id":      record.trade_id,
            "symbol":        record.symbol,
            "broker_symbol": record.broker_symbol,
            "signal":        record.signal,
            "open_time":     record.open_time.strftime("%Y-%m-%d %H:%M:%S"),
            "close_time":    "",
            "entry_price":   record.entry_price,
            "close_price":   "",
            "sl":            record.sl,
            "tp":            record.tp,
            "sl_pips":       record.sl_pips,
            "tp_pips":       record.tp_pips,
            "lot":           record.lot,
            "result":        "OPEN",
            "pnl_pips":      "",
            "rr_ratio":      f"1:{record.rr_ratio:.1f}",
            "position_id":   record.position_id,
            "duration_minutes": "",
        }
        self._append_row(row)
        log.debug(f"Journal: opened trade {record.trade_id}")

    def log_close(self, record: TradeRecord) -> None:
        row = {
            "trade_id":      record.trade_id,
            "symbol":        record.symbol,
            "broker_symbol": record.broker_symbol,
            "signal":        record.signal,
            "open_time":     record.open_time.strftime("%Y-%m-%d %H:%M:%S"),
            "close_time":    record.close_time.strftime("%Y-%m-%d %H:%M:%S") if record.close_time else "",
            "entry_price":   record.entry_price,
            "close_price":   record.close_price or "",
            "sl":            record.sl,
            "tp":            record.tp,
            "sl_pips":       record.sl_pips,
            "tp_pips":       record.tp_pips,
            "lot":           record.lot,
            "result":        record.result or "",
            "pnl_pips":      record.pnl_pips or "",
            "rr_ratio":      f"1:{record.rr_ratio:.1f}",
            "position_id":   record.position_id,
            "duration_minutes": record.duration_minutes or "",
        }
        self._append_row(row)
        log.debug(f"Journal: closed trade {record.trade_id} → {record.result} {record.pnl_pips:+.1f}p")

    def _append_row(self, row: dict) -> None:
        try:
            with open(TRADE_LOG_FILE, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=JOURNAL_HEADERS)
                writer.writerow(row)
        except Exception as exc:
            log.error(f"Journal write error: {exc}")
