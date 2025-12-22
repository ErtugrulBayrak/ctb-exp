"""
risk_manager.py - Risk Management & Position Sizing
====================================================

Bu modül tüm risk yönetimi, pozisyon büyüklüğü hesaplama ve güvenlik kontrollerini (guardrails) içerir.
StrategyEngine sadece sinyal üretir (BUY/SELL/HOLD), RiskManager bu sinyali doğrular ve parametreleri (Miktar, SL/TP) belirler.
"""

from typing import Dict, Any, Optional
from datetime import datetime
from config import SETTINGS

# Logger import
try:
    from trade_logger import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)

class RiskManager:
    """
    Risk yönetimi ve pozisyon boyutlandırma sınıfı.
    
    V1 Eklentileri:
    - Volatilite hedeflemeli pozisyon boyutlandırma
    - Ardışık stop takibi ve cooldown
    - V1 strateji modu için özel parametreler
    """
    
    def __init__(
        self,
        config: Dict[str, Any] = None,
        initial_balance: float = None
    ):
        """
        Args:
            config: Yapılandırma parametreleri
            initial_balance: Opsiyonel başlangıç bakiyesi
        """
        self.config = config or {}
        # Use config or fall back to SETTINGS
        self._min_adx = self.config.get("min_adx") or SETTINGS.MIN_ADX_ENTRY
        self._min_volume = self.config.get("min_volume") or getattr(SETTINGS, 'MIN_VOLUME_GUARDRAIL', 1_000_000)
        self._fng_extreme_fear = self.config.get("fng_extreme_fear") or getattr(SETTINGS, 'FNG_EXTREME_FEAR', 20)
        self._risk_per_trade = self.config.get("risk_per_trade") or getattr(SETTINGS, 'RISK_PER_TRADE', 0.02)
        self._initial_balance = initial_balance
        
        # V1 Parametreleri
        self._strategy_mode = getattr(SETTINGS, 'STRATEGY_MODE', 'LEGACY')
        self._risk_per_trade_v1 = getattr(SETTINGS, 'RISK_PER_TRADE_V1', 1.0) / 100.0  # %1 = 0.01
        self._target_atr_pct = getattr(SETTINGS, 'TARGET_ATR_PCT', 1.0)
        self._min_vol_scale = getattr(SETTINGS, 'MIN_VOL_SCALE', 0.5)
        self._max_vol_scale = getattr(SETTINGS, 'MAX_VOL_SCALE', 1.5)
        
        # Ardışık stop takibi
        self._max_consecutive_stops = getattr(SETTINGS, 'MAX_CONSECUTIVE_STOPS', 3)
        self._consecutive_stops_cooldown = getattr(SETTINGS, 'CONSECUTIVE_STOPS_EXTRA_COOLDOWN', 30)
        self._consecutive_stop_count = 0
        self._last_stop_time = None
        self._in_cooldown = False
        self._cooldown_end_time = None
        
        logger.debug(
            f"RiskManager başlatıldı: ADX={self._min_adx}, Volume=${self._min_volume/1e6:.1f}M, "
            f"Risk={self._risk_per_trade*100:.1f}%, Mode={self._strategy_mode}"
        )

    def evaluate_entry_risk(
        self,
        snapshot: Dict[str, Any],
        base_decision: Dict[str, Any],
        portfolio: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Giriş (BUY) sinyalini risk kurallarına göre değerlendirir ve tamamlar.
        """
        result = base_decision.copy()
        result.setdefault("metadata", {})
        
        # 1. Base Decision Check
        if base_decision.get("action") != "BUY":
            result["allowed"] = False
            result["reason"] = f"Base signal not BUY ({base_decision.get('action')})"
            return result

        # 2. Guardrails Check
        confidence = base_decision.get("confidence", 0)
        guardrails = self._check_guardrails(snapshot, confidence=confidence)
        
        if not guardrails["passed"]:
            result["allowed"] = False
            result["action"] = "HOLD"
            result["reason"] = f"Risk Guardrail: {guardrails['reason']}"
            return result
        
        result["metadata"]["guardrails_passed"] = True
        
        # 3. Calculate SL/TP
        technical = snapshot.get("technical", {})
        price = snapshot.get("price") or technical.get("price")
        if not price:
            result["allowed"] = False
            result["reason"] = "No price data for sizing"
            return result
            
        atr = technical.get("atr", 0)
        
        # Get bias from base_decision metadata (from LLM)
        metadata = base_decision.get("metadata", {})
        sl_bias = metadata.get("sl_bias", "neutral")
        tp_bias = metadata.get("tp_bias", "neutral")
        
        sl_tp = self._calculate_sl_tp(price, atr, sl_bias=sl_bias, tp_bias=tp_bias)
        result["stop_loss"] = sl_tp["stop_loss"]
        result["take_profit"] = sl_tp["take_profit"]
        result["metadata"]["sl_bias"] = sl_bias
        result["metadata"]["tp_bias"] = tp_bias
        
        # 4. Calculate Quantity
        balance = portfolio.get("balance", 0)
        
        quantity = self._calculate_quantity(balance, price, result["stop_loss"])
        
        if quantity <= 0:
            result["allowed"] = False
            result["action"] = "HOLD"
            result["reason"] = "Calculated quantity is 0 (insufficient funds or high risk)"
            return result
            
        result["quantity"] = quantity
        result["allowed"] = True
        
        return result

    def evaluate_exit_risk(
        self,
        snapshot: Dict[str, Any],
        position: Dict[str, Any],
        base_decision: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Çıkış (SELL) sinyalini risk kurallarına göre değerlendirir.
        
        Output: "allowed", "action", "reason", "quantity"
        """
        result = base_decision.copy()
        result.setdefault("metadata", {})
        
        current_price = snapshot.get("price")
        
        # 1. Hard Logic Checks (Pos SL/TP) - Bunlar StrategyEngine'de de olabilir ama
        # RiskManager bunları kesinleştirmeli.
        # StrategyEngine zaten "SL tetiklendi" diyorsa burada onaylarız.
        
        if base_decision.get("action") == "SELL":
            result["allowed"] = True
            
            # Miktar kontrolü (Strategy engine genelde hepsini sat der)
            pos_qty = position.get("quantity", 0)
            req_qty = base_decision.get("quantity", 0)
            
            if req_qty <= 0:
                result["quantity"] = pos_qty # Default all
            else:
                result["quantity"] = min(req_qty, pos_qty)
                
            return result
            
        # Eğer Strategy HOLD dediyse ama Risk "Acil Çık" diyorsa?
        # Örn: StrategyEngine Fiyat/ATR bazlı SL kullanıyor.
        # Olağanüstü durumlar için buraya ek kural konabilir (örn. %50 ani düşüş).
        # Şimdilik StrategyEngine kararına uyuyoruz.
        
        result["allowed"] = False
        return result

    def _check_guardrails(self, snapshot: Dict[str, Any], confidence: int = 0) -> Dict[str, Any]:
        """Güvenlik kontrolleri (StrategyEngine'den taşındı)."""
        technical = snapshot.get("technical", {})
        sentiment = snapshot.get("sentiment", {})
        
        # 1. Trend check (Düşüş trendinde alım yapma)
        trend = technical.get("trend", "NEUTRAL")
        if trend in ["BEARISH", "NEUTRAL_BEARISH"]:
            if technical.get("trend_strength") == "STRONG":
                return {"passed": False, "reason": f"Strong downtrend ({trend})"}
        
        # 2. ADX check with Softening
        adx = technical.get("adx")
        threshold = self._min_adx
        
        # Optional softening if confidence is high
        if confidence >= SETTINGS.SOFTEN_ADX_WHEN_CONF_GE:
             threshold = min(threshold, SETTINGS.MIN_ADX_ENTRY_SOFT)
             
        if adx is not None and adx < threshold:
            return {"passed": False, "reason": f"Low ADX ({adx:.1f} < {threshold})"}
        
        # 3. Volume check
        # Volume is now at root of snapshot (USDT volume)
        volume = snapshot.get("volume_24h")
        if not volume:
             # Fallback to technical if root is missing (legacy support)
             volume = technical.get("volume_24h")
        
        # If volume data is missing or non-positive, DO NOT enforce volume guardrail.
        if volume is None or volume <= 0:
             # from trade_logger import logger # risk_manager doesn't have logger by default?
             # Assuming we prefer silent skip or print. We can just pass.
             pass 
        else:
            if volume < self._min_volume:
                vol_m = volume / 1_000_000
                min_m = self._min_volume / 1_000_000
                return {
                    "passed": False, 
                    "reason": f"Low Volume: ${vol_m:.1f}M < ${min_m:.1f}M",
                    "blocked_by": "volume"
                }
        
        # 4. Fear & Greed check (Extreme Fear'da alma)
        fng = sentiment.get("fear_greed", {})
        fng_value = fng.get("value")
        if fng_value is not None and fng_value <= self._fng_extreme_fear:
             return {"passed": False, "reason": f"Extreme Fear ({fng_value})"}
             
        return {"passed": True, "reason": "OK"}

    def _calculate_sl_tp(
        self,
        price: float,
        atr: float,
        sl_bias: str = "neutral",
        tp_bias: str = "neutral"
    ) -> Dict[str, float]:
        """
        ATR tabanlı SL/TP hesapla.
        
        Args:
            price: Giriş fiyatı
            atr: Average True Range değeri
            sl_bias: "tighter" | "looser" | "neutral" - SL mesafesini ayarlar
            tp_bias: "tighter" | "looser" | "neutral" - TP mesafesini ayarlar
        
        Bias Etkileri:
            - tighter: Mesafeyi %25 azaltır (daha sıkı)
            - looser: Mesafeyi %25 artırır (daha geniş)
            - neutral: Varsayılan değer (değişiklik yok)
        """
        # Bias multipliers
        bias_map = {
            "tighter": 0.75,  # %25 daha sıkı
            "looser": 1.25,   # %25 daha geniş
            "neutral": 1.0
        }
        
        sl_mult = bias_map.get(sl_bias, 1.0)
        tp_mult = bias_map.get(tp_bias, 1.0)
        
        if not atr or atr <= 0:
            # Fallback: %3 SL, %5 TP (bias uygulanır)
            base_sl_pct = 0.03 * sl_mult
            base_tp_pct = 0.05 * tp_mult
            return {
                "stop_loss": round(price * (1 - base_sl_pct), 2),
                "take_profit": round(price * (1 + base_tp_pct), 2)
            }
        
        # ATR-based: 2x ATR SL, 3x ATR TP (bias uygulanır)
        sl_distance = 2 * atr * sl_mult
        tp_distance = 3 * atr * tp_mult
        
        return {
            "stop_loss": round(price - sl_distance, 2),
            "take_profit": round(price + tp_distance, 2)
        }

    def _calculate_quantity(
        self,
        balance: float,
        price: float,
        stop_loss: float,
        atr: float = None
    ) -> float:
        """
        Risk tabanlı pozisyon boyutu.
        
        V1 modunda volatilite hedeflemeli ölçekleme uygulanır:
        - atr_pct = atr / price * 100
        - vol_scale = clamp(TARGET_ATR_PCT / atr_pct, MIN_VOL_SCALE, MAX_VOL_SCALE)
        - qty *= vol_scale
        """
        if balance <= 0:
            return 0.0
        
        # V1 modunda risk yüzdesini kullan
        if self._strategy_mode == "REGIME_SWING_TREND_V1":
            risk_pct = self._risk_per_trade_v1
        else:
            risk_pct = self._risk_per_trade

        if not stop_loss or stop_loss >= price:
            # Fallback: bakiyenin risk%'si
            trade_amount = balance * risk_pct
            return round(trade_amount / price, 6)
        
        # Risk amount (Para bazında risk)
        risk_amount = balance * risk_pct
        
        # Risk per unit (Birim başına risk)
        risk_per_unit = price - stop_loss
        
        if risk_per_unit > 0:
            quantity = risk_amount / risk_per_unit
            
            # V1: Volatilite hedeflemeli ölçekleme
            if self._strategy_mode == "REGIME_SWING_TREND_V1" and atr and atr > 0:
                atr_pct = (atr / price) * 100
                if atr_pct > 0:
                    vol_scale = self._target_atr_pct / atr_pct
                    vol_scale = max(self._min_vol_scale, min(self._max_vol_scale, vol_scale))
                    quantity *= vol_scale
                    logger.debug(
                        f"[V1 VOL_SCALE] ATR%={atr_pct:.2f}, "
                        f"Scale={vol_scale:.2f}, Qty adjusted"
                    )
            
            # Max Cap: Bakiyenin %10'u (Likidite/Slipaj koruması)
            max_quantity = (balance * 0.10) / price
            
            return round(min(quantity, max_quantity), 6)
            
        # Fallback
        return round((balance * risk_pct) / price, 6)
    
    # ═══════════════════════════════════════════════════════════════════════════
    # V1: Ardışık Stop Yönetimi
    # ═══════════════════════════════════════════════════════════════════════════
    
    def register_stop_hit(self) -> bool:
        """
        Stop loss tetiklendiğinde çağır.
        
        Returns:
            True: Cooldown başladı
            False: Cooldown başlamadı
        """
        import time as t
        
        self._consecutive_stop_count += 1
        self._last_stop_time = t.time()
        
        logger.info(
            f"[RISK] Stop #{ self._consecutive_stop_count} / {self._max_consecutive_stops}"
        )
        
        if self._consecutive_stop_count >= self._max_consecutive_stops:
            self._in_cooldown = True
            cooldown_minutes = getattr(SETTINGS, 'COOLDOWN_MINUTES', 60) + self._consecutive_stops_cooldown
            self._cooldown_end_time = t.time() + (cooldown_minutes * 60)
            logger.warning(
                f"[RISK] ⚠️ MAX CONSECUTIVE STOPS reached! "
                f"Cooldown for {cooldown_minutes} minutes"
            )
            return True
        
        return False
    
    def register_win(self):
        """Kazançlı trade sonrası ardışık stop sayacını sıfırla."""
        if self._consecutive_stop_count > 0:
            logger.info(f"[RISK] Consecutive stops reset (was {self._consecutive_stop_count})")
        self._consecutive_stop_count = 0
    
    def is_in_cooldown(self) -> tuple:
        """
        Cooldown durumunu kontrol et.
        
        Returns:
            Tuple[bool, str]: (in_cooldown, reason)
        """
        import time as t
        
        if not self._in_cooldown:
            return False, ""
        
        if t.time() >= self._cooldown_end_time:
            self._in_cooldown = False
            self._consecutive_stop_count = 0
            logger.info("[RISK] Cooldown ended, trading resumed")
            return False, ""
        
        remaining = int((self._cooldown_end_time - t.time()) / 60)
        return True, f"Cooldown active ({remaining} min remaining)"
    
    def get_consecutive_stops(self) -> int:
        """Ardışık stop sayısını döndür."""
        return self._consecutive_stop_count


# ═══════════════════════════════════════════════════════════════════════════════
# TEST / DEMO
# ═══════════════════════════════════════════════════════════════════════════════

def demo():
    """RiskManager demo - tüm risk kontrollerini test eder."""
    print("\n" + "=" * 60)
    print("🧪 RISK MANAGER DEMO")
    print("=" * 60 + "\n")
    
    rm = RiskManager()
    
    print("📋 Config Değerleri (config.py'den):")
    print(f"   MIN_ADX_ENTRY: {rm._min_adx}")
    print(f"   MIN_VOLUME_GUARDRAIL: ${rm._min_volume/1e6:.1f}M")
    print(f"   FNG_EXTREME_FEAR: {rm._fng_extreme_fear}")
    print(f"   RISK_PER_TRADE: {rm._risk_per_trade*100:.1f}%")
    
    print("\n" + "-" * 60)
    print("📊 TEST 1: Guardrails Check")
    print("-" * 60)
    
    # Test senaryoları
    test_cases = [
        {
            "name": "✅ Tüm koşullar OK",
            "snapshot": {
                "technical": {"trend": "BULLISH", "adx": 25},
                "volume_24h": 5_000_000,
                "sentiment": {"fear_greed": {"value": 50}}
            }
        },
        {
            "name": "❌ Düşük ADX",
            "snapshot": {
                "technical": {"trend": "BULLISH", "adx": 15},
                "volume_24h": 5_000_000,
                "sentiment": {"fear_greed": {"value": 50}}
            }
        },
        {
            "name": "❌ Düşük Volume",
            "snapshot": {
                "technical": {"trend": "BULLISH", "adx": 25},
                "volume_24h": 500_000,
                "sentiment": {"fear_greed": {"value": 50}}
            }
        },
        {
            "name": "❌ Extreme Fear",
            "snapshot": {
                "technical": {"trend": "BULLISH", "adx": 25},
                "volume_24h": 5_000_000,
                "sentiment": {"fear_greed": {"value": 15}}
            }
        }
    ]
    
    for tc in test_cases:
        result = rm._check_guardrails(tc["snapshot"])
        status = "✅ PASSED" if result["passed"] else f"❌ BLOCKED: {result['reason']}"
        print(f"   {tc['name']}: {status}")
    
    print("\n" + "-" * 60)
    print("📊 TEST 2: SL/TP Hesaplama")
    print("-" * 60)
    
    price = 100.0
    atr = 2.0
    
    print(f"   Fiyat: ${price}, ATR: ${atr}")
    
    # Farklı bias senaryoları
    for sl_bias in ["neutral", "tighter", "looser"]:
        result = rm._calculate_sl_tp(price, atr, sl_bias=sl_bias)
        print(f"   SL Bias={sl_bias}: SL=${result['stop_loss']:.2f}, TP=${result['take_profit']:.2f}")
    
    print("\n" + "-" * 60)
    print("📊 TEST 3: Quantity Hesaplama")
    print("-" * 60)
    
    balance = 1000.0
    price = 100.0
    stop_loss = 95.0
    
    quantity = rm._calculate_quantity(balance, price, stop_loss)
    risk_amount = (price - stop_loss) * quantity
    
    print(f"   Bakiye: ${balance}")
    print(f"   Fiyat: ${price}, SL: ${stop_loss}")
    print(f"   Hesaplanan Quantity: {quantity:.6f}")
    print(f"   Risk Miktarı: ${risk_amount:.2f} ({risk_amount/balance*100:.1f}% of balance)")
    
    print("\n" + "-" * 60)
    print("📊 TEST 4: Entry Risk Evaluation")
    print("-" * 60)
    
    mock_snapshot = {
        "price": 100.0,
        "technical": {"price": 100.0, "trend": "BULLISH", "adx": 25, "atr": 2.0},
        "volume_24h": 5_000_000,
        "sentiment": {"fear_greed": {"value": 50}}
    }
    mock_decision = {"action": "BUY", "confidence": 80}
    mock_portfolio = {"balance": 1000.0}
    
    result = rm.evaluate_entry_risk(mock_snapshot, mock_decision, mock_portfolio)
    
    print(f"   Allowed: {result.get('allowed')}")
    if result.get("allowed"):
        print(f"   SL: ${result.get('stop_loss'):.2f}")
        print(f"   TP: ${result.get('take_profit'):.2f}")
        print(f"   Quantity: {result.get('quantity'):.6f}")
    else:
        print(f"   Reason: {result.get('reason')}")
    
    print("\n" + "=" * 60)
    print("✅ Demo tamamlandı!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    demo()
