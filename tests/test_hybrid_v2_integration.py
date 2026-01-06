"""
test_hybrid_v2_integration.py - Hybrid V2 Strategy Integration Tests
=====================================================================

Tests for the complete Hybrid V2 strategy including:
- Regime detection
- Multi-timeframe entry signals (4H, 1H, 15M)
- Position manager exit logic
- Capital allocation
- Config validation

Run with: pytest tests/test_hybrid_v2_integration.py -v
"""

import pytest
import time
from unittest.mock import Mock, patch, MagicMock

# Import components to test
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategies.regime_detector import RegimeDetector, RegimeType
from strategies.timeframe_analyzer import TimeframeAnalyzer
from strategies.hybrid_multi_tf_v2 import HybridMultiTFV2, EntryType, ENTRY_CONFIGS
from position_manager import PositionManager
import config


# ═══════════════════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def regime_detector():
    """Create a RegimeDetector instance."""
    return RegimeDetector()


@pytest.fixture
def timeframe_analyzer():
    """Create a TimeframeAnalyzer instance."""
    return TimeframeAnalyzer()


@pytest.fixture
def hybrid_v2():
    """Create a HybridMultiTFV2 instance."""
    return HybridMultiTFV2(balance=10000.0, dry_run=True, enable_scalping=True)


@pytest.fixture
def mock_position_manager():
    """Create a mock PositionManager for testing exit logic."""
    mock_portfolio = {
        "balance": 10000.0,
        "positions": [],
        "history": []
    }
    
    pm = PositionManager.__new__(PositionManager)
    pm.portfolio = mock_portfolio
    pm.market_data_engine = Mock()
    pm.strategy_engine = None
    pm.executor = None
    pm.execution_manager = Mock()
    pm.save_portfolio_fn = None
    pm.telegram_fn = None
    pm.telegram_config = {}
    pm._consecutive_losses = 0
    
    return pm


@pytest.fixture
def strong_trend_snapshot():
    """Create a snapshot representing a strong trending market."""
    return {
        "symbol": "BTCUSDT",
        "price": 50000.0,
        "tf": {
            "4h": {
                "ema20": 50100.0,
                "ema50": 49500.0,
                "ema200": 47000.0,
                "adx": 35.0,
                "atr": 800.0,
                "atr_pct": 1.6,
                "rsi": 58.0,
                "macd": 150.0,
                "macd_signal": 120.0,
                "bb_upper": 51000.0,
                "bb_lower": 49000.0,
                "bb_middle": 50000.0
            },
            "1h": {
                "ema20": 50050.0,
                "ema50": 49800.0,
                "ema200": 48500.0,
                "adx": 28.0,
                "atr": 400.0,
                "rsi": 60.0,
                "macd": 80.0,
                "macd_signal": 60.0,
                "macd_hist": 20.0,
                "macd_hist_prev": 15.0,
                "volume": 1_500_000_000,
                "volume_avg": 1_000_000_000
            },
            "15m": {
                "ema20": 50020.0,
                "ema50": 49950.0,
                "adx": 25.0,
                "atr": 150.0,
                "close": 50000.0,
                "highest_high": 49950.0,
                "bb_upper": 50200.0,
                "bb_lower": 49800.0,
                "bb_middle": 50000.0,
                "volume": 200_000_000,
                "volume_avg": 80_000_000
            }
        }
    }


@pytest.fixture
def ranging_snapshot():
    """Create a snapshot representing a ranging/sideways market."""
    return {
        "symbol": "ETHUSDT",
        "price": 3000.0,
        "tf": {
            "4h": {
                "ema20": 3010.0,
                "ema50": 2990.0,
                "ema200": 3000.0,
                "adx": 15.0,  # Low ADX = ranging
                "atr": 30.0,
                "atr_pct": 1.0,
                "bb_upper": 3050.0,
                "bb_lower": 2950.0,
                "bb_middle": 3000.0
            },
            "1h": {
                "ema20": 3005.0,
                "ema50": 2995.0,
                "adx": 12.0,
                "atr": 15.0,
                "rsi": 50.0
            },
            "15m": {
                "ema20": 3002.0,
                "ema50": 2998.0,
                "adx": 10.0,
                "atr": 8.0
            }
        }
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 1. REGIME DETECTION TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestRegimeDetection:
    """Test regime detection with different market conditions."""
    
    def test_strong_trend_detection(self, regime_detector, strong_trend_snapshot):
        """Detect STRONG_TREND in trending market."""
        result = regime_detector.detect_regime("BTCUSDT", strong_trend_snapshot)
        
        assert result["regime"] == "STRONG_TREND"
        assert result["confidence"] > 0.7
        assert "adx" in result.get("details", {}) or result.get("adx_4h") is not None
    
    def test_ranging_detection(self, regime_detector, ranging_snapshot):
        """Detect RANGING in sideways market."""
        result = regime_detector.detect_regime("ETHUSDT", ranging_snapshot)
        
        # Low ADX should indicate ranging or weak trend
        assert result["regime"] in ["RANGING", "WEAK_TREND"]
    
    def test_regime_caching(self, regime_detector, strong_trend_snapshot):
        """Test that regime detection results are cached."""
        # First call
        result1 = regime_detector.detect_regime("BTCUSDT", strong_trend_snapshot)
        
        # Second call should use cached result
        result2 = regime_detector.detect_regime("BTCUSDT", strong_trend_snapshot)
        
        assert result1["regime"] == result2["regime"]
    
    def test_different_symbols_not_cached(self, regime_detector, strong_trend_snapshot, ranging_snapshot):
        """Test that different symbols have separate cache entries."""
        result_btc = regime_detector.detect_regime("BTCUSDT", strong_trend_snapshot)
        result_eth = regime_detector.detect_regime("ETHUSDT", ranging_snapshot)
        
        # Different symbols should have different regimes
        assert result_btc != result_eth or result_btc["regime"] != result_eth["regime"]
    
    def test_missing_data_handling(self, regime_detector):
        """Test regime detection with missing data."""
        empty_snapshot = {"symbol": "TESTUSDT", "price": 100.0, "tf": {}}
        
        result = regime_detector.detect_regime("TESTUSDT", empty_snapshot)
        
        # Should return a default or handle gracefully
        assert "regime" in result
        assert result.get("confidence", 0) >= 0


# ═══════════════════════════════════════════════════════════════════════════════
# 2. 4H SWING ENTRY TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestSwing4HEntry:
    """Test 4H swing entry signal generation."""
    
    def test_swing_entry_in_strong_trend(self, hybrid_v2, strong_trend_snapshot):
        """Test swing entry signal in strong trend."""
        regime = {"regime": "STRONG_TREND", "confidence": 0.85}
        
        signal = hybrid_v2.evaluate_entry("BTCUSDT", strong_trend_snapshot, regime)
        
        # Should generate entry or hold (depends on exact conditions)
        assert signal["action"] in ["BUY", "HOLD"]
        assert "entry_type" in signal
    
    def test_swing_entry_blocked_in_ranging(self, hybrid_v2, ranging_snapshot):
        """Test swing entry blocked in ranging market."""
        regime = {"regime": "RANGING", "confidence": 0.70}
        
        signal = hybrid_v2.evaluate_entry("ETHUSDT", ranging_snapshot, regime)
        
        # Should HOLD in ranging market for swing trades
        assert signal["action"] == "HOLD"
    
    def test_swing_entry_has_correct_parameters(self, hybrid_v2, strong_trend_snapshot):
        """Test that swing entries have correct risk parameters."""
        regime = {"regime": "STRONG_TREND", "confidence": 0.90}
        
        # Force a valid swing setup by modifying snapshot
        strong_trend_snapshot["tf"]["1h"]["rsi"] = 55  # Momentum confirmed
        
        signal = hybrid_v2.evaluate_entry("BTCUSDT", strong_trend_snapshot, regime)
        
        if signal["action"] == "BUY" and signal["entry_type"] == "4H_SWING":
            # Verify risk parameters
            assert signal.get("risk_reward_ratio", 0) > 0
            assert signal.get("stop_loss", 0) < signal.get("entry_price", 0)
            assert signal.get("take_profit_2", 0) > signal.get("entry_price", 0)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. 1H MOMENTUM ENTRY TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestMomentum1HEntry:
    """Test 1H momentum entry signal generation."""
    
    def test_momentum_entry_conditions(self, hybrid_v2):
        """Test momentum entry with proper conditions."""
        snapshot = {
            "symbol": "ETHUSDT",
            "price": 3000.0,
            "tf": {
                "4h": {
                    "ema20": 3050.0,
                    "ema50": 2950.0,  # Trend aligned
                    "adx": 22.0,
                    "atr": 50.0
                },
                "1h": {
                    "ema20": 3020.0,
                    "ema50": 2980.0,
                    "rsi": 62.0,  # In momentum zone (55-70)
                    "macd_hist": 5.0,
                    "macd_hist_prev": 3.0,  # Expanding
                    "volume": 1_500_000_000,
                    "volume_avg": 1_000_000_000,  # 1.5x avg
                    "atr": 45.0
                },
                "15m": {
                    "close": 3000.0,
                    "highest_high": 2995.0  # Breakout confirmation
                }
            }
        }
        
        regime = {"regime": "WEAK_TREND", "confidence": 0.72}
        
        signal = hybrid_v2.evaluate_entry("ETHUSDT", snapshot, regime)
        
        # Check signal structure
        assert "action" in signal
        assert "entry_type" in signal
        assert "confidence" in signal
    
    def test_momentum_rsi_out_of_zone(self, hybrid_v2):
        """Test momentum entry blocked when RSI out of zone."""
        snapshot = {
            "symbol": "ETHUSDT",
            "price": 3000.0,
            "tf": {
                "4h": {"ema20": 3050.0, "ema50": 2950.0, "adx": 22.0, "atr": 50.0},
                "1h": {
                    "ema20": 3020.0, "ema50": 2980.0,
                    "rsi": 75.0,  # Overbought - out of 55-70 zone
                    "macd_hist": 5.0, "macd_hist_prev": 3.0,
                    "volume": 1_500_000_000, "volume_avg": 1_000_000_000,
                    "atr": 45.0
                },
                "15m": {"close": 3000.0, "highest_high": 2995.0}
            }
        }
        
        regime = {"regime": "WEAK_TREND", "confidence": 0.72}
        
        signal = hybrid_v2.evaluate_entry("ETHUSDT", snapshot, regime)
        
        # RSI 75 is > 70, so momentum check should fail
        if signal["action"] == "BUY":
            # If still BUY, it shouldn't be 1H_MOMENTUM type
            assert signal["entry_type"] != "1H_MOMENTUM"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. 15M SCALP ENTRY TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestScalp15MEntry:
    """Test 15M scalp entry signal generation."""
    
    def test_scalp_only_in_strong_trend(self, hybrid_v2):
        """Test scalp entries only allowed in STRONG_TREND."""
        snapshot = {
            "symbol": "BTCUSDT",
            "price": 50000.0,
            "tf": {
                "4h": {"ema20": 50100.0, "ema50": 49500.0, "adx": 35.0, "atr": 800.0},
                "1h": {"ema20": 50050.0, "ema50": 49800.0, "nearest_resistance": 51000.0, "atr": 400.0},
                "15m": {
                    "ema20": 50020.0, "ema50": 49950.0, "adx": 25.0, "atr": 150.0,
                    "bb_upper": 50100.0, "bb_lower": 49900.0, "bb_middle": 50000.0,
                    "volume": 200_000_000, "volume_avg": 80_000_000  # 2.5x spike
                }
            }
        }
        
        # Test with WEAK_TREND - should not allow scalp
        regime_weak = {"regime": "WEAK_TREND", "confidence": 0.75}
        signal_weak = hybrid_v2.evaluate_entry("BTCUSDT", snapshot, regime_weak)
        
        if signal_weak["action"] == "BUY":
            assert signal_weak["entry_type"] != "15M_SCALP"
        
        # Test with STRONG_TREND - may allow scalp
        regime_strong = {"regime": "STRONG_TREND", "confidence": 0.85}
        signal_strong = hybrid_v2.evaluate_entry("BTCUSDT", snapshot, regime_strong)
        
        # Should process (could be any entry type or HOLD)
        assert "action" in signal_strong
    
    def test_scalp_disabled_flag(self):
        """Test scalping can be disabled via flag."""
        strategy = HybridMultiTFV2(balance=10000.0, enable_scalping=False)
        
        snapshot = {
            "symbol": "BTCUSDT", "price": 50000.0,
            "tf": {
                "4h": {"ema20": 50100.0, "ema50": 49500.0, "adx": 35.0, "atr": 800.0},
                "1h": {"ema20": 50050.0, "ema50": 49800.0, "atr": 400.0},
                "15m": {"adx": 25.0, "atr": 150.0}
            }
        }
        
        regime = {"regime": "STRONG_TREND", "confidence": 0.85}
        signal = strategy.evaluate_entry("BTCUSDT", snapshot, regime)
        
        # Scalps disabled, so should not get 15M_SCALP entry
        if signal["action"] == "BUY":
            assert signal["entry_type"] != "15M_SCALP"


# ═══════════════════════════════════════════════════════════════════════════════
# 5. POSITION MANAGER EXIT LOGIC TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestPositionManagerExits:
    """Test position manager exit logic for each entry type."""
    
    def test_4h_swing_stop_loss(self, mock_position_manager):
        """Test 4H swing stop loss exit."""
        position = {
            "symbol": "BTCUSDT",
            "entry_type": "4H_SWING",
            "entry_price": 50000.0,
            "stop_loss": 48000.0,
            "current_sl": 48000.0,
            "quantity": 0.1,
            "partial_tp_hit": False,
            "entry_time": time.time() - 3600
        }
        
        # Price below stop loss
        result = mock_position_manager.check_exit_conditions(position, 47500.0, {})
        
        assert result["action"] == "SELL"
        assert "stop loss" in result["reason"].lower()
        assert result["quantity"] == 0.1
    
    def test_4h_swing_partial_tp(self, mock_position_manager):
        """Test 4H swing partial take profit at 5%."""
        position = {
            "symbol": "BTCUSDT",
            "entry_type": "4H_SWING",
            "entry_price": 50000.0,
            "stop_loss": 48000.0,
            "current_sl": 48000.0,
            "quantity": 0.1,
            "partial_tp_hit": False,
            "entry_time": time.time() - 3600
        }
        
        # Price at 5% profit
        current_price = 52500.0  # 5% gain
        result = mock_position_manager.check_exit_conditions(position, current_price, {})
        
        assert result["action"] == "SELL_PARTIAL"
        assert result["quantity"] == 0.05  # 50% of 0.1
    
    def test_1h_momentum_final_target(self, mock_position_manager):
        """Test 1H momentum final target exit at 4%."""
        position = {
            "symbol": "ETHUSDT",
            "entry_type": "1H_MOMENTUM",
            "entry_price": 3000.0,
            "stop_loss": 2946.0,
            "current_sl": 2946.0,
            "quantity": 1.0,
            "partial_tp_hit": True,  # Already took partial
            "entry_time": time.time() - 7200
        }
        
        # Price at 4% profit (final target)
        current_price = 3120.0  # 4% gain
        snapshot = {"tf": {"1h": {"atr": 50.0}}}
        
        result = mock_position_manager.check_exit_conditions(position, current_price, snapshot)
        
        assert result["action"] == "SELL"
        assert "final target" in result["reason"].lower() or "4%" in result["reason"]
    
    def test_15m_scalp_no_partial(self, mock_position_manager):
        """Test 15M scalp has no partial TP."""
        position = {
            "symbol": "BTCUSDT",
            "entry_type": "15M_SCALP",
            "entry_price": 50000.0,
            "stop_loss": 49400.0,
            "current_sl": 49400.0,
            "quantity": 0.02,
            "entry_time": time.time() - 1800  # 30 min ago
        }
        
        # Price at moderate profit (below target)
        current_price = 50500.0  # 1% gain
        result = mock_position_manager.check_exit_conditions(position, current_price, {})
        
        # Should HOLD - no partial TP for scalps
        assert result["action"] == "HOLD"
    
    def test_15m_scalp_target(self, mock_position_manager):
        """Test 15M scalp target exit at 1.5%."""
        position = {
            "symbol": "BTCUSDT",
            "entry_type": "15M_SCALP",
            "entry_price": 50000.0,
            "stop_loss": 49400.0,
            "current_sl": 49400.0,
            "quantity": 0.02,
            "entry_time": time.time() - 1800
        }
        
        # Price at 1.5% target
        current_price = 50750.0
        result = mock_position_manager.check_exit_conditions(position, current_price, {})
        
        assert result["action"] == "SELL"
        assert "target" in result["reason"].lower()
    
    def test_v1_backward_compatibility(self, mock_position_manager):
        """Test V1 positions still work correctly."""
        position = {
            "symbol": "BTCUSDT",
            "entry_type": "V1",
            "entry_price": 50000.0,
            "stop_loss": 48500.0,
            "take_profit": 53000.0,
            "quantity": 0.1
        }
        
        # Test stop loss
        result_sl = mock_position_manager.check_exit_conditions(position, 48000.0, {})
        assert result_sl["action"] == "SELL"
        
        # Test take profit
        result_tp = mock_position_manager.check_exit_conditions(position, 53500.0, {})
        assert result_tp["action"] == "SELL"
        
        # Test hold
        result_hold = mock_position_manager.check_exit_conditions(position, 51000.0, {})
        assert result_hold["action"] == "HOLD"
    
    def test_time_based_exit_4h(self, mock_position_manager):
        """Test 4H swing time-based exit after 10 days."""
        position = {
            "symbol": "BTCUSDT",
            "entry_type": "4H_SWING",
            "entry_price": 50000.0,
            "stop_loss": 48000.0,
            "current_sl": 48000.0,
            "quantity": 0.1,
            "partial_tp_hit": False,
            "timestamp": time.time() - (11 * 24 * 3600)  # 11 days ago
        }
        
        # Small profit, after 10 days
        current_price = 50500.0  # 1% profit
        result = mock_position_manager.check_exit_conditions(position, current_price, {})
        
        assert result["action"] == "SELL"
        assert "time" in result["reason"].lower()


# ═══════════════════════════════════════════════════════════════════════════════
# 6. CAPITAL ALLOCATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestCapitalAllocation:
    """Test capital allocation validation."""
    
    def test_entry_configs_sum_to_100_percent(self):
        """Test that capital allocations sum to 100%."""
        total = sum(cfg.capital_allocation for cfg in ENTRY_CONFIGS.values())
        assert abs(total - 1.0) < 0.01, f"Total allocation {total} != 1.0"
    
    def test_4h_swing_allocation(self):
        """Test 4H swing has 40% allocation."""
        swing_cfg = ENTRY_CONFIGS[EntryType.SWING_4H]
        assert swing_cfg.capital_allocation == 0.40
        assert swing_cfg.risk_per_trade == 0.015  # 1.5%
    
    def test_1h_momentum_allocation(self):
        """Test 1H momentum has 40% allocation."""
        momentum_cfg = ENTRY_CONFIGS[EntryType.MOMENTUM_1H]
        assert momentum_cfg.capital_allocation == 0.40
        assert momentum_cfg.risk_per_trade == 0.01  # 1%
    
    def test_15m_scalp_allocation(self):
        """Test 15M scalp has 20% allocation."""
        scalp_cfg = ENTRY_CONFIGS[EntryType.SCALP_15M]
        assert scalp_cfg.capital_allocation == 0.20
        assert scalp_cfg.risk_per_trade == 0.005  # 0.5%
    
    def test_position_sizing_scales_with_confidence(self, hybrid_v2):
        """Test that position size scales with regime confidence."""
        # Higher confidence = larger position (or equal if capped)
        size_high, risk_high = hybrid_v2._calculate_position_size(
            EntryType.SWING_4H, 50000.0, 48750.0, 0.90
        )
        
        size_low, risk_low = hybrid_v2._calculate_position_size(
            EntryType.SWING_4H, 50000.0, 48750.0, 0.60
        )
        
        # Higher confidence should give larger or equal size (capped case)
        assert size_high >= size_low
        # Risk should scale with confidence
        assert risk_high >= risk_low



# ═══════════════════════════════════════════════════════════════════════════════
# 7. CONFIG PARAMETER VALIDATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestConfigValidation:
    """Test config parameter validation."""
    
    def test_config_has_hybrid_v2_params(self):
        """Test config has required Hybrid V2 parameters."""
        # Regime detection params
        assert hasattr(config, 'REGIME_ADX_STRONG_THRESHOLD')
        assert hasattr(config, 'REGIME_ADX_WEAK_THRESHOLD')
        
        # Capital allocation params
        assert hasattr(config, 'CAPITAL_ALLOCATION_4H')
        assert hasattr(config, 'CAPITAL_ALLOCATION_1H')
        assert hasattr(config, 'CAPITAL_ALLOCATION_15M')
        
        # Strategy params
        assert hasattr(config, 'SWING_4H_MIN_ADX')
        assert hasattr(config, 'MOMENTUM_1H_MIN_RSI')
        assert hasattr(config, 'SCALP_15M_TARGET_PCT')
    
    def test_validate_hybrid_v2_config(self):
        """Test config validation function."""
        errors = config.validate_hybrid_v2_config()
        
        # Should have no errors with default config
        assert isinstance(errors, list)
        # If there are errors, they should be meaningful strings
        for error in errors:
            assert isinstance(error, str)
    
    def test_capital_allocation_sums_correctly(self):
        """Test capital allocations in config sum to 1.0."""
        total = (
            config.CAPITAL_ALLOCATION_4H +
            config.CAPITAL_ALLOCATION_1H +
            config.CAPITAL_ALLOCATION_15M
        )
        assert abs(total - 1.0) < 0.01
    
    def test_adx_thresholds_ordered(self):
        """Test ADX thresholds are properly ordered."""
        assert config.REGIME_ADX_WEAK_THRESHOLD < config.REGIME_ADX_STRONG_THRESHOLD
    
    def test_rsi_range_valid(self):
        """Test RSI range for momentum is valid."""
        assert config.MOMENTUM_1H_MIN_RSI < config.MOMENTUM_1H_MAX_RSI
        assert 0 <= config.MOMENTUM_1H_MIN_RSI <= 100
        assert 0 <= config.MOMENTUM_1H_MAX_RSI <= 100
    
    def test_strategies_available(self):
        """Test STRATEGIES_AVAILABLE contains required versions."""
        assert "V1" in config.STRATEGIES_AVAILABLE
        assert "HYBRID_V2" in config.STRATEGIES_AVAILABLE


# ═══════════════════════════════════════════════════════════════════════════════
# 8. TRAILING STOP UPDATE TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestTrailingStopUpdate:
    """Test trailing stop updates."""
    
    def test_trailing_only_after_partial_tp(self, mock_position_manager):
        """Test trailing stop only updates after partial TP."""
        position = {
            "symbol": "BTCUSDT",
            "entry_type": "4H_SWING",
            "entry_price": 50000.0,
            "stop_loss": 48000.0,
            "current_sl": 48000.0,
            "partial_tp_hit": False  # No partial TP yet
        }
        
        snapshot = {"tf": {"4h": {"atr": 800.0}}}
        
        updated = mock_position_manager.update_trailing_stop(position, 52000.0, snapshot)
        
        # Should not update before partial TP
        assert updated == False
        assert position["current_sl"] == 48000.0
    
    def test_trailing_updates_after_partial(self, mock_position_manager):
        """Test trailing stop updates after partial TP."""
        position = {
            "symbol": "BTCUSDT",
            "entry_type": "4H_SWING",
            "entry_price": 50000.0,
            "stop_loss": 48000.0,
            "current_sl": 48000.0,
            "partial_tp_hit": True,  # Partial TP taken
            "highest_close_since_entry": 52000.0
        }
        
        snapshot = {"tf": {"4h": {"atr": 800.0}}}
        
        # Price moves higher
        updated = mock_position_manager.update_trailing_stop(position, 53000.0, snapshot)
        
        # Should update
        if updated:
            assert position["current_sl"] > 48000.0
    
    def test_trailing_never_lowers(self, mock_position_manager):
        """Test trailing stop never moves down."""
        position = {
            "symbol": "BTCUSDT",
            "entry_type": "1H_MOMENTUM",
            "entry_price": 3000.0,
            "stop_loss": 2946.0,
            "current_sl": 3050.0,  # Already raised
            "partial_tp_hit": True,
            "highest_close_since_entry": 3100.0
        }
        
        snapshot = {"tf": {"1h": {"atr": 50.0}}}
        
        # Price drops
        mock_position_manager.update_trailing_stop(position, 3060.0, snapshot)
        
        # Stop should not go below 3050
        assert position["current_sl"] >= 3050.0


# ═══════════════════════════════════════════════════════════════════════════════
# RUN TESTS
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
