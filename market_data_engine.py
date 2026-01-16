"""
market_data_engine.py - Merkezi Veri Toplama ve İşleme Modülü
==============================================================

Bu modül tüm piyasa verisi toplama ve ön işleme görevlerini merkezileştirir.
StrategyEngine için ham yapılandırılmış veri sağlar (AI çağrısı yapmaz).

Önbellekleme Kuralları:
- Fiyat: 1s (ExchangeRouter üzerinden)
- Teknik Göstergeler: 15s
- Sentiment (FnG, Reddit, RSS): 90s
- On-chain: 120s

Kullanım:
--------
    from market_data_engine import MarketDataEngine
    from exchange_router import ExchangeRouter

    router = ExchangeRouter(api_key, api_secret)
    await router.start()
    
    engine = MarketDataEngine(router)
    
    price = engine.get_current_price("BTCUSDT")
    tech = engine.get_technical_snapshot("BTC", df)
    sentiment = engine.get_sentiment_snapshot()
    onchain = engine.get_onchain_snapshot("BTC")
"""

import time
import asyncio
import json
import logging
import re
import requests
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from threading import Lock

import pandas as pd
from config import SETTINGS
# llm_utils removed - using internal _safe_json_loads method

# Merkezi logger'ı import et
try:
    from trade_logger import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    # Handler ekleme main.py tarafından yapılır - duplikasyonu önle

# pandas_ta_classic import (fork of pandas_ta with better compatibility)
try:
    import pandas_ta_classic as ta
    PANDAS_TA_AVAILABLE = True
except ImportError:
    PANDAS_TA_AVAILABLE = False
    ta = None

# CCXT import for robust OHLCV data fetching
try:
    import ccxt
    import ccxt.async_support as ccxt_async
    CCXT_AVAILABLE = True
except ImportError:
    ccxt = None
    ccxt_async = None
    CCXT_AVAILABLE = False


# ═══════════════════════════════════════════════════════════════════════════════
# CCXT DATA PROVIDER
# ═══════════════════════════════════════════════════════════════════════════════
class CCXTDataProvider:
    """
    CCXT-based OHLCV data provider with retry mechanism and rate limiting.
    
    Features:
    - Exponential backoff retry for network errors
    - Built-in rate limiting (CCXT enableRateLimit)
    - Standardized DataFrame output
    - Async and sync support
    """
    
    MAX_RETRIES = 3
    RETRY_DELAYS = [1, 2, 4]  # Exponential backoff delays in seconds
    
    # CCXT timeframe mapping (Binance intervals)
    TIMEFRAME_MAP = {
        "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
        "1h": "1h", "4h": "4h", "1d": "1d", "1w": "1w"
    }
    
    def __init__(self, exchange_id: str = "binance", api_key: str = None, api_secret: str = None):
        """
        Initialize CCXT data provider.
        
        Args:
            exchange_id: Exchange identifier (default: binance)
            api_key: Optional API key (not needed for public data)
            api_secret: Optional API secret
        """
        self.exchange_id = exchange_id
        self._api_key = api_key
        self._api_secret = api_secret
        self._sync_exchange = None
        self._async_exchange = None
        
        # Initialize sync exchange for fallback
        self._init_sync_exchange()
    
    def _init_sync_exchange(self):
        """Initialize synchronous CCXT exchange."""
        if not CCXT_AVAILABLE:
            return
        
        try:
            exchange_class = getattr(ccxt, self.exchange_id)
            config = {
                'enableRateLimit': True,
                'rateLimit': 100,  # ms between requests
                'options': {
                    'defaultType': 'spot'
                }
            }
            if self._api_key:
                config['apiKey'] = self._api_key
            if self._api_secret:
                config['secret'] = self._api_secret
            
            self._sync_exchange = exchange_class(config)
            logger.debug(f"[CCXTDataProvider] Sync exchange initialized: {self.exchange_id}")
        except Exception as e:
            logger.warning(f"[CCXTDataProvider] Failed to init sync exchange: {e}")
            self._sync_exchange = None
    
    async def _get_async_exchange(self):
        """Get or create async exchange instance."""
        if not CCXT_AVAILABLE:
            return None
        
        if self._async_exchange is None:
            try:
                exchange_class = getattr(ccxt_async, self.exchange_id)
                config = {
                    'enableRateLimit': True,
                    'rateLimit': 100,
                    'options': {
                        'defaultType': 'spot'
                    }
                }
                if self._api_key:
                    config['apiKey'] = self._api_key
                if self._api_secret:
                    config['secret'] = self._api_secret
                
                self._async_exchange = exchange_class(config)
                logger.debug(f"[CCXTDataProvider] Async exchange initialized: {self.exchange_id}")
            except Exception as e:
                logger.warning(f"[CCXTDataProvider] Failed to init async exchange: {e}")
                return None
        
        return self._async_exchange
    
    async def close_async(self):
        """Close async exchange connection."""
        if self._async_exchange:
            try:
                await self._async_exchange.close()
            except Exception:
                pass
            self._async_exchange = None
    
    def _normalize_symbol(self, symbol: str) -> str:
        """Normalize symbol to CCXT format (BTC/USDT)."""
        symbol = symbol.upper()
        if symbol.endswith("USDT"):
            base = symbol[:-4]
            return f"{base}/USDT"
        elif "/" in symbol:
            return symbol
        else:
            return f"{symbol}/USDT"
    
    def _ohlcv_to_dataframe(self, ohlcv: list, symbol: str) -> Optional[pd.DataFrame]:
        """
        Convert CCXT OHLCV data to standardized DataFrame.
        
        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume
        """
        if not ohlcv:
            return None
        
        try:
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            
            # Ensure numeric types
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            
            # Drop rows with NaN in critical columns
            df = df.dropna(subset=['close', 'high', 'low'])
            
            return df
        except Exception as e:
            logger.warning(f"[CCXTDataProvider] DataFrame conversion error: {e}")
            return None
    
    async def fetch_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 200) -> Optional[pd.DataFrame]:
        """
        Fetch OHLCV data asynchronously with retry mechanism.
        
        Args:
            symbol: Trading symbol (e.g., BTCUSDT, BTC/USDT, BTC)
            timeframe: Candle timeframe (1m, 5m, 15m, 1h, 4h, 1d)
            limit: Number of candles to fetch
        
        Returns:
            DataFrame with OHLCV data or None on failure
        """
        if not CCXT_AVAILABLE:
            logger.warning("[CCXTDataProvider] CCXT not available, falling back")
            return None
        
        exchange = await self._get_async_exchange()
        if not exchange:
            return None
        
        ccxt_symbol = self._normalize_symbol(symbol)
        ccxt_timeframe = self.TIMEFRAME_MAP.get(timeframe, timeframe)
        
        last_error = None
        for attempt in range(self.MAX_RETRIES):
            try:
                ohlcv = await exchange.fetch_ohlcv(ccxt_symbol, ccxt_timeframe, limit=limit)
                
                if ohlcv:
                    df = self._ohlcv_to_dataframe(ohlcv, symbol)
                    if df is not None and len(df) >= 20:
                        logger.debug(f"[CCXTDataProvider] Fetched {len(df)} candles for {symbol} {timeframe}")
                        return df
                    
            except (ccxt.NetworkError, ccxt.RequestTimeout) as e:
                last_error = e
                if attempt < self.MAX_RETRIES - 1:
                    delay = self.RETRY_DELAYS[attempt]
                    logger.warning(f"[CCXTDataProvider] Network error, retry {attempt + 1}/{self.MAX_RETRIES} in {delay}s: {e}")
                    await asyncio.sleep(delay)
                    
            except ccxt.RateLimitExceeded as e:
                last_error = e
                # Wait longer for rate limit
                if attempt < self.MAX_RETRIES - 1:
                    delay = self.RETRY_DELAYS[attempt] * 2
                    logger.warning(f"[CCXTDataProvider] Rate limit, waiting {delay}s")
                    await asyncio.sleep(delay)
                    
            except ccxt.ExchangeError as e:
                logger.error(f"[CCXTDataProvider] Exchange error for {symbol}: {e}")
                return None
                
            except Exception as e:
                logger.error(f"[CCXTDataProvider] Unexpected error: {e}")
                return None
        
        logger.error(f"[CCXTDataProvider] All retries failed for {symbol} {timeframe}: {last_error}")
        return None
    
    def fetch_ohlcv_sync(self, symbol: str, timeframe: str = "1h", limit: int = 200) -> Optional[pd.DataFrame]:
        """
        Fetch OHLCV data synchronously with retry mechanism.
        
        Args:
            symbol: Trading symbol
            timeframe: Candle timeframe
            limit: Number of candles
        
        Returns:
            DataFrame with OHLCV data or None on failure
        """
        if not CCXT_AVAILABLE or not self._sync_exchange:
            return None
        
        ccxt_symbol = self._normalize_symbol(symbol)
        ccxt_timeframe = self.TIMEFRAME_MAP.get(timeframe, timeframe)
        
        last_error = None
        for attempt in range(self.MAX_RETRIES):
            try:
                ohlcv = self._sync_exchange.fetch_ohlcv(ccxt_symbol, ccxt_timeframe, limit=limit)
                
                if ohlcv:
                    df = self._ohlcv_to_dataframe(ohlcv, symbol)
                    if df is not None and len(df) >= 20:
                        logger.debug(f"[CCXTDataProvider] Sync fetched {len(df)} candles for {symbol}")
                        return df
                    
            except (ccxt.NetworkError, ccxt.RequestTimeout) as e:
                last_error = e
                if attempt < self.MAX_RETRIES - 1:
                    delay = self.RETRY_DELAYS[attempt]
                    logger.warning(f"[CCXTDataProvider] Sync network error, retry in {delay}s: {e}")
                    time.sleep(delay)
                    
            except ccxt.RateLimitExceeded as e:
                last_error = e
                if attempt < self.MAX_RETRIES - 1:
                    delay = self.RETRY_DELAYS[attempt] * 2
                    time.sleep(delay)
                    
            except Exception as e:
                logger.error(f"[CCXTDataProvider] Sync error: {e}")
                return None
        
        logger.error(f"[CCXTDataProvider] Sync retries failed: {last_error}")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# CACHE WRAPPER
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
# MARKET DATA ENGINE
# ═══════════════════════════════════════════════════════════════════════════════
class MarketDataEngine:
    """
    Merkezi piyasa verisi toplama ve işleme motoru.
    
    Tüm veri kaynaklarını birleştirir ve önbellekler.
    AI çağrısı yapmaz, sadece ham yapılandırılmış veri döndürür.
    
    Data Sources:
    - ExchangeRouter: Real-time prices via WebSocket
    - Technical: pandas_ta indicators (EMA, MACD, ADX, ATR)
    - Sentiment: Fear & Greed Index, Reddit, RSS
    - On-chain: Whale movements via Etherscan
    """
    
    # Cache TTL (saniye) - config'den oku, fallback değerlerle
    TECHNICAL_TTL = getattr(SETTINGS, 'CACHE_TTL_TECH', 15.0)
    SENTIMENT_TTL = getattr(SETTINGS, 'CACHE_TTL_SENTIMENT', 90.0)
    ONCHAIN_TTL = getattr(SETTINGS, 'CACHE_TTL_ONCHAIN', 120.0)
    RSS_TTL = getattr(SETTINGS, 'CACHE_TTL_SENTIMENT', 90.0)
    
    # Desteklenen mum periyotları
    VALID_INTERVALS = {"1m", "5m", "15m", "1h", "4h", "1d"}
    DEFAULT_INTERVAL = "15m"
    
    # API Timeout (config'den oku)
    API_TIMEOUT = getattr(SETTINGS, 'API_TIMEOUT_DEFAULT', 10)
    
    CACHE_TTL = {
        "fng": 3600,      # 1 hour (fixed - rarely changes)
        "reddit": 900,    # 15 min
        "whales": 300,    # 5 min
        "news": 900       # 15 min
    }
    
    def __init__(
        self,
        exchange_router=None,
        etherscan_api_key: str = "",
        reddit_credentials: Optional[Dict[str, str]] = None,
        offline_mode: bool = False,
        offline_row: Optional[Dict] = None,
        offline_extra: Optional[Dict] = None
    ):
        """
        MarketDataEngine başlat.
        
        Args:
            exchange_router: ExchangeRouter instance (fiyat için)
            etherscan_api_key: Etherscan API key (on-chain için)
            reddit_credentials: Reddit API bilgileri (dict)
            offline_mode: Backtest modu (True ise ağ çağrısı yapmaz)
            offline_row: Backtester'dan gelen anlık mum verisi
            offline_extra: Backtest için ekstra sentiment/haber verisi
        """
        self._router = exchange_router
        self._etherscan_key = etherscan_api_key
        self._reddit_creds = reddit_credentials or {}
        
        # Offline Mode State
        self.offline_mode = offline_mode
        self.offline_row = offline_row or {}
        self.offline_extra = offline_extra or {}
        
        # Caches
        self._technical_cache: Dict[str, CachedData] = {}  # symbol -> CachedData
        self._sentiment_cache = CachedData(ttl_seconds=self.SENTIMENT_TTL)
        self._onchain_cache: Dict[str, CachedData] = {}    # symbol -> CachedData
        self._rss_cache = CachedData(ttl_seconds=self.CACHE_TTL["news"])
        self._fng_cache = CachedData(ttl_seconds=self.CACHE_TTL["fng"])
        self._reddit_cache = CachedData(ttl_seconds=self.CACHE_TTL["reddit"])
        self._whale_cache = CachedData(ttl_seconds=self.CACHE_TTL["whales"])
        
        # Global News Summary Cache (TTL controlled by SETTINGS)
        self._news_llm_global_cache: Optional[Dict[str, Any]] = None
        self._news_llm_global_cache_ts: float = 0.0
        
        # Reddit LLM Summary Cache (15-min TTL)
        self._reddit_llm_cache: Optional[Dict[str, Any]] = None
        self._reddit_llm_cache_ts: float = 0.0
        
        # Per-Article Analysis Cache (URL -> analyzed result, 24h TTL)
        self._analyzed_news_cache: Dict[str, Dict[str, Any]] = {}
        self._analyzed_news_cache_ts: Dict[str, float] = {}  # URL -> timestamp
        self._article_analysis_ttl = 86400  # 24 hours
        
        # Lock for cache dict operations
        self._cache_lock = Lock()
        
        # LLM Metrics
        self.llm_metrics = {
            "news_calls": 0,
            "news_failures": 0,
            "news_fallbacks": 0,
            "news_latency_ema_ms": 0.0,
            # Reddit LLM metrics
            "reddit_calls": 0,
            "reddit_failures": 0,
            "reddit_latency_ema_ms": 0.0,
            # Per-Article Analysis metrics
            "article_calls": 0,
            "article_failures": 0,
            "article_latency_ema_ms": 0.0,
        }
        
        # V1 Multi-Timeframe Cache (symbol -> {1h: data, 15m: data})
        self._v1_tf_cache: Dict[str, CachedData] = {}
        self._v1_tf_ttl = 300  # 5 minutes
        
        # CCXT Data Provider for robust OHLCV fetching
        self._ccxt_provider: Optional[CCXTDataProvider] = None
        if CCXT_AVAILABLE and not offline_mode:
            try:
                self._ccxt_provider = CCXTDataProvider(exchange_id="binance")
                logger.info("[MarketDataEngine] CCXT provider initialized for OHLCV fetching")
            except Exception as e:
                logger.warning(f"[MarketDataEngine] CCXT init failed, using fallback: {e}")
                self._ccxt_provider = None
    
    # ═══════════════════════════════════════════════════════════════════════════
    # V1 MULTI-TIMEFRAME INDICATORS
    # ═══════════════════════════════════════════════════════════════════════════
    
    async def get_v1_timeframe_data(self, symbol: str) -> Dict[str, Any]:
        """
        V1 strateji için çoklu zaman dilimi göstergeleri hesapla.
        
        1h Timeframe:
        - EMA20, EMA50, EMA50_prev (slope için)
        - ADX, ATR
        
        15m Timeframe:
        - Close, HighestHigh(20), HighestClose(20)
        - ATR (trailing için)
        
        Args:
            symbol: Coin sembolü
        
        Returns:
            {"1h": {...}, "15m": {...}}
        """
        if self.offline_mode:
            return self._get_v1_offline_data()
        
        symbol = symbol.upper().replace("USDT", "")
        cache_key = symbol
        
        # Check cache
        with self._cache_lock:
            if cache_key in self._v1_tf_cache:
                cached = self._v1_tf_cache[cache_key].get()
                if cached is not None:
                    return cached
        
        result = {"1h": {}, "15m": {}}
        
        try:
            # Fetch 1h candles
            df_1h = await self._fetch_candles(symbol, interval="1h")
            if df_1h is not None and len(df_1h) >= 50:
                result["1h"] = self._compute_v1_1h_indicators(df_1h)
            
            # Fetch 15m candles
            df_15m = await self._fetch_candles(symbol, interval="15m")
            if df_15m is not None and len(df_15m) >= 20:
                result["15m"] = self._compute_v1_15m_indicators(df_15m)
            
        except Exception as e:
            logger.warning(f"[V1 TF] Error fetching timeframe data for {symbol}: {e}")
        
        # Update cache
        with self._cache_lock:
            if cache_key not in self._v1_tf_cache:
                self._v1_tf_cache[cache_key] = CachedData(ttl_seconds=self._v1_tf_ttl)
            self._v1_tf_cache[cache_key].set(result)
        
        return result
    
    def _compute_v1_1h_indicators(self, df: pd.DataFrame) -> Dict[str, Any]:
        """1h timeframe için V1 göstergelerini hesapla."""
        result = {}
        
        if not PANDAS_TA_AVAILABLE or df is None or df.empty:
            return result
        
        try:
            # EMA20, EMA50
            ema20 = df.ta.ema(length=20)
            ema50 = df.ta.ema(length=50)
            
            if ema20 is not None and not ema20.empty:
                result["ema20"] = float(ema20.iloc[-1]) if not pd.isna(ema20.iloc[-1]) else None
            
            if ema50 is not None and not ema50.empty:
                result["ema50"] = float(ema50.iloc[-1]) if not pd.isna(ema50.iloc[-1]) else None
                # EMA50 prev (5 bars ago for slope)
                if len(ema50) > 5:
                    result["ema50_prev"] = float(ema50.iloc[-6]) if not pd.isna(ema50.iloc[-6]) else None
            
            # ADX
            adx_df = df.ta.adx(length=14)
            if adx_df is not None and not adx_df.empty:
                result["adx"] = float(adx_df.iloc[-1, 0]) if not pd.isna(adx_df.iloc[-1, 0]) else None
            
            # ATR
            atr = df.ta.atr(length=14)
            if atr is not None and not atr.empty:
                result["atr"] = float(atr.iloc[-1]) if not pd.isna(atr.iloc[-1]) else None
            
            # Close price
            result["close"] = float(df["close"].iloc[-1])
            
            # Last closed bar timestamp (for deterministic signal_id)
            if "open_time" in df.columns:
                # Use second to last bar (the last fully closed bar)
                if len(df) >= 2:
                    result["last_closed_ts"] = int(df["open_time"].iloc[-2])
                else:
                    result["last_closed_ts"] = int(df["open_time"].iloc[-1])
            elif df.index.name == "timestamp" or hasattr(df.index, 'timestamp'):
                if len(df) >= 2:
                    result["last_closed_ts"] = int(df.index[-2].timestamp() * 1000) if hasattr(df.index[-2], 'timestamp') else 0
                else:
                    result["last_closed_ts"] = 0
            else:
                result["last_closed_ts"] = 0
            
        except Exception as e:
            logger.warning(f"[V1 1h] Indicator error: {e}")
        
        return result
    
    def _compute_v1_15m_indicators(self, df: pd.DataFrame) -> Dict[str, Any]:
        """15m timeframe için V1 göstergelerini hesapla."""
        result = {}
        
        if df is None or df.empty:
            return result
        
        try:
            lookback = 20
            
            # Son close
            result["close"] = float(df["close"].iloc[-1])
            
            # HighestHigh (son 20 bar)
            if len(df) >= lookback:
                result["highest_high"] = float(df["high"].tail(lookback).max())
                result["highest_close"] = float(df["close"].tail(lookback).max())
            
            # Trailing için HighestClose (22 bar)
            trail_lookback = 22
            if len(df) >= trail_lookback:
                result["highest_close_trail"] = float(df["close"].tail(trail_lookback).max())
            
            # ATR for trailing
            if PANDAS_TA_AVAILABLE:
                atr = df.ta.atr(length=14)
                if atr is not None and not atr.empty:
                    result["atr"] = float(atr.iloc[-1]) if not pd.isna(atr.iloc[-1]) else None
            
            # Last closed bar timestamp (for deterministic signal_id)
            if "open_time" in df.columns:
                # Use second to last bar (the last fully closed bar)
                if len(df) >= 2:
                    result["last_closed_ts"] = int(df["open_time"].iloc[-2])
                else:
                    result["last_closed_ts"] = int(df["open_time"].iloc[-1])
            elif df.index.name == "timestamp" or hasattr(df.index, 'timestamp'):
                if len(df) >= 2:
                    result["last_closed_ts"] = int(df.index[-2].timestamp() * 1000) if hasattr(df.index[-2], 'timestamp') else 0
                else:
                    result["last_closed_ts"] = 0
            else:
                result["last_closed_ts"] = 0
            
        except Exception as e:
            logger.warning(f"[V1 15m] Indicator error: {e}")
        
        return result
    
    def _get_v1_offline_data(self) -> Dict[str, Any]:
        """Offline mod için V1 veri yapısı."""
        row = self.offline_row
        return {
            "1h": {
                "ema20": row.get("ema20"),
                "ema50": row.get("ema50") or row.get("ema_50"),
                "ema50_prev": row.get("ema50_prev"),
                "adx": row.get("adx"),
                "atr": row.get("atr"),
                "close": row.get("close")
            },
            "15m": {
                "close": row.get("close"),
                "highest_high": row.get("highest_high"),
                "highest_close": row.get("highest_close"),
                "highest_close_trail": row.get("highest_close_trail"),
                "atr": row.get("atr")
            }
        }
    
    # ─────────────────────────────────────────────────────────────────────────
    # HYBRID V2 MULTI-TIMEFRAME SNAPSHOT
    # ─────────────────────────────────────────────────────────────────────────
    async def get_hybrid_v2_snapshot(self, symbol: str) -> Dict[str, Any]:
        """
        Get comprehensive snapshot for Hybrid V2 strategy.
        
        Includes 1d, 4h, 1h, 15m timeframes with all indicators needed for:
        - Regime detection (ADX, ATR%)
        - Multi-timeframe alignment (EMAs, trend)
        - Entry signals (RSI, Bollinger, volume)
        
        Args:
            symbol: Coin sembolü (örn: BTCUSDT veya BTC)
        
        Returns:
            {
                "symbol": str,
                "price": float,
                "volume_24h": float,
                "tf": {"1d": {...}, "4h": {...}, "1h": {...}, "15m": {...}},
                "timestamp": float
            }
        """
        if self.offline_mode:
            return self._get_v2_offline_data()
        
        symbol_clean = symbol.upper().replace("USDT", "")
        symbol_full = f"{symbol_clean}USDT"
        
        # Cache key for V2 snapshot
        cache_key = f"v2_{symbol_clean}"
        with self._cache_lock:
            if cache_key in self._technical_cache:
                cached = self._technical_cache[cache_key].get()
                if cached is not None:
                    return cached
        
        try:
            logger.debug(f"[HYBRID V2] Fetching multi-TF data for {symbol_clean}...")
            
            # Check PANDAS_TA availability
            if not PANDAS_TA_AVAILABLE:
                logger.error("[HYBRID V2] pandas_ta_classic not available! Indicators will be 0.")
            
            # Fetch candles for all timeframes in parallel (including 1d)
            df_1d, df_4h, df_1h, df_15m = await asyncio.gather(
                self._fetch_candles(symbol_clean, interval="1d"),
                self._fetch_candles(symbol_clean, interval="4h"),
                self._fetch_candles(symbol_clean, interval="1h"),
                self._fetch_candles(symbol_clean, interval="15m"),
                return_exceptions=True
            )
            
            # Handle exceptions and use sync fallback
            if isinstance(df_1d, Exception) or df_1d is None:
                logger.warning(f"[V2] 1d async fetch failed, trying sync: {df_1d if isinstance(df_1d, Exception) else 'None'}")
                df_1d = self._get_klines_sync(symbol_clean, "1d", 200)
            if isinstance(df_4h, Exception) or df_4h is None:
                logger.warning(f"[V2] 4h async fetch failed, trying sync: {df_4h if isinstance(df_4h, Exception) else 'None'}")
                df_4h = self._get_klines_sync(symbol_clean, "4h", 200)
            if isinstance(df_1h, Exception) or df_1h is None:
                logger.warning(f"[V2] 1h async fetch failed, trying sync: {df_1h if isinstance(df_1h, Exception) else 'None'}")
                df_1h = self._get_klines_sync(symbol_clean, "1h", 200)
            if isinstance(df_15m, Exception) or df_15m is None:
                logger.warning(f"[V2] 15m async fetch failed, trying sync: {df_15m if isinstance(df_15m, Exception) else 'None'}")
                df_15m = self._get_klines_sync(symbol_clean, "15m", 100)
            
            # Log candle fetch status (DEBUG - reduces log spam)
            logger.debug(
                f"[V2] {symbol_clean} candle fetch: "
                f"1d={len(df_1d) if df_1d is not None else 'None'}, "
                f"4h={len(df_4h) if df_4h is not None else 'None'}, "
                f"1h={len(df_1h) if df_1h is not None else 'None'}, "
                f"15m={len(df_15m) if df_15m is not None else 'None'}"
            )
            
            # Calculate indicators for each timeframe
            tf_data = {}
            
            if df_1d is not None and len(df_1d) >= 50:
                tf_data["1d"] = self._compute_v2_timeframe_indicators(df_1d, "1d")
                logger.debug(f"[V2] {symbol_clean} 1d: ADX={tf_data['1d'].get('adx', 0):.1f}, trend={tf_data['1d'].get('trend', 'N/A')}")
            else:
                logger.warning(f"[V2] {symbol_clean} 1d: skipped (insufficient data)")
            
            if df_4h is not None and len(df_4h) >= 50:
                tf_data["4h"] = self._compute_v2_timeframe_indicators(df_4h, "4h")
                logger.debug(f"[V2] {symbol_clean} 4h: ADX={tf_data['4h'].get('adx', 0):.1f}, ATR%={tf_data['4h'].get('atr_pct', 0):.2f}%")
            else:
                logger.warning(f"[V2] {symbol_clean} 4h: skipped (insufficient data)")
            
            if df_1h is not None and len(df_1h) >= 50:
                tf_data["1h"] = self._compute_v2_timeframe_indicators(df_1h, "1h")
                logger.debug(f"[V2] {symbol_clean} 1h: ADX={tf_data['1h'].get('adx', 0):.1f}, RSI={tf_data['1h'].get('rsi', 0):.1f}")
            else:
                logger.warning(f"[V2] {symbol_clean} 1h: skipped (insufficient data)")
            
            if df_15m is not None and len(df_15m) >= 30:
                tf_data["15m"] = self._compute_v2_timeframe_indicators(df_15m, "15m")
            
            # Get current price (3-tier)
            price = self.get_current_price(symbol_full)
            if price is None and self._router:
                try:
                    price = await self._router.get_price_async(symbol_full, fallback_rest=True, timeout_s=3.0)
                except Exception:
                    pass
            if price is None and tf_data.get("15m"):
                price = tf_data["15m"].get("close")
            
            # Get 24h volume
            vol_24h = await self.get_24h_volume_usd(symbol_full)
            
            result = {
                "symbol": symbol_clean,
                "price": price,
                "volume_24h": vol_24h,
                "tf": tf_data,
                "timestamp": time.time(),
                # Add sentiment for completeness
                "sentiment": self.get_sentiment_snapshot(),
                "onchain": self.get_onchain_snapshot(symbol_clean)
            }
            
            # Cache result
            with self._cache_lock:
                if cache_key not in self._technical_cache:
                    self._technical_cache[cache_key] = CachedData(ttl_seconds=60.0)  # 1 min TTL
                self._technical_cache[cache_key].set(result)
            
            logger.info(
                f"[HYBRID V2 SNAPSHOT] {symbol_clean}: price=${price or 0:.2f}, "
                f"1d={'OK' if '1d' in tf_data else 'MISS'}, "
                f"4h={'OK' if '4h' in tf_data else 'MISS'}, "
                f"1h={'OK' if '1h' in tf_data else 'MISS'}, "
                f"15m={'OK' if '15m' in tf_data else 'MISS'}"
            )
            
            return result
            
        except Exception as e:
            logger.error(f"[V2] Error creating snapshot for {symbol}: {e}", exc_info=True)
            return {
                "symbol": symbol_clean,
                "price": None,
                "volume_24h": 0,
                "tf": {},
                "timestamp": time.time(),
                "error": str(e)
            }
    
    def _compute_v2_timeframe_indicators(self, df: pd.DataFrame, timeframe: str) -> Dict[str, Any]:
        """
        Calculate all indicators needed for V2 strategy for a single timeframe.
        
        Returns:
            {
                "ema20": float, "ema50": float, "ema200": float,
                "ema50_prev": float (for slope),
                "adx": float, "atr": float, "atr_pct": float,
                "rsi": float,
                "bb_upper": float, "bb_middle": float, "bb_lower": float, "bb_width_pct": float,
                "close": float, "high": float, "low": float,
                "highest_high": float, "highest_close": float,
                "volume_avg": float, "volume_current": float, "volume_ratio": float,
                "timestamp": int
            }
        """
        result = {
            "timeframe": timeframe,
            "ema20": None, "ema50": None, "ema200": None, "ema50_prev": None,
            "adx": 0.0, "atr": 0.0, "atr_pct": 0.0,
            "rsi": 50.0,
            "bb_upper": None, "bb_middle": None, "bb_lower": None, "bb_width_pct": 0.0,
            "close": 0.0, "high": 0.0, "low": 0.0,
            "highest_high": 0.0, "highest_close": 0.0,
            "volume_avg": 0.0, "volume_current": 0.0, "volume_ratio": 0.0,
            "trend": "NEUTRAL",
            "timestamp": int(time.time() * 1000)
        }
        
        if not PANDAS_TA_AVAILABLE or df is None or df.empty:
            return result
        
        try:
            close = df['close']
            high = df['high']
            low = df['low']
            volume = df['volume'] if 'volume' in df.columns else None
            
            result["close"] = float(close.iloc[-1])
            result["high"] = float(high.iloc[-1])
            result["low"] = float(low.iloc[-1])
            
            # ─────────── EMAs ───────────
            ema20 = df.ta.ema(length=20)
            ema50 = df.ta.ema(length=50)
            
            if ema20 is not None and not ema20.empty:
                result["ema20"] = float(ema20.iloc[-1]) if not pd.isna(ema20.iloc[-1]) else None
            
            if ema50 is not None and not ema50.empty:
                result["ema50"] = float(ema50.iloc[-1]) if not pd.isna(ema50.iloc[-1]) else None
                # EMA50 prev (5 bars ago for slope calculation)
                if len(ema50) > 5:
                    result["ema50_prev"] = float(ema50.iloc[-6]) if not pd.isna(ema50.iloc[-6]) else None
            
            # EMA200 only if enough data
            if len(df) >= 200:
                ema200 = df.ta.ema(length=200)
                if ema200 is not None and not ema200.empty:
                    result["ema200"] = float(ema200.iloc[-1]) if not pd.isna(ema200.iloc[-1]) else None
            
            # Trend determination
            if result["ema50"] and result["ema20"]:
                if result["close"] > result["ema20"] > result["ema50"]:
                    result["trend"] = "BULLISH"
                elif result["close"] < result["ema20"] < result["ema50"]:
                    result["trend"] = "BEARISH"
                else:
                    result["trend"] = "NEUTRAL"
            
            # ─────────── ADX ───────────
            adx_df = df.ta.adx(length=14)
            if adx_df is not None and not adx_df.empty:
                adx_val = adx_df.iloc[-1, 0]  # ADX column
                result["adx"] = float(adx_val) if not pd.isna(adx_val) else 0.0
            
            # ─────────── ATR & ATR% ───────────
            atr = df.ta.atr(length=14)
            if atr is not None and not atr.empty:
                atr_val = float(atr.iloc[-1]) if not pd.isna(atr.iloc[-1]) else 0.0
                result["atr"] = atr_val
                # ATR as percentage of price
                if result["close"] > 0:
                    result["atr_pct"] = (atr_val / result["close"]) * 100
            
            # ─────────── RSI ───────────
            rsi = df.ta.rsi(length=14)
            if rsi is not None and not rsi.empty:
                result["rsi"] = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0
            
            # ─────────── Bollinger Bands ───────────
            bb = df.ta.bbands(length=20, std=2)
            if bb is not None and not bb.empty:
                # Columns: BBL_20_2.0, BBM_20_2.0, BBU_20_2.0, BBB_20_2.0, BBP_20_2.0
                try:
                    bb_lower = bb.iloc[-1, 0]
                    bb_middle = bb.iloc[-1, 1]
                    bb_upper = bb.iloc[-1, 2]
                    
                    result["bb_lower"] = float(bb_lower) if not pd.isna(bb_lower) else None
                    result["bb_middle"] = float(bb_middle) if not pd.isna(bb_middle) else None
                    result["bb_upper"] = float(bb_upper) if not pd.isna(bb_upper) else None
                    
                    if result["bb_middle"] and result["bb_middle"] > 0:
                        result["bb_width_pct"] = ((result["bb_upper"] - result["bb_lower"]) / result["bb_middle"]) * 100
                except (IndexError, KeyError):
                    pass
            
            # ─────────── Highs (for breakout detection) ───────────
            lookback = 20
            if len(df) >= lookback:
                result["highest_high"] = float(high.tail(lookback).max())
                result["highest_close"] = float(close.tail(lookback).max())
            
            # ─────────── Volume Analysis ───────────
            if volume is not None and not volume.empty:
                vol_lookback = 20
                if len(volume) >= vol_lookback:
                    result["volume_avg"] = float(volume.tail(vol_lookback).mean())
                    result["volume_current"] = float(volume.iloc[-1])
                    if result["volume_avg"] > 0:
                        result["volume_ratio"] = result["volume_current"] / result["volume_avg"]
            
            # ─────────── Timestamp ───────────
            if 'timestamp' in df.columns:
                result["timestamp"] = int(df['timestamp'].iloc[-1])
            elif 'close_time' in df.columns:
                result["timestamp"] = int(df['close_time'].iloc[-1])
            
        except Exception as e:
            logger.warning(f"[V2 {timeframe}] Indicator calculation error: {e}")
        
        return result
    
    def _get_v2_offline_data(self) -> Dict[str, Any]:
        """Offline mod için V2 veri yapısı."""
        row = self.offline_row
        price = float(row.get("close") or row.get("price") or 0.0)
        
        # Build minimal tf structure from offline data
        tf_data = {
            "4h": {
                "adx": row.get("adx", 20.0),
                "atr": row.get("atr", 0.0),
                "atr_pct": row.get("atr_pct", 1.0),
                "ema50": row.get("ema50") or row.get("ema_50"),
                "close": price,
                "trend": row.get("trend", "NEUTRAL")
            },
            "1h": {
                "adx": row.get("adx", 20.0),
                "atr": row.get("atr", 0.0),
                "atr_pct": row.get("atr_pct", 1.0),
                "rsi": row.get("rsi", 50.0),
                "ema20": row.get("ema20"),
                "ema50": row.get("ema50") or row.get("ema_50"),
                "close": price,
                "trend": row.get("trend", "NEUTRAL")
            },
            "15m": {
                "close": price,
                "highest_high": row.get("highest_high", price),
                "highest_close": row.get("highest_close", price),
                "atr": row.get("atr", 0.0),
                "volume_ratio": row.get("volume_ratio", 1.0)
            }
        }
        
        return {
            "symbol": row.get("symbol", "UNKNOWN"),
            "price": price,
            "volume_24h": row.get("volume", 0),
            "tf": tf_data,
            "timestamp": time.time()
        }
    
    def _get_klines_sync(self, symbol: str, interval: str, limit: int = 200) -> Optional[pd.DataFrame]:
        """
        Synchronously fetch klines with CCXT or Binance client.
        
        Uses CCXT sync provider as primary method with exponential backoff retry.
        Falls back to python-binance if CCXT fails.
        
        Args:
            symbol: Symbol (e.g., BTCUSDT or BTC)
            interval: Candle interval (1d, 4h, 1h, 15m)
            limit: Number of candles to fetch
        
        Returns:
            DataFrame with OHLCV data or None
        """
        # Normalize symbol
        if not symbol.upper().endswith("USDT"):
            symbol = f"{symbol.upper()}USDT"
        
        # ─────────── Try CCXT sync first ───────────
        if self._ccxt_provider:
            try:
                df = self._ccxt_provider.fetch_ohlcv_sync(symbol, interval, limit=limit)
                if df is not None and len(df) >= 20:
                    logger.debug(f"[MarketDataEngine] CCXT sync fetch success: {symbol} {interval} ({len(df)} candles)")
                    return df
            except Exception as e:
                logger.warning(f"[MarketDataEngine] CCXT sync failed, trying fallback: {e}")
        
        # ─────────── Fallback to python-binance ───────────
        try:
            if not self._router:
                logger.warning("[MarketDataEngine] _get_klines_sync: No router")
                return None
            
            client = self._router.get_client()
            if not client:
                logger.warning("[MarketDataEngine] _get_klines_sync: Client reconnect failed")
                return None
            
            # Fetch klines directly
            klines = client.get_klines(
                symbol=symbol,
                interval=interval,
                limit=limit
            )
            
            if not klines:
                logger.warning(f"[MarketDataEngine] _get_klines_sync: No data for {symbol} {interval}")
                return None
            
            # Create DataFrame
            df = pd.DataFrame(klines, columns=[
                'timestamp', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'quote_volume', 'trades', 'taker_buy_base',
                'taker_buy_quote', 'ignore'
            ])
            
            # Convert to numeric
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            
            # Drop NaN rows
            df = df.dropna(subset=['close', 'high', 'low'])
            
            if len(df) < 20:
                logger.warning(f"[MarketDataEngine] Insufficient data for {symbol} {interval}: {len(df)} rows")
                return None
            
            logger.debug(f"[MarketDataEngine] _get_klines_sync: {symbol} {interval} fetched {len(df)} candles")
            return df
            
        except Exception as e:
            logger.error(f"[MarketDataEngine] _get_klines_sync error ({symbol}, {interval}): {e}")
            return None
    
    def _get_24h_volume_sync(self, symbol: str) -> float:
        """
        Synchronously get 24h volume.
        
        Args:
            symbol: Symbol (e.g., BTCUSDT)
        
        Returns:
            24h quote volume in USD
        """
        try:
            if not self._router:
                return 0.0
            
            client = self._router.get_client()
            if not client:
                return 0.0
            
            if not symbol.upper().endswith("USDT"):
                symbol = f"{symbol.upper()}USDT"
            
            ticker = client.get_ticker(symbol=symbol)
            return float(ticker.get('quoteVolume', 0))
        except Exception as e:
            logger.warning(f"[MarketDataEngine] _get_24h_volume_sync error ({symbol}): {e}")
            return 0.0

    def get_llm_metrics(self) -> Dict[str, Any]:
        """LLM metriklerini döndür."""
        return dict(self.llm_metrics)
    
    def _update_latency_ema(self, key: str, latency_ms: float, alpha: float = 0.2) -> None:
        """Update latency EMA."""
        old_ema = self.llm_metrics.get(key, 0.0)
        if old_ema == 0.0:
            self.llm_metrics[key] = latency_ms
        else:
            self.llm_metrics[key] = alpha * latency_ms + (1 - alpha) * old_ema

    def get_global_news_summary(self) -> Optional[Dict[str, Any]]:
        """
        Get a cached global news summary from LLM.
        
        Returns None if:
        - USE_NEWS_LLM is False
        - NEWS_LLM_MODE is not "global_summary"
        - LLM call fails
        
        Uses TTL caching to avoid repeated calls.
        """
        if not SETTINGS.USE_NEWS_LLM:
            return None
        if SETTINGS.NEWS_LLM_MODE != "global_summary":
            return None
        
        # Check cache freshness
        now = time.time()
        if self._news_llm_global_cache is not None:
            age = now - self._news_llm_global_cache_ts
            if age < SETTINGS.NEWS_LLM_GLOBAL_TTL_SEC:
                return self._news_llm_global_cache
        
        # Get cached RSS articles
        rss_articles = self._rss_cache.get()
        if not rss_articles or not isinstance(rss_articles, list):
            return None
        
        # Take top 10 by recency
        sorted_articles = sorted(
            rss_articles,
            key=lambda x: x.get("published_parsed", (0,)*9),
            reverse=True
        )[:10]
        
        if not sorted_articles:
            return None
        
        # Build compact input for LLM
        articles_text = "\n".join([
            f"- {a.get('title', 'No title')} [{a.get('source', 'Unknown')}]"
            for a in sorted_articles
        ])
        
        prompt = f"""Analyze these recent crypto news headlines and provide a JSON summary:

{articles_text}

Output ONLY valid JSON with this structure:
{{
  "macro_bias": "bullish|bearish|neutral",
  "risk_flags": ["regulation", "exchange", "hack", "macro", ...],
  "top_topics": ["topic1", "topic2", ...],
  "coins_mentioned": ["BTC", "ETH", ...],
  "one_line": "Brief summary in 60 chars max"
}}
"""
        
        try:
            import google.generativeai as genai
            
            gemini_key = SETTINGS.GEMINI_API_KEY
            if not gemini_key:
                return None
            
            genai.configure(api_key=gemini_key)
            safety_settings = [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
            ]
            model = genai.GenerativeModel('models/gemini-2.5-flash', safety_settings=safety_settings)
            
            # Metrics: Start Timer
            self.llm_metrics["news_calls"] += 1
            start_time = time.perf_counter()
            
            response = model.generate_content(prompt)
            
            # Metrics: End Timer
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            self._update_latency_ema("news_latency_ema_ms", elapsed_ms)
            
            if not response.parts:
                self.llm_metrics["news_failures"] += 1
                self.llm_metrics["news_fallbacks"] += 1
                return None
            
            # Parse response using llm_utils
            result = self._safe_json_loads(response.text.strip())
            
            if result:
                # Validate required keys
                required = ["macro_bias", "risk_flags", "top_topics", "coins_mentioned", "one_line"]
                if all(k in result for k in required):
                    self._news_llm_global_cache = result
                    self._news_llm_global_cache_ts = now
                    logger.info(f"[NEWS SUMMARY] bias={result.get('macro_bias')} topics={result.get('top_topics', [])[:3]}")
                    return result
            
            # Parse failed
            if not result:
                logger.warning("[NEWS LLM PARSE FAIL] JSON parsing failed")
            self.llm_metrics["news_failures"] += 1
            self.llm_metrics["news_fallbacks"] += 1
            return None
            
        except Exception as e:
            self.llm_metrics["news_failures"] += 1
            self.llm_metrics["news_fallbacks"] += 1
            logger.warning(f"[MarketDataEngine] Global news summary error: {e}")
            return None

    async def get_crypto_reddit_summary(self, watchlist: List[str]) -> Optional[Dict[str, Any]]:
        """
        Get a cached LLM-analyzed Reddit summary with coin-specific impacts.
        
        Args:
            watchlist: List of coin symbols (e.g., ["BTCUSDT", "ETHUSDT"])
        
        Returns:
            Dict with general_impact and coin_specific_impacts, or None on failure
            
        Output Schema:
            {
              "general_impact": "Overall Reddit market mood description",
              "coin_specific_impacts": {
                "BTC": "Impact for BTC...",
                "ETH": "Impact for ETH...",
                ...
              }
            }
        """
        # TTL for Reddit LLM cache (default 15 min)
        reddit_llm_ttl = getattr(SETTINGS, 'REDDIT_LLM_TTL_SEC', 900)
        
        # Check cache freshness
        now = time.time()
        if self._reddit_llm_cache is not None:
            age = now - self._reddit_llm_cache_ts
            if age < reddit_llm_ttl:
                return self._reddit_llm_cache
        
        # Fetch raw Reddit posts
        reddit_data = await self._fetch_reddit_raw()
        if not reddit_data or not reddit_data.get("posts"):
            return None
        
        posts = reddit_data["posts"]
        if not posts:
            return None
        
        # Build post titles for prompt
        post_titles = "\n".join([
            f"- [{p.get('subreddit', 'unknown')}] {p.get('title', 'No title')} (score: {p.get('score', 0)})"
            for p in posts[:25]  # Limit to 25 posts for token efficiency
        ])
        
        # Normalize watchlist: BTCUSDT -> BTC
        normalized_coins = []
        for symbol in watchlist:
            coin = symbol.upper().replace("USDT", "").replace("USD", "")
            if coin not in normalized_coins:
                normalized_coins.append(coin)
        
        coins_str = ", ".join(normalized_coins)
        
        # Build prompt
        prompt = f"""Analyze these recent Reddit crypto post titles for market sentiment:

{post_titles}

Coins to analyze: {coins_str}

Output ONLY valid JSON with this exact structure:
{{
  "general_impact": "A short sentence describing the overall market mood on Reddit.",
  "coin_specific_impacts": {{
{chr(10).join([f'    "{coin}": "Short impact sentence for {coin}",' for coin in normalized_coins[:-1]])}
    "{normalized_coins[-1]}": "Short impact sentence for {normalized_coins[-1]}"
  }}
}}

IMPORTANT: For coins NOT mentioned in any post, return "No specific discussion found" for that coin.
"""
        
        try:
            import google.generativeai as genai
            
            gemini_key = SETTINGS.GEMINI_API_KEY
            if not gemini_key:
                return None
            
            genai.configure(api_key=gemini_key)
            safety_settings = [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
            ]
            model = genai.GenerativeModel('models/gemini-2.5-flash', safety_settings=safety_settings)
            
            # Metrics: Start Timer
            self.llm_metrics["reddit_calls"] += 1
            start_time = time.perf_counter()
            
            response = model.generate_content(prompt)
            
            # Metrics: End Timer
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            self._update_latency_ema("reddit_latency_ema_ms", elapsed_ms)
            
            if not response.parts:
                self.llm_metrics["reddit_failures"] += 1
                return None
            
            # Parse response using internal method
            result = self._safe_json_loads(response.text.strip())
            
            if result:
                # Validate required keys
                if "general_impact" in result and "coin_specific_impacts" in result:
                    self._reddit_llm_cache = result
                    self._reddit_llm_cache_ts = now
                    logger.info(f"[REDDIT SUMMARY] {result.get('general_impact', 'N/A')[:60]}")
                    return result
            
            # Parse failed
            if not result:
                logger.warning("[REDDIT LLM PARSE FAIL] JSON parsing failed")
            self.llm_metrics["reddit_failures"] += 1
            return None
            
        except Exception as e:
            self.llm_metrics["reddit_failures"] += 1
            logger.warning(f"[MarketDataEngine] Reddit LLM summary error: {e}")
            return None

    def _extract_json_object(self, text: str) -> Optional[str]:
        """
        Robust extraction of JSON object substring.
        Handles fences, prose, and finding outer braces.
        """
        if not text:
            return None
        text = text.strip()
        # Remove triple backtick fences
        if "```" in text:
            text = re.sub(r'^```(?:json)?\s*', '', text)
            text = re.sub(r'```\s*$', '', text)
            text = text.strip()
        # Find brace span
        first = text.find('{')
        last = text.rfind('}')
        if first == -1 or last == -1 or first > last:
            return None
        return text[first:last+1]

    def _safe_json_loads(self, text: str) -> Optional[Dict[str, Any]]:
        """Safely parse JSON from LLM output."""
        extracted = self._extract_json_object(text)
        if not extracted:
            return None
        try:
            return json.loads(extracted)
        except json.JSONDecodeError:
            # Simple cleanup attempts
            try:
                cleaned = re.sub(r',\s*}', '}', extracted)
                cleaned = re.sub(r',\s*]', ']', cleaned)
                return json.loads(cleaned)
            except json.JSONDecodeError:
                return None
    
    # ─────────────────────────────────────────────────────────────────────────
    # SNAPSHOT BUILDER (Single Entry Point)
    # ─────────────────────────────────────────────────────────────────────────
    async def build_snapshot(self, symbol: str) -> Dict[str, Any]:
        """
        Symbol için anlık piyasa durumunu oluştur (Live veya Offline).
        
        Live Mode: Tüm veri kaynaklarını birleştirir (get_full_snapshot).
        Offline Mode: Backtest verisini kullanır (offline_row).
        
        Args:
            symbol: Coin sembolü
            
        Returns:
            Standardize market snapshot dict
        """
        # OFFLINE / BACKTEST MODE
        if self.offline_mode:
            row = self.offline_row
            extra = self.offline_extra
            
            # Fiyat (Close)
            price = float(row.get("close") or row.get("price") or 0.0)
            
            # Technicals (Row'dan map et)
            technical = {
                "symbol": symbol,
                "price": price,
                "rsi": row.get("rsi"),
                "adx": row.get("adx"),
                "ema50": row.get("ema_50") or row.get("ema50"),
                "ema200": row.get("ema_200") or row.get("ema200"),
                "atr": row.get("atr"),
                "trend": row.get("trend", "NEUTRAL"),
                "volume_24h": row.get("volume", 0),
                "summary": "Offline Backtest Data"
            }
            
            # Sentiment (Extra'dan veya nötr)
            sentiment = extra.get("sentiment") or {
                "overall_sentiment": "NEUTRAL",
                "fear_greed": {"value": 50, "classification": "Neutral"},
                "reddit": None,
                "rss_news": None
            }
            
            # On-chain (Extra'dan veya nötr)
            onchain = extra.get("onchain") or {
                "signal": row.get("onchain_signal", "NEUTRAL"),
                "total_inflow_usd": 0
            }
            
            return {
                "symbol": symbol,
                "timestamp": row.get("timestamp", datetime.now().isoformat()),
                "price": price,
                "technical": technical,
                "sentiment": sentiment,
                "onchain": onchain,
                "volume": technical["volume_24h"],
                "has_open_position": extra.get("has_open_position", False),
                "entry_price": extra.get("entry_price", 0)
            }

        # LIVE MODE
        return await self.get_full_snapshot(symbol, df=None)

    # ─────────────────────────────────────────────────────────────────────────
    # PRICE (uses ExchangeRouter)
    # ─────────────────────────────────────────────────────────────────────────
    def get_current_price(self, symbol: str) -> Optional[float]:
        """
        ExchangeRouter üzerinden güncel fiyat al.
        
        Args:
            symbol: Sembol (örn: BTCUSDT veya BTC)
        
        Returns:
            Fiyat float veya None
        """
        if self.offline_mode:
            return float(self.offline_row.get("close") or self.offline_row.get("price") or 0.0)

        if not self._router:
            logger.warning("[MarketDataEngine] ExchangeRouter yok, fiyat alınamadı")
            return None
        
        # BTCUSDT formatına çevir
        if not symbol.upper().endswith("USDT"):
            symbol = f"{symbol.upper()}USDT"
        
        # Use get_price_or_fetch to fallback to REST API on cache miss
        # Critical for watchdog SL/TP monitoring reliability
        price = self._router.get_price_or_fetch(symbol)
        
        if price is not None:
            logger.debug(f"[MarketDataEngine] Price from REST API for {symbol}: ${price:.2f}")
        
        return price

    
    def get_price_or_fetch(self, symbol: str) -> Optional[float]:
        """Cache miss durumunda API'den çek."""
        if not self._router:
            return None
        
        if not symbol.upper().endswith("USDT"):
            symbol = f"{symbol.upper()}USDT"
        
        return self._router.get_price_or_fetch(symbol)


    async def _fetch_candles(self, symbol: str, interval: str = None) -> Optional[pd.DataFrame]:
        """
        Fetch OHLCV candle data (Async).
        
        Uses CCXT provider with exponential backoff retry as primary method.
        Falls back to python-binance if CCXT fails.
        
        Args:
            symbol: Coin symbol (e.g., BTCUSDT, BTC)
            interval: Candle period (1m, 5m, 15m, 1h, 4h, 1d). Default: DEFAULT_INTERVAL
        
        Returns:
            OHLCV DataFrame or None
        """
        # Default interval
        if interval is None:
            interval = self.DEFAULT_INTERVAL
        
        # Validate interval
        if interval not in self.VALID_INTERVALS:
            logger.warning(f"[MarketDataEngine] Invalid interval: {interval}. Using default: {self.DEFAULT_INTERVAL}")
            interval = self.DEFAULT_INTERVAL
        
        # Normalize symbol
        if not symbol.upper().endswith("USDT"):
            symbol = f"{symbol.upper()}USDT"
        
        # ─────────── Try CCXT first (robust with retry) ───────────
        if self._ccxt_provider:
            try:
                df = await self._ccxt_provider.fetch_ohlcv(symbol, interval, limit=200)
                if df is not None and len(df) >= 20:
                    logger.debug(f"[MarketDataEngine] CCXT fetch success: {symbol} {interval} ({len(df)} candles)")
                    return df
            except Exception as e:
                logger.warning(f"[MarketDataEngine] CCXT fetch failed, trying fallback: {e}")
        
        # ─────────── Fallback to python-binance ───────────
        if not self._router:
            logger.warning("[MarketDataEngine] No router, cannot fetch candles")
            return None
        
        client = self._router.get_client()
        if not client:
            logger.warning("[MarketDataEngine] Client connection failed (reconnect unsuccessful)")
            return None

        try:
            # Run blocking call in executor
            loop = asyncio.get_running_loop()
            klines = await loop.run_in_executor(
                None,
                lambda: client.get_klines(
                    symbol=symbol,
                    interval=interval,
                    limit=200
                )
            )
            
            if not klines:
                return None
                
            # Create DataFrame
            df = pd.DataFrame(klines, columns=[
                'timestamp', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'quote_asset_volume', 'number_of_trades',
                'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
            ])
            
            # Numeric conversion
            for col in ['open', 'high', 'low', 'close', 'volume', 'quote_asset_volume']:
                df[col] = pd.to_numeric(df[col], errors='coerce').astype(float)
                
            return df
            
        except Exception as e:
            logger.warning(f"[MarketDataEngine] Candle fetch error ({symbol}, {interval}): {e}")
            return None

    async def get_24h_volume_usd(self, symbol: str) -> float:
        """
        Fetch 24h USD volume using Binance quoteVolume field.
        Fallback: compute from candles if ticker unavailable.
        """
        try:
            data = await self._router.fetch_24h_ticker(symbol)
            vol_usd = float(data.get("quoteVolume", 0.0))
        except Exception:
            vol_usd = 0.0

        # If API fails → fallback to candle-based USD volume
        if vol_usd <= 0:
            try:
                candles = await self._fetch_candles(symbol)
                if candles is not None and not candles.empty:
                    candles["usd_volume"] = candles["volume"] * candles["close"]
                    vol_usd = float(candles["usd_volume"].tail(96).sum())
            except Exception:
                vol_usd = 0.0

        return vol_usd
    
    # ─────────────────────────────────────────────────────────────────────────
    # TECHNICAL INDICATORS
    # ─────────────────────────────────────────────────────────────────────────
    async def get_technical_snapshot(
        self,
        symbol: str,
        df: Optional[pd.DataFrame] = None,
        force_refresh: bool = False,
        interval: str = None
    ) -> Dict[str, Any]:
        """
        Teknik gösterge snapshot'ı hesapla.
        
        EMA 50/200, MACD, ADX, ATR hesaplar.
        Sonucu normalize edilmiş dict olarak döndürür.
        
        Args:
            symbol: Coin sembolü (BTC, ETH)
            df: OHLCV DataFrame (columns: open, high, low, close, volume)
            force_refresh: Cache'i atla
            interval: Mum periyodu (1m, 5m, 15m, 1h, 4h, 1d). Default: DEFAULT_INTERVAL
        
        Returns:
            Normalized technical indicators dict
        """
        # Default interval
        if interval is None:
            interval = self.DEFAULT_INTERVAL
        
        # OFFLINE MODE
        if self.offline_mode:
            price = self.get_current_price(symbol)
            return {
                "symbol": symbol,
                "price": price,
                "rsi": self.offline_row.get("rsi"),
                "adx": self.offline_row.get("adx"),
                "ema50": self.offline_row.get("ema_50") or self.offline_row.get("ema50"),
                "ema200": self.offline_row.get("ema_200") or self.offline_row.get("ema200"),
                "atr": self.offline_row.get("atr"),
                "trend": self.offline_row.get("trend", "NEUTRAL"),
                "volume_24h": self.offline_row.get("volume", 0),
                "summary": "Offline Data"
            }

        symbol = symbol.upper().replace("USDT", "")
        
        # Cache key includes interval (e.g., BTC_15m)
        cache_key = f"{symbol}_{interval}"
        
        # Check cache
        if not force_refresh:
            with self._cache_lock:
                if cache_key in self._technical_cache:
                    cached = self._technical_cache[cache_key].get()
                    if cached is not None:
                        return cached
        
        if df is None:
             df = await self._fetch_candles(symbol, interval=interval)
             
             if df is None or df.empty:
                 logger.warning("[MarketDataEngine] Technical analizi için DataFrame eksik ve çekilemedi")
                 return self._compute_technical_indicators(symbol, pd.DataFrame())

        # Compute indicators
        result = self._compute_technical_indicators(symbol, df)
        result["interval"] = interval  # Add interval to result
        
        # Update cache
        with self._cache_lock:
            if cache_key not in self._technical_cache:
                self._technical_cache[cache_key] = CachedData(ttl_seconds=self.TECHNICAL_TTL)
            self._technical_cache[cache_key].set(result)
        
        return result
    
    def _compute_technical_indicators(
        self,
        symbol: str,
        df: pd.DataFrame
    ) -> Dict[str, Any]:
        """Teknik göstergeleri hesapla."""
        result = {
            "symbol": symbol,
            "timestamp": datetime.now().isoformat(),
            "price": None,
            "ema50": None,
            "ema200": None,
            "macd_line": None,
            "signal_line": None,
            "macd_histogram": None,
            "adx": None,
            "atr": None,
            "rsi": None,
            "volume_24h": None,
            # Derived signals
            "trend": "NEUTRAL",
            "trend_bullish": False,
            "momentum_positive": False,
            "trend_strength": "WEAK",
            # Summary
            "summary": ""
        }
        
        if df is None or df.empty or len(df) < 200:
            result["summary"] = "Yetersiz veri"
            return result
        
        # 24h Volume Calculation - REMOVED (Handled by get_24h_volume_usd)
        # if 'quote_asset_volume' in df.columns:
        #    result["volume_24h"] = float(df['quote_asset_volume'].tail(96).astype(float).sum()) 
        
        
        if df is None or df.empty or len(df) < 200:
            result["summary"] = "Yetersiz veri"
            return result
        
        if not PANDAS_TA_AVAILABLE:
            result["summary"] = "pandas_ta yüklü değil"
            return result
        
        try:
            # Current price
            current_price = float(df['close'].iloc[-1])
            result["price"] = current_price
            
            # ────────── EMA 50 & 200 ──────────
            ema50 = df.ta.ema(length=50)
            ema200 = df.ta.ema(length=200)
            
            if ema50 is not None and not ema50.empty:
                result["ema50"] = float(ema50.iloc[-1]) if not pd.isna(ema50.iloc[-1]) else None
            
            if ema200 is not None and not ema200.empty:
                result["ema200"] = float(ema200.iloc[-1]) if not pd.isna(ema200.iloc[-1]) else None
            
            # Trend determination
            if result["ema50"] and result["ema200"]:
                if current_price > result["ema50"] > result["ema200"]:
                    result["trend"] = "BULLISH"
                    result["trend_bullish"] = True
                elif current_price < result["ema50"] < result["ema200"]:
                    result["trend"] = "BEARISH"
                elif current_price > result["ema200"]:
                    result["trend"] = "NEUTRAL_BULLISH"
                else:
                    result["trend"] = "NEUTRAL_BEARISH"
            
            # ────────── RSI ──────────
            rsi = df.ta.rsi(length=14)
            if rsi is not None and not rsi.empty:
                result["rsi"] = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else None
            
            # ────────── MACD ──────────
            macd_df = df.ta.macd(fast=12, slow=26, signal=9)
            if macd_df is not None and not macd_df.empty:
                macd_line = macd_df.iloc[-1, 0]
                signal_line = macd_df.iloc[-1, 2]
                histogram = macd_df.iloc[-1, 1]
                
                result["macd_line"] = float(macd_line) if not pd.isna(macd_line) else None
                result["signal_line"] = float(signal_line) if not pd.isna(signal_line) else None
                result["macd_histogram"] = float(histogram) if not pd.isna(histogram) else None
                
                # Momentum
                if result["macd_line"] and result["signal_line"]:
                    result["momentum_positive"] = result["macd_line"] > result["signal_line"]
            
            # ────────── ADX ──────────
            adx = df.ta.adx(length=14)
            if adx is not None and not adx.empty:
                adx_value = adx.iloc[-1, 0]  # ADX column
                result["adx"] = float(adx_value) if not pd.isna(adx_value) else None
                
                # Trend strength
                if result["adx"]:
                    if result["adx"] >= 40:
                        result["trend_strength"] = "STRONG"
                    elif result["adx"] >= 25:
                        result["trend_strength"] = "MODERATE"
                    else:
                        result["trend_strength"] = "WEAK"
            
            # ────────── ATR ──────────
            atr = df.ta.atr(length=14)
            if atr is not None and not atr.empty:
                result["atr"] = float(atr.iloc[-1]) if not pd.isna(atr.iloc[-1]) else None
            
            # ────────── Volume ──────────
            if 'volume' in df.columns:
                # Son 24 mumu topla (4h bars = 6 bars per day)
                vol_24h = df['volume'].tail(6).sum()
                result["volume_24h"] = float(vol_24h) if not pd.isna(vol_24h) else None
            
            # ────────── Summary String ──────────
            trend_str = result["trend"].replace("_", " ")
            momentum_str = "POZİTİF" if result["momentum_positive"] else "NEGATİF"
            strength_str = result["trend_strength"]
            
            rsi_str = f"RSI: {result['rsi']:.1f}" if result["rsi"] else "RSI: N/A"
            atr_str = f"ATR: ${result['atr']:.2f}" if result["atr"] else "ATR: N/A"
            adx_str = f"ADX: {result['adx']:.1f}" if result["adx"] else "ADX: N/A"
            
            result["summary"] = (
                f"TREND: {trend_str} ({strength_str}) | "
                f"MOMENTUM: {momentum_str} | "
                f"{rsi_str} | {adx_str} | {atr_str}"
            )
            
        except Exception as e:
            logger.error(f"[MarketDataEngine] Teknik hesaplama hatası: {e}")
            result["summary"] = f"Hesaplama hatası: {e}"
        
        return result
    
    # ─────────────────────────────────────────────────────────────────────────
    # SENTIMENT SNAPSHOT
    # ─────────────────────────────────────────────────────────────────────────
    def get_sentiment_snapshot(
        self,
        include_reddit: bool = True,
        include_rss: bool = True,
        force_refresh: bool = False
    ) -> Dict[str, Any]:
        """
        Tüm sentiment verilerini birleştir.
        
        Fear & Greed, Reddit, RSS özetlerini içerir.
        AI yorumu içermez - sadece ham veri.
        
        Returns:
            Combined sentiment snapshot dict
        """
        # Check cache first
        if not force_refresh:
            cached = self._sentiment_cache.get()
            if cached is not None:
                return cached
        
        result = {
            "timestamp": datetime.now().isoformat(),
            "fear_greed": self._fetch_fear_and_greed(),
            # Reddit is fetched separately via async get_crypto_reddit_summary
            "reddit": None,
            # Skip RSS if news LLM is disabled (no point fetching if not analyzed)
            "rss_news": self._fetch_rss_raw() if (include_rss and SETTINGS.USE_NEWS_LLM) else None,
            # Derived signals
            "overall_sentiment": "NEUTRAL",
            "retail_signal": "NEUTRAL"
        }
        
        # Derive overall sentiment
        fng = result["fear_greed"]
        if fng and fng.get("value"):
            val = fng["value"]
            if val <= 25:
                result["overall_sentiment"] = "EXTREME_FEAR"
            elif val <= 45:
                result["overall_sentiment"] = "FEAR"
            elif val <= 55:
                result["overall_sentiment"] = "NEUTRAL"
            elif val <= 75:
                result["overall_sentiment"] = "GREED"
            else:
                result["overall_sentiment"] = "EXTREME_GREED"
        
        # Reddit derived signal
        reddit = result["reddit"]
        if reddit and reddit.get("signal"):
            result["retail_signal"] = reddit["signal"]
        
        # Update cache
        self._sentiment_cache.set(result)
        
        return result
    
    def _fetch_fear_and_greed(self) -> Optional[Dict[str, Any]]:
        """Fear & Greed Index çek (retry destekli)."""
        # Check dedicated cache
        cached = self._fng_cache.get()
        if cached is not None:
            return cached
        
        # Retry configuration
        max_retries = 3
        timeouts = [10, 15, 20]  # Progressive timeouts (increased for slow connections)
        
        for attempt in range(max_retries):
            try:
                timeout = timeouts[min(attempt, len(timeouts) - 1)]
                response = requests.get(
                    "https://api.alternative.me/fng/",
                    timeout=timeout
                )
                response.raise_for_status()
                data = response.json()
                
                if data.get("data"):
                    fng_data = data["data"][0]
                    result = {
                        "value": int(fng_data.get("value", 0)),
                        "classification": fng_data.get("value_classification", "Unknown"),
                        "timestamp": fng_data.get("timestamp", ""),
                        "source": "alternative.me"
                    }
                    
                    # Add emoji
                    val = result["value"]
                    if val <= 25:
                        result["emoji"] = "😱"
                    elif val <= 45:
                        result["emoji"] = "😟"
                    elif val <= 55:
                        result["emoji"] = "😐"
                    elif val <= 75:
                        result["emoji"] = "😊"
                    else:
                        result["emoji"] = "🤑"
                    
                    if attempt > 0:
                        logger.info(f"[MarketDataEngine] Fear & Greed alındı (retry {attempt + 1}/{max_retries})")
                    
                    self._fng_cache.set(result)
                    return result
                
            except requests.exceptions.Timeout:
                if attempt < max_retries - 1:
                    logger.warning(f"[MarketDataEngine] Fear & Greed API timeout, retry {attempt + 2}/{max_retries}...")
                    time.sleep(1)  # Wait before retry
                else:
                    logger.warning("[MarketDataEngine] Fear & Greed API timeout (tüm denemeler başarısız)")
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(f"[MarketDataEngine] Fear & Greed hatası: {e}, retry {attempt + 2}/{max_retries}...")
                    time.sleep(1)
                else:
                    logger.error(f"[MarketDataEngine] Fear & Greed hatası (tüm denemeler): {e}")
                    break
        
        return cached
    
    async def _fetch_reddit_raw(self) -> Optional[Dict[str, Any]]:
        """Reddit raw post verisi çek (AI analizi yok) - Async versiyonu."""
        # Check dedicated cache
        cached = self._reddit_cache.get()
        if cached is not None:
            return cached
        
        # Reddit credentials check
        required = ["client_id", "client_secret", "user_agent", "username", "password"]
        if not all(self._reddit_creds.get(k) for k in required):
            return {"posts": [], "signal": "UNAVAILABLE", "reason": "credentials_missing"}
        
        try:
            import asyncpraw
            
            reddit = asyncpraw.Reddit(
                client_id=self._reddit_creds["client_id"],
                client_secret=self._reddit_creds["client_secret"],
                user_agent=self._reddit_creds["user_agent"],
                username=self._reddit_creds["username"],
                password=self._reddit_creds["password"]
            )
            
            subreddits = ["CryptoCurrency", "Bitcoin", "ethereum"]
            cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
            
            posts = []
            try:
                for sub_name in subreddits:
                    try:
                        subreddit = await reddit.subreddit(sub_name)
                        async for post in subreddit.hot(limit=10):
                            post_time = datetime.fromtimestamp(post.created_utc, tz=timezone.utc)
                            if post_time >= cutoff:
                                posts.append({
                                    "subreddit": sub_name,
                                    "title": post.title,
                                    "score": post.score,
                                    "comments": post.num_comments,
                                    "created_utc": post.created_utc
                                })
                    except Exception as e:
                        logger.warning(f"[MarketDataEngine] Reddit {sub_name} hatası: {e}")
            finally:
                await reddit.close()
            
            result = {
                "posts": posts[:30],  # Max 30 post
                "post_count": len(posts),
                "signal": "NEUTRAL",
                "fetch_time": datetime.now().isoformat()
            }
            
            self._reddit_cache.set(result)
            return result
            
        except ImportError:
            return {"posts": [], "signal": "UNAVAILABLE", "reason": "asyncpraw_not_installed"}
        except Exception as e:
            logger.error(f"[MarketDataEngine] Reddit hatası: {e}")
            return {"posts": [], "signal": "ERROR", "reason": str(e)}
    
    def _fetch_rss_raw(self) -> Optional[Dict[str, Any]]:
        """RSS feed'lerden ham haber verisi çek."""
        cached = self._rss_cache.get()
        if cached is not None:
            return cached
        
        try:
            import feedparser
            
            # Config'den RSS feed'leri al
            feeds = list(getattr(SETTINGS, 'RSS_FEED_URLS', (
                "https://cointelegraph.com/rss",
                "https://decrypt.co/feed",
                "https://www.coindesk.com/arc/outboundfeeds/rss/"
            )))
            
            max_age = getattr(SETTINGS, 'RSS_MAX_AGE_HOURS', 4)
            
            cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age)
            articles = []
            
            for feed_url in feeds:
                try:
                    feed = feedparser.parse(feed_url)
                    for entry in feed.entries[:10]:
                        # Parse date
                        pub_date = None
                        if hasattr(entry, 'published_parsed') and entry.published_parsed:
                            pub_date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                        
                        if pub_date and pub_date >= cutoff:
                            articles.append({
                                "title": entry.get("title", ""),
                                "link": entry.get("link", ""),
                                "source": feed_url.split("/")[2],
                                "published": pub_date.isoformat() if pub_date else None
                            })
                except Exception as e:
                    logger.warning(f"[MarketDataEngine] RSS {feed_url} hatası: {e}")
            
            result = {
                "articles": articles[:20],
                "article_count": len(articles),
                "fetch_time": datetime.now().isoformat()
            }
            
            self._rss_cache.set(result)
            return result
            
        except ImportError:
            return {"articles": [], "article_count": 0, "reason": "feedparser_not_installed"}
        except Exception as e:
            logger.error(f"[MarketDataEngine] RSS hatası: {e}")
            return cached or {"articles": [], "article_count": 0, "reason": str(e)}

    def analyze_single_article(self, article_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Analyze a single article with LLM to extract coin impacts.
        
        Args:
            article_data: Dict with keys: title, content, link, source
        
        Returns:
            Analysis result or None on failure:
            {
              "related_coins": ["BTC", "ETH"],
              "sentiment": "POSITIVE|NEGATIVE|NEUTRAL",
              "impact_score": 1-10,
              "summary": "One sentence summary"
            }
        """
        url = article_data.get("link", "")
        if not url:
            return None
        
        # Check if already analyzed (URL-based cache)
        now = time.time()
        if url in self._analyzed_news_cache:
            cache_time = self._analyzed_news_cache_ts.get(url, 0)
            if now - cache_time < self._article_analysis_ttl:
                return self._analyzed_news_cache[url]
        
        title = article_data.get("title", "")
        content = article_data.get("content", "")
        
        if not title:
            return None
        
        # Use title if no content, truncate content to 2000 chars
        text_for_analysis = content[:2000] if content else title
        
        prompt = f"""Analyze this crypto news article for market impact:

Title: {title}
Content: {text_for_analysis}

Output ONLY valid JSON with this exact structure:
{{
  "related_coins": ["BTC", "ETH"],
  "sentiment": "POSITIVE",
  "impact_score": 5,
  "summary": "Brief one-sentence summary"
}}

Rules:
- related_coins: List crypto symbols mentioned (BTC, ETH, SOL, etc). Use "MARKET" if it's general/macro news.
- sentiment: POSITIVE, NEGATIVE, or NEUTRAL
- impact_score: 1-10 (1=minor news, 10=massive market-moving event)
- summary: One sentence, max 80 characters
"""
        
        try:
            import google.generativeai as genai
            
            gemini_key = SETTINGS.GEMINI_API_KEY
            if not gemini_key:
                return None
            
            genai.configure(api_key=gemini_key)
            safety_settings = [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
            ]
            model = genai.GenerativeModel('models/gemini-2.5-flash', safety_settings=safety_settings)
            
            # Metrics
            self.llm_metrics["article_calls"] += 1
            start_time = time.perf_counter()
            
            response = model.generate_content(prompt)
            
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            self._update_latency_ema("article_latency_ema_ms", elapsed_ms)
            
            if not response.parts:
                self.llm_metrics["article_failures"] += 1
                return None
            
            result = self._safe_json_loads(response.text.strip())
            
            if result:
                required = ["related_coins", "sentiment", "impact_score", "summary"]
                if all(k in result for k in required):
                    # Add metadata
                    result["url"] = url
                    result["title"] = title
                    result["source"] = article_data.get("source", "Unknown")
                    result["analyzed_at"] = now
                    
                    # Cache it
                    self._analyzed_news_cache[url] = result
                    self._analyzed_news_cache_ts[url] = now
                    return result
            
            if not result:
                logger.warning("[ARTICLE PARSE FAIL] JSON parsing failed")
            self.llm_metrics["article_failures"] += 1
            return None
            
        except Exception as e:
            self.llm_metrics["article_failures"] += 1
            logger.warning(f"[MarketDataEngine] Article analysis error: {e}")
            return None

    def run_news_analysis_pipeline(self) -> int:
        """
        Fetch and analyze recent news articles.
        
        Returns:
            Count of newly analyzed articles
        """
        # 1. Fetch RSS articles
        rss_data = self._fetch_rss_raw()
        if not rss_data or not rss_data.get("articles"):
            return 0
        
        articles = rss_data["articles"][:10]  # Limit to 10 per cycle
        new_count = 0
        
        # 2. For each article, try to get content and analyze
        for article in articles:
            url = article.get("link", "")
            if not url:
                continue
            
            # Skip if already analyzed
            if url in self._analyzed_news_cache:
                cache_time = self._analyzed_news_cache_ts.get(url, 0)
                if time.time() - cache_time < self._article_analysis_ttl:
                    continue
            
            try:
                # Try to fetch full content
                content = self._get_article_content(url)
                
                # Build article data for analysis
                article_data = {
                    "title": article.get("title", ""),
                    "content": content or "",  # Will fallback to title if empty
                    "link": url,
                    "source": article.get("source", "Unknown")
                }
                
                # Analyze
                result = self.analyze_single_article(article_data)
                if result:
                    new_count += 1
                    
            except Exception as e:
                logger.warning(f"[MarketDataEngine] Article pipeline error for {url[:50]}: {e}")
                continue
        
        # 3. Cleanup old cache entries (older than TTL)
        self._cleanup_old_article_cache()
        
        return new_count

    def _cleanup_old_article_cache(self) -> None:
        """Remove expired entries from article cache."""
        now = time.time()
        expired_urls = [
            url for url, ts in self._analyzed_news_cache_ts.items()
            if now - ts > self._article_analysis_ttl
        ]
        for url in expired_urls:
            self._analyzed_news_cache.pop(url, None)
            self._analyzed_news_cache_ts.pop(url, None)

    def get_coin_specific_news(self, symbol: str) -> List[Dict[str, Any]]:
        """
        Get analyzed news relevant to a specific coin.
        
        Args:
            symbol: Coin symbol (e.g., "BTCUSDT" or "BTC")
        
        Returns:
            List of relevant news sorted by impact_score descending
        """
        # Normalize: BTCUSDT -> BTC
        coin = symbol.upper().replace("USDT", "").replace("USD", "")
        
        relevant = []
        for url, analysis in self._analyzed_news_cache.items():
            related_coins = analysis.get("related_coins", [])
            # Match if coin is mentioned OR if it's market-wide news
            if coin in related_coins or "MARKET" in related_coins:
                relevant.append(analysis)
        
        # Sort by impact_score descending
        relevant.sort(key=lambda x: x.get("impact_score", 0), reverse=True)
        
        # Return top 5
        return relevant[:5]

    # ─────────────────────────────────────────────────────────────────────────
    # ON-CHAIN DATA
    # ─────────────────────────────────────────────────────────────────────────

    def get_onchain_snapshot(
        self,
        symbol: str = "ETH",
        force_refresh: bool = False
    ) -> Dict[str, Any]:
        """
        On-chain whale hareketi snapshot'ı.
        
        Etherscan API ile büyük borsa girişlerini izler.
        
        Args:
            symbol: Coin sembolü (ETH için whale tracking)
            force_refresh: Cache'i atla
        
        Returns:
            On-chain data dict
        """
        symbol = symbol.upper()
        
        # Check cache
        if not force_refresh:
            # Global whale cache first
            global_cached = self._whale_cache.get()
            if global_cached is not None:
                return global_cached
            with self._cache_lock:
                if symbol in self._onchain_cache:
                    cached = self._onchain_cache[symbol].get()
                    if cached is not None:
                        return cached
        
        # Fetch data
        result = self._fetch_whale_movements()
        
        # Update cache
        with self._cache_lock:
            if symbol not in self._onchain_cache:
                self._onchain_cache[symbol] = CachedData(ttl_seconds=self.ONCHAIN_TTL)
            self._onchain_cache[symbol].set(result)
        self._whale_cache.set(result)
        
        return result
    
    def _fetch_whale_movements(self) -> Dict[str, Any]:
        """Etherscan'dan whale hareketleri çek."""
        result = {
            "timestamp": datetime.now().isoformat(),
            "movements": [],
            "total_inflow_usd": 0,
            "signal": "NEUTRAL",
            "whale_alert": False
        }
        
        if not self._etherscan_key:
            result["reason"] = "etherscan_api_key_missing"
            return result
        
        try:
            # Exchange hot wallets
            EXCHANGE_WALLETS = {
                "0x28c6c06298d514db089934071355e5743bf21d60": "Binance",
                "0x71660c4005ba85c37ccec55d0c4493e66feef4ff": "Coinbase"
            }
            
            # Stablecoin contracts
            TOKEN_CONTRACTS = {
                "0xdac17f958d2ee523a2206206994597c13d831ec7": "USDT",
                "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": "USDC"
            }
            
            movements = []
            cutoff = int(time.time()) - 3600  # Son 1 saat
            
            for wallet, exchange_name in EXCHANGE_WALLETS.items():
                for contract, token_name in TOKEN_CONTRACTS.items():
                    try:
                        url = (
                            f"https://api.etherscan.io/v2/api"
                            f"?chainid=1"
                            f"&module=account"
                            f"&action=tokentx"
                            f"&contractaddress={contract}"
                            f"&address={wallet}"
                            f"&startblock=0"
                            f"&endblock=99999999"
                            f"&sort=desc"
                            f"&apikey={self._etherscan_key}"
                        )
                        
                        response = requests.get(url, timeout=5)
                        data = response.json()
                        
                        if data.get("status") == "1" and data.get("result"):
                            # Only log at DEBUG level to reduce spam
                            logger.debug(f"[OnChain] Raw API response ({exchange_name}/{token_name}): {len(data.get('result', []))} txs")
                            for tx in data["result"][:50]:
                                timestamp = int(tx.get("timeStamp", 0))
                                if timestamp < cutoff:
                                    break
                                
                                # Sadece girişleri al
                                if tx.get("to", "").lower() != wallet.lower():
                                    continue
                                
                                decimals = int(tx.get("tokenDecimal", 6))
                                value = float(tx.get("value", 0)) / (10 ** decimals)
                                
                                if value >= 500_000:  # $500K+ transfers
                                    movements.append({
                                        "exchange": exchange_name,
                                        "token": token_name,
                                        "amount_usd": value,
                                        "tx_hash": tx.get("hash", ""),
                                        "timestamp": timestamp
                                    })
                        
                    except Exception as e:
                        logger.warning(f"[MarketDataEngine] Etherscan {exchange_name}/{token_name}: {e}")
            
            result["movements"] = movements
            result["total_inflow_usd"] = sum(m["amount_usd"] for m in movements)
            
            # Signal derivation
            total = result["total_inflow_usd"]
            if total >= 10_000_000:  # $10M+
                result["signal"] = "STRONG_SELL_PRESSURE"
                result["whale_alert"] = True
            elif total >= 5_000_000:  # $5M+
                result["signal"] = "MODERATE_SELL_PRESSURE"
                result["whale_alert"] = True
            elif total >= 1_000_000:  # $1M+
                result["signal"] = "LIGHT_SELL_PRESSURE"
            else:
                result["signal"] = "NEUTRAL"
            
        except Exception as e:
            logger.error(f"[MarketDataEngine] On-chain hatası: {e}")
            result["reason"] = str(e)
            logger.warning(f"[OnChain] API error → fallback to zero: {e}")
        
        # Only log OnChain result when signal is not NEUTRAL (meaningful events)
        if result['signal'] != "NEUTRAL":
            logger.info(f"[OnChain] Parsed: inflow=${result['total_inflow_usd']:.2f}, movements={len(result['movements'])}, signal={result['signal']}")
        return result
    
    # ─────────────────────────────────────────────────────────────────────────
    # FULL MARKET SNAPSHOT
    # ─────────────────────────────────────────────────────────────────────────
    async def get_full_snapshot(
        self,
        symbol: str,
        df: pd.DataFrame
    ) -> Dict[str, Any]:
        """
        Tüm veri kaynaklarını birleştiren tam snapshot.
        
        StrategyEngine için tek çağrıda tüm veriyi sağlar.
        
        Args:
            symbol: Coin sembolü
            df: Technical analysis için OHLCV DataFrame
        
        Returns:
            Complete market snapshot dict
        """
        symbol_clean = symbol.upper().replace("USDT", "")
        
        # Fetch volume asynchronously
        vol_24h = await self.get_24h_volume_usd(symbol)
        
        # Get technical snapshot first (contains calculated price from candles)
        technical = await self.get_technical_snapshot(symbol_clean, df)
        
        # 3-tier price fetching:
        # 1) WebSocket cache (fastest, ~100ms freshness)
        # 2) REST API fallback (reliable, ~300ms)
        # 3) Candle close price (last resort, up to 15min delay)
        price = self.get_current_price(symbol)
        
        if price is None and self._router:
            # Tier 2: Try REST API async fallback
            try:
                price = await self._router.get_price_async(symbol, fallback_rest=True, timeout_s=3.0)
                if price is not None:
                    logger.debug(f"[MarketDataEngine] Price from REST API for {symbol}: ${price:.2f}")
            except Exception as e:
                logger.warning(f"[MarketDataEngine] REST price fetch failed for {symbol}: {e}")
        
        if price is None:
            # Tier 3: Use candle close price as last resort
            price = technical.get("price")
            if price is not None:
                logger.debug(f"[MarketDataEngine] Price fallback for {symbol}: using candle close={price}")
        
        return {
            "symbol": symbol_clean,
            "timestamp": datetime.now().isoformat(),
            "price": price,
            "volume_24h": vol_24h,
            "technical": technical,
            "sentiment": self.get_sentiment_snapshot(),
            "onchain": self.get_onchain_snapshot(symbol_clean)
        }
    
    # ─────────────────────────────────────────────────────────────────────────
    # CACHE MANAGEMENT
    # ─────────────────────────────────────────────────────────────────────────
    def clear_all_caches(self) -> None:
        """Tüm cache'leri temizle."""
        with self._cache_lock:
            self._technical_cache.clear()
            self._onchain_cache.clear()
        
        self._sentiment_cache.invalidate()
        self._fng_cache.invalidate()
        self._reddit_cache.invalidate()
        self._rss_cache.invalidate()
        self._whale_cache.invalidate()
        
        logger.info("[MarketDataEngine] Tüm cache'ler temizlendi")
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Cache durumu istatistikleri."""
        with self._cache_lock:
            tech_count = len(self._technical_cache)
            onchain_count = len(self._onchain_cache)
        
        return {
            "technical_cached_symbols": tech_count,
            "onchain_cached_symbols": onchain_count,
            "sentiment_valid": self._sentiment_cache.is_valid(),
            "fng_valid": self._fng_cache.is_valid(),
            "reddit_valid": self._reddit_cache.is_valid(),
            "rss_valid": self._rss_cache.is_valid(),
            "whales_valid": self._whale_cache.is_valid()
        }

    # Lightweight cached accessors
    def get_fng_cached(self):
        return self._fetch_fear_and_greed()

    def get_reddit_cached(self):
        return self._fetch_reddit_raw()

    def get_whales_cached(self):
        return self._fetch_whale_movements()

    def get_news_cached(self):
        return self._fetch_rss_raw()
    
    # ─────────────────────────────────────────────────────────────────────────
    # NEWS PROCESSING (moved from scraper)
    # ─────────────────────────────────────────────────────────────────────────
    def get_news_snapshot(
        self,
        rss_feeds: Optional[List[str]] = None,
        max_age_hours: int = 4,
        gemini_api_key: str = "",
        force_refresh: bool = False
    ) -> Dict[str, Any]:
        """
        Taze haberleri topla ve analiz et.
        
        Args:
            rss_feeds: RSS feed URL listesi
            max_age_hours: Maximum haber yaşı (saat)
            gemini_api_key: Gemini API key (analiz için)
            force_refresh: Cache'i atla
            
        Returns:
            News snapshot with articles and analysis
        """
        # Default feeds
        if rss_feeds is None:
            rss_feeds = [
                "https://cointelegraph.com/rss",
                "https://www.coindesk.com/arc/outboundfeeds/rss/",
                "https://decrypt.co/feed",
                "https://cryptoslate.com/feed/",
            ]
        
        # Fetch articles
        articles = self._fetch_news_articles(rss_feeds, max_age_hours)
        
        return {
            "timestamp": datetime.now().isoformat(),
            "articles": articles,
            "article_count": len(articles),
            "max_age_hours": max_age_hours
        }
    
    def _fetch_news_articles(
        self,
        rss_feeds: List[str],
        max_age_hours: int = 4
    ) -> List[Dict[str, Any]]:
        """RSS Feed'lerden haberleri çek."""
        try:
            import feedparser
            from dateutil import parser as dateutil_parser
        except ImportError:
            logger.warning("[MarketDataEngine] feedparser/dateutil yüklü değil")
            return []
        
        articles = []
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        
        for feed_url in rss_feeds:
            try:
                feed_name = feed_url.split("//")[1].split("/")[0].replace("www.", "").split(".")[0].title()
                feed = feedparser.parse(feed_url)
                
                if feed.bozo and not feed.entries:
                    continue
                
                for entry in feed.entries:
                    try:
                        baslik = entry.get('title', '')
                        if not baslik or '[Removed]' in baslik:
                            continue
                        
                        link = entry.get('link', '')
                        if not link:
                            continue
                        
                        published_str = entry.get('published') or entry.get('updated') or ''
                        if published_str:
                            try:
                                published_time = dateutil_parser.parse(published_str)
                                if published_time.tzinfo is None:
                                    published_time = published_time.replace(tzinfo=timezone.utc)
                                if published_time < cutoff_time:
                                    continue
                                tarih_str = published_time.isoformat()
                            except (ValueError, TypeError):
                                tarih_str = published_str
                        else:
                            tarih_str = ''
                        
                        articles.append({
                            'baslik': baslik,
                            'link': link,
                            'kaynak': feed_name,
                            'tarih': tarih_str
                        })
                    except Exception:
                        continue
            except Exception as e:
                logger.warning(f"[MarketDataEngine] RSS hatası ({feed_url[:30]}): {e}")
                continue
        
        articles.sort(key=lambda x: x.get('tarih', ''), reverse=True)
        return articles
    
    def _get_article_content(self, url: str) -> Optional[str]:
        """Newspaper3k ile makale içeriği çek."""
        try:
            from newspaper import Article, Config
            
            config = Config()
            config.browser_user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            config.request_timeout = 20
            config.verify_ssl = False
            config.fetch_images = False
            config.memoize_articles = False
            
            article = Article(url, config=config)
            article.download()
            article.parse()
            
            if not article.text or len(article.text) < 100:
                return None
            return article.text[:7000]
        except Exception as e:
            logger.warning(f"[MarketDataEngine] Makale çekme hatası: {e}")
            return None
    
    def _analyze_news_with_llm(
        self,
        gemini_api_key: str,
        haber_basligi: str,
        haber_icerigi: str
    ) -> Optional[Dict[str, Any]]:
        """Gemini ile haber analizi yap."""
        if not SETTINGS.USE_NEWS_LLM:
            return None
        if not gemini_api_key:
            return None
        
        try:
            import google.generativeai as genai
            
            genai.configure(api_key=gemini_api_key)
            safety_settings = [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
            ]
            model = genai.GenerativeModel('models/gemini-2.5-flash', safety_settings=safety_settings)
            
            prompt = f"""
            GÖREV: Aşağıdaki haber başlığını ve metnini analiz et. Çıktın SADECE geçerli bir JSON objesi olmalı.

            Haber Başlığı: "{haber_basligi}"
            Haber Metni: "{haber_icerigi}"

            İstenen JSON Yapısı:
            {{
              "kripto_ile_ilgili_mi": boolean,
              "onem_derecesi": string ('Düşük', 'Orta', 'Yüksek', 'Çok Yüksek'),
              "etkilenen_coinler": array[string],
              "duygu": string ('Çok Pozitif', 'Pozitif', 'Nötr', 'Negatif', 'Çok Negatif'),
              "ozet_tr": string
            }}

            SADECE JSON ÇIKTISI:
            """
            
            # Metrics: Start Timer
            self.llm_metrics["news_calls"] += 1
            start_time = time.perf_counter()
            
            response = model.generate_content(prompt)
            
            # Metrics: End Timer
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            self._update_latency_ema("news_latency_ema_ms", elapsed_ms)
            
            if not response.parts:
                self.llm_metrics["news_failures"] += 1
                self.llm_metrics["news_fallbacks"] += 1
                return None
            
            # Robust Parsing
            analiz = self._safe_json_loads(response.text.strip())
            
            if analiz:
                # Key normalization
                key_variants = ['önem_derecesi', 'onem_derecisi', 'önem_derecisi']
                for variant in key_variants:
                    if variant in analiz and 'onem_derecesi' not in analiz:
                        analiz['onem_derecesi'] = analiz[variant]
                        break
                
                required_keys = ["kripto_ile_ilgili_mi", "onem_derecesi", "etkilenen_coinler", "duygu", "ozet_tr"]
                if all(k in analiz for k in required_keys):
                    return analiz
                else:
                    logger.warning(f"[MarketDataEngine] LLM eksik anahtar: {list(analiz.keys())}")
            
            # Parse fail logic
            self.llm_metrics["news_failures"] += 1
            self.llm_metrics["news_fallbacks"] += 1
            return None
            
        except Exception as e:
            self.llm_metrics["news_failures"] += 1
            self.llm_metrics["news_fallbacks"] += 1
            logger.warning(f"[MarketDataEngine] LLM haber analizi hatası: {e}")
            return None
    
    def __repr__(self) -> str:
        router_status = "attached" if self._router else "detached"
        return f"<MarketDataEngine router={router_status}>"


# ═══════════════════════════════════════════════════════════════════════════════
# FACTORY FUNCTION
# ═══════════════════════════════════════════════════════════════════════════════
def create_market_data_engine(
    exchange_router=None,
    etherscan_api_key: str = "",
    reddit_credentials: Optional[Dict[str, str]] = None
) -> MarketDataEngine:
    """Factory fonksiyonu."""
    return MarketDataEngine(
        exchange_router=exchange_router,
        etherscan_api_key=etherscan_api_key,
        reddit_credentials=reddit_credentials
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TEST / DEMO
# ═══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("📊 MARKET DATA ENGINE DEMO")
    print("=" * 60 + "\n")
    
    # Router olmadan da çalışabilir (fiyat hariç)
    engine = MarketDataEngine()
    
    # Fear & Greed
    print("▶️  Fear & Greed Index çekiliyor...")
    sentiment = engine.get_sentiment_snapshot(include_reddit=False, include_rss=False)
    fng = sentiment.get("fear_greed")
    if fng:
        print(f"   Value: {fng['value']} ({fng['classification']}) {fng['emoji']}")
    else:
        print("   ❌ Veri alınamadı")
    
    # Cache stats
    print("\n📋 Cache Stats:")
    stats = engine.get_cache_stats()
    for k, v in stats.items():
        print(f"   {k}: {v}")
    
    print("\n" + "=" * 60 + "\n")
