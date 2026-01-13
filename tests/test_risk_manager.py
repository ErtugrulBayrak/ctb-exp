"""
test_risk_manager.py - Unit Tests for Risk Manager
===================================================

Tests guardrails, SL/TP calculation, and position sizing.
"""

import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from risk_manager import RiskManager


class TestRiskManagerInit(unittest.TestCase):
    """Tests for RiskManager initialization."""
    
    def test_default_config(self):
        """Should initialize with default values."""
        rm = RiskManager()
        self.assertIsNotNone(rm)
        # risk_per_trade varies by profile: paper=0.005 (0.5%), live=0.02 (2%)
        self.assertGreater(rm._risk_per_trade, 0)
        self.assertLessEqual(rm._risk_per_trade, 0.05)  # Max 5%
    
    def test_custom_config(self):
        """Should accept custom config."""
        config = {
            "min_adx": 25,
            "min_volume": 500_000,
            "risk_per_trade": 0.01
        }
        rm = RiskManager(config=config)
        self.assertEqual(rm._min_adx, 25)
        self.assertEqual(rm._min_volume, 500_000)
        self.assertEqual(rm._risk_per_trade, 0.01)


class TestCalculateSlTp(unittest.TestCase):
    """Tests for SL/TP calculation."""
    
    def setUp(self):
        self.rm = RiskManager()
    
    def test_atr_based_sl_tp(self):
        """Should calculate ATR-based SL/TP."""
        result = self.rm._calculate_sl_tp(price=100, atr=5)
        # 2x ATR SL, 3x ATR TP
        self.assertEqual(result["stop_loss"], 90.0)  # 100 - 2*5
        self.assertEqual(result["take_profit"], 115.0)  # 100 + 3*5
    
    def test_fallback_without_atr(self):
        """Should use percentage fallback when ATR is zero."""
        result = self.rm._calculate_sl_tp(price=100, atr=0)
        # 3% SL, 5% TP fallback
        self.assertEqual(result["stop_loss"], 97.0)
        self.assertEqual(result["take_profit"], 105.0)
    
    def test_tighter_sl_bias(self):
        """tighter bias should reduce SL distance by 25%."""
        result = self.rm._calculate_sl_tp(price=100, atr=5, sl_bias="tighter")
        # 2 * 5 * 0.75 = 7.5 → SL = 100 - 7.5 = 92.5
        self.assertEqual(result["stop_loss"], 92.5)
    
    def test_looser_tp_bias(self):
        """looser bias should increase TP distance by 25%."""
        result = self.rm._calculate_sl_tp(price=100, atr=5, tp_bias="looser")
        # 3 * 5 * 1.25 = 18.75 → TP = 100 + 18.75 = 118.75
        self.assertEqual(result["take_profit"], 118.75)
    
    def test_neutral_bias(self):
        """neutral bias should not change distances."""
        result_neutral = self.rm._calculate_sl_tp(price=100, atr=5, sl_bias="neutral", tp_bias="neutral")
        result_default = self.rm._calculate_sl_tp(price=100, atr=5)
        self.assertEqual(result_neutral, result_default)
    
    def test_combined_biases(self):
        """Should handle tighter SL and looser TP together."""
        result = self.rm._calculate_sl_tp(price=100, atr=5, sl_bias="tighter", tp_bias="looser")
        self.assertEqual(result["stop_loss"], 92.5)
        self.assertEqual(result["take_profit"], 118.75)


class TestCalculateQuantity(unittest.TestCase):
    """Tests for position sizing."""
    
    def setUp(self):
        self.rm = RiskManager()
    
    def test_risk_based_sizing(self):
        """Should calculate quantity based on risk per trade, capped at 10%."""
        # Balance: $1000, Risk: 2% = $20
        # Price: $100, SL: $90 → Risk per unit: $10
        # Quantity from risk: $20 / $10 = 2
        # But max cap: 10% of $1000 / $100 = 1
        # So final quantity = min(2, 1) = 1
        qty = self.rm._calculate_quantity(balance=1000, price=100, stop_loss=90)
        max_qty = (1000 * 0.10) / 100  # = 1
        self.assertLessEqual(qty, max_qty)
    
    def test_max_cap(self):
        """Should cap at 10% of balance."""
        # Very tight SL would give huge quantity, should be capped
        # Balance: $1000, 10% = $100 / $100 price = 1 unit max
        qty = self.rm._calculate_quantity(balance=1000, price=100, stop_loss=99.9)
        max_qty = (1000 * 0.10) / 100  # = 1
        self.assertLessEqual(qty, max_qty)
    
    def test_zero_balance(self):
        """Should return 0 for zero balance."""
        qty = self.rm._calculate_quantity(balance=0, price=100, stop_loss=90)
        self.assertEqual(qty, 0.0)
    
    def test_invalid_stop_loss(self):
        """Should use fallback when SL >= price."""
        qty = self.rm._calculate_quantity(balance=1000, price=100, stop_loss=105)
        # Fallback: risk% of balance / price
        # Note: risk_per_trade depends on strategy mode and config
        # Just verify we get a positive, reasonable quantity
        self.assertGreater(qty, 0)
        self.assertLessEqual(qty, 1.0)  # Should be at most 10% of balance/price = 1


class TestCheckGuardrails(unittest.TestCase):
    """Tests for risk guardrails."""
    
    def setUp(self):
        self.rm = RiskManager()
    
    def test_pass_all_guardrails(self):
        """Should pass when all conditions are met."""
        snapshot = {
            "technical": {
                "trend": "BULLISH",
                "adx": 30,
                "trend_strength": "MODERATE"
            },
            "volume_24h": 5_000_000,  # $5M > $1M minimum
            "sentiment": {
                "fear_greed": {"value": 50}
            }
        }
        result = self.rm._check_guardrails(snapshot, confidence=70)
        self.assertTrue(result["passed"])
    
    def test_block_strong_bearish(self):
        """Should block on strong bearish trend."""
        snapshot = {
            "technical": {
                "trend": "BEARISH",
                "trend_strength": "STRONG",
                "adx": 35
            },
            "volume_24h": 5_000_000,
            "sentiment": {"fear_greed": {"value": 50}}
        }
        result = self.rm._check_guardrails(snapshot)
        self.assertFalse(result["passed"])
        self.assertIn("downtrend", result["reason"])
    
    def test_block_low_adx(self):
        """Should block on low ADX (weak trend)."""
        snapshot = {
            "technical": {
                "trend": "BULLISH",
                "adx": 8,  # Below MIN_ADX_ENTRY (10) threshold
                "trend_strength": "WEAK"
            },
            "volume_24h": 5_000_000,
            "sentiment": {"fear_greed": {"value": 50}}
        }
        result = self.rm._check_guardrails(snapshot)
        self.assertFalse(result["passed"])
        self.assertIn("ADX", result["reason"])
    
    def test_block_low_volume(self):
        """Should block on low volume."""
        snapshot = {
            "technical": {
                "trend": "BULLISH",
                "adx": 30,
                "trend_strength": "MODERATE"
            },
            "volume_24h": 500_000,  # $500K < $1M minimum
            "sentiment": {"fear_greed": {"value": 50}}
        }
        result = self.rm._check_guardrails(snapshot)
        self.assertFalse(result["passed"])
        self.assertIn("Volume", result["reason"])
    
    def test_block_extreme_fear(self):
        """Should block on extreme fear."""
        snapshot = {
            "technical": {
                "trend": "BULLISH",
                "adx": 30,
                "trend_strength": "MODERATE"
            },
            "volume_24h": 5_000_000,
            "sentiment": {
                "fear_greed": {"value": 15}  # Below 20 threshold
            }
        }
        result = self.rm._check_guardrails(snapshot)
        self.assertFalse(result["passed"])
        self.assertIn("Fear", result["reason"])
    
    def test_skip_volume_check_when_missing(self):
        """Should skip volume check when data missing."""
        snapshot = {
            "technical": {
                "trend": "BULLISH",
                "adx": 30,
                "trend_strength": "MODERATE"
            },
            # No volume_24h key
            "sentiment": {"fear_greed": {"value": 50}}
        }
        result = self.rm._check_guardrails(snapshot)
        # Should pass (volume check skipped)
        self.assertTrue(result["passed"])


class TestEvaluateEntryRisk(unittest.TestCase):
    """Tests for full entry risk evaluation."""
    
    def setUp(self):
        self.rm = RiskManager()
    
    def test_reject_non_buy(self):
        """Should reject when base decision is not BUY."""
        snapshot = {"technical": {}, "sentiment": {}}
        decision = {"action": "HOLD", "confidence": 50}
        portfolio = {"balance": 1000}
        
        result = self.rm.evaluate_entry_risk(snapshot, decision, portfolio)
        self.assertFalse(result["allowed"])
        self.assertIn("not BUY", result["reason"])
    
    def test_full_buy_evaluation(self):
        """Should complete full evaluation for valid BUY."""
        snapshot = {
            "price": 100,
            "volume_24h": 5_000_000,
            "technical": {
                "price": 100,
                "trend": "BULLISH",
                "adx": 30,
                "atr": 5,
                "trend_strength": "MODERATE"
            },
            "sentiment": {"fear_greed": {"value": 50}}
        }
        decision = {
            "action": "BUY",
            "confidence": 80,
            "metadata": {"sl_bias": "neutral", "tp_bias": "neutral"}
        }
        portfolio = {"balance": 1000}
        
        result = self.rm.evaluate_entry_risk(snapshot, decision, portfolio)
        self.assertTrue(result["allowed"])
        self.assertIn("stop_loss", result)
        self.assertIn("take_profit", result)
        self.assertIn("quantity", result)
        self.assertGreater(result["quantity"], 0)


if __name__ == "__main__":
    unittest.main()
