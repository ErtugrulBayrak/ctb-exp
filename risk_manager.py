"""
risk_manager.py - Risk Management & Position Sizing
====================================================

Bu modül tüm risk yönetimi, pozisyon büyüklüğü hesaplama ve güvenlik kontrollerini (guardrails) içerir.
StrategyEngine sadece sinyal üretir (BUY/SELL/HOLD), RiskManager bu sinyali doğrular ve parametreleri (Miktar, SL/TP) belirler.
"""

from typing import Dict, Any, Optional
from datetime import datetime
from config import SETTINGS

# Default Constants (StrategyEngine'den taşındı)
# now using settings defaults
DEFAULT_MIN_VOLUME = 1_000_000
DEFAULT_FNG_EXTREME_FEAR = 20
DEFAULT_RISK_PER_TRADE = 0.02

class RiskManager:
    """
    Risk yönetimi ve pozisyon boyutlandırma sınıfı.
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
        self._min_volume = self.config.get("min_volume") or DEFAULT_MIN_VOLUME
        self._fng_extreme_fear = self.config.get("fng_extreme_fear") or DEFAULT_FNG_EXTREME_FEAR
        self._risk_per_trade = self.config.get("risk_per_trade") or DEFAULT_RISK_PER_TRADE
        self._initial_balance = initial_balance

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
        sl_tp = self._calculate_sl_tp(price, atr)
        result["stop_loss"] = sl_tp["stop_loss"]
        result["take_profit"] = sl_tp["take_profit"]
        
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

    def _calculate_sl_tp(self, price: float, atr: float) -> Dict[str, float]:
        """ATR tabanlı SL/TP (StrategyEngine'den taşındı)."""
        if not atr or atr <= 0:
            # Fallback: %3 SL, %5 TP
            return {
                "stop_loss": price * 0.97,
                "take_profit": price * 1.05
            }
        
        # 2x ATR SL, 3x ATR TP
        return {
            "stop_loss": round(price - (2 * atr), 2),
            "take_profit": round(price + (3 * atr), 2)
        }

    def _calculate_quantity(self, balance: float, price: float, stop_loss: float) -> float:
        """Risk tabanlı pozisyon boyutu (StrategyEngine'den taşındı)."""
        if balance <= 0:
            return 0.0

        if not stop_loss or stop_loss >= price:
            # Fallback: bakiyenin %2'si
            trade_amount = balance * self._risk_per_trade
            return round(trade_amount / price, 6)
        
        # Risk amount (Para bazında risk)
        risk_amount = balance * self._risk_per_trade
        
        # Risk per unit (Birim başına risk)
        risk_per_unit = price - stop_loss
        
        if risk_per_unit > 0:
            quantity = risk_amount / risk_per_unit
            
            # Max Cap: Bakiyenin %10'u (Likidite/Slipaj koruması)
            max_quantity = (balance * 0.10) / price
            
            return round(min(quantity, max_quantity), 6)
            
        # Fallback
        return round((balance * self._risk_per_trade) / price, 6)
