"""
order_executor.py - Emir Yürütücü Modülü
=========================================

Bu modül Binance API üzerinden emir yürütme işlemlerini yönetir.
Hem gerçek (canlı) hem de simülasyon (dry_run) modunu destekler.

Özellikler:
- Exponential backoff ile retry mekanizması
- Simülasyon modu (paper trading)
- Slippage ve fee hesaplama
- İdempotent clientOrderId üretimi
- Detaylı loglama

Kullanım Örnekleri:
------------------

# 1. Dry Run Modu (Paper Trading - Test için):
executor = OrderExecutor(dry_run=True)
order = await executor.create_order(
    symbol="BTCUSDT",
    side="BUY",
    quantity=0.001
)
print(order)  # Simüle edilmiş order response

# 2. Canlı Mod (Gerçek İşlemler):
from binance.client import Client
client = Client(api_key, api_secret)
executor = OrderExecutor(client=client, dry_run=False)
order = await executor.create_order(
    symbol="BTCUSDT",
    side="BUY",
    quantity=0.001,
    order_type="MARKET"
)

# 3. Limit Order:
order = await executor.create_order(
    symbol="ETHUSDT",
    side="SELL",
    quantity=0.5,
    order_type="LIMIT",
    price=4000.00,
    timeInForce="GTC"
)

# 4. Slippage ve Fee Hesaplama:
executed_price, fee = executor.simulate_slippage_and_fees(
    price=3500.00,
    quantity=0.1,
    slippage_pct=0.001,  # %0.1 slippage
    fee_pct=0.001        # %0.1 fee
)
"""

import asyncio
import logging
import time
import uuid
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

# Binance exception import
try:
    from binance.exceptions import BinanceAPIException
except ImportError:
    # Fallback if binance not installed
    class BinanceAPIException(Exception):
        def __init__(self, response=None, status_code=None, text=None):
            self.code = -1
            self.message = text or "Unknown error"
            super().__init__(self.message)

# Merkezi logger'ı import et
try:
    from trade_logger import logger
except ImportError:
    # Fallback: trade_logger yoksa kendi logger'ını kullan
    import logging
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(logging.INFO)
        formatter = logging.Formatter('[%(asctime)s] %(levelname)s - %(message)s', datefmt='%H:%M:%S')
        handler.setFormatter(formatter)
        logger.addHandler(handler)



class OrderExecutor:
    """
    Binance emir yürütücü sınıfı.
    
    Hem gerçek (canlı) hem de simülasyon (dry_run) modunu destekler.
    Retry mekanizması ve exponential backoff içerir.
    
    Attributes:
        client: Binance Client instance (canlı mod için zorunlu)
        dry_run: True ise simülasyon modu, False ise canlı mod
        max_retries: Başarısız işlemlerde maksimum deneme sayısı
    """
    
    def __init__(
        self,
        client: Optional[Any] = None,
        dry_run: bool = True,
        max_retries: int = 3
    ):
        """
        OrderExecutor'ı başlat.
        
        Args:
            client: Binance Client instance. dry_run=False için zorunlu.
            dry_run: True = simülasyon, False = gerçek işlem. Default: True
            max_retries: API hatalarında tekrar deneme sayısı. Default: 3
        
        Raises:
            ValueError: dry_run=False ve client=None ise
        """
        self.client = client
        self.dry_run = dry_run
        self.max_retries = max_retries
        
        # Canlı modda client zorunlu
        if not dry_run and client is None:
            raise ValueError(
                "Canlı mod (dry_run=False) için Binance client gerekli! "
                "OrderExecutor(client=your_client, dry_run=False)"
            )
        
        mode = "🟢 DRY RUN (Simülasyon)" if dry_run else "🔴 CANLI MOD"
        logger.info(f"OrderExecutor başlatıldı: {mode}")
    
    def _generate_client_order_id(self, symbol: str) -> str:
        """
        İdempotent clientOrderId üret.
        Format: {symbol}_{timestamp}_{short_uuid}
        
        Args:
            symbol: İşlem yapılacak sembol (örn: BTCUSDT)
        
        Returns:
            Benzersiz client order ID string
        """
        timestamp = int(time.time() * 1000)
        short_uuid = uuid.uuid4().hex[:8]
        return f"{symbol}_{timestamp}_{short_uuid}"
    
    def simulate_slippage_and_fees(
        self,
        price: float,
        quantity: float,
        slippage_pct: float = 0.001,
        fee_pct: float = 0.001
    ) -> Tuple[float, float]:
        """
        Slippage ve fee simülasyonu.
        
        Gerçek piyasa koşullarını simüle eder:
        - Slippage: Market emrinde gerçekleşen fiyat farkı
        - Fee: Binance işlem ücreti
        
        Args:
            price: Baz fiyat
            quantity: İşlem miktarı
            slippage_pct: Slippage yüzdesi (0.001 = %0.1). Default: 0.001
            fee_pct: Fee yüzdesi (0.001 = %0.1). Default: 0.001
        
        Returns:
            Tuple[executed_price, fee_amount]
            - executed_price: Slippage sonrası gerçekleşen fiyat
            - fee_amount: Ödenen toplam fee
        
        Example:
            >>> executor = OrderExecutor(dry_run=True)
            >>> price, fee = executor.simulate_slippage_and_fees(100.0, 1.0)
            >>> print(f"Price: {price}, Fee: {fee}")
            Price: 100.1, Fee: 0.1001
        """
        # Slippage uygula (alımda fiyat artar, satımda azalır - burada genel ortalama)
        executed_price = price * (1 + slippage_pct)
        
        # Fee hesapla
        trade_value = executed_price * quantity
        fee_amount = trade_value * fee_pct
        
        return round(executed_price, 8), round(fee_amount, 8)
    
    def _create_simulated_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "MARKET",
        price: Optional[float] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Simüle edilmiş order response oluştur.
        Binance API response formatıyla uyumlu.
        
        Args:
            symbol: İşlem sembolü
            side: BUY veya SELL
            quantity: İşlem miktarı
            order_type: MARKET, LIMIT, vb.
            price: Limit fiyatı (LIMIT emirler için)
            **kwargs: Ek parametreler
        
        Returns:
            Binance order response formatında dict
        """
        client_order_id = self._generate_client_order_id(symbol)
        order_id = int(time.time() * 1000) % 10000000000
        timestamp = int(time.time() * 1000)
        
        # Simüle edilmiş fiyat (gerçek fiyat verilmemişse)
        if price is None:
            # Bu değer normalde piyasadan alınır
            # Simülasyonda placeholder kullanıyoruz
            price = 0.0  # Gerçek fiyat dışarıdan sağlanmalı
        
        # Slippage ve fee uygula
        executed_price, fee = self.simulate_slippage_and_fees(price, quantity)
        
        # Binance response formatı
        order_response = {
            "symbol": symbol,
            "orderId": order_id,
            "orderListId": -1,
            "clientOrderId": client_order_id,
            "transactTime": timestamp,
            "price": str(price) if order_type == "LIMIT" else "0.00000000",
            "origQty": str(quantity),
            "executedQty": str(quantity),
            "cummulativeQuoteQty": str(round(executed_price * quantity, 8)),
            "status": "FILLED",
            "timeInForce": kwargs.get("timeInForce", "GTC"),
            "type": order_type,
            "side": side,
            "fills": [
                {
                    "price": str(executed_price),
                    "qty": str(quantity),
                    "commission": str(fee),
                    "commissionAsset": "USDT" if "USDT" in symbol else "BNB",
                    "tradeId": order_id + 1
                }
            ],
            # Simülasyon meta bilgisi
            "_simulated": True,
            "_executed_price": executed_price,
            "_fee": fee
        }
        
        return order_response
    
    async def create_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "MARKET",
        price: Optional[float] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Emir oluştur (async).
        
        dry_run=True ise simüle edilmiş order döner.
        dry_run=False ise gerçek Binance API çağrısı yapar.
        
        Args:
            symbol: İşlem sembolü (örn: BTCUSDT, ETHUSDT)
            side: İşlem yönü - "BUY" veya "SELL"
            quantity: İşlem miktarı
            order_type: Emir tipi - "MARKET", "LIMIT", vb. Default: "MARKET"
            price: Fiyat (LIMIT emirler için zorunlu)
            **kwargs: Ek Binance API parametreleri (timeInForce, stopPrice, vb.)
        
        Returns:
            Binance order response dict
        
        Raises:
            ValueError: Geçersiz parametreler
            BinanceAPIException: API hatası (max_retries sonrası)
        
        Example:
            >>> executor = OrderExecutor(dry_run=True)
            >>> order = await executor.create_order("BTCUSDT", "BUY", 0.001)
            >>> print(order["status"])  # "FILLED"
        """
        # Parametre validasyonu
        side = side.upper()
        if side not in ("BUY", "SELL"):
            raise ValueError(f"Geçersiz side: {side}. 'BUY' veya 'SELL' olmalı.")
        
        order_type = order_type.upper()
        if order_type == "LIMIT" and price is None:
            raise ValueError("LIMIT emirler için price zorunlu!")
        
        if quantity <= 0:
            raise ValueError(f"Geçersiz quantity: {quantity}. Pozitif olmalı.")
        
        # Client order ID oluştur
        client_order_id = self._generate_client_order_id(symbol)
        
        logger.info(
            f"{'[DRY RUN] ' if self.dry_run else ''}"
            f"Emir oluşturuluyor: {side} {quantity} {symbol} @ {order_type}"
            f"{f' ${price}' if price else ''}"
        )
        
        # ═══════════════════════════════════════════════════════════════════
        # DRY RUN MODU - Simülasyon
        # ═══════════════════════════════════════════════════════════════════
        if self.dry_run:
            # Küçük gecikme simülasyonu
            await asyncio.sleep(0.1)
            
            order = self._create_simulated_order(
                symbol=symbol,
                side=side,
                quantity=quantity,
                order_type=order_type,
                price=price,
                **kwargs
            )
            
            logger.info(
                f"[DRY RUN] ✅ Simüle edilmiş emir: "
                f"OrderId={order['orderId']}, Status={order['status']}"
            )
            
            return order
        
        # ═══════════════════════════════════════════════════════════════════
        # CANLI MOD - Gerçek İşlem
        # ═══════════════════════════════════════════════════════════════════
        last_exception = None
        
        for attempt in range(1, self.max_retries + 1):
            try:
                logger.info(f"API çağrısı deneme {attempt}/{self.max_retries}")
                
                # Binance API çağrısı
                if order_type == "MARKET":
                    order = self.client.create_order(
                        symbol=symbol,
                        side=side,
                        type=order_type,
                        quantity=quantity,
                        newClientOrderId=client_order_id,
                        **kwargs
                    )
                elif order_type == "LIMIT":
                    order = self.client.create_order(
                        symbol=symbol,
                        side=side,
                        type=order_type,
                        quantity=quantity,
                        price=str(price),
                        timeInForce=kwargs.get("timeInForce", "GTC"),
                        newClientOrderId=client_order_id,
                        **kwargs
                    )
                else:
                    order = self.client.create_order(
                        symbol=symbol,
                        side=side,
                        type=order_type,
                        quantity=quantity,
                        price=str(price) if price else None,
                        newClientOrderId=client_order_id,
                        **kwargs
                    )
                
                logger.info(
                    f"✅ Emir başarılı: OrderId={order.get('orderId')}, "
                    f"Status={order.get('status')}"
                )
                
                return order
                
            except BinanceAPIException as e:
                last_exception = e
                
                # Kalıcı hatalar (retry yapma)
                permanent_errors = [-1021, -2010, -2011, -1013, -1111]  # Timestamp, funds, order, lot size
                if e.code in permanent_errors:
                    logger.error(f"❌ Kalıcı API hatası (kod: {e.code}): {e.message}")
                    raise
                
                # Geçici hatalar - retry yap
                if attempt < self.max_retries:
                    # Exponential backoff: 1s, 2s, 4s...
                    wait_time = 2 ** (attempt - 1)
                    logger.warning(
                        f"⚠️ API hatası (kod: {e.code}): {e.message}. "
                        f"{wait_time}s sonra tekrar denenecek..."
                    )
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(
                        f"❌ Maksimum deneme sayısına ulaşıldı. "
                        f"Son hata (kod: {e.code}): {e.message}"
                    )
                    raise
                    
            except Exception as e:
                last_exception = e
                logger.error(f"❌ Beklenmeyen hata: {type(e).__name__}: {e}")
                
                if attempt < self.max_retries:
                    wait_time = 2 ** (attempt - 1)
                    logger.warning(f"⚠️ {wait_time}s sonra tekrar denenecek...")
                    await asyncio.sleep(wait_time)
                else:
                    raise
        
        # Bu noktaya ulaşılmamalı ama güvenlik için
        if last_exception:
            raise last_exception
        
        raise RuntimeError("Beklenmeyen durum: Order oluşturulamadı")
    
    async def cancel_order(
        self,
        symbol: str,
        order_id: Optional[int] = None,
        client_order_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Emri iptal et.
        
        Args:
            symbol: İşlem sembolü
            order_id: Binance order ID
            client_order_id: Client tarafından verilen order ID
        
        Returns:
            İptal response dict
        """
        if order_id is None and client_order_id is None:
            raise ValueError("order_id veya client_order_id gerekli!")
        
        if self.dry_run:
            logger.info(f"[DRY RUN] Emir iptal edildi: {order_id or client_order_id}")
            return {
                "symbol": symbol,
                "orderId": order_id or 0,
                "clientOrderId": client_order_id or "",
                "status": "CANCELED",
                "_simulated": True
            }
        
        # Canlı mod
        try:
            if order_id:
                result = self.client.cancel_order(symbol=symbol, orderId=order_id)
            else:
                result = self.client.cancel_order(symbol=symbol, origClientOrderId=client_order_id)
            
            logger.info(f"✅ Emir iptal edildi: {result.get('orderId')}")
            return result
            
        except BinanceAPIException as e:
            logger.error(f"❌ İptal hatası: {e.message}")
            raise


# ═══════════════════════════════════════════════════════════════════════════════
# TEST / DEMO
# ═══════════════════════════════════════════════════════════════════════════════
async def demo():
    """Demo fonksiyonu - dry_run modunda test."""
    print("\n" + "=" * 60)
    print("🧪 OrderExecutor Demo (Dry Run)")
    print("=" * 60 + "\n")
    
    # Dry run executor oluştur
    executor = OrderExecutor(dry_run=True)
    
    # Market BUY emri
    print("1. Market BUY emri:")
    order = await executor.create_order(
        symbol="BTCUSDT",
        side="BUY",
        quantity=0.001
    )
    print(f"   Order ID: {order['orderId']}")
    print(f"   Status: {order['status']}")
    print(f"   Simulated: {order.get('_simulated', False)}")
    
    # Market SELL emri
    print("\n2. Market SELL emri:")
    order = await executor.create_order(
        symbol="ETHUSDT",
        side="SELL",
        quantity=0.5
    )
    print(f"   Order ID: {order['orderId']}")
    
    # Slippage hesaplama
    print("\n3. Slippage ve Fee hesaplama:")
    price, fee = executor.simulate_slippage_and_fees(
        price=3500.0,
        quantity=0.1
    )
    print(f"   Orijinal fiyat: $3500.00")
    print(f"   Executed fiyat: ${price:.2f}")
    print(f"   Fee: ${fee:.4f}")
    
    print("\n" + "=" * 60)
    print("✅ Demo tamamlandı!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(demo())
