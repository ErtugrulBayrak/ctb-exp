"""
summary_reporter.py - Periodic Summary Reporter
================================================

Günlük ve saatlik özet raporlar oluşturur.
metrics.py'den metrikleri çeker, log'a yazar, opsiyonel Telegram gönderir.

Usage:
    from summary_reporter import get_reporter
    
    reporter = get_reporter()
    await reporter.maybe_report(portfolio, telegram_fn, telegram_config)
"""

import os
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Callable

# Timezone support
try:
    from zoneinfo import ZoneInfo
    ISTANBUL_TZ = ZoneInfo("Europe/Istanbul")
except ImportError:
    try:
        import pytz
        ISTANBUL_TZ = pytz.timezone("Europe/Istanbul")
    except ImportError:
        ISTANBUL_TZ = None

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
        DAILY_SUMMARY_ENABLED = True
        DAILY_SUMMARY_TIME = "23:59"
        HOURLY_SUMMARY_ENABLED = False
        SUMMARY_SEND_TELEGRAM = False
        SUMMARY_TELEGRAM_CHAT_ID = None
        SUMMARY_PERSIST_STATE = True
    SETTINGS = MockSettings()

# Metrics import
try:
    from metrics import get_metrics
except ImportError:
    get_metrics = lambda: {}

# Atomic IO import
try:
    from utils.io import write_atomic_json, read_json_safe
except ImportError:
    import json
    def write_atomic_json(path, data, indent=2):
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


class SummaryReporter:
    """
    Periodic Summary Reporter.
    
    Günlük (23:59 Istanbul) ve/veya saatlik raporlar üretir.
    Restart sonrası aynı gün/saat tekrar rapor atmaz (state persist).
    """
    
    STATE_FILE = "data/summary_state.json"
    
    def __init__(self):
        """Initialize SummaryReporter."""
        self.daily_enabled = getattr(SETTINGS, 'DAILY_SUMMARY_ENABLED', True)
        self.daily_time = getattr(SETTINGS, 'DAILY_SUMMARY_TIME', "23:59")
        self.hourly_enabled = getattr(SETTINGS, 'HOURLY_SUMMARY_ENABLED', False)
        self.send_telegram = getattr(SETTINGS, 'SUMMARY_SEND_TELEGRAM', False)
        self.persist_state = getattr(SETTINGS, 'SUMMARY_PERSIST_STATE', True)
        
        # Parse daily time
        try:
            parts = self.daily_time.split(":")
            self._daily_hour = int(parts[0])
            self._daily_minute = int(parts[1])
        except:
            self._daily_hour = 23
            self._daily_minute = 59
        
        # Load persisted state
        self._state = self._load_state()
    
    def _get_istanbul_now(self) -> datetime:
        """Get current time in Europe/Istanbul timezone."""
        if ISTANBUL_TZ:
            return datetime.now(ISTANBUL_TZ)
        else:
            # Fallback: UTC+3
            from datetime import timedelta
            utc_now = datetime.now(timezone.utc)
            return utc_now + timedelta(hours=3)
    
    def _load_state(self) -> Dict:
        """Load persisted state from file."""
        default = {
            "last_daily_date": None,
            "last_hourly_hour": None,
            "last_hourly_date": None
        }
        if self.persist_state:
            return read_json_safe(self.STATE_FILE, default=default)
        return default
    
    def _save_state(self) -> bool:
        """Save state to file atomically."""
        if not self.persist_state:
            return True
        
        # Ensure data directory exists
        os.makedirs(os.path.dirname(self.STATE_FILE), exist_ok=True)
        return write_atomic_json(self.STATE_FILE, self._state)
    
    def _should_report_daily(self) -> bool:
        """Check if daily report should be generated."""
        if not self.daily_enabled:
            return False
        
        now = self._get_istanbul_now()
        today_str = now.strftime("%Y-%m-%d")
        
        # Already reported today?
        if self._state.get("last_daily_date") == today_str:
            return False
        
        # Is it past the configured time?
        current_mins = now.hour * 60 + now.minute
        target_mins = self._daily_hour * 60 + self._daily_minute
        
        return current_mins >= target_mins
    
    def _should_report_hourly(self) -> bool:
        """Check if hourly report should be generated."""
        if not self.hourly_enabled:
            return False
        
        now = self._get_istanbul_now()
        today_str = now.strftime("%Y-%m-%d")
        current_hour = now.hour
        
        # Already reported this hour today?
        if (self._state.get("last_hourly_date") == today_str and 
            self._state.get("last_hourly_hour") == current_hour):
            return False
        
        return True
    
    def _build_summary(self, portfolio: Dict = None, report_type: str = "DAILY") -> str:
        """
        Build summary report string.
        
        Args:
            portfolio: Portfolio dict for trade stats
            report_type: "DAILY" or "HOURLY"
        
        Returns:
            Formatted summary string
        """
        now = self._get_istanbul_now()
        date_str = now.strftime("%Y-%m-%d")
        
        # Get metrics from centralized metrics module
        m = get_metrics()
        
        # Portfolio stats
        trade_count = 0
        win_count = 0
        loss_count = 0
        pnl = 0.0
        
        if portfolio:
            history = portfolio.get("history", [])
            trade_count = len(history)
            for trade in history:
                p = trade.get("profit_loss", 0)
                if p and p > 0:
                    win_count += 1
                elif p and p < 0:
                    loss_count += 1
                pnl += (p or 0)
        
        pnl_str = f"${pnl:.2f}" if pnl != 0 or trade_count > 0 else "N/A"
        win_loss_str = f"{win_count}/{loss_count}" if trade_count > 0 else "N/A"
        
        # Format report
        lines = [
            f"[{report_type} SUMMARY] {date_str}",
            f"cycles={m.get('cycle_count', 0)} trades={trade_count} win/loss={win_loss_str} pnl={pnl_str}",
            f"regime_blocks={m.get('regime_block_count', 0)} ledger_blocks={m.get('order_ledger_block_count', 0)}",
            f"veto: checked={m.get('veto_checked_count', 0)} prefilter_skip={m.get('veto_prefilter_skip_count', 0)} "
            f"llm_called={m.get('veto_llm_called_count', 0)} true={m.get('veto_true_count', 0)} "
            f"blocked={m.get('veto_blocked_entry_count', 0)}",
            f"slippage_bps_avg=N/A max_dd=N/A"
        ]
        
        return "\n".join(lines)
    
    async def maybe_report(
        self,
        portfolio: Dict = None,
        telegram_fn: Callable = None,
        telegram_config: Dict = None
    ) -> bool:
        """
        Generate report if scheduled time has passed.
        
        Should be called at the end of each loop cycle.
        
        Args:
            portfolio: Portfolio dict
            telegram_fn: Async telegram function
            telegram_config: Dict with bot_token and chat_id
        
        Returns:
            True if any report was generated
        """
        reported = False
        
        # Check daily
        if self._should_report_daily():
            summary = self._build_summary(portfolio, "DAILY")
            
            # Always log
            logger.info("\n" + "=" * 50)
            logger.info(summary)
            logger.info("=" * 50)
            
            # Telegram if enabled
            if self.send_telegram and telegram_fn:
                await self._send_telegram(summary, telegram_fn, telegram_config)
            
            # Update state
            now = self._get_istanbul_now()
            self._state["last_daily_date"] = now.strftime("%Y-%m-%d")
            self._save_state()
            
            reported = True
        
        # Check hourly
        if self._should_report_hourly():
            summary = self._build_summary(portfolio, "HOURLY")
            
            # Always log
            logger.info(summary)
            
            # Telegram if enabled
            if self.send_telegram and telegram_fn:
                await self._send_telegram(summary, telegram_fn, telegram_config)
            
            # Update state
            now = self._get_istanbul_now()
            self._state["last_hourly_date"] = now.strftime("%Y-%m-%d")
            self._state["last_hourly_hour"] = now.hour
            self._save_state()
            
            reported = True
        
        return reported
    
    async def _send_telegram(
        self,
        message: str,
        telegram_fn: Callable,
        telegram_config: Dict
    ) -> bool:
        """Send summary via Telegram."""
        if not telegram_fn or not telegram_config:
            return False
        
        bot_token = telegram_config.get("bot_token", "")
        
        # Use summary-specific chat_id if configured, else fallback to default
        chat_id = getattr(SETTINGS, 'SUMMARY_TELEGRAM_CHAT_ID', None)
        if not chat_id:
            chat_id = telegram_config.get("chat_id", "")
        
        if not bot_token or not chat_id:
            return False
        
        try:
            # Format for Telegram (monospace)
            tg_message = f"<pre>{message}</pre>"
            await telegram_fn(bot_token, chat_id, tg_message)
            logger.debug("[SUMMARY] Telegram sent")
            return True
        except Exception as e:
            logger.error(f"[SUMMARY] Telegram failed: {e}")
            return False
    
    def force_daily_now(self, portfolio: Dict = None) -> str:
        """
        Force generate daily report now (for testing).
        
        Returns:
            Summary string
        """
        return self._build_summary(portfolio, "DAILY")


# ═══════════════════════════════════════════════════════════════════════════════
# GLOBAL INSTANCE
# ═══════════════════════════════════════════════════════════════════════════════
_reporter: Optional[SummaryReporter] = None


def get_reporter() -> SummaryReporter:
    """Get or create global reporter instance."""
    global _reporter
    if _reporter is None:
        _reporter = SummaryReporter()
    return _reporter


# ═══════════════════════════════════════════════════════════════════════════════
# SELFTEST
# ═══════════════════════════════════════════════════════════════════════════════
def run_selftest():
    """
    Selftest for SummaryReporter.
    
    Tests:
    1. Report format
    2. State persistence
    3. Timezone handling
    """
    import tempfile
    import shutil
    
    print("\n" + "=" * 60)
    print("SUMMARY REPORTER SELFTEST")
    print("=" * 60)
    
    all_passed = True
    
    # ─────────────────────────────────────────────────────────────────────────────
    # TEST 1: Report Format
    # ─────────────────────────────────────────────────────────────────────────────
    print("\n[TEST 1] Report Format")
    
    reporter = SummaryReporter()
    
    mock_portfolio = {
        "balance": 1050.0,
        "history": [
            {"symbol": "BTCUSDT", "profit_loss": 30.0},
            {"symbol": "ETHUSDT", "profit_loss": -15.0},
            {"symbol": "SOLUSDT", "profit_loss": 25.0}
        ]
    }
    
    summary = reporter._build_summary(mock_portfolio, "DAILY")
    print(f"\n{summary}\n")
    
    # Validate format
    assert "[DAILY SUMMARY]" in summary
    assert "cycles=" in summary
    assert "trades=3" in summary
    assert "win/loss=2/1" in summary
    assert "regime_blocks=" in summary
    assert "veto:" in summary
    print("   [PASS] Report format correct")
    
    # ─────────────────────────────────────────────────────────────────────────────
    # TEST 2: State Persistence
    # ─────────────────────────────────────────────────────────────────────────────
    print("\n[TEST 2] State Persistence")
    
    test_dir = tempfile.mkdtemp()
    test_state_file = os.path.join(test_dir, "summary_state.json")
    
    try:
        # Create reporter with custom state file
        reporter2 = SummaryReporter()
        reporter2.STATE_FILE = test_state_file
        reporter2._state = {"last_daily_date": "2025-12-19"}
        reporter2._save_state()
        
        # Verify file exists
        assert os.path.exists(test_state_file)
        print("   State saved successfully")
        
        # Reload
        reporter3 = SummaryReporter()
        reporter3.STATE_FILE = test_state_file
        reporter3._state = reporter3._load_state()
        
        assert reporter3._state.get("last_daily_date") == "2025-12-19"
        print("   State loaded correctly")
        print("   [PASS] Persistence works")
        
    finally:
        shutil.rmtree(test_dir)
    
    # ─────────────────────────────────────────────────────────────────────────────
    # TEST 3: Timezone
    # ─────────────────────────────────────────────────────────────────────────────
    print("\n[TEST 3] Istanbul Timezone")
    
    now_istanbul = reporter._get_istanbul_now()
    print(f"   Istanbul now: {now_istanbul}")
    
    # Should be a valid datetime
    assert now_istanbul.year >= 2025
    print("   [PASS] Timezone works")
    
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
        print("SummaryReporter Demo")
        reporter = get_reporter()
        summary = reporter.force_daily_now()
        print(summary)
