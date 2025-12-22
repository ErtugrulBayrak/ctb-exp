"""
metrics.py - Merkezi Metrik Sayaçları
=====================================

Production-grade telemetry için merkezi sayaç sistemi.

Usage:
    from metrics import increment, get_metrics, log_summary
    
    increment("veto_checked_count")
    increment("regime_block_count", 2)
    
    log_summary()  # Periyodik özet logla
"""

import time
import os
from datetime import datetime
from typing import Dict, Any, Optional

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
        METRICS_LOG_EVERY_N_CYCLES = 20
        METRICS_PERSIST_DAILY = True
    SETTINGS = MockSettings()


# ═══════════════════════════════════════════════════════════════════════════════
# GLOBAL METRICS STATE
# ═══════════════════════════════════════════════════════════════════════════════
_METRICS: Dict[str, int] = {
    # Regime filter
    "regime_block_count": 0,
    
    # Veto system
    "veto_checked_count": 0,
    "veto_prefilter_skip_count": 0,
    "veto_llm_called_count": 0,
    "veto_true_count": 0,
    "veto_blocked_entry_count": 0,
    
    # Order ledger
    "order_ledger_block_count": 0,
    
    # LLM rate limiting
    "llm_calls_this_hour": 0,
    "llm_rate_limited_count": 0,
    
    # Session tracking
    "cycle_count": 0,
    "session_start_ts": int(time.time()),
    "last_hourly_reset_ts": int(time.time()),
}

_LAST_LOG_CYCLE = 0


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def increment(key: str, amount: int = 1) -> int:
    """
    Metrik sayacını artır.
    
    Args:
        key: Metrik adı
        amount: Artış miktarı (default: 1)
    
    Returns:
        Yeni değer
    """
    global _METRICS
    if key in _METRICS:
        _METRICS[key] += amount
        return _METRICS[key]
    else:
        _METRICS[key] = amount
        return amount


def get(key: str, default: int = 0) -> int:
    """Metrik değerini al."""
    return _METRICS.get(key, default)


def get_metrics() -> Dict[str, int]:
    """Tüm metriklerin kopyasını döndür."""
    return _METRICS.copy()


def reset(key: str) -> None:
    """Tek bir metriği sıfırla."""
    if key in _METRICS:
        _METRICS[key] = 0


def reset_hourly() -> None:
    """Saatlik metrikleri sıfırla (LLM rate limit için)."""
    global _METRICS
    _METRICS["llm_calls_this_hour"] = 0
    _METRICS["last_hourly_reset_ts"] = int(time.time())
    logger.debug("[METRICS] Hourly counters reset")


def check_hourly_reset() -> None:
    """Saat geçtiyse hourly metrikleri sıfırla."""
    now = int(time.time())
    last_reset = _METRICS.get("last_hourly_reset_ts", 0)
    
    if now - last_reset >= 3600:  # 1 hour
        reset_hourly()


def log_summary(force: bool = False) -> None:
    """
    Metrik özetini logla.
    
    METRICS_LOG_EVERY_N_CYCLES döngüde bir otomatik çağrılır.
    force=True ile her zaman loglar.
    """
    global _LAST_LOG_CYCLE
    
    cycle = _METRICS.get("cycle_count", 0)
    log_interval = getattr(SETTINGS, 'METRICS_LOG_EVERY_N_CYCLES', 20)
    
    if not force and cycle - _LAST_LOG_CYCLE < log_interval:
        return
    
    _LAST_LOG_CYCLE = cycle
    
    # Session duration
    session_secs = int(time.time()) - _METRICS.get("session_start_ts", int(time.time()))
    session_mins = session_secs // 60
    
    summary = (
        f"[METRICS SUMMARY] Cycle #{cycle} | Session: {session_mins}m | "
        f"RegimeBlock={_METRICS['regime_block_count']} | "
        f"VetoChecked={_METRICS['veto_checked_count']} | "
        f"VetoTrue={_METRICS['veto_true_count']} | "
        f"VetoBlocked={_METRICS['veto_blocked_entry_count']} | "
        f"LedgerBlock={_METRICS['order_ledger_block_count']} | "
        f"LLMHour={_METRICS['llm_calls_this_hour']} | "
        f"LLMRateLimited={_METRICS['llm_rate_limited_count']}"
    )
    
    logger.info(summary)


def persist_daily(data_dir: str = "data") -> bool:
    """
    Günlük metrikleri dosyaya kaydet.
    
    File: data/metrics_daily.json
    """
    if not getattr(SETTINGS, 'METRICS_PERSIST_DAILY', True):
        return False
    
    try:
        from utils.io import write_atomic_json, read_json_safe
    except ImportError:
        return False
    
    filepath = os.path.join(data_dir, "metrics_daily.json")
    
    # Load existing
    existing = read_json_safe(filepath, default={"days": []})
    
    today = datetime.now().strftime("%Y-%m-%d")
    
    # Find today's entry or create new
    today_entry = None
    for entry in existing.get("days", []):
        if entry.get("date") == today:
            today_entry = entry
            break
    
    if today_entry is None:
        today_entry = {"date": today, "metrics": {}}
        existing["days"].append(today_entry)
    
    # Update metrics
    today_entry["metrics"] = get_metrics()
    today_entry["last_updated"] = datetime.now().isoformat()
    
    # Keep only last 30 days
    existing["days"] = existing["days"][-30:]
    
    return write_atomic_json(filepath, existing)


def on_cycle_end() -> None:
    """
    Her döngü sonunda çağrılmalı.
    
    - Sayacı artırır
    - Saatlik reset kontrolü
    - Periyodik log
    """
    increment("cycle_count")
    check_hourly_reset()
    log_summary()


# ═══════════════════════════════════════════════════════════════════════════════
# LLM RATE LIMITING HELPER
# ═══════════════════════════════════════════════════════════════════════════════

def can_call_llm() -> bool:
    """
    LLM çağrısı yapılabilir mi kontrol et.
    
    MAX_LLM_CALLS_PER_HOUR aşıldıysa False döner.
    
    Returns:
        bool: LLM çağrısı yapılabilir mi
    """
    check_hourly_reset()
    
    max_calls = getattr(SETTINGS, 'MAX_LLM_CALLS_PER_HOUR', 10)
    current = _METRICS.get("llm_calls_this_hour", 0)
    
    return current < max_calls


def record_llm_call() -> None:
    """LLM çağrısını kaydet."""
    increment("llm_calls_this_hour")
    increment("veto_llm_called_count")


def record_llm_rate_limited() -> None:
    """Rate limit nedeniyle atlandığını kaydet."""
    increment("llm_rate_limited_count")
    logger.warning("[METRICS] LLM call rate limited")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST
# ═══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 50)
    print("METRICS TEST")
    print("=" * 50)
    
    # Test increment
    print("\n[TEST 1] Increment")
    increment("veto_checked_count")
    increment("veto_checked_count")
    assert get("veto_checked_count") == 2
    print(f"  veto_checked_count = {get('veto_checked_count')}")
    print("  PASS")
    
    # Test LLM rate limiting
    print("\n[TEST 2] LLM Rate Limiting")
    # Mock low limit
    class MockSettings2:
        MAX_LLM_CALLS_PER_HOUR = 2
        METRICS_LOG_EVERY_N_CYCLES = 20
        METRICS_PERSIST_DAILY = False
    
    import metrics
    metrics.SETTINGS = MockSettings2()
    
    reset("llm_calls_this_hour")
    assert can_call_llm() == True
    record_llm_call()
    assert can_call_llm() == True
    record_llm_call()
    assert can_call_llm() == False
    print(f"  After 2 calls, can_call_llm = {can_call_llm()}")
    print("  PASS")
    
    # Test summary
    print("\n[TEST 3] Log Summary")
    log_summary(force=True)
    print("  PASS")
    
    print("\n" + "=" * 50)
    print("ALL TESTS PASSED")
    print("=" * 50)
