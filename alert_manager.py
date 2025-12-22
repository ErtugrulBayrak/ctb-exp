"""
alert_manager.py - Critical Event Alert System
===============================================

Kritik olaylarda operatöre anında uyarı üretir.
Log'a yazar, opsiyonel Telegram gönderir.

Usage:
    from alert_manager import get_alert_manager, AlertLevel
    
    am = get_alert_manager()
    am.emit("DAILY_LOSS_LIMIT_HIT", AlertLevel.CRITICAL, 
            "Daily loss limit reached", pnl=-30.0, limit=-30.0)
"""

import os
import time
from datetime import datetime
from enum import IntEnum
from typing import Dict, Any, Optional, Callable

# Logger import
try:
    from trade_logger import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)

# Config import
try:
    from config import SETTINGS
except ImportError:
    class MockSettings:
        ALERTS_ENABLED = True
        ALERT_SEND_TELEGRAM = False
        ALERT_TELEGRAM_CHAT_ID = None
        ALERT_THROTTLE_MINUTES = 30
        ALERT_PERSIST_STATE = True
        ALERT_LEVEL_MIN = "INFO"
    SETTINGS = MockSettings()

# Atomic IO import
try:
    from utils.io import write_atomic_json, read_json_safe
except ImportError:
    import json
    def write_atomic_json(path, data, indent=2):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=indent)
        return True
    def read_json_safe(path, default=None, schema_keys=None):
        if not os.path.exists(path):
            return default
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return default


class AlertLevel(IntEnum):
    """Alert severity levels."""
    INFO = 1
    WARN = 2
    CRITICAL = 3
    
    @classmethod
    def from_string(cls, s: str) -> "AlertLevel":
        mapping = {"INFO": cls.INFO, "WARN": cls.WARN, "CRITICAL": cls.CRITICAL}
        return mapping.get(s.upper(), cls.INFO)
    
    def __str__(self):
        return self.name


# ═══════════════════════════════════════════════════════════════════════════════
# ALERT CODES
# ═══════════════════════════════════════════════════════════════════════════════
class AlertCode:
    """Predefined alert codes."""
    # Risk / Trading
    DAILY_LOSS_LIMIT_HIT = "DAILY_LOSS_LIMIT_HIT"
    CONSECUTIVE_STOPS_LIMIT_HIT = "CONSECUTIVE_STOPS_LIMIT_HIT"
    RISK_KILL_SWITCH = "RISK_KILL_SWITCH"
    MAX_OPEN_POSITIONS_REACHED = "MAX_OPEN_POSITIONS_REACHED"
    
    # Execution
    ORDER_REJECTED = "ORDER_REJECTED"
    ORDER_TIMEOUT_FALLBACK = "ORDER_TIMEOUT_FALLBACK"
    PARTIAL_FILL_STUCK = "PARTIAL_FILL_STUCK"
    
    # LLM / News Veto
    LLM_RATE_LIMITED = "LLM_RATE_LIMITED"
    NEWS_VETO_TRUE = "NEWS_VETO_TRUE"
    
    # I/O and State
    PORTFOLIO_IO_ERROR = "PORTFOLIO_IO_ERROR"
    LEDGER_IO_ERROR = "LEDGER_IO_ERROR"
    METRICS_IO_ERROR = "METRICS_IO_ERROR"
    SUMMARY_STATE_IO_ERROR = "SUMMARY_STATE_IO_ERROR"


class AlertManager:
    """
    Critical Event Alert System.
    
    Features:
    - Log always
    - Telegram optional
    - Throttling to prevent spam
    - State persistence for restart
    """
    
    STATE_FILE = "data/alert_state.json"
    
    def __init__(self):
        """Initialize AlertManager."""
        self.enabled = getattr(SETTINGS, 'ALERTS_ENABLED', True)
        self.send_telegram = getattr(SETTINGS, 'ALERT_SEND_TELEGRAM', False)
        self.throttle_minutes = getattr(SETTINGS, 'ALERT_THROTTLE_MINUTES', 30)
        self.persist_state = getattr(SETTINGS, 'ALERT_PERSIST_STATE', True)
        self.min_level = AlertLevel.from_string(
            getattr(SETTINGS, 'ALERT_LEVEL_MIN', "INFO")
        )
        
        # Load persisted throttle state
        self._throttle_state: Dict[str, float] = self._load_state()
        
        # Telegram config (set via set_telegram_config)
        self._telegram_fn: Optional[Callable] = None
        self._telegram_config: Dict = {}
    
    def set_telegram_config(self, telegram_fn: Callable, telegram_config: Dict):
        """Set Telegram function and config for sending alerts."""
        self._telegram_fn = telegram_fn
        self._telegram_config = telegram_config
    
    def _load_state(self) -> Dict[str, float]:
        """Load throttle state from file."""
        if not self.persist_state:
            return {}
        data = read_json_safe(self.STATE_FILE, default={"throttle": {}})
        return data.get("throttle", {})
    
    def _save_state(self) -> bool:
        """Save throttle state atomically."""
        if not self.persist_state:
            return True
        os.makedirs(os.path.dirname(self.STATE_FILE), exist_ok=True)
        return write_atomic_json(self.STATE_FILE, {"throttle": self._throttle_state})
    
    def _is_throttled(self, code: str) -> bool:
        """Check if alert is throttled."""
        if code not in self._throttle_state:
            return False
        
        last_ts = self._throttle_state[code]
        throttle_secs = self.throttle_minutes * 60
        
        return (time.time() - last_ts) < throttle_secs
    
    def _record_throttle(self, code: str):
        """Record alert timestamp for throttling."""
        self._throttle_state[code] = time.time()
        self._save_state()
    
    def emit(
        self,
        code: str,
        level: AlertLevel,
        message: str,
        force: bool = False,
        **context
    ) -> bool:
        """
        Emit an alert.
        
        Args:
            code: Alert code (e.g. AlertCode.DAILY_LOSS_LIMIT_HIT)
            level: AlertLevel (INFO, WARN, CRITICAL)
            message: Human-readable message
            force: Bypass throttle (for CRITICAL repeated alerts)
            **context: Key-value context data
        
        Returns:
            True if alert was sent, False if throttled/disabled
        """
        if not self.enabled:
            return False
        
        # Level filter
        if level < self.min_level:
            return False
        
        # Throttle check (unless force or CRITICAL)
        if not force and level != AlertLevel.CRITICAL:
            if self._is_throttled(code):
                logger.debug(f"[ALERT] Throttled: {code}")
                return False
        
        # Build alert
        now = datetime.now()
        timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
        
        context_str = " ".join(f"{k}={v}" for k, v in context.items()) if context else ""
        
        alert_text = (
            f"[ALERT] {level} {code} {timestamp}\n"
            f"message={message}\n"
            f"context={context_str}" if context_str else f"[ALERT] {level} {code} {timestamp}\nmessage={message}"
        )
        
        # Log always
        if level == AlertLevel.CRITICAL:
            logger.error(alert_text)
        elif level == AlertLevel.WARN:
            logger.warning(alert_text)
        else:
            logger.info(alert_text)
        
        # Telegram if enabled
        if self.send_telegram and self._telegram_fn:
            self._send_telegram_sync(alert_text, level)
        
        # Record throttle
        self._record_throttle(code)
        
        return True
    
    async def emit_async(
        self,
        code: str,
        level: AlertLevel,
        message: str,
        force: bool = False,
        **context
    ) -> bool:
        """Async version of emit for use in async contexts."""
        if not self.enabled:
            return False
        
        if level < self.min_level:
            return False
        
        if not force and level != AlertLevel.CRITICAL:
            if self._is_throttled(code):
                return False
        
        now = datetime.now()
        timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
        context_str = " ".join(f"{k}={v}" for k, v in context.items()) if context else ""
        
        alert_text = (
            f"[ALERT] {level} {code} {timestamp}\n"
            f"message={message}\n"
            f"context={context_str}" if context_str else f"[ALERT] {level} {code} {timestamp}\nmessage={message}"
        )
        
        if level == AlertLevel.CRITICAL:
            logger.error(alert_text)
        elif level == AlertLevel.WARN:
            logger.warning(alert_text)
        else:
            logger.info(alert_text)
        
        if self.send_telegram and self._telegram_fn:
            await self._send_telegram_async(alert_text, level)
        
        self._record_throttle(code)
        return True
    
    def _send_telegram_sync(self, message: str, level: AlertLevel):
        """Attempt sync telegram send (may not work in async context)."""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Can't await in sync context when loop is running
                logger.debug("[ALERT] Telegram skipped (async context)")
            else:
                loop.run_until_complete(self._send_telegram_async(message, level))
        except:
            pass
    
    async def _send_telegram_async(self, message: str, level: AlertLevel):
        """Send alert via Telegram."""
        if not self._telegram_fn or not self._telegram_config:
            return
        
        bot_token = self._telegram_config.get("bot_token", "")
        chat_id = getattr(SETTINGS, 'ALERT_TELEGRAM_CHAT_ID', None)
        if not chat_id:
            chat_id = self._telegram_config.get("chat_id", "")
        
        if not bot_token or not chat_id:
            return
        
        try:
            emoji = "🚨" if level == AlertLevel.CRITICAL else "⚠️" if level == AlertLevel.WARN else "ℹ️"
            tg_message = f"{emoji} <pre>{message}</pre>"
            await self._telegram_fn(bot_token, chat_id, tg_message)
        except Exception as e:
            logger.debug(f"[ALERT] Telegram send failed: {e}")
    
    async def poll(self, portfolio: Dict = None, metrics: Dict = None):
        """
        Poll for threshold-based alerts.
        
        Call at end of each loop cycle.
        Checks metrics for limit violations.
        """
        if not self.enabled:
            return
        
        # Import metrics if not passed
        if metrics is None:
            try:
                from metrics import get_metrics
                metrics = get_metrics()
            except:
                metrics = {}
        
        # Check for rate limit alerts
        if metrics.get("llm_rate_limited_count", 0) > 0:
            # Only alert if this is a new rate limit event
            # (tracked by checking if counter increased)
            pass  # Integration handled in news_veto.py directly


# ═══════════════════════════════════════════════════════════════════════════════
# GLOBAL INSTANCE
# ═══════════════════════════════════════════════════════════════════════════════
_alert_manager: Optional[AlertManager] = None


def get_alert_manager() -> AlertManager:
    """Get or create global AlertManager instance."""
    global _alert_manager
    if _alert_manager is None:
        _alert_manager = AlertManager()
    return _alert_manager


# ═══════════════════════════════════════════════════════════════════════════════
# CONVENIENCE FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def emit_alert(code: str, level: AlertLevel, message: str, **context) -> bool:
    """Convenience function to emit alert."""
    return get_alert_manager().emit(code, level, message, **context)


async def emit_alert_async(code: str, level: AlertLevel, message: str, **context) -> bool:
    """Async convenience function."""
    return await get_alert_manager().emit_async(code, level, message, **context)


# ═══════════════════════════════════════════════════════════════════════════════
# SELFTEST
# ═══════════════════════════════════════════════════════════════════════════════

def run_selftest():
    """
    Selftest for AlertManager.
    
    Tests:
    1. Alert emission
    2. Throttling
    3. State persistence
    4. Level filtering
    """
    import tempfile
    import shutil
    
    print("\n" + "=" * 60)
    print("ALERT MANAGER SELFTEST")
    print("=" * 60)
    
    all_passed = True
    
    # ─────────────────────────────────────────────────────────────────────────────
    # TEST 1: Alert Emission
    # ─────────────────────────────────────────────────────────────────────────────
    print("\n[TEST 1] Alert Emission")
    
    am = AlertManager()
    am.throttle_minutes = 0  # Disable throttle for test
    
    result = am.emit(
        code=AlertCode.DAILY_LOSS_LIMIT_HIT,
        level=AlertLevel.CRITICAL,
        message="Daily loss limit reached",
        pnl=-30.0,
        limit=-30.0
    )
    
    assert result == True
    print("   [PASS] Alert emitted successfully")
    
    # ─────────────────────────────────────────────────────────────────────────────
    # TEST 2: Throttling
    # ─────────────────────────────────────────────────────────────────────────────
    print("\n[TEST 2] Throttling")
    
    am2 = AlertManager()
    am2.throttle_minutes = 30  # 30 min throttle
    am2._throttle_state = {}  # Reset
    
    # First alert should pass
    r1 = am2.emit(AlertCode.MAX_OPEN_POSITIONS_REACHED, AlertLevel.WARN, "Max positions")
    assert r1 == True
    print("   First alert: sent")
    
    # Second alert should be throttled
    r2 = am2.emit(AlertCode.MAX_OPEN_POSITIONS_REACHED, AlertLevel.WARN, "Max positions")
    assert r2 == False
    print("   Second alert: throttled")
    print("   [PASS] Throttling works")
    
    # ─────────────────────────────────────────────────────────────────────────────
    # TEST 3: State Persistence
    # ─────────────────────────────────────────────────────────────────────────────
    print("\n[TEST 3] State Persistence")
    
    test_dir = tempfile.mkdtemp()
    test_state_file = os.path.join(test_dir, "alert_state.json")
    
    try:
        am3 = AlertManager()
        am3.STATE_FILE = test_state_file
        am3._throttle_state = {"TEST_CODE": time.time()}
        am3._save_state()
        
        assert os.path.exists(test_state_file)
        print("   State saved")
        
        # Reload
        am4 = AlertManager()
        am4.STATE_FILE = test_state_file
        am4._throttle_state = am4._load_state()
        
        assert "TEST_CODE" in am4._throttle_state
        print("   State loaded")
        print("   [PASS] Persistence works")
        
    finally:
        shutil.rmtree(test_dir)
    
    # ─────────────────────────────────────────────────────────────────────────────
    # TEST 4: Level Filtering
    # ─────────────────────────────────────────────────────────────────────────────
    print("\n[TEST 4] Level Filtering")
    
    am5 = AlertManager()
    am5.min_level = AlertLevel.WARN
    am5._throttle_state = {}
    
    # INFO should be filtered
    r_info = am5.emit(AlertCode.ORDER_TIMEOUT_FALLBACK, AlertLevel.INFO, "Fallback")
    assert r_info == False
    print("   INFO filtered (min=WARN)")
    
    # WARN should pass
    r_warn = am5.emit(AlertCode.NEWS_VETO_TRUE, AlertLevel.WARN, "Veto true")
    assert r_warn == True
    print("   WARN passed")
    print("   [PASS] Level filtering works")
    
    # ─────────────────────────────────────────────────────────────────────────────
    # EXAMPLE OUTPUTS
    # ─────────────────────────────────────────────────────────────────────────────
    print("\n[EXAMPLE OUTPUTS]")
    
    am6 = AlertManager()
    am6._throttle_state = {}
    am6.throttle_minutes = 0
    
    print("\n--- Example 1: CRITICAL ---")
    am6.emit(AlertCode.DAILY_LOSS_LIMIT_HIT, AlertLevel.CRITICAL, 
             "Daily loss limit reached", pnl=-30.0, limit=-30.0)
    
    print("\n--- Example 2: WARN ---")
    am6.emit(AlertCode.NEWS_VETO_TRUE, AlertLevel.WARN,
             "Entry vetoed by news analysis", symbol="BTCUSDT", reason="SEC investigation")
    
    print("\n--- Example 3: INFO ---")
    am6.emit(AlertCode.ORDER_TIMEOUT_FALLBACK, AlertLevel.INFO,
             "Limit order timed out, using market", symbol="ETHUSDT")
    
    # ─────────────────────────────────────────────────────────────────────────────
    # SUMMARY
    # ─────────────────────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    if all_passed:
        print("ALL SELFTEST SCENARIOS PASSED")
    else:
        print("SOME SELFTEST SCENARIOS FAILED")
    print("=" * 60)
    
    return all_passed


if __name__ == "__main__":
    import sys
    
    if "--selftest" in sys.argv:
        success = run_selftest()
        sys.exit(0 if success else 1)
    else:
        # Demo
        print("AlertManager Demo")
        am = get_alert_manager()
        am.emit(AlertCode.DAILY_LOSS_LIMIT_HIT, AlertLevel.CRITICAL, 
                "Demo alert", test=True)
