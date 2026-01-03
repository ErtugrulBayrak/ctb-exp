"""
regime_filter.py - Rejim Filtresi Modülü
=========================================

Trade açma öncesi piyasa koşullarını kontrol eder.
Rejim filtresi geçmezse BUY kesinlikle yapılmaz.

Filtreler:
1. ADX Filtresi: ADX(14) >= MIN_ADX_ENTRY
2. Volatilite Filtresi: MIN_ATR_PCT <= ATR_PCT <= MAX_ATR_PCT
3. Likidite Filtresi: Current volume >= Average volume * MIN_VOLUME_MULT

Kullanım:
    from strategies.regime_filter import RegimeFilter
    
    rf = RegimeFilter()
    passed, details = rf.check(snapshot)
    if not passed:
        logger.info(f"Rejim filtresi geçemedi: {details}")
"""

from typing import Dict, Any, Tuple, Optional
from dataclasses import dataclass

# Logger import
try:
    from trade_logger import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)

# Config import
try:
    from config import SETTINGS, get_min_atr_pct_for_symbol
except ImportError:
    # Fallback defaults
    class MockSettings:
        MIN_ADX_ENTRY = 20.0
        MIN_ATR_PCT = 0.3
        MAX_ATR_PCT = 3.0
        MIN_VOLUME_LOOKBACK = 10
        MIN_VOLUME_MULT = 0.8
    SETTINGS = MockSettings()
    
    def get_min_atr_pct_for_symbol(symbol: str) -> float:
        """Fallback: her sembol için sabit değer."""
        return 0.22


@dataclass
class RegimeCheckResult:
    """Rejim filtresi sonucu."""
    passed: bool
    blocked_by_regime: bool
    adx_ok: bool
    atr_ok: bool
    volume_ok: bool
    adx_value: float
    atr_pct: float
    volume_ratio: float
    reason: str


class RegimeFilter:
    """
    Rejim Filtresi - Trade sayısını düşürmek için zorunlu kontroller.
    
    Tüm filtreler geçmedikçe BUY sinyali üretilmez.
    """
    
    def __init__(
        self,
        min_adx: float = None,
        min_atr_pct: float = None,
        max_atr_pct: float = None,
        min_volume_lookback: int = None,
        min_volume_mult: float = None
    ):
        """
        RegimeFilter başlat.
        
        Args:
            min_adx: Minimum ADX değeri (varsayılan: SETTINGS.MIN_ADX_ENTRY)
            min_atr_pct: Minimum ATR yüzdesi (varsayılan: SETTINGS.MIN_ATR_PCT)
            max_atr_pct: Maximum ATR yüzdesi (varsayılan: SETTINGS.MAX_ATR_PCT)
            min_volume_lookback: Hacim karşılaştırma penceresi (varsayılan: SETTINGS.MIN_VOLUME_LOOKBACK)
            min_volume_mult: Minimum hacim çarpanı (varsayılan: SETTINGS.MIN_VOLUME_MULT)
        """
        self.min_adx = min_adx if min_adx is not None else getattr(SETTINGS, 'MIN_ADX_ENTRY', 20.0)
        self.min_atr_pct = min_atr_pct if min_atr_pct is not None else getattr(SETTINGS, 'MIN_ATR_PCT', 0.3)
        self.max_atr_pct = max_atr_pct if max_atr_pct is not None else getattr(SETTINGS, 'MAX_ATR_PCT', 3.0)
        self.min_volume_lookback = min_volume_lookback if min_volume_lookback is not None else getattr(SETTINGS, 'MIN_VOLUME_LOOKBACK', 10)
        self.min_volume_mult = min_volume_mult if min_volume_mult is not None else getattr(SETTINGS, 'MIN_VOLUME_MULT', 0.8)
    
    def check(self, snapshot: Dict[str, Any]) -> Tuple[bool, RegimeCheckResult]:
        """
        Rejim filtresini uygula.
        
        Args:
            snapshot: Piyasa snapshot'ı
                - technical.adx: ADX değeri
                - technical.atr: ATR değeri
                - price: Güncel fiyat
                - tf.1h.atr: 1h ATR (varsa)
                - volume_24h: 24 saatlik hacim
                - volume_avg: Ortalama hacim (varsa)
        
        Returns:
            Tuple[passed: bool, details: RegimeCheckResult]
        """
        # Varsayılan değerler
        adx_ok = False
        atr_ok = False
        volume_ok = False
        adx_value = 0.0
        atr_pct = 0.0
        volume_ratio = 0.0
        reasons = []
        
        # ─────────────────────────────────────────────────────────────────────────
        # 1. ADX Filtresi (YALNIZCA 1h - timeframe-safe)
        # ─────────────────────────────────────────────────────────────────────────
        technical = snapshot.get("technical", {})
        
        # ADX değerini YALNIZCA 1h kaynaktan al (timeframe karışmasını önle)
        tf_data = snapshot.get("tf", {}).get("1h", {})
        adx_value = tf_data.get("adx")
        adx_src = "tf.1h.adx"
        
        # Fallback: 1h olduğu garanti edilmiş technical_1h varsa
        if adx_value is None:
            technical_1h = snapshot.get("technical_1h", {})
            adx_value = technical_1h.get("adx")
            adx_src = "technical_1h.adx" if adx_value is not None else "missing"
        
        # ADX yoksa blokla (belirsiz timeframe fallback YOK)
        if adx_value is None:
            adx_value = 0.0
            adx_ok = False
            adx_src = "missing"
            reasons.append("ADX_MISSING_1H")
        elif adx_value >= self.min_adx:
            adx_ok = True
        else:
            reasons.append(f"ADX({adx_value:.1f}) < {self.min_adx}")
        
        # ─────────────────────────────────────────────────────────────────────────
        # 2. ATR Volatilite Filtresi (Sembol Bazlı Dinamik Eşik)
        # ─────────────────────────────────────────────────────────────────────────
        price = snapshot.get("price", 0.0)
        if price is None or price <= 0:
            price = technical.get("price", 0.0) or 0.0
        
        # ATR değerini al
        atr_value = tf_data.get("atr", technical.get("atr", 0.0))
        if atr_value is None:
            atr_value = 0.0
        
        # Sembol bazlı dinamik ATR eşiği (BTC=0.15%, ETH=0.20%, Altcoin=0.25%)
        symbol = snapshot.get("symbol", "UNKNOWN")
        dynamic_min_atr_pct = get_min_atr_pct_for_symbol(symbol)
        
        if price > 0 and atr_value > 0:
            atr_pct = (atr_value / price) * 100
            
            if dynamic_min_atr_pct <= atr_pct <= self.max_atr_pct:
                atr_ok = True
            else:
                if atr_pct < dynamic_min_atr_pct:
                    reasons.append(f"ATR_PCT({atr_pct:.2f}%) < {dynamic_min_atr_pct}% (düşük volatilite)")
                else:
                    reasons.append(f"ATR_PCT({atr_pct:.2f}%) > {self.max_atr_pct}% (aşırı volatilite)")
        else:
            reasons.append("ATR veya fiyat verisi eksik")
        
        # ─────────────────────────────────────────────────────────────────────────
        # 3. Likidite/Hacim Filtresi
        # ─────────────────────────────────────────────────────────────────────────
        current_volume = snapshot.get("volume_24h", 0.0)
        avg_volume = snapshot.get("volume_avg", 0.0)
        
        # Eğer volume_avg yoksa, volume_24h'i kendisiyle karşılaştır (her zaman geçer)
        if avg_volume is None or avg_volume <= 0:
            avg_volume = current_volume if current_volume and current_volume > 0 else 1.0
        
        if current_volume is None:
            current_volume = 0.0
        
        if avg_volume > 0:
            volume_ratio = current_volume / avg_volume
            
            if volume_ratio >= self.min_volume_mult:
                volume_ok = True
            else:
                reasons.append(f"Volume ratio({volume_ratio:.2f}) < {self.min_volume_mult}")
        else:
            # Hacim verisi yoksa filreyi geç (konservatif değil)
            volume_ok = True
            volume_ratio = 1.0
        
        # ─────────────────────────────────────────────────────────────────────────
        # Sonuç
        # ─────────────────────────────────────────────────────────────────────────
        passed = adx_ok and atr_ok and volume_ok
        
        if passed:
            reason = "Tüm rejim filtreleri geçildi"
        else:
            reason = "; ".join(reasons) if reasons else "Bilinmeyen sebep"
        
        result = RegimeCheckResult(
            passed=passed,
            blocked_by_regime=not passed,
            adx_ok=adx_ok,
            atr_ok=atr_ok,
            volume_ok=volume_ok,
            adx_value=adx_value,
            atr_pct=atr_pct,
            volume_ratio=volume_ratio,
            reason=reason
        )
        
        # Log
        if not passed:
            symbol = snapshot.get("symbol", "UNKNOWN")
            logger.info(
                f"[REGIME BLOCK] {symbol}: blocked_by_regime=True | "
                f"ADX={adx_value:.1f} ({'+' if adx_ok else 'X'}) ADX_SRC={adx_src} | "
                f"ATR_PCT={atr_pct:.2f}% ({'+' if atr_ok else 'X'}) | "
                f"VOL_RATIO={volume_ratio:.2f} ({'+' if volume_ok else 'X'}) | "
                f"Reason: {reason}"
            )
        
        return passed, result
    
    def check_simple(self, snapshot: Dict[str, Any]) -> bool:
        """
        Basit rejim kontrolü - sadece bool döndürür.
        
        Args:
            snapshot: Piyasa snapshot'ı
        
        Returns:
            True: Rejim filtresi geçildi
            False: Rejim filtresi geçilemedi
        """
        passed, _ = self.check(snapshot)
        return passed


# ═══════════════════════════════════════════════════════════════════════════════
# TEST / DEMO
# ═══════════════════════════════════════════════════════════════════════════════

def demo():
    """RegimeFilter demo - filtreleri test eder."""
    print("\n" + "=" * 60)
    print("📊 REGIME FILTER DEMO")
    print("=" * 60)
    
    rf = RegimeFilter()
    
    # Test 1: Tüm filtreler geçer
    snapshot_pass = {
        "symbol": "BTCUSDT",
        "price": 50000.0,
        "technical": {
            "adx": 28.5,
            "atr": 750.0
        },
        "volume_24h": 1_000_000_000,
        "volume_avg": 800_000_000
    }
    
    passed, result = rf.check(snapshot_pass)
    print(f"\n✅ Test 1 (Tümü Geçmeli):")
    print(f"   Passed: {passed}")
    print(f"   ADX: {result.adx_value:.1f} (ok={result.adx_ok})")
    print(f"   ATR%: {result.atr_pct:.2f}% (ok={result.atr_ok})")
    print(f"   Vol Ratio: {result.volume_ratio:.2f} (ok={result.volume_ok})")
    
    # Test 2: Düşük ADX
    snapshot_low_adx = {
        "symbol": "ETHUSDT",
        "price": 3000.0,
        "technical": {
            "adx": 15.0,  # Düşük
            "atr": 45.0
        },
        "volume_24h": 500_000_000,
        "volume_avg": 400_000_000
    }
    
    passed, result = rf.check(snapshot_low_adx)
    print(f"\n❌ Test 2 (Düşük ADX):")
    print(f"   Passed: {passed}")
    print(f"   Blocked: {result.blocked_by_regime}")
    print(f"   Reason: {result.reason}")
    
    # Test 3: Aşırı volatilite
    snapshot_high_vol = {
        "symbol": "SOLUSDT",
        "price": 100.0,
        "technical": {
            "adx": 35.0,
            "atr": 5.0  # %5 ATR - çok yüksek
        },
        "volume_24h": 100_000_000,
        "volume_avg": 80_000_000
    }
    
    passed, result = rf.check(snapshot_high_vol)
    print(f"\n❌ Test 3 (Aşırı Volatilite):")
    print(f"   Passed: {passed}")
    print(f"   ATR%: {result.atr_pct:.2f}%")
    print(f"   Reason: {result.reason}")
    
    print("\n" + "=" * 60)
    print("✅ Demo tamamlandı!")
    print("=" * 60)


if __name__ == "__main__":
    demo()
