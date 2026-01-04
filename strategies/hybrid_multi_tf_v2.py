"""
hybrid_multi_tf_v2.py - Hybrid Multi-Timeframe Strategy V2
============================================================

Main strategy file combining regime detection and multi-timeframe analysis.

Strategy Philosophy:
- Position sizing scales with timeframe and regime confidence
- 4h trades: Larger size, longer hold (swing trades)
- 1h trades: Medium size, medium hold (momentum trades)
- 15m trades: Smaller size, short hold (scalp opportunities)

Entry Types:
- 4H_SWING: 40% capital, 1.5% risk, 3-10 day hold
- 1H_MOMENTUM: 40% capital, 1% risk, 4-24 hour hold
- 15M_SCALP: 20% capital, 0.5% risk, 15min-4 hour hold

Usage:
    from strategies.hybrid_multi_tf_v2 import HybridMultiTFV2
    
    strategy = HybridMultiTFV2()
    signal = strategy.evaluate_entry("BTCUSDT", snapshot, regime)
    if signal["action"] == "BUY":
        execute_trade(signal)
"""

import time
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timezone

# Logger import
try:
    from trade_logger import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)

# Config import
try:
    import config
    from config import SETTINGS
except ImportError:
    class MockSettings:
        SL_ATR_MULT = 1.5
        RISK_PER_TRADE_V1 = 1.0
        USE_NEWS_LLM_VETO = False  # No longer used
    SETTINGS = MockSettings()

# Import regime detector and timeframe analyzer
try:
    from strategies.regime_detector import RegimeDetector, RegimeType
except ImportError:
    RegimeDetector = None
    RegimeType = None

try:
    from strategies.timeframe_analyzer import TimeframeAnalyzer
except ImportError:
    TimeframeAnalyzer = None


# NewsVeto removed - no longer used



# ═══════════════════════════════════════════════════════════════════════════════
# ENUMS & DATACLASSES
# ═══════════════════════════════════════════════════════════════════════════════

class EntryType(Enum):
    """Entry type classification."""
    SWING_4H = "4H_SWING"
    MOMENTUM_1H = "1H_MOMENTUM"
    SCALP_15M = "15M_SCALP"
    NONE = "NONE"


@dataclass
class EntryConfig:
    """Configuration for each entry type."""
    capital_allocation: float  # % of total capital
    risk_per_trade: float      # % risk per trade
    atr_mult_stop: float       # ATR multiplier for stop loss
    target_gain_min: float     # Minimum target gain %
    target_gain_max: float     # Maximum target gain %
    partial_tp_pct: float      # % gain for partial TP
    partial_tp_fraction: float # Fraction to sell at partial
    hold_hours_min: int        # Minimum hold time
    hold_hours_max: int        # Maximum hold time


# Entry type configurations
# NOTE: 15M scalp disabled by default (15min loop too slow for scalping)
# Capital redistributed: 50% 4H swing, 50% 1H momentum, 0% 15M scalp
ENTRY_CONFIGS = {
    EntryType.SWING_4H: EntryConfig(
        capital_allocation=0.50,   # 50% (was 40%)
        risk_per_trade=0.015,      # 1.5%
        atr_mult_stop=2.5,
        target_gain_min=8.0,
        target_gain_max=12.0,
        partial_tp_pct=5.0,
        partial_tp_fraction=0.5,
        hold_hours_min=72,         # 3 days
        hold_hours_max=240         # 10 days
    ),
    EntryType.MOMENTUM_1H: EntryConfig(
        capital_allocation=0.50,   # 50% (was 40%)
        risk_per_trade=0.01,       # 1%
        atr_mult_stop=1.8,
        target_gain_min=3.0,
        target_gain_max=5.0,
        partial_tp_pct=2.0,
        partial_tp_fraction=0.5,
        hold_hours_min=4,
        hold_hours_max=24
    ),
    EntryType.SCALP_15M: EntryConfig(
        capital_allocation=0.00,   # 0% disabled (was 20%)
        risk_per_trade=0.005,      # 0.5%
        atr_mult_stop=1.2,
        target_gain_min=1.0,
        target_gain_max=2.0,
        partial_tp_pct=0.0,        # No partial TP
        partial_tp_fraction=0.0,
        hold_hours_min=0.25,       # 15 min
        hold_hours_max=4
    )
}


# ═══════════════════════════════════════════════════════════════════════════════
# HYBRID MULTI-TF STRATEGY V2
# ═══════════════════════════════════════════════════════════════════════════════

class HybridMultiTFV2:
    """
    Hybrid Multi-Timeframe Strategy V2.
    
    Combines regime detection and multi-timeframe analysis for
    adaptive position sizing and entry selection.
    """
    
    # Valid regimes for each entry type
    VALID_REGIMES = {
        EntryType.SWING_4H: ["STRONG_TREND", "WEAK_TREND"],
        EntryType.MOMENTUM_1H: ["STRONG_TREND", "WEAK_TREND", "VOLATILE"],
        EntryType.SCALP_15M: ["STRONG_TREND"]
    }
    
    # High liquidity hours (UTC) for scalping
    HIGH_LIQUIDITY_HOURS = list(range(8, 21))  # 8am-8pm UTC
    
    def __init__(
        self,
        balance: float = 10000.0,
        dry_run: bool = False,
        enable_scalping: bool = None,  # None = read from config
        liquidity_filter: bool = True
    ):
        """
        Initialize HybridMultiTFV2 strategy.
        
        Args:
            balance: Account balance for position sizing
            dry_run: If True, log decisions without executing
            enable_scalping: If True, allow 15m scalp entries (default: from config)
            liquidity_filter: If True, only scalp during high liquidity hours
        """
        self.balance = balance
        self.dry_run = dry_run
        
        # Read scalping enabled from config if not explicitly set
        # Default False - 15min loop is too slow for effective scalping
        if enable_scalping is None:
            try:
                import config
                self.enable_scalping = getattr(config, 'SCALP_15M_ENABLED', False)
            except ImportError:
                self.enable_scalping = False
        else:
            self.enable_scalping = enable_scalping
        
        self.liquidity_filter = liquidity_filter
        
        # Initialize components
        self.regime_detector = RegimeDetector() if RegimeDetector else None
        self.tf_analyzer = TimeframeAnalyzer() if TimeframeAnalyzer else None
        # NewsVeto removed
        
        # Validate configuration
        self._validate_config()
        
        # Track last signals for idempotency
        self._last_signals: Dict[str, str] = {}
        
        # Log allocation and scalping status
        logger.info(
            f"[HYBRID V2] Initialized | balance=${balance:.2f} | "
            f"dry_run={dry_run} | scalping={self.enable_scalping}"
        )
        logger.info(
            f"[HYBRID V2] Capital: 4H_SWING={ENTRY_CONFIGS[EntryType.SWING_4H].capital_allocation*100:.0f}% | "
            f"1H_MOM={ENTRY_CONFIGS[EntryType.MOMENTUM_1H].capital_allocation*100:.0f}% | "
            f"15M_SCALP={ENTRY_CONFIGS[EntryType.SCALP_15M].capital_allocation*100:.0f}%"
        )
    
    def _validate_config(self) -> None:
        """Validate strategy configuration."""
        total_allocation = sum(cfg.capital_allocation for cfg in ENTRY_CONFIGS.values())
        if abs(total_allocation - 1.0) > 0.01:
            logger.warning(f"[HYBRID V2] Capital allocation != 100%: {total_allocation*100:.1f}%")
    
    def evaluate_entry(
        self,
        symbol: str,
        snapshot: Dict[str, Any],
        regime: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Evaluate entry opportunity across all timeframes.
        
        Args:
            symbol: Trading symbol
            snapshot: Market snapshot with multi-timeframe data
            regime: Pre-computed regime (optional, will detect if not provided)
        
        Returns:
            Entry signal dict
        """
        symbol = symbol.upper()
        price = snapshot.get("price", 0.0)
        
        # Validate inputs
        if not price or price <= 0:
            return self._hold_signal(symbol, "Price data missing")
        
        # Get regime if not provided
        if regime is None and self.regime_detector:
            regime = self.regime_detector.detect_regime(symbol, snapshot)
        
        if not regime:
            return self._hold_signal(symbol, "Regime detection failed")
        
        regime_type = regime.get("regime", "UNKNOWN")
        regime_confidence = regime.get("confidence", 0.0)
        
        logger.debug(
            f"[HYBRID V2] {symbol}: Evaluating | regime={regime_type} | "
            f"conf={regime_confidence:.2f}"
        )
        
        # Analyze all timeframes
        tf_scores = {}
        for tf in ["4h", "1h", "15m"]:
            if self.tf_analyzer:
                analysis = self.tf_analyzer.analyze_timeframe(symbol, tf, snapshot)
                tf_scores[tf] = {
                    "trend": analysis.get("trend_score", 0.0),
                    "momentum": analysis.get("momentum_score", 0.0),
                    "volatility": analysis.get("volatility_score", 0.0),
                    "combined": (
                        analysis.get("trend_score", 0.0) * 0.4 +
                        analysis.get("momentum_score", 0.0) * 0.4 +
                        analysis.get("volatility_score", 0.0) * 0.2
                    )
                }
            else:
                tf_scores[tf] = {"trend": 0.5, "momentum": 0.5, "volatility": 0.5, "combined": 0.5}
        
        # News veto removed - proceed directly to entry checks
        
        # Priority order: 4H Swing > 1H Momentum > 15M Scalp
        # Check 4H Swing Setup
        swing_signal = self._check_4h_swing_setup(symbol, snapshot, regime, tf_scores)
        if swing_signal.get("valid"):
            return self._build_entry_signal(
                symbol, snapshot, regime, tf_scores,
                EntryType.SWING_4H, swing_signal
            )
        
        # Check 1H Momentum Setup
        momentum_signal = self._check_1h_momentum_setup(symbol, snapshot, regime, tf_scores)
        if momentum_signal.get("valid"):
            return self._build_entry_signal(
                symbol, snapshot, regime, tf_scores,
                EntryType.MOMENTUM_1H, momentum_signal
            )
        
        # Check 15M Scalp Setup (if enabled)
        # 15M scalp check (only if enabled in config)
        if getattr(config, 'SCALP_15M_ENABLED', True):
            scalp_signal = self._check_15m_scalp_setup(symbol, snapshot, regime, tf_scores)
            if scalp_signal.get("valid"):
                # logger.info(f"[{symbol}] Scalp signal generated") # Optional: user added this, can keep it if desired but it's redundant with _build_entry_signal logs
                return self._build_entry_signal(
                    symbol, snapshot, regime, tf_scores,
                    EntryType.SCALP_15M, scalp_signal
                )
        
        return self._hold_signal(symbol, "No valid setup found", regime=regime, tf_scores=tf_scores)
    
    def _check_4h_swing_setup(
        self,
        symbol: str,
        snapshot: Dict[str, Any],
        regime: Dict[str, Any],
        tf_scores: Dict[str, Dict]
    ) -> Dict[str, Any]:
        """
        Check for 4H swing entry setup.
        
        Conditions:
        1. Regime = STRONG_TREND or WEAK_TREND
        2. Weekly EMA50 > EMA200 (higher TF confirmation)
        3. 4h: EMA20 > EMA50 > EMA200
        4. 4h: ADX > 25
        5. 1h pullback to 4h EMA20 (±2%)
        6. 1h momentum turn (RSI > 50 or MACD crossover)
        """
        result = {"valid": False, "reason": "", "confidence": 0.0}
        
        # 1. Regime check
        regime_type = regime.get("regime", "UNKNOWN")
        if regime_type not in self.VALID_REGIMES[EntryType.SWING_4H]:
            result["reason"] = f"Regime {regime_type} not valid for 4H swing"
            logger.debug(f"[4H SWING] {symbol}: {result['reason']}")
            return result
        
        tf_4h = snapshot.get("tf", {}).get("4h", {})
        tf_1h = snapshot.get("tf", {}).get("1h", {})
        price = snapshot.get("price", 0.0)
        
        # 2. Weekly confirmation (simplified - check if available)
        # Skip if not available, just log
        tf_weekly = snapshot.get("tf", {}).get("1w", {})
        weekly_ema50 = tf_weekly.get("ema50", 0)
        weekly_ema200 = tf_weekly.get("ema200", 0)
        weekly_aligned = True
        if weekly_ema50 and weekly_ema200:
            weekly_aligned = weekly_ema50 > weekly_ema200
            if not weekly_aligned:
                result["reason"] = "Weekly EMA50 < EMA200"
                logger.debug(f"[4H SWING] {symbol}: {result['reason']}")
                return result
        
        # 3. 4h EMA alignment: EMA20 > EMA50 > EMA200
        ema20_4h = tf_4h.get("ema20", 0)
        ema50_4h = tf_4h.get("ema50", 0)
        ema200_4h = tf_4h.get("ema200", 0)
        
        if ema20_4h and ema50_4h:
            if not (ema20_4h > ema50_4h):
                result["reason"] = f"4h EMA20({ema20_4h:.2f}) <= EMA50({ema50_4h:.2f})"
                logger.debug(f"[4H SWING] {symbol}: {result['reason']}")
                return result
            
            if ema200_4h and ema50_4h <= ema200_4h:
                result["reason"] = f"4h EMA50({ema50_4h:.2f}) <= EMA200({ema200_4h:.2f})"
                logger.debug(f"[4H SWING] {symbol}: {result['reason']}")
                return result
        else:
            result["reason"] = "4h EMA data missing"
            return result
        
        # 4. 4h ADX > 25
        adx_4h = tf_4h.get("adx", 0)
        if not adx_4h or adx_4h < 25:
            result["reason"] = f"4h ADX({adx_4h:.1f}) < 25"
            logger.debug(f"[4H SWING] {symbol}: {result['reason']}")
            return result
        
        # 5. 1h pullback to 4h EMA20 (±2%)
        if price and ema20_4h:
            distance_pct = abs(price - ema20_4h) / ema20_4h * 100
            if distance_pct > 2.0:
                result["reason"] = f"Price {distance_pct:.1f}% from 4h EMA20 (need ≤2%)"
                logger.debug(f"[4H SWING] {symbol}: {result['reason']}")
                return result
        
        # 6. 1h momentum turn (RSI > 50 or MACD crossover)
        rsi_1h = tf_1h.get("rsi", 50)
        macd_1h = tf_1h.get("macd", 0)
        macd_signal_1h = tf_1h.get("macd_signal", 0)
        
        momentum_confirmed = False
        if rsi_1h and rsi_1h > 50:
            momentum_confirmed = True
        elif macd_1h and macd_signal_1h and macd_1h > macd_signal_1h:
            momentum_confirmed = True
        
        if not momentum_confirmed:
            result["reason"] = f"1h momentum not confirmed (RSI={rsi_1h:.1f})"
            logger.debug(f"[4H SWING] {symbol}: {result['reason']}")
            return result
        
        # All conditions met
        confidence = self._calculate_setup_confidence(
            regime.get("confidence", 0.5),
            tf_scores.get("4h", {}).get("combined", 0.5),
            adx_4h / 50.0  # Normalize ADX contribution
        )
        
        result.update({
            "valid": True,
            "confidence": confidence,
            "reason": "4h swing: trend aligned, pullback complete, momentum turning",
            "adx_4h": adx_4h,
            "ema20_4h": ema20_4h,
            "rsi_1h": rsi_1h
        })
        
        logger.info(
            f"[4H SWING] {symbol}: ✅ Setup valid | "
            f"ADX={adx_4h:.1f} | EMA20={ema20_4h:.2f} | RSI_1h={rsi_1h:.1f} | "
            f"conf={confidence:.2f}"
        )
        
        return result
    
    def _check_1h_momentum_setup(
        self,
        symbol: str,
        snapshot: Dict[str, Any],
        regime: Dict[str, Any],
        tf_scores: Dict[str, Dict]
    ) -> Dict[str, Any]:
        """
        Check for 1H momentum entry setup.
        
        Conditions:
        1. Regime = STRONG_TREND, WEAK_TREND, or VOLATILE
        2. 4h trend aligned (EMA20 > EMA50)
        3. 1h: Strong momentum (RSI 55-70)
        4. 1h: MACD histogram expanding
        5. 1h: Volume > 1.2× average
        6. 15m confirmation (breakout or consolidation break)
        """
        result = {"valid": False, "reason": "", "confidence": 0.0}
        
        # 1. Regime check
        regime_type = regime.get("regime", "UNKNOWN")
        if regime_type not in self.VALID_REGIMES[EntryType.MOMENTUM_1H]:
            result["reason"] = f"Regime {regime_type} not valid for 1H momentum"
            return result
        
        tf_4h = snapshot.get("tf", {}).get("4h", {})
        tf_1h = snapshot.get("tf", {}).get("1h", {})
        tf_15m = snapshot.get("tf", {}).get("15m", {})
        
        # 2. 4h trend aligned
        ema20_4h = tf_4h.get("ema20", 0)
        ema50_4h = tf_4h.get("ema50", 0)
        
        if ema20_4h and ema50_4h:
            if not (ema20_4h > ema50_4h):
                result["reason"] = "4h trend not aligned"
                logger.debug(f"[1H MOM] {symbol}: {result['reason']}")
                return result
        
        # 3. 1h RSI 55-70 (strong momentum zone)
        rsi_1h = tf_1h.get("rsi", 50)
        if not rsi_1h or not (55 <= rsi_1h <= 70):
            result["reason"] = f"1h RSI({rsi_1h:.1f}) not in momentum zone (55-70)"
            logger.debug(f"[1H MOM] {symbol}: {result['reason']}")
            return result
        
        # 4. 1h MACD histogram expanding
        macd_hist = tf_1h.get("macd_hist", 0)
        macd_hist_prev = tf_1h.get("macd_hist_prev", 0)
        
        histogram_expanding = True
        if macd_hist and macd_hist_prev:
            histogram_expanding = macd_hist > macd_hist_prev
        
        if not histogram_expanding:
            result["reason"] = "1h MACD histogram not expanding"
            logger.debug(f"[1H MOM] {symbol}: {result['reason']}")
            return result
        
        # 5. 1h Volume > 1.2× average
        volume_1h = tf_1h.get("volume", 0)
        volume_avg = tf_1h.get("volume_avg", snapshot.get("volume_avg", 0))
        
        if volume_1h and volume_avg and volume_avg > 0:
            if volume_1h < volume_avg * 1.2:
                result["reason"] = f"Volume({volume_1h/1e6:.1f}M) < 1.2× avg"
                logger.debug(f"[1H MOM] {symbol}: {result['reason']}")
                return result
        
        # 6. 15m confirmation (simplified: check for breakout structure)
        close_15m = tf_15m.get("close", 0)
        highest_high_15m = tf_15m.get("highest_high", 0)
        
        confirmation_15m = True
        if close_15m and highest_high_15m:
            # Close should be near or above recent high
            confirmation_15m = close_15m >= highest_high_15m * 0.995
        
        if not confirmation_15m:
            result["reason"] = "15m breakout confirmation missing"
            logger.debug(f"[1H MOM] {symbol}: {result['reason']}")
            return result
        
        # All conditions met
        confidence = self._calculate_setup_confidence(
            regime.get("confidence", 0.5),
            tf_scores.get("1h", {}).get("combined", 0.5),
            (rsi_1h - 50) / 30  # Normalize RSI contribution
        )
        
        result.update({
            "valid": True,
            "confidence": confidence,
            "reason": "1h momentum: trend aligned, strong RSI, MACD expanding, volume confirmed",
            "rsi_1h": rsi_1h,
            "macd_hist": macd_hist
        })
        
        logger.info(
            f"[1H MOM] {symbol}: ✅ Setup valid | "
            f"RSI={rsi_1h:.1f} | MACD_hist={macd_hist:.4f} | conf={confidence:.2f}"
        )
        
        return result
    
    def _check_15m_scalp_setup(
        self,
        symbol: str,
        snapshot: Dict[str, Any],
        regime: Dict[str, Any],
        tf_scores: Dict[str, Dict]
    ) -> Dict[str, Any]:
        """
        Check for 15M scalp entry setup.
        
        Conditions:
        1. Regime = STRONG_TREND only
        2. 4h and 1h trends strongly aligned
        3. 15m: Tight consolidation break (Bollinger squeeze)
        4. 15m: High volume spike (>2× average)
        5. 15m: ADX > 20
        6. Distance to 1h resistance > 1%
        7. High liquidity hours (optional)
        """
        # Check if scalping is enabled in config
        if not getattr(config, 'SCALP_15M_ENABLED', True):
            logger.debug(f"[{snapshot.get('symbol', symbol)}] 15M scalping disabled in config")
            return {
                "action": "HOLD",
                "valid": False, 
                "reason": "15M scalping disabled"
            }

        result = {"valid": False, "reason": "", "confidence": 0.0}
        
        # 1. Regime check - STRONG_TREND only
        regime_type = regime.get("regime", "UNKNOWN")
        if regime_type not in self.VALID_REGIMES[EntryType.SCALP_15M]:
            result["reason"] = f"Regime {regime_type} not valid for 15M scalp (need STRONG_TREND)"
            return result
        
        # 7. Liquidity hours check (if enabled)
        if self.liquidity_filter:
            current_hour = datetime.now(timezone.utc).hour
            if current_hour not in self.HIGH_LIQUIDITY_HOURS:
                result["reason"] = f"Outside high liquidity hours ({current_hour}:00 UTC)"
                logger.debug(f"[15M SCALP] {symbol}: {result['reason']}")
                return result
        
        tf_4h = snapshot.get("tf", {}).get("4h", {})
        tf_1h = snapshot.get("tf", {}).get("1h", {})
        tf_15m = snapshot.get("tf", {}).get("15m", {})
        price = snapshot.get("price", 0.0)
        
        # 2. 4h and 1h strongly aligned
        ema20_4h = tf_4h.get("ema20", 0)
        ema50_4h = tf_4h.get("ema50", 0)
        ema20_1h = tf_1h.get("ema20", 0)
        ema50_1h = tf_1h.get("ema50", 0)
        
        trends_aligned = True
        if ema20_4h and ema50_4h and ema20_1h and ema50_1h:
            trends_aligned = (ema20_4h > ema50_4h) and (ema20_1h > ema50_1h)
        
        if not trends_aligned:
            result["reason"] = "4h/1h trends not strongly aligned"
            logger.debug(f"[15M SCALP] {symbol}: {result['reason']}")
            return result
        
        # 3. 15m Bollinger squeeze detection
        bb_upper = tf_15m.get("bb_upper", 0)
        bb_lower = tf_15m.get("bb_lower", 0)
        bb_middle = tf_15m.get("bb_middle", 0)
        
        bb_squeeze = False
        if bb_upper and bb_lower and bb_middle and bb_middle > 0:
            bb_width_pct = ((bb_upper - bb_lower) / bb_middle) * 100
            bb_squeeze = bb_width_pct < 4.0  # Tight bands
        
        if not bb_squeeze:
            result["reason"] = "No 15m Bollinger squeeze detected"
            logger.debug(f"[15M SCALP] {symbol}: {result['reason']}")
            return result
        
        # 4. 15m volume spike > 2× average
        volume_15m = tf_15m.get("volume", 0)
        volume_avg_15m = tf_15m.get("volume_avg", 0)
        
        if volume_15m and volume_avg_15m and volume_avg_15m > 0:
            if volume_15m < volume_avg_15m * 2.0:
                result["reason"] = f"15m volume spike insufficient ({volume_15m/volume_avg_15m:.1f}× avg)"
                logger.debug(f"[15M SCALP] {symbol}: {result['reason']}")
                return result
        
        # 5. 15m ADX > 20
        adx_15m = tf_15m.get("adx", 0)
        if not adx_15m or adx_15m < 20:
            result["reason"] = f"15m ADX({adx_15m:.1f}) < 20"
            logger.debug(f"[15M SCALP] {symbol}: {result['reason']}")
            return result
        
        # 6. Distance to 1h resistance > 1%
        resistance_1h = tf_1h.get("nearest_resistance", 0)
        if price and resistance_1h and resistance_1h > price:
            distance_to_resistance = (resistance_1h - price) / price * 100
            if distance_to_resistance < 1.0:
                result["reason"] = f"Too close to 1h resistance ({distance_to_resistance:.2f}%)"
                logger.debug(f"[15M SCALP] {symbol}: {result['reason']}")
                return result
        
        # All conditions met
        confidence = self._calculate_setup_confidence(
            regime.get("confidence", 0.5),
            tf_scores.get("15m", {}).get("combined", 0.5),
            min(adx_15m / 40, 1.0)
        )
        
        result.update({
            "valid": True,
            "confidence": confidence,
            "reason": "15m scalp: BB squeeze break, volume spike, strong trend alignment",
            "adx_15m": adx_15m,
            "bb_squeeze": True
        })
        
        logger.info(
            f"[15M SCALP] {symbol}: ✅ Setup valid | "
            f"ADX={adx_15m:.1f} | BB_squeeze=True | conf={confidence:.2f}"
        )
        
        return result
    
    def _calculate_position_size(
        self,
        entry_type: EntryType,
        price: float,
        stop_loss: float,
        regime_confidence: float
    ) -> Tuple[float, float]:
        """
        Calculate position size based on entry type and regime confidence.
        
        Args:
            entry_type: Type of entry (4H_SWING, 1H_MOMENTUM, 15M_SCALP)
            price: Entry price
            stop_loss: Stop loss price
            regime_confidence: Regime detection confidence (0-1)
        
        Returns:
            Tuple of (quantity, risk_usd)
        """
        if price <= 0 or stop_loss >= price:
            return 0.0, 0.0
        
        config = ENTRY_CONFIGS[entry_type]
        
        # Calculate available capital for this entry type
        available_capital = self.balance * config.capital_allocation
        
        # Calculate risk amount (scaled by regime confidence)
        confidence_scale = 0.7 + (regime_confidence * 0.3)  # 70-100% of risk
        risk_amount = available_capital * config.risk_per_trade * confidence_scale
        
        # Calculate position size
        stop_distance = price - stop_loss
        quantity = risk_amount / stop_distance
        
        # Cap at max position size (10% of balance)
        max_position_value = self.balance * 0.10
        max_quantity = max_position_value / price
        quantity = min(quantity, max_quantity)
        
        return round(quantity, 8), round(risk_amount, 2)
    
    def _validate_entry_with_regime(
        self,
        entry_signal: Dict[str, Any],
        regime: Dict[str, Any]
    ) -> bool:
        """
        Validate entry signal against regime constraints.
        
        Args:
            entry_signal: Entry signal dict
            regime: Regime detection result
        
        Returns:
            True if entry is valid given regime
        """
        entry_type_str = entry_signal.get("entry_type", "")
        regime_type = regime.get("regime", "UNKNOWN")
        
        try:
            entry_type = EntryType(entry_type_str)
        except ValueError:
            return False
        
        valid_regimes = self.VALID_REGIMES.get(entry_type, [])
        return regime_type in valid_regimes
    
        # NewsVeto removed - always return False
        return False
    
    def _calculate_setup_confidence(self, *factors) -> float:
        """Calculate combined confidence from factors."""
        if not factors:
            return 0.5
        
        # Weighted average with diminishing returns
        total = 0.0
        for i, f in enumerate(factors):
            weight = 1.0 / (i + 1)  # Diminishing weights
            total += (f or 0.0) * weight
        
        return min(max(total / sum(1.0/(i+1) for i in range(len(factors))), 0.0), 1.0)
    
    def _build_entry_signal(
        self,
        symbol: str,
        snapshot: Dict[str, Any],
        regime: Dict[str, Any],
        tf_scores: Dict[str, Dict],
        entry_type: EntryType,
        setup: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Build complete entry signal."""
        price = snapshot.get("price", 0.0)
        config = ENTRY_CONFIGS[entry_type]
        
        # Get ATR for stop loss calculation
        tf_key = {"4H_SWING": "4h", "1H_MOMENTUM": "1h", "15M_SCALP": "15m"}.get(entry_type.value, "1h")
        tf_data = snapshot.get("tf", {}).get(tf_key, {})
        atr = tf_data.get("atr", price * 0.02)
        
        if not atr:
            atr = price * 0.02
        
        # Calculate stop loss
        stop_loss = price - (config.atr_mult_stop * atr)
        
        # Calculate targets
        take_profit_1 = price * (1 + config.partial_tp_pct / 100) if config.partial_tp_pct else 0
        take_profit_2 = price * (1 + config.target_gain_min / 100)
        
        # Calculate position size
        quantity, risk_usd = self._calculate_position_size(
            entry_type,
            price,
            stop_loss,
            regime.get("confidence", 0.5)
        )
        
        # Calculate R:R ratio
        stop_distance = price - stop_loss
        target_distance = take_profit_2 - price
        rr_ratio = target_distance / stop_distance if stop_distance > 0 else 0
        
        # Estimate hold time
        expected_hold_hours = (config.hold_hours_min + config.hold_hours_max) / 2
        
        signal = {
            "action": "BUY",
            "entry_type": entry_type.value,
            "confidence": round(setup.get("confidence", 0.7), 2),
            "reasoning": setup.get("reason", ""),
            "entry_price": round(price, 2),
            "stop_loss": round(stop_loss, 2),
            "take_profit_1": round(take_profit_1, 2) if take_profit_1 else None,
            "take_profit_2": round(take_profit_2, 2),
            # Explicit partial TP fields for V2 strategies (position_manager uses these)
            "partial_tp_target": round(take_profit_1, 2) if take_profit_1 else None,
            "partial_tp_percentage": config.partial_tp_fraction if config.partial_tp_pct > 0 else 0.0,
            "quantity": quantity,
            "risk_usd": risk_usd,
            "risk_reward_ratio": round(rr_ratio, 2),
            "expected_hold_hours": expected_hold_hours,
            "regime_aligned": True,
            "regime": regime.get("regime", "UNKNOWN"),
            "timeframe_scores": {
                tf: round(scores.get("combined", 0.0), 2)
                for tf, scores in tf_scores.items()
            },
            "symbol": symbol,
            "dry_run": self.dry_run
        }
        
        if self.dry_run:
            logger.info(f"[HYBRID V2 DRY RUN] {symbol}: Would BUY | {entry_type.value}")
        else:
            logger.info(
                f"[HYBRID V2 ENTRY] {symbol}: {entry_type.value} | "
                f"price={price:.2f} | SL={stop_loss:.2f} | TP={take_profit_2:.2f} | "
                f"qty={quantity:.6f} | risk=${risk_usd:.2f} | R:R={rr_ratio:.2f}"
            )
        
        return signal
    
    def _hold_signal(
        self,
        symbol: str,
        reason: str,
        regime: Dict[str, Any] = None,
        tf_scores: Dict[str, Dict] = None
    ) -> Dict[str, Any]:
        """Build HOLD signal."""
        return {
            "action": "HOLD",
            "entry_type": "NONE",
            "confidence": 0.0,
            "reasoning": reason,
            "entry_price": 0,
            "stop_loss": 0,
            "take_profit_1": None,
            "take_profit_2": 0,
            "quantity": 0,
            "risk_usd": 0,
            "risk_reward_ratio": 0,
            "expected_hold_hours": 0,
            "regime_aligned": False,
            "regime": regime.get("regime", "UNKNOWN") if regime else "UNKNOWN",
            "timeframe_scores": {
                tf: round(scores.get("combined", 0.0), 2)
                for tf, scores in (tf_scores or {}).items()
            } if tf_scores else {},
            "symbol": symbol
        }
    
    def update_balance(self, new_balance: float) -> None:
        """Update account balance for position sizing."""
        self.balance = new_balance


# ═══════════════════════════════════════════════════════════════════════════════
# DEMO / TEST
# ═══════════════════════════════════════════════════════════════════════════════

def demo():
    """Demo the HybridMultiTFV2 strategy."""
    print("\n" + "=" * 60)
    print("📊 HYBRID MULTI-TF V2 STRATEGY DEMO")
    print("=" * 60)
    
    strategy = HybridMultiTFV2(balance=10000.0, dry_run=True)
    
    # Test 1: 4H Swing Setup
    snapshot_swing = {
        "symbol": "BTCUSDT",
        "price": 50000.0,
        "tf": {
            "4h": {
                "ema20": 50100.0,
                "ema50": 49500.0,
                "ema200": 48000.0,
                "adx": 28.0,
                "atr": 800.0
            },
            "1h": {
                "ema20": 50050.0,
                "ema50": 49800.0,
                "rsi": 55.0,
                "macd": 50.0,
                "macd_signal": 40.0
            },
            "15m": {
                "close": 50000.0,
                "highest_high": 49900.0
            }
        }
    }
    
    regime_strong = {
        "regime": "STRONG_TREND",
        "confidence": 0.85
    }
    
    signal = strategy.evaluate_entry("BTCUSDT", snapshot_swing, regime_strong)
    print(f"\n✅ Test 1 - 4H Swing Setup:")
    print(f"   Action: {signal['action']}")
    print(f"   Entry Type: {signal['entry_type']}")
    print(f"   Confidence: {signal['confidence']}")
    print(f"   Risk USD: ${signal['risk_usd']}")
    print(f"   R:R Ratio: {signal['risk_reward_ratio']}")
    print(f"   Reasoning: {signal['reasoning']}")
    
    # Test 2: 1H Momentum Setup
    snapshot_momentum = {
        "symbol": "ETHUSDT",
        "price": 3000.0,
        "tf": {
            "4h": {
                "ema20": 3050.0,
                "ema50": 2950.0,
                "adx": 22.0,
                "atr": 50.0
            },
            "1h": {
                "ema20": 3020.0,
                "ema50": 2980.0,
                "rsi": 62.0,
                "macd_hist": 5.0,
                "macd_hist_prev": 3.0,
                "volume": 1_500_000_000,
                "volume_avg": 1_000_000_000,
                "atr": 45.0
            },
            "15m": {
                "close": 3000.0,
                "highest_high": 2995.0
            }
        }
    }
    
    regime_weak = {
        "regime": "WEAK_TREND",
        "confidence": 0.72
    }
    
    signal2 = strategy.evaluate_entry("ETHUSDT", snapshot_momentum, regime_weak)
    print(f"\n📈 Test 2 - 1H Momentum Setup:")
    print(f"   Action: {signal2['action']}")
    print(f"   Entry Type: {signal2['entry_type']}")
    print(f"   Confidence: {signal2['confidence']}")
    print(f"   Reasoning: {signal2['reasoning']}")
    
    # Test 3: Ranging regime (should HOLD)
    regime_ranging = {
        "regime": "RANGING",
        "confidence": 0.65
    }
    
    signal3 = strategy.evaluate_entry("BTCUSDT", snapshot_swing, regime_ranging)
    print(f"\n⏸️ Test 3 - Ranging Regime (should HOLD):")
    print(f"   Action: {signal3['action']}")
    print(f"   Reasoning: {signal3['reasoning']}")
    
    print("\n" + "=" * 60)
    print("✅ Demo complete!")
    print("=" * 60)


if __name__ == "__main__":
    demo()
