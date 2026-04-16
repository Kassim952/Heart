"""
=============================================================
  METATRADER EXECUTION MODULE (MetaAPI Cloud)
=============================================================
  Handles:
    • Async MetaAPI connection with auto-reconnect
    • Market order placement (BUY / SELL)
    • Position close
    • Real-time price retrieval
    • Simulation mode when SDK unavailable
=============================================================
"""

import asyncio
import time
import threading
from typing import Optional
from config import (
    META_API_TOKEN,
    META_ACCOUNT_ID,
    META_DEPLOY_TIMEOUT,
    META_SYNC_TIMEOUT,
    META_ORDER_TIMEOUT,
    RECONNECT_WAIT_SECONDS,
    MAX_RECONNECT_ATTEMPTS,
)
from logger import get_logger

log = get_logger("MetaTrader")


# ─── SDK IMPORT GUARD ─────────────────────────────────────────────────────────

try:
    from metaapi_cloud_sdk import MetaApi
    METAAPI_AVAILABLE = True
    log.info("metaapi-cloud-sdk loaded successfully.")
except ImportError:
    METAAPI_AVAILABLE = False
    log.warning(
        "metaapi-cloud-sdk not installed — running in SIMULATION mode. "
        "Install with: pip install metaapi-cloud-sdk"
    )


# ─── ASYNC RUNNER ─────────────────────────────────────────────────────────────

class _AsyncRunner:
    """
    Manages a dedicated event loop on a background thread.
    Allows calling async MetaAPI methods from synchronous bot code.
    """

    def __init__(self):
        self._loop   = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="MetaAPI-Loop"
        )
        self._thread.start()

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def run(self, coro, timeout: int = META_ORDER_TIMEOUT):
        """Submit a coroutine to the background loop and block until done."""
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=timeout)
        except asyncio.TimeoutError:
            log.error(f"MetaAPI coroutine timed out after {timeout}s.")
            return None
        except Exception as exc:
            log.error(f"MetaAPI coroutine error: {exc}")
            return None


# ─── MAIN EXECUTOR CLASS ──────────────────────────────────────────────────────

class MetaTrader:
    """
    High-level interface to MetaAPI Cloud.
    All public methods are synchronous (blocking).
    """

    def __init__(self):
        self._runner:      _AsyncRunner = _AsyncRunner()
        self._api                       = None
        self._account                   = None
        self._connection                = None
        self._connected:   bool         = False
        self._sim_counter: int          = 1000   # Simulation position ID counter

    # ── Connection ────────────────────────────────────────────────────────────

    async def _connect_async(self) -> bool:
        if not METAAPI_AVAILABLE:
            log.info("[SIM] MetaAPI SDK not available — simulation mode active.")
            self._connected = True
            return True

        try:
            self._api = MetaApi(META_API_TOKEN)
            log.info(f"Fetching MetaTrader account {META_ACCOUNT_ID}…")
            self._account = await self._api.metatrader_account_api.get_account(META_ACCOUNT_ID)

            if self._account.state not in ("DEPLOYING", "DEPLOYED"):
                log.info("Deploying account — this may take up to 2 minutes…")
                await self._account.deploy()

            log.info("Waiting for account to reach DEPLOYED state…")
            await self._account.wait_connected(timeout_in_seconds=META_DEPLOY_TIMEOUT)

            self._connection = self._account.get_rpc_connection()
            await self._connection.connect()

            log.info("Waiting for connection to synchronize…")
            await self._connection.wait_synchronized(timeout_in_seconds=META_SYNC_TIMEOUT)

            self._connected = True
            log.info("✅ MetaAPI connected and synchronized.")
            return True

        except Exception as exc:
            log.error(f"MetaAPI connection failed: {exc}")
            self._connected = False
            return False

    def connect(self) -> bool:
        return self._runner.run(self._connect_async(), timeout=META_DEPLOY_TIMEOUT + 30)

    def reconnect(self, attempt: int = 1) -> bool:
        log.warning(f"[Reconnect] Attempt {attempt}/{MAX_RECONNECT_ATTEMPTS}…")
        self._connected = False
        self._connection = None
        time.sleep(RECONNECT_WAIT_SECONDS)
        return self.connect()

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ── Live Price ─────────────────────────────────────────────────────────────

    async def _get_price_async(self, mt_symbol: str) -> Optional[float]:
        if not METAAPI_AVAILABLE or not self._connection:
            return None
        try:
            data = await self._connection.get_symbol_price(mt_symbol)
            if data:
                return float(data.get("ask", data.get("bid", 0)))
            return None
        except Exception as exc:
            log.error(f"[{mt_symbol}] get_price error: {exc}")
            return None

    def get_current_price(self, mt_symbol: str) -> Optional[float]:
        return self._runner.run(self._get_price_async(mt_symbol))

    # ── Order Placement ───────────────────────────────────────────────────────

    async def _place_order_async(
        self,
        mt_symbol: str,
        order_type: str,
        lots:       float,
        sl:         float,
        tp:         float,
    ) -> Optional[str]:
        if not METAAPI_AVAILABLE or not self._connection:
            sim_id = f"SIM-{mt_symbol}-{self._sim_counter}"
            self._sim_counter += 1
            log.info(
                f"[SIM] {order_type} {lots:.3f} lots {mt_symbol} "
                f"SL={sl} TP={tp} → ID={sim_id}"
            )
            return sim_id

        options = {"comment": "ForexBotPro", "clientId": "forex_bot_v2"}
        try:
            if order_type == "BUY":
                result = await self._connection.create_market_buy_order(
                    mt_symbol, lots, sl, tp, options=options
                )
            else:
                result = await self._connection.create_market_sell_order(
                    mt_symbol, lots, sl, tp, options=options
                )

            position_id = str(
                result.get("positionId") or result.get("orderId") or "UNKNOWN"
            )
            log.info(
                f"✅ Order placed: {order_type} {lots} lots {mt_symbol} "
                f"SL={sl} TP={tp} | Position ID: {position_id}"
            )
            return position_id

        except Exception as exc:
            log.error(f"[{mt_symbol}] Order placement failed: {exc}")
            return None

    def place_order(
        self,
        mt_symbol:  str,
        order_type: str,
        lots:       float,
        sl:         float,
        tp:         float,
    ) -> Optional[str]:
        return self._runner.run(
            self._place_order_async(mt_symbol, order_type, lots, sl, tp)
        )

    # ── Position Management ───────────────────────────────────────────────────

    async def _close_position_async(self, position_id: str) -> bool:
        if not METAAPI_AVAILABLE or not self._connection:
            log.info(f"[SIM] Close position {position_id}")
            return True
        try:
            await self._connection.close_position(position_id)
            log.info(f"Position {position_id} closed.")
            return True
        except Exception as exc:
            log.error(f"Failed to close position {position_id}: {exc}")
            return False

    def close_position(self, position_id: str) -> bool:
        return self._runner.run(self._close_position_async(position_id)) or False

    async def _get_positions_async(self) -> list:
        if not METAAPI_AVAILABLE or not self._connection:
            return []
        try:
            return (await self._connection.get_positions()) or []
        except Exception as exc:
            log.error(f"get_positions error: {exc}")
            return []

    def get_positions(self) -> list:
        return self._runner.run(self._get_positions_async()) or []

    async def _get_account_info_async(self) -> dict:
        if not METAAPI_AVAILABLE or not self._connection:
            return {"balance": 10000.0, "equity": 10000.0, "margin": 0.0, "freeMargin": 10000.0}
        try:
            return (await self._connection.get_account_information()) or {}
        except Exception as exc:
            log.error(f"get_account_information error: {exc}")
            return {}

    def get_account_info(self) -> dict:
        return self._runner.run(self._get_account_info_async()) or {}
