"""
timeframe_analyzer.py - Multi-Timeframe Analysis Module
=========================================================

Analyzes specific timeframes for entry/exit signals.
Supports 4h, 1h, 15m timeframes with trend, momentum, and volatility analysis.

Features:
1. Trend Structure Analysis:
   - EMA alignment (20/50/200)
   - Slope calculation
   - Support/resistance levels

2. Momentum Analysis:
   - RSI with divergence detection
   - MACD histogram
   - Volume confirmation

3. Volatility Analysis:
   - ATR normalization
   - Bollinger squeeze detection
   - Historical volatility percentile

Usage:
    from strategies.timeframe_analyzer import TimeframeAnalyzer
    
    analyzer = TimeframeAnalyzer()
    result = analyzer.analyze_timeframe("BTCUSDT", "4h", snapshot)
    print(result["trend_score"], result["momentum_score"])
"""

import time
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List, Tuple
from threading import Lock

import pandas as pd
import numpy as np

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

class EMAAlignment(Enum):
    """EMA alignment classification."""
    BULLISH = "BULLISH"      # EMA20 > EMA50 > EMA200
    BEARISH = "BEARISH"      # EMA20 < EMA50 < EMA200
    NEUTRAL = "NEUTRAL"      # Mixed alignment


@dataclass
class TimeframeResult:
    """Timeframe analysis result."""
    timeframe: str
    trend_score: float
    momentum_score: float
    volatility_score: float
    ema_structure: Dict[str, Any]
    rsi: Dict[str, Any]
    support_resistance: Dict[str, Any]
    volume_confirmed: bool
    macd: Dict[str, Any] = field(default_factory=dict)
    bb_squeeze: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format."""
        return {
            "timeframe": self.timeframe,
            "trend_score": round(self.trend_score, 2),
            "momentum_score": round(self.momentum_score, 2),
            "volatility_score": round(self.volatility_score, 2),
            "ema_structure": self.ema_structure,
            "rsi": self.rsi,
            "support_resistance": self.support_resistance,
            "volume_confirmed": self.volume_confirmed,
            "macd": self.macd,
            "bb_squeeze": self.bb_squeeze
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
# TIMEFRAME ANALYZER
# ═══════════════════════════════════════════════════════════════════════════════

class TimeframeAnalyzer:
    """
    Multi-timeframe analysis for entry/exit signals.
    
    Analyzes trend structure, momentum, and volatility across
    4h, 1h, 15m timeframes with performance-optimized caching.
    """
    
    # Supported timeframes
    SUPPORTED_TIMEFRAMES = ("4h", "1h", "15m", "1d")
    
    # Cache TTL: 5 minutes for indicator calculations
    CACHE_TTL = 300.0
    
    # RSI thresholds
    RSI_OVERBOUGHT = 70
    RSI_OVERSOLD = 30
    
    # Bollinger squeeze threshold (% width)
    BB_SQUEEZE_THRESHOLD = 4.0
    
    # Volume confirmation multiplier
    VOLUME_CONFIRM_MULT = 1.2
    
    def __init__(
        self,
        cache_ttl: float = None,
        rsi_overbought: int = None,
        rsi_oversold: int = None
    ):
        """
        Initialize TimeframeAnalyzer.
        
        Args:
            cache_ttl: Cache TTL in seconds (default: 300)
            rsi_overbought: RSI overbought threshold (default: 70)
            rsi_oversold: RSI oversold threshold (default: 30)
        """
        self._cache_ttl = cache_ttl if cache_ttl is not None else self.CACHE_TTL
        self._rsi_overbought = rsi_overbought if rsi_overbought is not None else self.RSI_OVERBOUGHT
        self._rsi_oversold = rsi_oversold if rsi_oversold is not None else self.RSI_OVERSOLD
        
        # Per-symbol-timeframe cache: "symbol_tf" -> CachedData
        self._cache: Dict[str, CachedData] = {}
        self._cache_lock = Lock()
    
    def analyze_timeframe(
        self,
        symbol: str,
        timeframe: str,
        snapshot: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Analyze a specific timeframe for entry/exit signals.
        
        Args:
            symbol: Trading symbol (e.g., "BTCUSDT")
            timeframe: Timeframe to analyze ("4h", "1h", "15m")
            snapshot: Market snapshot containing timeframe data
        
        Returns:
            Dict with analysis results
        """
        symbol = symbol.upper()
        timeframe = timeframe.lower()
        
        if timeframe not in self.SUPPORTED_TIMEFRAMES:
            logger.warning(f"[TF ANALYZER] Unsupported timeframe: {timeframe}")
            return self._empty_result(timeframe)
        
        cache_key = f"{symbol}_{timeframe}"
        
        # Check cache
        with self._cache_lock:
            if cache_key in self._cache:
                cached = self._cache[cache_key].get()
                if cached is not None:
                    return cached
        
        # Extract timeframe data
        tf_data = snapshot.get("tf", {}).get(timeframe, {})
        technical = snapshot.get("technical", {})
        price = snapshot.get("price", 0.0) or tf_data.get("close", 0.0)
        
        if not price or price <= 0:
            return self._empty_result(timeframe)
        
        # 1. EMA Structure Analysis
        ema_structure = self._analyze_ema_structure(tf_data, technical, price)
        
        # 2. RSI Analysis with Divergence
        rsi_analysis = self._analyze_rsi(tf_data, technical, snapshot)
        
        # 3. Support/Resistance
        sr_levels = self.find_support_resistance(tf_data, technical, price)
        
        # 4. Volume Confirmation
        volume_confirmed = self._check_volume_confirmation(tf_data, snapshot)
        
        # 5. MACD Analysis
        macd_analysis = self._analyze_macd(tf_data, technical)
        
        # 6. Bollinger Squeeze Detection
        bb_squeeze = self._detect_bb_squeeze(tf_data, technical, price)
        
        # Calculate scores
        trend_score = self.calculate_trend_score({
            "ema_structure": ema_structure,
            "price": price
        })
        
        momentum_score = self.calculate_momentum_score({
            "rsi": rsi_analysis,
            "macd": macd_analysis,
            "volume_confirmed": volume_confirmed
        })
        
        volatility_score = self._calculate_volatility_score(tf_data, technical, price)
        
        # Build result
        result = TimeframeResult(
            timeframe=timeframe,
            trend_score=trend_score,
            momentum_score=momentum_score,
            volatility_score=volatility_score,
            ema_structure=ema_structure,
            rsi=rsi_analysis,
            support_resistance=sr_levels,
            volume_confirmed=volume_confirmed,
            macd=macd_analysis,
            bb_squeeze=bb_squeeze
        )
        
        result_dict = result.to_dict()
        
        # Update cache
        with self._cache_lock:
            if cache_key not in self._cache:
                self._cache[cache_key] = CachedData(ttl_seconds=self._cache_ttl)
            self._cache[cache_key].set(result_dict)
        
        return result_dict
    
    def calculate_trend_score(self, indicators: Dict[str, Any]) -> float:
        """
        Calculate trend strength score (0-1).
        
        Based on:
        - EMA alignment (40%)
        - EMA50 slope (30%)
        - Price position relative to EMAs (30%)
        
        Args:
            indicators: Dict containing ema_structure and price
        
        Returns:
            Trend score between 0.0 and 1.0
        """
        score = 0.0
        ema = indicators.get("ema_structure", {})
        price = indicators.get("price", 0.0)
        
        # EMA Alignment (0.4 max)
        alignment = ema.get("alignment", "NEUTRAL")
        if alignment == "BULLISH":
            score += 0.4
        elif alignment == "BEARISH":
            score += 0.0  # Still valid trend, just bearish
        else:
            score += 0.2  # Neutral/mixed
        
        # EMA50 Slope (0.3 max)
        slope = ema.get("ema50_slope", 0.0)
        if slope > 0.001:  # Positive slope
            score += min(0.3, slope * 100)  # Scale slope contribution
        elif slope < -0.001:  # Negative slope
            score += 0.0
        else:
            score += 0.15  # Flat
        
        # Price position (0.3 max)
        ema20 = ema.get("ema20", 0.0)
        ema50 = ema.get("ema50", 0.0)
        
        if price and ema20 and ema50:
            if price > ema20 > ema50:
                score += 0.3
            elif price > ema20:
                score += 0.2
            elif price > ema50:
                score += 0.1
        
        return min(max(score, 0.0), 1.0)
    
    def calculate_momentum_score(self, indicators: Dict[str, Any]) -> float:
        """
        Calculate momentum score (0-1).
        
        Based on:
        - RSI position (40%)
        - MACD histogram (30%)
        - Volume confirmation (30%)
        
        Args:
            indicators: Dict containing rsi, macd, volume_confirmed
        
        Returns:
            Momentum score between 0.0 and 1.0
        """
        score = 0.0
        
        # RSI contribution (0.4 max)
        rsi = indicators.get("rsi", {})
        rsi_value = rsi.get("value", 50)
        
        if rsi_value:
            if 40 <= rsi_value <= 60:
                score += 0.2  # Neutral
            elif 30 <= rsi_value < 40:
                score += 0.3  # Approaching oversold (bullish opportunity)
            elif 60 < rsi_value <= 70:
                score += 0.35  # Strong momentum
            elif rsi_value < 30:
                score += 0.4  # Oversold (potential reversal)
            elif rsi_value > 70:
                score += 0.1  # Overbought (caution)
        
        # MACD contribution (0.3 max)
        macd = indicators.get("macd", {})
        histogram = macd.get("histogram", 0.0)
        
        if histogram:
            if histogram > 0:
                score += min(0.3, 0.15 + abs(histogram) * 0.1)
            else:
                score += max(0.0, 0.15 - abs(histogram) * 0.1)
        else:
            score += 0.15  # No MACD data
        
        # Volume confirmation (0.3 max)
        if indicators.get("volume_confirmed", False):
            score += 0.3
        else:
            score += 0.1
        
        return min(max(score, 0.0), 1.0)
    
    def detect_divergence(
        self,
        price_data: List[float],
        indicator_data: List[float],
        lookback: int = 14
    ) -> bool:
        """
        Detect divergence between price and indicator.
        
        Bullish divergence: Price makes lower low, indicator makes higher low
        Bearish divergence: Price makes higher high, indicator makes lower high
        
        Args:
            price_data: List of price values (most recent last)
            indicator_data: List of indicator values (e.g., RSI)
            lookback: Number of bars to analyze
        
        Returns:
            True if divergence detected
        """
        if not price_data or not indicator_data:
            return False
        
        if len(price_data) < lookback or len(indicator_data) < lookback:
            return False
        
        try:
            # Get recent data
            prices = price_data[-lookback:]
            indicators = indicator_data[-lookback:]
            
            # Find local extremes
            price_min_idx = prices.index(min(prices))
            price_max_idx = prices.index(max(prices))
            ind_min_idx = indicators.index(min(indicators))
            ind_max_idx = indicators.index(max(indicators))
            
            # Bullish divergence: price low after indicator low
            if price_min_idx > ind_min_idx:
                # More recent price low but indicator didn't make new low
                if prices[-1] < prices[0] and indicators[-1] > indicators[0]:
                    return True
            
            # Bearish divergence: price high after indicator high
            if price_max_idx > ind_max_idx:
                # More recent price high but indicator didn't make new high
                if prices[-1] > prices[0] and indicators[-1] < indicators[0]:
                    return True
            
            return False
            
        except Exception:
            return False
    
    def find_support_resistance(
        self,
        tf_data: Dict[str, Any],
        technical: Dict[str, Any],
        price: float
    ) -> Dict[str, Any]:
        """
        Find nearest support and resistance levels.
        
        Uses:
        - Recent highs/lows
        - EMA levels as dynamic S/R
        - Bollinger bands
        
        Args:
            tf_data: Timeframe specific data
            technical: Technical indicators
            price: Current price
        
        Returns:
            Dict with support/resistance info
        """
        result = {
            "nearest_support": 0.0,
            "nearest_resistance": 0.0,
            "distance_to_support_pct": 0.0,
            "distance_to_resistance_pct": 0.0
        }
        
        if not price or price <= 0:
            return result
        
        # Collect potential levels
        support_levels = []
        resistance_levels = []
        
        # EMA levels
        ema20 = tf_data.get("ema20", technical.get("ema20", 0.0))
        ema50 = tf_data.get("ema50", technical.get("ema50", 0.0))
        ema200 = tf_data.get("ema200", technical.get("ema200", 0.0))
        
        for ema in [ema20, ema50, ema200]:
            if ema and ema > 0:
                if ema < price:
                    support_levels.append(ema)
                else:
                    resistance_levels.append(ema)
        
        # Bollinger bands
        bb_lower = tf_data.get("bb_lower", technical.get("bb_lower", 0.0))
        bb_upper = tf_data.get("bb_upper", technical.get("bb_upper", 0.0))
        
        if bb_lower and bb_lower > 0:
            support_levels.append(bb_lower)
        if bb_upper and bb_upper > 0:
            resistance_levels.append(bb_upper)
        
        # Recent high/low
        highest_high = tf_data.get("highest_high", 0.0)
        lowest_low = tf_data.get("lowest_low", 0.0)
        
        if highest_high and highest_high > price:
            resistance_levels.append(highest_high)
        if lowest_low and lowest_low < price:
            support_levels.append(lowest_low)
        
        # Find nearest levels
        if support_levels:
            result["nearest_support"] = max(support_levels)
            result["distance_to_support_pct"] = ((price - result["nearest_support"]) / price) * 100
        
        if resistance_levels:
            result["nearest_resistance"] = min(resistance_levels)
            result["distance_to_resistance_pct"] = ((result["nearest_resistance"] - price) / price) * 100
        
        return result
    
    def _analyze_ema_structure(
        self,
        tf_data: Dict[str, Any],
        technical: Dict[str, Any],
        price: float
    ) -> Dict[str, Any]:
        """Analyze EMA alignment and slope."""
        ema20 = tf_data.get("ema20", technical.get("ema20", 0.0)) or 0.0
        ema50 = tf_data.get("ema50", technical.get("ema50", 0.0)) or 0.0
        ema200 = tf_data.get("ema200", technical.get("ema200", 0.0)) or 0.0
        ema50_prev = tf_data.get("ema50_prev", technical.get("ema50_prev", 0.0)) or 0.0
        
        # Calculate EMA50 slope
        ema50_slope = 0.0
        if ema50 and ema50_prev and ema50_prev > 0:
            ema50_slope = (ema50 - ema50_prev) / ema50_prev
        
        # Determine alignment
        alignment = EMAAlignment.NEUTRAL
        
        if ema20 and ema50 and ema200:
            if ema20 > ema50 > ema200:
                alignment = EMAAlignment.BULLISH
            elif ema20 < ema50 < ema200:
                alignment = EMAAlignment.BEARISH
        elif ema20 and ema50:
            if ema20 > ema50:
                alignment = EMAAlignment.BULLISH
            elif ema20 < ema50:
                alignment = EMAAlignment.BEARISH
        
        return {
            "ema20": round(ema20, 2) if ema20 else 0.0,
            "ema50": round(ema50, 2) if ema50 else 0.0,
            "ema200": round(ema200, 2) if ema200 else 0.0,
            "alignment": alignment.value,
            "ema50_slope": round(ema50_slope, 6)
        }
    
    def _analyze_rsi(
        self,
        tf_data: Dict[str, Any],
        technical: Dict[str, Any],
        snapshot: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Analyze RSI with divergence detection."""
        rsi_value = tf_data.get("rsi", technical.get("rsi", 50.0)) or 50.0
        
        # Check for divergence using historical data if available
        divergence = False
        price_history = snapshot.get("price_history", [])
        rsi_history = snapshot.get("rsi_history", [])
        
        if price_history and rsi_history:
            divergence = self.detect_divergence(price_history, rsi_history)
        
        return {
            "value": round(rsi_value, 1) if rsi_value else 50.0,
            "divergence": divergence,
            "overbought": rsi_value >= self._rsi_overbought if rsi_value else False,
            "oversold": rsi_value <= self._rsi_oversold if rsi_value else False
        }
    
    def _analyze_macd(
        self,
        tf_data: Dict[str, Any],
        technical: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Analyze MACD histogram and signal."""
        macd_line = tf_data.get("macd", technical.get("macd", 0.0)) or 0.0
        signal_line = tf_data.get("macd_signal", technical.get("macd_signal", 0.0)) or 0.0
        histogram = tf_data.get("macd_hist", technical.get("macd_hist", 0.0)) or 0.0
        
        # If histogram not provided, calculate it
        if not histogram and macd_line and signal_line:
            histogram = macd_line - signal_line
        
        return {
            "macd_line": round(macd_line, 4) if macd_line else 0.0,
            "signal_line": round(signal_line, 4) if signal_line else 0.0,
            "histogram": round(histogram, 4) if histogram else 0.0,
            "bullish_cross": macd_line > signal_line if (macd_line and signal_line) else False,
            "bearish_cross": macd_line < signal_line if (macd_line and signal_line) else False
        }
    
    def _check_volume_confirmation(
        self,
        tf_data: Dict[str, Any],
        snapshot: Dict[str, Any]
    ) -> bool:
        """Check if volume confirms price movement."""
        current_volume = tf_data.get("volume", snapshot.get("volume_24h", 0.0))
        avg_volume = tf_data.get("volume_avg", snapshot.get("volume_avg", 0.0))
        
        if not current_volume or not avg_volume or avg_volume <= 0:
            return True  # No volume data, assume confirmed
        
        return current_volume >= (avg_volume * self.VOLUME_CONFIRM_MULT)
    
    def _detect_bb_squeeze(
        self,
        tf_data: Dict[str, Any],
        technical: Dict[str, Any],
        price: float
    ) -> bool:
        """Detect Bollinger Band squeeze (low volatility compression)."""
        bb_upper = tf_data.get("bb_upper", technical.get("bb_upper", 0.0))
        bb_lower = tf_data.get("bb_lower", technical.get("bb_lower", 0.0))
        bb_middle = tf_data.get("bb_middle", technical.get("bb_middle", 0.0))
        
        if not bb_upper or not bb_lower or not bb_middle or bb_middle <= 0:
            return False
        
        bb_width_pct = ((bb_upper - bb_lower) / bb_middle) * 100
        
        return bb_width_pct < self.BB_SQUEEZE_THRESHOLD
    
    def _calculate_volatility_score(
        self,
        tf_data: Dict[str, Any],
        technical: Dict[str, Any],
        price: float
    ) -> float:
        """Calculate normalized volatility score (0-1)."""
        atr = tf_data.get("atr", technical.get("atr", 0.0))
        
        if not atr or not price or price <= 0:
            return 0.5  # Default neutral
        
        atr_pct = (atr / price) * 100
        
        # Normalize: 0.5% ATR = 0.25, 1.5% ATR = 0.5, 3% ATR = 0.75, >5% = 1.0
        if atr_pct <= 0.5:
            score = atr_pct / 2  # 0 to 0.25
        elif atr_pct <= 1.5:
            score = 0.25 + (atr_pct - 0.5) * 0.25  # 0.25 to 0.5
        elif atr_pct <= 3.0:
            score = 0.5 + (atr_pct - 1.5) / 6  # 0.5 to 0.75
        else:
            score = min(1.0, 0.75 + (atr_pct - 3.0) / 8)  # 0.75 to 1.0
        
        return round(score, 2)
    
    def _empty_result(self, timeframe: str) -> Dict[str, Any]:
        """Return empty result for missing data."""
        return {
            "timeframe": timeframe,
            "trend_score": 0.0,
            "momentum_score": 0.0,
            "volatility_score": 0.0,
            "ema_structure": {
                "ema20": 0.0,
                "ema50": 0.0,
                "ema200": 0.0,
                "alignment": "NEUTRAL",
                "ema50_slope": 0.0
            },
            "rsi": {
                "value": 50,
                "divergence": False,
                "overbought": False,
                "oversold": False
            },
            "support_resistance": {
                "nearest_support": 0.0,
                "nearest_resistance": 0.0,
                "distance_to_support_pct": 0.0,
                "distance_to_resistance_pct": 0.0
            },
            "volume_confirmed": False,
            "macd": {
                "macd_line": 0.0,
                "signal_line": 0.0,
                "histogram": 0.0,
                "bullish_cross": False,
                "bearish_cross": False
            },
            "bb_squeeze": False
        }
    
    def invalidate_cache(self, symbol: str = None, timeframe: str = None) -> None:
        """
        Invalidate cache for a symbol/timeframe or all.
        
        Args:
            symbol: Symbol to invalidate, or None for all
            timeframe: Timeframe to invalidate, or None for all
        """
        with self._cache_lock:
            if symbol and timeframe:
                key = f"{symbol.upper()}_{timeframe.lower()}"
                if key in self._cache:
                    self._cache[key].invalidate()
            elif symbol:
                symbol = symbol.upper()
                for key in list(self._cache.keys()):
                    if key.startswith(symbol):
                        self._cache[key].invalidate()
            else:
                for cache in self._cache.values():
                    cache.invalidate()


# ═══════════════════════════════════════════════════════════════════════════════
# DEMO / TEST
# ═══════════════════════════════════════════════════════════════════════════════

def demo():
    """Demo the TimeframeAnalyzer with sample data."""
    print("\n" + "=" * 60)
    print("📊 TIMEFRAME ANALYZER DEMO")
    print("=" * 60)
    
    analyzer = TimeframeAnalyzer()
    
    # Test 1: Bullish 4h timeframe
    snapshot_bullish = {
        "symbol": "BTCUSDT",
        "price": 50000.0,
        "tf": {
            "4h": {
                "ema20": 50200.0,
                "ema50": 49500.0,
                "ema200": 48000.0,
                "ema50_prev": 49400.0,
                "rsi": 58.0,
                "atr": 800.0,
                "macd": 150.0,
                "macd_signal": 100.0,
                "bb_upper": 51500,
                "bb_lower": 48500,
                "bb_middle": 50000,
                "volume": 1_200_000_000,
                "volume_avg": 1_000_000_000
            }
        },
        "volume_24h": 1_200_000_000,
        "volume_avg": 1_000_000_000
    }
    
    result = analyzer.analyze_timeframe("BTCUSDT", "4h", snapshot_bullish)
    print(f"\n✅ Test 1 - Bullish 4h:")
    print(f"   Trend Score: {result['trend_score']}")
    print(f"   Momentum Score: {result['momentum_score']}")
    print(f"   Volatility Score: {result['volatility_score']}")
    print(f"   EMA Alignment: {result['ema_structure']['alignment']}")
    print(f"   RSI: {result['rsi']['value']}")
    print(f"   Volume Confirmed: {result['volume_confirmed']}")
    
    # Test 2: Bearish 1h timeframe
    snapshot_bearish = {
        "symbol": "ETHUSDT",
        "price": 3000.0,
        "tf": {
            "1h": {
                "ema20": 2950.0,
                "ema50": 3050.0,
                "ema200": 3100.0,
                "ema50_prev": 3055.0,
                "rsi": 35.0,
                "atr": 45.0,
                "macd": -20.0,
                "macd_signal": -10.0,
                "bb_upper": 3080,
                "bb_lower": 2920,
                "bb_middle": 3000
            }
        }
    }
    
    result = analyzer.analyze_timeframe("ETHUSDT", "1h", snapshot_bearish)
    print(f"\n📉 Test 2 - Bearish 1h:")
    print(f"   Trend Score: {result['trend_score']}")
    print(f"   Momentum Score: {result['momentum_score']}")
    print(f"   EMA Alignment: {result['ema_structure']['alignment']}")
    print(f"   RSI: {result['rsi']['value']} (Oversold: {result['rsi']['oversold']})")
    
    # Test 3: BB Squeeze detection
    snapshot_squeeze = {
        "symbol": "SOLUSDT",
        "price": 100.0,
        "tf": {
            "15m": {
                "ema20": 100.5,
                "ema50": 99.5,
                "rsi": 50.0,
                "atr": 1.0,
                "bb_upper": 101.5,
                "bb_lower": 98.5,
                "bb_middle": 100.0  # 3% width = squeeze
            }
        }
    }
    
    result = analyzer.analyze_timeframe("SOLUSDT", "15m", snapshot_squeeze)
    print(f"\n⚡ Test 3 - BB Squeeze Detection:")
    print(f"   BB Squeeze: {result['bb_squeeze']}")
    print(f"   Volatility Score: {result['volatility_score']}")
    
    print("\n" + "=" * 60)
    print("✅ Demo complete!")
    print("=" * 60)


if __name__ == "__main__":
    demo()
