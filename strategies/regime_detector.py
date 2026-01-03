"""
regime_detector.py - Multi-Timeframe Market Regime Detector
============================================================

Detects market regime across multiple timeframes (1D, 4h, 1h).
Returns regime classification with confidence score.

Regime Types:
- STRONG_TREND: ADX(4h) > 30 AND ATR%(4h) > 1.5% AND BB_width > 5%
- WEAK_TREND: ADX(4h) 20-30 AND ATR%(4h) 0.8-1.5%
- RANGING: ADX(4h) < 20 AND ATR%(4h) < 0.8% AND BB_width < 3%
- VOLATILE: ATR%(4h) > 3% OR price swing > 5% in 24h

Usage:
    from strategies.regime_detector import RegimeDetector
    
    detector = RegimeDetector()
    result = detector.detect_regime("BTCUSDT", snapshot)
    print(result["regime"], result["confidence"])
"""

import time
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, Tuple
from threading import Lock

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
        CACHE_TTL_TECH = 15.0
    SETTINGS = MockSettings()


# ═══════════════════════════════════════════════════════════════════════════════
# ENUMS & DATACLASSES
# ═══════════════════════════════════════════════════════════════════════════════

class RegimeType(Enum):
    """Market regime classification types."""
    STRONG_TREND = "STRONG_TREND"
    WEAK_TREND = "WEAK_TREND"
    RANGING = "RANGING"
    VOLATILE = "VOLATILE"
    UNKNOWN = "UNKNOWN"


@dataclass
class RegimeResult:
    """Regime detection result with all indicators."""
    regime: RegimeType
    confidence: float
    timeframe_alignment: Dict[str, str]
    adx_4h: float
    atr_pct_4h: float
    bb_width_4h: float
    trend_strength_score: float
    price_swing_24h: float = 0.0
    reason: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format."""
        return {
            "regime": self.regime.value,
            "confidence": round(self.confidence, 2),
            "timeframe_alignment": self.timeframe_alignment,
            "adx_4h": round(self.adx_4h, 2) if self.adx_4h else 0.0,
            "atr_pct_4h": round(self.atr_pct_4h, 2) if self.atr_pct_4h else 0.0,
            "bb_width_4h": round(self.bb_width_4h, 2) if self.bb_width_4h else 0.0,
            "trend_strength_score": round(self.trend_strength_score, 2),
            "price_swing_24h": round(self.price_swing_24h, 2),
            "reason": self.reason
        }


# ═══════════════════════════════════════════════════════════════════════════════
# CACHED DATA (Thread-safe TTL cache)
# ═══════════════════════════════════════════════════════════════════════════════

class CachedData:
    """Thread-safe cache wrapper with TTL."""
    
    def __init__(self, ttl_seconds: float = 60.0):
        self._data: Any = None
        self._timestamp: float = 0.0
        self._ttl = ttl_seconds
        self._lock = Lock()
    
    def get(self) -> Optional[Any]:
        """Return cached data if still valid, else None."""
        with self._lock:
            if self._data is None:
                return None
            if time.time() - self._timestamp > self._ttl:
                return None
            return self._data
    
    def set(self, data: Any) -> None:
        """Update cache with new data."""
        with self._lock:
            self._data = data
            self._timestamp = time.time()
    
    def is_valid(self) -> bool:
        """Check if cache is still valid."""
        with self._lock:
            if self._data is None:
                return False
            return time.time() - self._timestamp <= self._ttl
    
    def invalidate(self) -> None:
        """Force cache invalidation."""
        with self._lock:
            self._data = None
            self._timestamp = 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# REGIME DETECTOR
# ═══════════════════════════════════════════════════════════════════════════════

class RegimeDetector:
    """
    Multi-timeframe market regime detector.
    
    Analyzes ADX, ATR%, and Bollinger Band width across 1D, 4h, and 1h 
    timeframes to classify the current market regime.
    
    Results are cached with 1-hour TTL.
    """
    
    # Cache TTL: 1 hour (3600 seconds)
    CACHE_TTL = 3600.0
    
    # Classification thresholds
    STRONG_TREND_ADX = 30.0
    STRONG_TREND_ATR_PCT = 1.5
    STRONG_TREND_BB_WIDTH = 5.0
    
    WEAK_TREND_ADX_MIN = 20.0
    WEAK_TREND_ADX_MAX = 30.0
    WEAK_TREND_ATR_PCT_MIN = 0.8
    WEAK_TREND_ATR_PCT_MAX = 1.5
    
    RANGING_ADX = 20.0
    RANGING_ATR_PCT = 0.8
    RANGING_BB_WIDTH = 3.0
    
    VOLATILE_ATR_PCT = 3.0
    VOLATILE_SWING_PCT = 5.0
    
    def __init__(
        self,
        cache_ttl: float = None,
        strong_trend_adx: float = None,
        volatile_atr_pct: float = None
    ):
        """
        Initialize RegimeDetector.
        
        Args:
            cache_ttl: Cache TTL in seconds (default: 3600 = 1 hour)
            strong_trend_adx: ADX threshold for strong trend (default: 30)
            volatile_atr_pct: ATR% threshold for volatile regime (default: 3.0)
        """
        self._cache_ttl = cache_ttl if cache_ttl is not None else self.CACHE_TTL
        self._strong_trend_adx = strong_trend_adx if strong_trend_adx is not None else self.STRONG_TREND_ADX
        self._volatile_atr_pct = volatile_atr_pct if volatile_atr_pct is not None else self.VOLATILE_ATR_PCT
        
        # Per-symbol cache: symbol -> CachedData
        self._cache: Dict[str, CachedData] = {}
        self._cache_lock = Lock()
        
        # Track previous regimes for change detection
        self._previous_regimes: Dict[str, str] = {}
    
    def detect_regime(self, symbol: str, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """
        Detect market regime for a symbol.
        
        Args:
            symbol: Trading symbol (e.g., "BTCUSDT")
            snapshot: Market snapshot containing timeframe data
                - tf.4h.adx: ADX from 4h timeframe
                - tf.4h.atr: ATR from 4h timeframe
                - tf.4h.bb_upper, bb_lower, bb_middle: Bollinger Bands
                - tf.1h, tf.1d: Other timeframe data
                - price: Current price
                - high_24h, low_24h: 24h price range
        
        Returns:
            Dict with regime classification and indicators
        """
        symbol = symbol.upper()
        
        # Check cache
        with self._cache_lock:
            if symbol in self._cache:
                cached = self._cache[symbol].get()
                if cached is not None:
                    return cached
        
        # Extract indicators
        indicators = self._extract_indicators(snapshot, symbol)
        
        # Determine regime
        regime, reason = self._classify_regime(indicators)
        
        # Calculate confidence
        confidence = self.get_regime_confidence(indicators, regime)
        
        # Check timeframe alignment
        alignment = self._check_alignment_across_timeframes(snapshot)
        
        # Calculate trend strength score
        trend_strength = self._calculate_trend_strength(indicators, alignment)
        
        # Build result
        result = RegimeResult(
            regime=regime,
            confidence=confidence,
            timeframe_alignment=alignment,
            adx_4h=indicators.get("adx_4h", 0.0),
            atr_pct_4h=indicators.get("atr_pct_4h", 0.0),
            bb_width_4h=indicators.get("bb_width_4h", 0.0),
            trend_strength_score=trend_strength,
            price_swing_24h=indicators.get("price_swing_24h", 0.0),
            reason=reason
        )
        
        result_dict = result.to_dict()
        
        # Log regime changes
        self._log_regime_change(symbol, regime, result_dict)
        
        # Update cache
        with self._cache_lock:
            if symbol not in self._cache:
                self._cache[symbol] = CachedData(ttl_seconds=self._cache_ttl)
            self._cache[symbol].set(result_dict)
        
        return result_dict
    
    def get_regime_confidence(
        self, 
        indicators: Dict[str, Any], 
        regime: RegimeType = None
    ) -> float:
        """
        Calculate confidence score for the detected regime.
        
        Confidence is based on how clearly the indicators match the regime:
        - How far ADX is from threshold boundaries
        - How far ATR% is from threshold boundaries
        - How aligned timeframes are
        
        Args:
            indicators: Dict of extracted indicators
            regime: Optional pre-determined regime
        
        Returns:
            Confidence score between 0.0 and 1.0
        """
        if regime is None:
            regime, _ = self._classify_regime(indicators)
        
        adx = indicators.get("adx_4h", 0.0) or 0.0
        atr_pct = indicators.get("atr_pct_4h", 0.0) or 0.0
        bb_width = indicators.get("bb_width_4h", 0.0) or 0.0
        
        confidence = 0.5  # Base confidence
        
        if regime == RegimeType.STRONG_TREND:
            # Stronger ADX = higher confidence
            adx_factor = min((adx - self.STRONG_TREND_ADX) / 20.0, 0.3) if adx > self.STRONG_TREND_ADX else 0.0
            atr_factor = min((atr_pct - self.STRONG_TREND_ATR_PCT) / 1.5, 0.1) if atr_pct > self.STRONG_TREND_ATR_PCT else 0.0
            bb_factor = 0.1 if bb_width > self.STRONG_TREND_BB_WIDTH else 0.0
            confidence += adx_factor + atr_factor + bb_factor
            
        elif regime == RegimeType.WEAK_TREND:
            # Closer to center of range = higher confidence
            adx_center = (self.WEAK_TREND_ADX_MIN + self.WEAK_TREND_ADX_MAX) / 2
            adx_dist = abs(adx - adx_center) / (self.WEAK_TREND_ADX_MAX - self.WEAK_TREND_ADX_MIN)
            confidence += 0.2 * (1 - adx_dist)
            
        elif regime == RegimeType.RANGING:
            # Lower ADX and ATR = higher confidence
            adx_factor = min((self.RANGING_ADX - adx) / 10.0, 0.2) if adx < self.RANGING_ADX else 0.0
            atr_factor = min((self.RANGING_ATR_PCT - atr_pct) / 0.5, 0.2) if atr_pct < self.RANGING_ATR_PCT else 0.0
            bb_factor = 0.1 if bb_width < self.RANGING_BB_WIDTH else 0.0
            confidence += adx_factor + atr_factor + bb_factor
            
        elif regime == RegimeType.VOLATILE:
            # Higher ATR% or swing = higher confidence
            swing = indicators.get("price_swing_24h", 0.0) or 0.0
            atr_factor = min((atr_pct - self.VOLATILE_ATR_PCT) / 2.0, 0.3) if atr_pct > self.VOLATILE_ATR_PCT else 0.0
            swing_factor = min((swing - self.VOLATILE_SWING_PCT) / 5.0, 0.2) if swing > self.VOLATILE_SWING_PCT else 0.0
            confidence += atr_factor + swing_factor
        
        # Clamp to [0.0, 1.0]
        return min(max(confidence, 0.0), 1.0)
    
    def _extract_indicators(self, snapshot: Dict[str, Any], symbol: str) -> Dict[str, Any]:
        """Extract relevant indicators from snapshot."""
        indicators = {"symbol": symbol}
        
        # Get price
        price = snapshot.get("price", 0.0)
        if price is None or price <= 0:
            price = snapshot.get("technical", {}).get("price", 0.0) or 0.0
        indicators["price"] = price
        
        # Get 4h timeframe data
        tf_4h = snapshot.get("tf", {}).get("4h", {})
        
        # ADX from 4h
        adx_4h = tf_4h.get("adx")
        if adx_4h is None:
            # Fallback to technical data
            adx_4h = snapshot.get("technical", {}).get("adx", 0.0)
        indicators["adx_4h"] = adx_4h or 0.0
        
        # ATR from 4h and calculate ATR%
        atr_4h = tf_4h.get("atr")
        if atr_4h is None:
            atr_4h = snapshot.get("technical", {}).get("atr", 0.0)
        
        if price > 0 and atr_4h:
            indicators["atr_pct_4h"] = (atr_4h / price) * 100
        else:
            indicators["atr_pct_4h"] = 0.0
        
        # Bollinger Band width from 4h
        indicators["bb_width_4h"] = self._calculate_bb_width(snapshot, "4h")
        
        # 24h price swing
        high_24h = snapshot.get("high_24h", 0.0)
        low_24h = snapshot.get("low_24h", 0.0)
        
        if high_24h and low_24h and low_24h > 0:
            indicators["price_swing_24h"] = ((high_24h - low_24h) / low_24h) * 100
        else:
            indicators["price_swing_24h"] = 0.0
        
        return indicators
    
    def _calculate_bb_width(self, snapshot: Dict[str, Any], tf: str) -> float:
        """
        Calculate Bollinger Band width as percentage of price.
        
        BB Width % = ((BB_upper - BB_lower) / BB_middle) * 100
        
        Args:
            snapshot: Market snapshot
            tf: Timeframe key (e.g., "4h", "1h", "1d")
        
        Returns:
            BB width as percentage
        """
        tf_data = snapshot.get("tf", {}).get(tf, {})
        
        bb_upper = tf_data.get("bb_upper", 0.0)
        bb_lower = tf_data.get("bb_lower", 0.0)
        bb_middle = tf_data.get("bb_middle", 0.0)
        
        # Fallback to technical data
        if not bb_middle or bb_middle <= 0:
            tech = snapshot.get("technical", {})
            bb_upper = tech.get("bb_upper", 0.0) or bb_upper
            bb_lower = tech.get("bb_lower", 0.0) or bb_lower
            bb_middle = tech.get("bb_middle", 0.0) or bb_middle
        
        if bb_middle and bb_middle > 0 and bb_upper and bb_lower:
            return ((bb_upper - bb_lower) / bb_middle) * 100
        
        return 0.0
    
    def _check_alignment_across_timeframes(self, snapshot: Dict[str, Any]) -> Dict[str, str]:
        """
        Check if trend is aligned across multiple timeframes.
        
        Args:
            snapshot: Market snapshot with tf.1d, tf.4h, tf.1h data
        
        Returns:
            Dict mapping timeframe to trend classification ("TREND" or "RANGE")
        """
        alignment = {}
        
        for tf_key in ["1d", "4h", "1h"]:
            tf_data = snapshot.get("tf", {}).get(tf_key, {})
            
            adx = tf_data.get("adx", 0.0) or 0.0
            
            # Simple classification: ADX > 25 = TREND, else RANGE
            if adx > 25:
                alignment[tf_key] = "TREND"
            elif adx > 20:
                alignment[tf_key] = "WEAK_TREND"
            else:
                alignment[tf_key] = "RANGE"
        
        return alignment
    
    def _classify_regime(self, indicators: Dict[str, Any]) -> Tuple[RegimeType, str]:
        """
        Classify market regime based on indicators.
        
        Priority order (checked first to last):
        1. VOLATILE: ATR% > 3% OR swing > 5%
        2. STRONG_TREND: ADX > 30 AND ATR% > 1.5% AND BB_width > 5%
        3. RANGING: ADX < 20 AND ATR% < 0.8% AND BB_width < 3%
        4. WEAK_TREND: ADX 20-30 AND ATR% 0.8-1.5%
        5. UNKNOWN: Default
        
        Returns:
            Tuple of (RegimeType, reason string)
        """
        adx = indicators.get("adx_4h", 0.0) or 0.0
        atr_pct = indicators.get("atr_pct_4h", 0.0) or 0.0
        bb_width = indicators.get("bb_width_4h", 0.0) or 0.0
        swing = indicators.get("price_swing_24h", 0.0) or 0.0
        
        # 1. VOLATILE (highest priority - dangerous conditions)
        if atr_pct > self.VOLATILE_ATR_PCT:
            return (
                RegimeType.VOLATILE, 
                f"ATR%({atr_pct:.1f}%) > {self.VOLATILE_ATR_PCT}%"
            )
        if swing > self.VOLATILE_SWING_PCT:
            return (
                RegimeType.VOLATILE, 
                f"24h swing({swing:.1f}%) > {self.VOLATILE_SWING_PCT}%"
            )
        
        # 2. STRONG_TREND
        if (adx > self.STRONG_TREND_ADX and 
            atr_pct > self.STRONG_TREND_ATR_PCT and 
            bb_width > self.STRONG_TREND_BB_WIDTH):
            return (
                RegimeType.STRONG_TREND,
                f"ADX={adx:.1f} ATR%={atr_pct:.1f}% BB={bb_width:.1f}%"
            )
        
        # 3. RANGING
        if (adx < self.RANGING_ADX and 
            atr_pct < self.RANGING_ATR_PCT and 
            bb_width < self.RANGING_BB_WIDTH):
            return (
                RegimeType.RANGING,
                f"ADX={adx:.1f} ATR%={atr_pct:.1f}% BB={bb_width:.1f}%"
            )
        
        # 4. WEAK_TREND
        if (self.WEAK_TREND_ADX_MIN <= adx <= self.WEAK_TREND_ADX_MAX and
            self.WEAK_TREND_ATR_PCT_MIN <= atr_pct <= self.WEAK_TREND_ATR_PCT_MAX):
            return (
                RegimeType.WEAK_TREND,
                f"ADX={adx:.1f} ATR%={atr_pct:.1f}%"
            )
        
        # 5. Default classification based on dominant indicator
        if adx > self.STRONG_TREND_ADX:
            return (RegimeType.STRONG_TREND, f"ADX={adx:.1f} indicates trend")
        elif adx < self.RANGING_ADX:
            return (RegimeType.RANGING, f"ADX={adx:.1f} indicates ranging")
        else:
            return (RegimeType.WEAK_TREND, f"ADX={adx:.1f} indicates weak trend")
    
    def _calculate_trend_strength(
        self, 
        indicators: Dict[str, Any], 
        alignment: Dict[str, str]
    ) -> float:
        """
        Calculate overall trend strength score (0.0 to 1.0).
        
        Based on:
        - ADX value normalized
        - Timeframe alignment agreement
        """
        adx = indicators.get("adx_4h", 0.0) or 0.0
        
        # ADX contribution (0-50 ADX mapped to 0-0.6)
        adx_score = min(adx / 50.0, 1.0) * 0.6
        
        # Alignment contribution (0.4 max)
        trend_count = sum(1 for v in alignment.values() if v == "TREND")
        alignment_score = (trend_count / max(len(alignment), 1)) * 0.4
        
        return round(adx_score + alignment_score, 2)
    
    def _log_regime_change(
        self, 
        symbol: str, 
        regime: RegimeType, 
        result_dict: Dict[str, Any]
    ) -> None:
        """Log regime changes for monitoring."""
        prev_regime = self._previous_regimes.get(symbol)
        current = regime.value
        
        if prev_regime is None:
            # First detection
            logger.info(
                f"[REGIME DETECT] {symbol}: {current} | "
                f"conf={result_dict['confidence']:.2f} | "
                f"ADX={result_dict['adx_4h']:.1f} | "
                f"ATR%={result_dict['atr_pct_4h']:.1f}% | "
                f"BB%={result_dict['bb_width_4h']:.1f}%"
            )
        elif prev_regime != current:
            # Regime change
            logger.warning(
                f"[REGIME CHANGE] {symbol}: {prev_regime} → {current} | "
                f"conf={result_dict['confidence']:.2f} | "
                f"reason={result_dict['reason']}"
            )
        
        self._previous_regimes[symbol] = current
    
    def invalidate_cache(self, symbol: str = None) -> None:
        """
        Invalidate cache for a symbol or all symbols.
        
        Args:
            symbol: Symbol to invalidate, or None for all
        """
        with self._cache_lock:
            if symbol:
                if symbol.upper() in self._cache:
                    self._cache[symbol.upper()].invalidate()
            else:
                for cache in self._cache.values():
                    cache.invalidate()


# ═══════════════════════════════════════════════════════════════════════════════
# DEMO / TEST
# ═══════════════════════════════════════════════════════════════════════════════

def demo():
    """Demo the RegimeDetector with sample data."""
    print("\n" + "=" * 60)
    print("📊 REGIME DETECTOR DEMO")
    print("=" * 60)
    
    detector = RegimeDetector()
    
    # Test 1: Strong Trend
    snapshot_strong = {
        "symbol": "BTCUSDT",
        "price": 50000.0,
        "tf": {
            "4h": {
                "adx": 35.0,
                "atr": 1000.0,  # 2% ATR
                "bb_upper": 52500,
                "bb_lower": 47500,
                "bb_middle": 50000
            },
            "1h": {"adx": 32.0},
            "1d": {"adx": 28.0}
        }
    }
    
    result = detector.detect_regime("BTCUSDT", snapshot_strong)
    print(f"\n✅ Test 1 - Strong Trend:")
    print(f"   Regime: {result['regime']}")
    print(f"   Confidence: {result['confidence']}")
    print(f"   Alignment: {result['timeframe_alignment']}")
    
    # Test 2: Ranging
    snapshot_ranging = {
        "symbol": "ETHUSDT",
        "price": 3000.0,
        "tf": {
            "4h": {
                "adx": 15.0,
                "atr": 15.0,  # 0.5% ATR
                "bb_upper": 3050,
                "bb_lower": 2950,
                "bb_middle": 3000
            },
            "1h": {"adx": 12.0},
            "1d": {"adx": 18.0}
        }
    }
    
    result = detector.detect_regime("ETHUSDT", snapshot_ranging)
    print(f"\n📊 Test 2 - Ranging:")
    print(f"   Regime: {result['regime']}")
    print(f"   Confidence: {result['confidence']}")
    
    # Test 3: Volatile
    snapshot_volatile = {
        "symbol": "SOLUSDT",
        "price": 100.0,
        "tf": {
            "4h": {
                "adx": 25.0,
                "atr": 4.0,  # 4% ATR
                "bb_upper": 108,
                "bb_lower": 92,
                "bb_middle": 100
            },
            "1h": {"adx": 22.0},
            "1d": {"adx": 20.0}
        },
        "high_24h": 110.0,
        "low_24h": 95.0  # ~16% swing
    }
    
    result = detector.detect_regime("SOLUSDT", snapshot_volatile)
    print(f"\n⚡ Test 3 - Volatile:")
    print(f"   Regime: {result['regime']}")
    print(f"   Confidence: {result['confidence']}")
    print(f"   24h Swing: {result['price_swing_24h']:.1f}%")
    
    print("\n" + "=" * 60)
    print("✅ Demo complete!")
    print("=" * 60)


if __name__ == "__main__":
    demo()
