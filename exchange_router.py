"""
exchange_router.py - Exchange Bağlantı Yöneticisi
==================================================

Bu modül Binance bağlantısını merkezi olarak yönetir:
- Paylaşımlı binance.Client instance
- WebSocket fiyat stream dinleyicisi
- Önbellekli get_price() fonksiyonu
- Order status takibi
- Otomatik reconnect mantığı
- Heartbeat ile bağlantı sağlığı kontrolü

Kullanım:
--------
    from exchange_router import ExchangeRouter

    router = ExchangeRouter(api_key, api_secret)
    await router.start()
    
    price = router.get_price("BTCUSDT")
    client = router.get_client()  # OrderExecutor için
    
    await router.stop()
"""

import asyncio
import time
from datetime import datetime
from typing import Any, Dict, Optional, Set, Callable
from threading import Thread, Lock

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

# Binance imports
try:
    from binance.client import Client
    from binance import ThreadedWebsocketManager
    BINANCE_AVAILABLE = True
except ImportError:
    BINANCE_AVAILABLE = False
    Client = None
    ThreadedWebsocketManager = None


# ═══════════════════════════════════════════════════════════════════════════════
# EXCHANGE ROUTER CLASS
# ═══════════════════════════════════════════════════════════════════════════════
class ExchangeRouter:
    """
    Merkezi exchange bağlantı yöneticisi.
    
    Binance client, websocket stream ve fiyat cache'ini yönetir.
    OrderExecutor bu sınıftan client instance alır.
    
    Attributes:
        client: Binance Client instance
        is_connected: Bağlantı durumu
        last_heartbeat: Son heartbeat zamanı
    """
    
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        symbols: Optional[Set[str]] = None,
        testnet: bool = False
    ):
        """
        ExchangeRouter'ı başlat.
        
        Args:
            api_key: Binance API key
            api_secret: Binance API secret
            symbols: İzlenecek semboller (default: {'BTCUSDT', 'ETHUSDT'})
            testnet: Testnet kullan (default: False)
        """
        if not BINANCE_AVAILABLE:
            raise ImportError("python-binance paketi yüklü değil. pip install python-binance")
        
        self._api_key = api_key
        self._api_secret = api_secret
        self._testnet = testnet
        self._symbols = symbols or {'BTCUSDT', 'ETHUSDT'}
        
        # Client
        self._client: Optional[Client] = None
        
        # WebSocket Manager
        self._twm: Optional[ThreadedWebsocketManager] = None
        self._ws_thread: Optional[Thread] = None
        
        # Price Cache
        self._price_cache: Dict[str, float] = {}
        self._price_lock = Lock()
        self._cache_ttl = 5.0  # saniye
        self._price_timestamps: Dict[str, float] = {}
        
        # Order State Tracking
        self._order_state: Dict[str, Dict[str, Any]] = {}
        self._order_lock = Lock()
        
        # Connection State
        self._is_connected = False
        self._last_heartbeat: float = 0.0
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 5
        self._reconnect_delay = 5.0  # saniye
        
        # Asyncio
        self._ws_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._running = False
        
        # Callbacks
        self._price_callbacks: list[Callable[[str, float], None]] = []
    
    # ─────────────────────────────────────────────────────────────────────────
    # PROPERTIES
    # ─────────────────────────────────────────────────────────────────────────
    @property
    def is_connected(self) -> bool:
        """WebSocket bağlantı durumu."""
        return self._is_connected
    
    @property
    def last_heartbeat(self) -> float:
        """Son heartbeat timestamp (Unix time)."""
        return self._last_heartbeat
    
    @property
    def heartbeat_age(self) -> float:
        """Son heartbeat'ten bu yana geçen süre (saniye)."""
        if self._last_heartbeat == 0:
            return float('inf')
        return time.time() - self._last_heartbeat
    
    # ─────────────────────────────────────────────────────────────────────────
    # CLIENT ACCESS
    # ─────────────────────────────────────────────────────────────────────────
    def get_client(self) -> Optional[Client]:
        """
        Binance Client instance döndür.
        OrderExecutor bu client'ı kullanır.
        
        Returns:
            Binance Client veya None
        """
        return self._client
    
    def _create_client(self) -> Client:
        """Binance Client oluştur."""
        if self._testnet:
            client = Client(
                self._api_key,
                self._api_secret,
                testnet=True
            )
            client.API_URL = 'https://testnet.binance.vision/api'
        else:
            client = Client(self._api_key, self._api_secret)
        
        return client
    
    # ─────────────────────────────────────────────────────────────────────────
    # PRICE CACHE
    # ─────────────────────────────────────────────────────────────────────────
    def get_price(self, symbol: str) -> Optional[float]:
        """
        Önbellekten fiyat al.
        
        Cache miss veya stale ise None döner.
        WebSocket aktifse sürekli güncel fiyat alınır.
        
        Args:
            symbol: Sembol (örn: BTCUSDT)
        
        Returns:
            Fiyat float veya None
        """
        symbol = symbol.upper()
        
        with self._price_lock:
            if symbol not in self._price_cache:
                return None
            
            # TTL kontrolü
            timestamp = self._price_timestamps.get(symbol, 0)
            if time.time() - timestamp > self._cache_ttl:
                return None
            
            return self._price_cache[symbol]
    
    def get_price_or_fetch(self, symbol: str) -> Optional[float]:
        """
        Önce cache'e bak, yoksa API'den çek.
        
        Args:
            symbol: Sembol (örn: BTCUSDT)
        
        Returns:
            Fiyat float veya None
        """
        # Cache'den dene
        cached = self.get_price(symbol)
        if cached is not None:
            return cached
        
        # API'den çek
        if self._client:
            try:
                ticker = self._client.get_symbol_ticker(symbol=symbol.upper())
                price = float(ticker['price'])
                self._update_price_cache(symbol, price)
                return price
            except Exception as e:
                logger.warning(f"[ExchangeRouter] Fiyat çekilemedi {symbol}: {e}")
        
        return None
    
    def _update_price_cache(self, symbol: str, price: float) -> None:
        """Cache'i güncelle."""
        with self._price_lock:
            self._price_cache[symbol.upper()] = price
            self._price_timestamps[symbol.upper()] = time.time()
    
    def get_all_prices(self) -> Dict[str, float]:
        """Tüm cached fiyatları döndür."""
        with self._price_lock:
            return dict(self._price_cache)
    
    def add_price_callback(self, callback: Callable[[str, float], None]) -> None:
        """Fiyat güncellemesi callback'i ekle."""
        self._price_callbacks.append(callback)
    
    # ─────────────────────────────────────────────────────────────────────────
    # ORDER STATE
    # ─────────────────────────────────────────────────────────────────────────
    def get_order_state(self, order_id: str) -> Optional[Dict[str, Any]]:
        """
        Bellekteki order state'ini al.
        
        Args:
            order_id: Order ID (string)
        
        Returns:
            Order state dict veya None
        """
        with self._order_lock:
            return self._order_state.get(order_id)
    
    def set_order_state(self, order_id: str, state: Dict[str, Any]) -> None:
        """Order state kaydet."""
        with self._order_lock:
            self._order_state[order_id] = {
                **state,
                'updated_at': time.time()
            }
    
    def order_status(self, order_id: int, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Binance'den order status çek.
        
        Args:
            order_id: Binance order ID
            symbol: Sembol
        
        Returns:
            Order info dict veya None
        """
        if not self._client:
            logger.warning("[ExchangeRouter] Client yok, order_status çekilemedi")
            return None
        
        try:
            order = self._client.get_order(symbol=symbol, orderId=order_id)
            
            # In-memory state güncelle
            self.set_order_state(str(order_id), order)
            
            return order
        except Exception as e:
            logger.error(f"[ExchangeRouter] Order status hatası: {e}")
            return None
    
    # ─────────────────────────────────────────────────────────────────────────
    # WEBSOCKET MANAGEMENT
    # ─────────────────────────────────────────────────────────────────────────
    def _handle_socket_message(self, msg: Dict[str, Any]) -> None:
        """WebSocket mesaj handler."""
        if msg.get('e') == 'error':
            logger.error(f"[ExchangeRouter] WebSocket error: {msg}")
            self._is_connected = False
            return
        
        # Mini ticker stream
        if 's' in msg and 'c' in msg:
            symbol = msg['s']
            price = float(msg['c'])
            self._update_price_cache(symbol, price)
            self._last_heartbeat = time.time()
            
            # Callback'leri çağır
            for cb in self._price_callbacks:
                try:
                    cb(symbol, price)
                except Exception as e:
                    logger.warning(f"[ExchangeRouter] Callback hatası: {e}")
    
    def _start_websocket_sync(self) -> None:
        """Senkron WebSocket başlat (thread içinde)."""
        try:
            self._twm = ThreadedWebsocketManager(
                api_key=self._api_key,
                api_secret=self._api_secret
            )
            self._twm.start()
            
            # Her sembol için mini ticker stream başlat
            for symbol in self._symbols:
                self._twm.start_symbol_miniticker_socket(
                    callback=self._handle_socket_message,
                    symbol=symbol.lower()
                )
                logger.info(f"[ExchangeRouter] WebSocket stream başlatıldı: {symbol}")
            
            self._is_connected = True
            self._last_heartbeat = time.time()
            self._reconnect_attempts = 0
            
            logger.info("[ExchangeRouter] ✅ WebSocket bağlantısı kuruldu")
            
        except Exception as e:
            logger.error(f"[ExchangeRouter] WebSocket başlatma hatası: {e}")
            self._is_connected = False
    
    def _stop_websocket_sync(self) -> None:
        """WebSocket'i durdur."""
        if self._twm:
            try:
                self._twm.stop()
                self._twm = None
                self._is_connected = False
                logger.info("[ExchangeRouter] WebSocket durduruldu")
            except Exception as e:
                logger.warning(f"[ExchangeRouter] WebSocket durdurma hatası: {e}")
    
    # ─────────────────────────────────────────────────────────────────────────
    # ASYNC LIFECYCLE
    # ─────────────────────────────────────────────────────────────────────────
    async def start(self) -> bool:
        """
        Router'ı başlat (async).
        
        Client oluşturur ve WebSocket stream'i başlatır.
        
        Returns:
            Başarılı ise True
        """
        if self._running:
            logger.warning("[ExchangeRouter] Zaten çalışıyor")
            return True
        
        try:
            # Client oluştur
            self._client = self._create_client()
            logger.info("[ExchangeRouter] ✅ Binance Client oluşturuldu")
            
            # WebSocket'i ayrı thread'de başlat
            self._ws_thread = Thread(
                target=self._start_websocket_sync,
                daemon=True
            )
            self._ws_thread.start()
            
            # Bağlantı için bekle
            await asyncio.sleep(2)
            
            # Heartbeat task başlat
            self._running = True
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            
            return True
            
        except Exception as e:
            logger.error(f"[ExchangeRouter] Başlatma hatası: {e}")
            return False
    
    async def stop(self) -> None:
        """Router'ı durdur."""
        self._running = False
        
        # Heartbeat task'ı iptal et
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        
        # WebSocket durdur
        self._stop_websocket_sync()
        
        # Client temizle
        self._client = None
        
        logger.info("[ExchangeRouter] ✅ Router durduruldu")
    
    async def _heartbeat_loop(self) -> None:
        """Heartbeat ve reconnect döngüsü."""
        while self._running:
            try:
                await asyncio.sleep(10)  # 10 saniyede bir kontrol
                
                # Heartbeat kontrolü
                if self.heartbeat_age > 30:  # 30 saniyedir veri yok
                    logger.warning(f"[ExchangeRouter] ⚠️ Heartbeat timeout ({self.heartbeat_age:.0f}s)")
                    
                    if self._reconnect_attempts < self._max_reconnect_attempts:
                        await self._reconnect()
                    else:
                        logger.error("[ExchangeRouter] ❌ Max reconnect denemesi aşıldı")
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[ExchangeRouter] Heartbeat hatası: {e}")
    
    async def _reconnect(self) -> None:
        """WebSocket'i yeniden bağla."""
        self._reconnect_attempts += 1
        logger.info(f"[ExchangeRouter] 🔄 Reconnect denemesi {self._reconnect_attempts}/{self._max_reconnect_attempts}")
        
        # Mevcut bağlantıyı kapat
        self._stop_websocket_sync()
        
        # Bekle
        await asyncio.sleep(self._reconnect_delay)
        
        # Yeniden bağlan
        self._ws_thread = Thread(
            target=self._start_websocket_sync,
            daemon=True
        )
        self._ws_thread.start()
        
        # Bağlantı için bekle
        await asyncio.sleep(2)
    
    # ─────────────────────────────────────────────────────────────────────────
    # SYMBOL MANAGEMENT
    # ─────────────────────────────────────────────────────────────────────────
    def add_symbol(self, symbol: str) -> None:
        """
        İzlenecek sembol ekle.
        
        Not: Aktif WebSocket'e ekleme yapmak için restart gerekir.
        """
        self._symbols.add(symbol.upper())
        logger.info(f"[ExchangeRouter] Sembol eklendi: {symbol}")
    
    def remove_symbol(self, symbol: str) -> None:
        """Sembolü kaldır."""
        self._symbols.discard(symbol.upper())
    
    def get_symbols(self) -> Set[str]:
        """İzlenen sembolleri döndür."""
        return set(self._symbols)
    
    # ─────────────────────────────────────────────────────────────────────────
    # HEALTH CHECK
    # ─────────────────────────────────────────────────────────────────────────
    def health_check(self) -> Dict[str, Any]:
        """
        Bağlantı sağlık kontrolü.
        
        Returns:
            Health status dict
        """
        return {
            'is_connected': self._is_connected,
            'last_heartbeat': self._last_heartbeat,
            'heartbeat_age_seconds': self.heartbeat_age,
            'reconnect_attempts': self._reconnect_attempts,
            'cached_prices_count': len(self._price_cache),
            'tracked_orders_count': len(self._order_state),
            'symbols': list(self._symbols),
            'client_ready': self._client is not None,
            'timestamp': datetime.now().isoformat()
        }
    
    def __repr__(self) -> str:
        status = "connected" if self._is_connected else "disconnected"
        return f"<ExchangeRouter {status} symbols={len(self._symbols)}>"


# ═══════════════════════════════════════════════════════════════════════════════
# FACTORY FUNCTION
# ═══════════════════════════════════════════════════════════════════════════════
def create_router(
    api_key: str,
    api_secret: str,
    symbols: Optional[Set[str]] = None,
    testnet: bool = False
) -> ExchangeRouter:
    """
    ExchangeRouter factory fonksiyonu.
    
    Args:
        api_key: Binance API key
        api_secret: Binance API secret
        symbols: İzlenecek semboller
        testnet: Testnet modu
    
    Returns:
        ExchangeRouter instance
    """
    return ExchangeRouter(
        api_key=api_key,
        api_secret=api_secret,
        symbols=symbols,
        testnet=testnet
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TEST / DEMO
# ═══════════════════════════════════════════════════════════════════════════════
async def demo():
    """Demo - API key gerektirir."""
    import os
    from dotenv import load_dotenv
    
    load_dotenv()
    
    api_key = os.getenv('BINANCE_API_KEY', '')
    api_secret = os.getenv('BINANCE_API_SECRET', '')
    
    if not api_key or not api_secret:
        print("❌ BINANCE_API_KEY ve BINANCE_API_SECRET gerekli")
        return
    
    print("\n" + "=" * 60)
    print("🔄 EXCHANGE ROUTER DEMO")
    print("=" * 60 + "\n")
    
    # Router oluştur
    router = create_router(
        api_key=api_key,
        api_secret=api_secret,
        symbols={'BTCUSDT', 'ETHUSDT'}
    )
    
    # Başlat
    print("▶️  Router başlatılıyor...")
    success = await router.start()
    
    if not success:
        print("❌ Router başlatılamadı")
        return
    
    print("✅ Router başlatıldı\n")
    
    # Fiyatları izle
    print("📊 Fiyat stream'i dinleniyor (10 saniye)...\n")
    
    for i in range(10):
        await asyncio.sleep(1)
        
        btc = router.get_price("BTCUSDT")
        eth = router.get_price("ETHUSDT")
        
        btc_str = f"${btc:,.2f}" if btc else "N/A"
        eth_str = f"${eth:,.2f}" if eth else "N/A"
        
        print(f"  [{i+1}/10] BTC: {btc_str} | ETH: {eth_str}")
    
    # Health check
    print("\n📋 Health Check:")
    health = router.health_check()
    for k, v in health.items():
        print(f"   {k}: {v}")
    
    # Client'ı OrderExecutor'a ver
    print("\n🔗 OrderExecutor entegrasyonu:")
    client = router.get_client()
    print(f"   Client ready: {client is not None}")
    
    # Durdur
    await router.stop()
    print("\n✅ Router durduruldu")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(demo())
