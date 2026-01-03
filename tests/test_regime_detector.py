"""
test_regime_detector.py - Unit Tests for Regime Detector
=========================================================

Tests for multi-timeframe regime detection, classification logic,
confidence scoring, and caching behavior.
"""

import sys
import os
import time
import unittest

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategies.regime_detector import RegimeDetector, RegimeType, RegimeResult


class TestRegimeDetectorInit(unittest.TestCase):
    """Tests for RegimeDetector initialization."""
    
    def test_default_init(self):
        """Should initialize with default values."""
        detector = RegimeDetector()
        self.assertIsNotNone(detector)
        self.assertEqual(detector._cache_ttl, 3600.0)
    
    def test_custom_config(self):
        """Should accept custom configuration."""
        detector = RegimeDetector(
            cache_ttl=1800.0,
            strong_trend_adx=35.0,
            volatile_atr_pct=4.0
        )
        self.assertEqual(detector._cache_ttl, 1800.0)
        self.assertEqual(detector._strong_trend_adx, 35.0)
        self.assertEqual(detector._volatile_atr_pct, 4.0)


class TestDetectRegime(unittest.TestCase):
    """Tests for regime classification logic."""
    
    def setUp(self):
        self.detector = RegimeDetector()
    
    def test_strong_trend_detection(self):
        """Should detect STRONG_TREND when ADX > 30, ATR% > 1.5%, BB > 5%."""
        snapshot = {
            "symbol": "BTCUSDT",
            "price": 50000.0,
            "tf": {
                "4h": {
                    "adx": 35.0,
                    "atr": 1000.0,  # 2% ATR
                    "bb_upper": 53000,
                    "bb_lower": 47000,
                    "bb_middle": 50000  # 12% width
                },
                "1h": {"adx": 32.0},
                "1d": {"adx": 30.0}
            }
        }
        
        result = self.detector.detect_regime("BTCUSDT", snapshot)
        self.assertEqual(result["regime"], "STRONG_TREND")
        self.assertGreater(result["confidence"], 0.5)
    
    def test_weak_trend_detection(self):
        """Should detect WEAK_TREND when ADX 20-30 and ATR% 0.8-1.5%."""
        snapshot = {
            "symbol": "ETHUSDT",
            "price": 3000.0,
            "tf": {
                "4h": {
                    "adx": 25.0,
                    "atr": 30.0,  # 1.0% ATR
                    "bb_upper": 3100,
                    "bb_lower": 2900,
                    "bb_middle": 3000
                },
                "1h": {"adx": 22.0},
                "1d": {"adx": 24.0}
            }
        }
        
        result = self.detector.detect_regime("ETHUSDT", snapshot)
        self.assertEqual(result["regime"], "WEAK_TREND")
    
    def test_ranging_detection(self):
        """Should detect RANGING when ADX < 20, ATR% < 0.8%, BB < 3%."""
        snapshot = {
            "symbol": "BNBUSDT",
            "price": 300.0,
            "tf": {
                "4h": {
                    "adx": 15.0,
                    "atr": 1.5,  # 0.5% ATR
                    "bb_upper": 304,
                    "bb_lower": 296,
                    "bb_middle": 300  # 2.67% width
                },
                "1h": {"adx": 12.0},
                "1d": {"adx": 16.0}
            }
        }
        
        result = self.detector.detect_regime("BNBUSDT", snapshot)
        self.assertEqual(result["regime"], "RANGING")
    
    def test_volatile_atr_detection(self):
        """Should detect VOLATILE when ATR% > 3%."""
        snapshot = {
            "symbol": "SOLUSDT",
            "price": 100.0,
            "tf": {
                "4h": {
                    "adx": 25.0,
                    "atr": 4.0,  # 4% ATR
                    "bb_upper": 110,
                    "bb_lower": 90,
                    "bb_middle": 100
                },
                "1h": {"adx": 22.0},
                "1d": {"adx": 20.0}
            }
        }
        
        result = self.detector.detect_regime("SOLUSDT", snapshot)
        self.assertEqual(result["regime"], "VOLATILE")
        self.assertIn("ATR%", result["reason"])
    
    def test_volatile_swing_detection(self):
        """Should detect VOLATILE when 24h swing > 5%."""
        snapshot = {
            "symbol": "DOGEUSDT",
            "price": 0.10,
            "high_24h": 0.11,
            "low_24h": 0.10,  # 10% swing
            "tf": {
                "4h": {
                    "adx": 25.0,
                    "atr": 0.001,  # 1% ATR (not volatile by ATR)
                    "bb_upper": 0.102,
                    "bb_lower": 0.098,
                    "bb_middle": 0.10
                },
                "1h": {"adx": 22.0},
                "1d": {"adx": 20.0}
            }
        }
        
        result = self.detector.detect_regime("DOGEUSDT", snapshot)
        self.assertEqual(result["regime"], "VOLATILE")
        self.assertIn("swing", result["reason"])


class TestRegimeConfidence(unittest.TestCase):
    """Tests for confidence scoring."""
    
    def setUp(self):
        self.detector = RegimeDetector()
    
    def test_confidence_range(self):
        """Confidence should always be between 0.0 and 1.0."""
        test_cases = [
            {"adx_4h": 50.0, "atr_pct_4h": 3.0, "bb_width_4h": 10.0},
            {"adx_4h": 5.0, "atr_pct_4h": 0.1, "bb_width_4h": 1.0},
            {"adx_4h": 25.0, "atr_pct_4h": 1.0, "bb_width_4h": 4.0},
        ]
        
        for indicators in test_cases:
            confidence = self.detector.get_regime_confidence(indicators)
            self.assertGreaterEqual(confidence, 0.0)
            self.assertLessEqual(confidence, 1.0)
    
    def test_strong_trend_high_confidence(self):
        """Strong indicators should yield higher confidence."""
        indicators = {
            "adx_4h": 45.0,
            "atr_pct_4h": 2.5,
            "bb_width_4h": 8.0
        }
        
        confidence = self.detector.get_regime_confidence(
            indicators, 
            regime=RegimeType.STRONG_TREND
        )
        self.assertGreater(confidence, 0.7)


class TestTimeframeAlignment(unittest.TestCase):
    """Tests for timeframe alignment checking."""
    
    def setUp(self):
        self.detector = RegimeDetector()
    
    def test_all_trend_alignment(self):
        """Should show TREND across all timeframes when ADX > 25."""
        snapshot = {
            "tf": {
                "1d": {"adx": 30.0},
                "4h": {"adx": 28.0},
                "1h": {"adx": 26.0}
            }
        }
        
        alignment = self.detector._check_alignment_across_timeframes(snapshot)
        
        self.assertEqual(alignment.get("1d"), "TREND")
        self.assertEqual(alignment.get("4h"), "TREND")
        self.assertEqual(alignment.get("1h"), "TREND")
    
    def test_mixed_alignment(self):
        """Should show RANGE for low ADX timeframes."""
        snapshot = {
            "tf": {
                "1d": {"adx": 30.0},
                "4h": {"adx": 22.0},
                "1h": {"adx": 15.0}
            }
        }
        
        alignment = self.detector._check_alignment_across_timeframes(snapshot)
        
        self.assertEqual(alignment.get("1d"), "TREND")
        self.assertEqual(alignment.get("4h"), "WEAK_TREND")
        self.assertEqual(alignment.get("1h"), "RANGE")


class TestCaching(unittest.TestCase):
    """Tests for cache behavior."""
    
    def test_cache_hit(self):
        """Should return cached result on second call."""
        detector = RegimeDetector(cache_ttl=60.0)
        
        snapshot = {
            "symbol": "BTCUSDT",
            "price": 50000.0,
            "tf": {"4h": {"adx": 35.0, "atr": 1000.0}}
        }
        
        # First call - populates cache
        result1 = detector.detect_regime("BTCUSDT", snapshot)
        
        # Second call - should use cache
        result2 = detector.detect_regime("BTCUSDT", snapshot)
        
        self.assertEqual(result1["regime"], result2["regime"])
        self.assertEqual(result1["confidence"], result2["confidence"])
    
    def test_cache_invalidation(self):
        """Should clear cache on invalidation."""
        detector = RegimeDetector(cache_ttl=60.0)
        
        snapshot = {
            "symbol": "BTCUSDT",
            "price": 50000.0,
            "tf": {"4h": {"adx": 35.0, "atr": 1000.0}}
        }
        
        # Populate cache
        detector.detect_regime("BTCUSDT", snapshot)
        
        # Invalidate
        detector.invalidate_cache("BTCUSDT")
        
        # Cache should be empty
        with detector._cache_lock:
            if "BTCUSDT" in detector._cache:
                cached = detector._cache["BTCUSDT"].get()
                self.assertIsNone(cached)


class TestBBWidthCalculation(unittest.TestCase):
    """Tests for Bollinger Band width calculation."""
    
    def setUp(self):
        self.detector = RegimeDetector()
    
    def test_bb_width_calculation(self):
        """Should correctly calculate BB width percentage."""
        snapshot = {
            "tf": {
                "4h": {
                    "bb_upper": 52500,
                    "bb_lower": 47500,
                    "bb_middle": 50000
                }
            }
        }
        
        bb_width = self.detector._calculate_bb_width(snapshot, "4h")
        # (52500 - 47500) / 50000 * 100 = 10%
        self.assertEqual(bb_width, 10.0)
    
    def test_bb_width_missing_data(self):
        """Should return 0 when BB data is missing."""
        snapshot = {"tf": {}}
        
        bb_width = self.detector._calculate_bb_width(snapshot, "4h")
        self.assertEqual(bb_width, 0.0)


class TestRegimeResult(unittest.TestCase):
    """Tests for RegimeResult dataclass."""
    
    def test_to_dict(self):
        """Should convert to dictionary correctly."""
        result = RegimeResult(
            regime=RegimeType.STRONG_TREND,
            confidence=0.85,
            timeframe_alignment={"1d": "TREND", "4h": "TREND", "1h": "TREND"},
            adx_4h=35.5,
            atr_pct_4h=2.1,
            bb_width_4h=8.5,
            trend_strength_score=0.78,
            reason="Test"
        )
        
        d = result.to_dict()
        
        self.assertEqual(d["regime"], "STRONG_TREND")
        self.assertEqual(d["confidence"], 0.85)
        self.assertEqual(d["adx_4h"], 35.5)
        self.assertEqual(d["trend_strength_score"], 0.78)


if __name__ == "__main__":
    unittest.main()
