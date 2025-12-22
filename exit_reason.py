"""
exit_reason.py - Exit Reason Enum
=================================

Standardized exit reason codes for position closures.

Usage:
    from exit_reason import ExitReason
    
    close_position(pos_id, price, reason=ExitReason.STOP_LOSS)
"""

from enum import Enum


class ExitReason(str, Enum):
    """
    Pozisyon kapanış sebepleri.
    
    str mixin ile string olarak da kullanılabilir:
        reason = ExitReason.STOP_LOSS
        print(reason)  # "STOP_LOSS"
    """
    
    # Risk-based exits
    STOP_LOSS = "STOP_LOSS"        # İlk stop loss tetiklendi
    TRAIL_STOP = "TRAIL_STOP"      # Trailing stop tetiklendi
    
    # Profit-taking exits
    PARTIAL_TP = "PARTIAL_TP"      # Kısmi kar alma (1R)
    TAKE_PROFIT = "TAKE_PROFIT"    # Tam TP
    
    # Manual/AI exits
    MANUAL = "MANUAL"              # Manuel kapanış
    AI_SELL = "AI_SELL"            # AI satış kararı
    
    # Error/Safety exits
    EXCHANGE_REJECT = "EXCHANGE_REJECT"      # Borsa emri reddetti
    RISK_KILL_SWITCH = "RISK_KILL_SWITCH"    # Risk limiti aşıldı
    
    # Backtest specific
    BACKTEST_END = "BACKTEST_END"  # Backtest sonu pozisyon kapanışı
    
    def __str__(self) -> str:
        return self.value
    
    @classmethod
    def from_string(cls, value: str) -> "ExitReason":
        """
        String'den ExitReason'a çevir.
        
        Eski kod uyumluluğu için:
            "SL" -> STOP_LOSS
            "TP" -> TAKE_PROFIT
            "TRAIL_SL" -> TRAIL_STOP
        """
        # Normalize
        value = value.upper().strip()
        
        # Legacy mappings
        legacy_map = {
            "SL": cls.STOP_LOSS,
            "TP": cls.TAKE_PROFIT,
            "TRAIL_SL": cls.TRAIL_STOP,
            "AI-SELL": cls.AI_SELL,
        }
        
        if value in legacy_map:
            return legacy_map[value]
        
        # Direct match
        try:
            return cls(value)
        except ValueError:
            return cls.MANUAL  # Fallback


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def is_stop_exit(reason: ExitReason) -> bool:
    """Stop-based kapanış mı?"""
    return reason in (ExitReason.STOP_LOSS, ExitReason.TRAIL_STOP)


def is_profit_exit(reason: ExitReason) -> bool:
    """Kar alarak kapanış mı?"""
    return reason in (ExitReason.PARTIAL_TP, ExitReason.TAKE_PROFIT)


def is_error_exit(reason: ExitReason) -> bool:
    """Hata/güvenlik kapanışı mı?"""
    return reason in (ExitReason.EXCHANGE_REJECT, ExitReason.RISK_KILL_SWITCH)


# ═══════════════════════════════════════════════════════════════════════════════
# TEST
# ═══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 50)
    print("EXIT REASON TEST")
    print("=" * 50)
    
    # Test string conversion
    print("\n[TEST 1] String Conversion")
    reason = ExitReason.STOP_LOSS
    print(f"  ExitReason.STOP_LOSS = '{reason}'")
    assert str(reason) == "STOP_LOSS"
    print("  PASS")
    
    # Test from_string
    print("\n[TEST 2] From String")
    assert ExitReason.from_string("SL") == ExitReason.STOP_LOSS
    assert ExitReason.from_string("TRAIL_SL") == ExitReason.TRAIL_STOP
    assert ExitReason.from_string("STOP_LOSS") == ExitReason.STOP_LOSS
    print("  Legacy mappings work")
    print("  PASS")
    
    # Test helpers
    print("\n[TEST 3] Helpers")
    assert is_stop_exit(ExitReason.STOP_LOSS) == True
    assert is_stop_exit(ExitReason.TAKE_PROFIT) == False
    assert is_profit_exit(ExitReason.PARTIAL_TP) == True
    print("  Helpers work")
    print("  PASS")
    
    print("\n" + "=" * 50)
    print("ALL TESTS PASSED")
    print("=" * 50)
