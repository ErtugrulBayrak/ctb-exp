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
import requests
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from threading import Lock

import pandas as pd

# Merkezi logger'ı import et
try:
    from trade_logger import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter('[%(asctime)s] %(levelname)s - %(message)s'))
        logger.addHandler(handler)

# pandas_ta import
try:
    import pandas_ta as ta
    PANDAS_TA_AVAILABLE = True
except ImportError:
    PANDAS_TA_AVAILABLE = False
    ta = None


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
    
    # Cache TTL (saniye)
    TECHNICAL_TTL = 15.0
    SENTIMENT_TTL = 90.0
    ONCHAIN_TTL = 120.0
    RSS_TTL = 90.0
    
    def __init__(
        self,
        exchange_router=None,
        etherscan_api_key: str = "",
        reddit_credentials: Optional[Dict[str, str]] = None
    ):
        """
        MarketDataEngine başlat.
        
        Args:
            exchange_router: ExchangeRouter instance (fiyat için)
            etherscan_api_key: Etherscan API key (on-chain için)
            reddit_credentials: Reddit API bilgileri (dict)
        """
        self._router = exchange_router
        self._etherscan_key = etherscan_api_key
        self._reddit_creds = reddit_credentials or {}
        
        # Caches
        self._technical_cache: Dict[str, CachedData] = {}  # symbol -> CachedData
        self._sentiment_cache = CachedData(ttl_seconds=self.SENTIMENT_TTL)
        self._onchain_cache: Dict[str, CachedData] = {}    # symbol -> CachedData
        self._rss_cache = CachedData(ttl_seconds=self.RSS_TTL)
        self._fng_cache = CachedData(ttl_seconds=self.SENTIMENT_TTL)
        self._reddit_cache = CachedData(ttl_seconds=self.SENTIMENT_TTL)
        
        # Lock for cache dict operations
        self._cache_lock = Lock()
    
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
        if not self._router:
            logger.warning("[MarketDataEngine] ExchangeRouter yok, fiyat alınamadı")
            return None
        
        # BTCUSDT formatına çevir
        if not symbol.upper().endswith("USDT"):
            symbol = f"{symbol.upper()}USDT"
        
        return self._router.get_price(symbol)
    
    def get_price_or_fetch(self, symbol: str) -> Optional[float]:
        """Cache miss durumunda API'den çek."""
        if not self._router:
            return None
        
        if not symbol.upper().endswith("USDT"):
            symbol = f"{symbol.upper()}USDT"
        
        return self._router.get_price_or_fetch(symbol)
    
    # ─────────────────────────────────────────────────────────────────────────
    # TECHNICAL INDICATORS
    # ─────────────────────────────────────────────────────────────────────────
    def get_technical_snapshot(
        self,
        symbol: str,
        df: pd.DataFrame,
        force_refresh: bool = False
    ) -> Dict[str, Any]:
        """
        Teknik gösterge snapshot'ı hesapla.
        
        EMA 50/200, MACD, ADX, ATR hesaplar.
        Sonucu normalize edilmiş dict olarak döndürür.
        
        Args:
            symbol: Coin sembolü (BTC, ETH)
            df: OHLCV DataFrame (columns: open, high, low, close, volume)
            force_refresh: Cache'i atla
        
        Returns:
            Normalized technical indicators dict
        """
        symbol = symbol.upper().replace("USDT", "")
        
        # Check cache
        if not force_refresh:
            with self._cache_lock:
                if symbol in self._technical_cache:
                    cached = self._technical_cache[symbol].get()
                    if cached is not None:
                        return cached
        
        # Compute indicators
        result = self._compute_technical_indicators(symbol, df)
        
        # Update cache
        with self._cache_lock:
            if symbol not in self._technical_cache:
                self._technical_cache[symbol] = CachedData(ttl_seconds=self.TECHNICAL_TTL)
            self._technical_cache[symbol].set(result)
        
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
            "reddit": self._fetch_reddit_raw() if include_reddit else None,
            "rss_news": self._fetch_rss_raw() if include_rss else None,
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
        """Fear & Greed Index çek."""
        # Check dedicated cache
        cached = self._fng_cache.get()
        if cached is not None:
            return cached
        
        try:
            response = requests.get(
                "https://api.alternative.me/fng/",
                timeout=10
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
                
                self._fng_cache.set(result)
                return result
            
        except requests.exceptions.Timeout:
            logger.warning("[MarketDataEngine] Fear & Greed API timeout")
        except Exception as e:
            logger.error(f"[MarketDataEngine] Fear & Greed hatası: {e}")
        
        return None
    
    def _fetch_reddit_raw(self) -> Optional[Dict[str, Any]]:
        """Reddit raw post verisi çek (AI analizi yok)."""
        # Check dedicated cache
        cached = self._reddit_cache.get()
        if cached is not None:
            return cached
        
        # Reddit credentials check
        required = ["client_id", "client_secret", "user_agent", "username", "password"]
        if not all(self._reddit_creds.get(k) for k in required):
            return {"posts": [], "signal": "UNAVAILABLE", "reason": "credentials_missing"}
        
        try:
            import praw
            
            reddit = praw.Reddit(
                client_id=self._reddit_creds["client_id"],
                client_secret=self._reddit_creds["client_secret"],
                user_agent=self._reddit_creds["user_agent"],
                username=self._reddit_creds["username"],
                password=self._reddit_creds["password"]
            )
            
            subreddits = ["CryptoCurrency", "Bitcoin", "ethereum"]
            cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
            
            posts = []
            for sub_name in subreddits:
                try:
                    subreddit = reddit.subreddit(sub_name)
                    for post in subreddit.hot(limit=10):
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
            
            result = {
                "posts": posts[:30],  # Max 30 post
                "post_count": len(posts),
                "signal": "NEUTRAL",
                "fetch_time": datetime.now().isoformat()
            }
            
            self._reddit_cache.set(result)
            return result
            
        except ImportError:
            return {"posts": [], "signal": "UNAVAILABLE", "reason": "praw_not_installed"}
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
            
            feeds = [
                "https://cointelegraph.com/rss",
                "https://decrypt.co/feed",
                "https://www.coindesk.com/arc/outboundfeeds/rss/"
            ]
            
            cutoff = datetime.now(timezone.utc) - timedelta(hours=4)
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
            return {"articles": [], "article_count": 0, "reason": str(e)}
    
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
                        
                        response = requests.get(url, timeout=15)
                        data = response.json()
                        
                        if data.get("status") == "1" and data.get("result"):
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
        
        return result
    
    # ─────────────────────────────────────────────────────────────────────────
    # FULL MARKET SNAPSHOT
    # ─────────────────────────────────────────────────────────────────────────
    def get_full_snapshot(
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
        
        return {
            "symbol": symbol_clean,
            "timestamp": datetime.now().isoformat(),
            "price": self.get_current_price(symbol),
            "technical": self.get_technical_snapshot(symbol_clean, df),
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
            "rss_valid": self._rss_cache.is_valid()
        }
    
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
