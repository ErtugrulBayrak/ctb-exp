"""
order_ledger.py - Order Idempotency Ledger
==========================================

Ensures the same signal_id cannot produce duplicate orders.

Usage:
    from order_ledger import OrderLedger
    
    ledger = OrderLedger()
    
    if ledger.is_blocked(signal_id):
        logger.info("blocked_by_order_ledger=True")
        return
    
    # Execute order...
    ledger.record(signal_id, "filled", order_id, qty, price)
"""

import os
import time
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass, asdict
from enum import Enum

# Logger import
try:
    from trade_logger import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

# Config import
try:
    from config import SETTINGS
except ImportError:
    class MockSettings:
        ORDER_LEDGER_ENABLED = True
        ALLOW_RETRY_SAME_SIGNAL = False
    SETTINGS = MockSettings()


class OrderStatus(str, Enum):
    """Order status enumeration."""
    SUBMITTED = "submitted"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"


@dataclass
class LedgerEntry:
    """Single order ledger entry."""
    symbol: str
    side: str
    status: str
    created_ts: int
    order_ids: list
    filled_qty: float
    avg_price: float
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class OrderLedger:
    """
    Order Idempotency Ledger.
    
    Prevents duplicate orders for the same signal_id.
    
    File: data/order_ledger.json
    """
    
    def __init__(self, filepath: str = "data/order_ledger.json", enabled: bool = None):
        """
        Initialize OrderLedger.
        
        Args:
            filepath: Ledger file path
            enabled: Override ORDER_LEDGER_ENABLED config
        """
        self.filepath = filepath
        self.enabled = enabled if enabled is not None else getattr(SETTINGS, 'ORDER_LEDGER_ENABLED', True)
        self._cache: Dict[str, Dict] = {}
        
        if self.enabled:
            self._load()
    
    def _load(self) -> None:
        """Load ledger from file."""
        try:
            from utils.io import read_json_safe
            self._cache = read_json_safe(self.filepath, default={})
        except ImportError:
            # Fallback if utils.io not available
            import json
            if os.path.exists(self.filepath):
                try:
                    with open(self.filepath, 'r', encoding='utf-8') as f:
                        self._cache = json.load(f)
                except:
                    self._cache = {}
            else:
                self._cache = {}
    
    def _save(self) -> bool:
        """Save ledger to file atomically."""
        try:
            from utils.io import write_atomic_json
            return write_atomic_json(self.filepath, self._cache)
        except ImportError:
            # Fallback
            import json
            try:
                os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
                with open(self.filepath, 'w', encoding='utf-8') as f:
                    json.dump(self._cache, f, indent=2)
                return True
            except Exception as e:
                logger.error(f"[ORDER_LEDGER] Save failed: {e}")
                return False
    
    def is_blocked(self, signal_id: str) -> Tuple[bool, str]:
        """
        Check if order should be blocked for this signal_id.
        
        Args:
            signal_id: Unique signal identifier
        
        Returns:
            (blocked: bool, reason: str)
        """
        if not self.enabled:
            return False, "ledger_disabled"
        
        if not signal_id:
            return False, "empty_signal_id"
        
        if signal_id not in self._cache:
            return False, "new_signal"
        
        entry = self._cache[signal_id]
        status = entry.get("status", "")
        
        # Block if submitted or filled
        if status in (OrderStatus.SUBMITTED.value, OrderStatus.FILLED.value):
            return True, f"duplicate_signal_status_{status}"
        
        # Check retry policy for canceled/rejected
        if status in (OrderStatus.CANCELED.value, OrderStatus.REJECTED.value):
            allow_retry = getattr(SETTINGS, 'ALLOW_RETRY_SAME_SIGNAL', False)
            if not allow_retry:
                return True, f"retry_blocked_status_{status}"
            else:
                return False, f"retry_allowed_status_{status}"
        
        return False, "unknown_status"
    
    def record(
        self,
        signal_id: str,
        symbol: str,
        side: str,
        status: str,
        order_ids: list = None,
        filled_qty: float = 0.0,
        avg_price: float = 0.0
    ) -> bool:
        """
        Record order in ledger.
        
        Args:
            signal_id: Unique signal identifier
            symbol: Trading pair
            side: "BUY" or "SELL"
            status: Order status
            order_ids: List of exchange order IDs
            filled_qty: Filled quantity
            avg_price: Average fill price
        
        Returns:
            Success
        """
        if not self.enabled:
            return True
        
        self._cache[signal_id] = {
            "symbol": symbol,
            "side": side,
            "status": status,
            "created_ts": int(time.time()),
            "order_ids": order_ids or [],
            "filled_qty": filled_qty,
            "avg_price": avg_price
        }
        
        saved = self._save()
        
        if saved:
            logger.debug(f"[ORDER_LEDGER] Recorded: {signal_id} | {symbol} {side} | status={status}")
        
        return saved
    
    def update_status(self, signal_id: str, status: str, filled_qty: float = None, avg_price: float = None) -> bool:
        """Update existing entry status."""
        if not self.enabled or signal_id not in self._cache:
            return False
        
        self._cache[signal_id]["status"] = status
        
        if filled_qty is not None:
            self._cache[signal_id]["filled_qty"] = filled_qty
        if avg_price is not None:
            self._cache[signal_id]["avg_price"] = avg_price
        
        return self._save()
    
    def get_entry(self, signal_id: str) -> Optional[Dict]:
        """Get ledger entry for signal_id."""
        return self._cache.get(signal_id)
    
    def cleanup_old(self, max_age_days: int = 30) -> int:
        """Remove entries older than max_age_days."""
        if not self.enabled:
            return 0
        
        now = int(time.time())
        max_age_secs = max_age_days * 24 * 3600
        
        to_remove = []
        for signal_id, entry in self._cache.items():
            created_ts = entry.get("created_ts", 0)
            if now - created_ts > max_age_secs:
                to_remove.append(signal_id)
        
        for signal_id in to_remove:
            del self._cache[signal_id]
        
        if to_remove:
            self._save()
            logger.info(f"[ORDER_LEDGER] Cleaned up {len(to_remove)} old entries")
        
        return len(to_remove)


# ═══════════════════════════════════════════════════════════════════════════════
# GLOBAL INSTANCE
# ═══════════════════════════════════════════════════════════════════════════════
_ledger: Optional[OrderLedger] = None


def get_ledger() -> OrderLedger:
    """Get or create global ledger instance."""
    global _ledger
    if _ledger is None:
        _ledger = OrderLedger()
    return _ledger


# ═══════════════════════════════════════════════════════════════════════════════
# TEST
# ═══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import tempfile
    import shutil
    
    print("=" * 50)
    print("ORDER LEDGER TEST")
    print("=" * 50)
    
    # Test directory
    test_dir = tempfile.mkdtemp()
    test_file = os.path.join(test_dir, "ledger.json")
    
    try:
        # Test 1: New signal not blocked
        print("\n[TEST 1] New Signal")
        ledger = OrderLedger(filepath=test_file)
        blocked, reason = ledger.is_blocked("BTCUSDT_15m_1234567890")
        print(f"  blocked={blocked}, reason={reason}")
        assert blocked == False
        print("  PASS")
        
        # Test 2: Record and block
        print("\n[TEST 2] Record and Block")
        ledger.record(
            signal_id="BTCUSDT_15m_1234567890",
            symbol="BTCUSDT",
            side="BUY",
            status="filled",
            filled_qty=0.01,
            avg_price=42000.0
        )
        blocked, reason = ledger.is_blocked("BTCUSDT_15m_1234567890")
        print(f"  blocked={blocked}, reason={reason}")
        assert blocked == True
        assert "duplicate" in reason
        print("  PASS")
        
        # Test 3: Persistence
        print("\n[TEST 3] Persistence")
        ledger2 = OrderLedger(filepath=test_file)
        blocked, reason = ledger2.is_blocked("BTCUSDT_15m_1234567890")
        print(f"  Reloaded: blocked={blocked}")
        assert blocked == True
        print("  PASS")
        
        # Test 4: Canceled signal retry
        print("\n[TEST 4] Canceled Signal")
        ledger.record(
            signal_id="BTCUSDT_15m_9999999999",
            symbol="BTCUSDT",
            side="BUY",
            status="canceled"
        )
        blocked, reason = ledger.is_blocked("BTCUSDT_15m_9999999999")
        print(f"  ALLOW_RETRY=False: blocked={blocked}, reason={reason}")
        # Default: retry not allowed
        assert blocked == True
        print("  PASS")
        
        print("\n" + "=" * 50)
        print("ALL TESTS PASSED")
        print("=" * 50)
        
    finally:
        shutil.rmtree(test_dir)
