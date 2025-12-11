"""
strategy_engine.py - Merkezi Strateji ve Karar Motoru
======================================================

Bu modül tüm trade karar mantığını kapsüller.
scraper-v90.py sadece orkestrasyon yapar.

Karar Hiyerarşisi:
1. Rule-based guardrails (ön filtreler)
2. Weighted scoring (teknik, on-chain, sentiment)
3. LLM refinement (son adım, sadece belirsiz durumlar)

Kullanım:
--------
    from strategy_engine import StrategyEngine
    from market_data_engine import MarketDataEngine

    engine = StrategyEngine(
        gemini_api_key=API_KEY,
        min_adx=25,
        min_volume_usdt=10_000_000
    )
    
    # Buy opportunity
    result = await engine.evaluate_buy_opportunity("BTC", snapshot)
    # → {"action": "BUY", "confidence": 75, "quantity": 0.001, "reason": "..."}
    
    # Sell opportunity  
    result = await engine.evaluate_sell_opportunity(position, snapshot)
    # → {"action": "SELL", "confidence": 80, "reason": "..."}
"""

import asyncio
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# Merkezi logger
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

# Gemini import
try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    genai = None


# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS & DEFAULTS
# ═══════════════════════════════════════════════════════════════════════════════
DEFAULT_MIN_ADX = 25           # Güçlü trend eşiği
DEFAULT_MIN_VOLUME = 10_000_000  # $10M minimum 24h hacim
DEFAULT_FNG_EXTREME_FEAR = 20  # Alım engeli eşiği
DEFAULT_SELL_CONFIDENCE = 70   # Minimum SELL confidence
DEFAULT_BUY_CONFIDENCE = 65    # Minimum BUY confidence
DEFAULT_RISK_PER_TRADE = 0.02  # Bakiyenin %2'si

# Weight distribution
WEIGHT_TECHNICAL = 0.40
WEIGHT_ONCHAIN = 0.30
WEIGHT_NEWS = 0.20
WEIGHT_REDDIT = 0.10


# ═══════════════════════════════════════════════════════════════════════════════
# STRATEGY ENGINE
# ═══════════════════════════════════════════════════════════════════════════════
class StrategyEngine:
    """
    Merkezi strateji ve karar motoru.
    
    Rule-based guardrails + weighted scoring + LLM refinement.
    Hem live trading hem backtesting için yapılandırılmış output verir.
    
    Decision Flow:
    1. Guardrails (hard filters)
    2. Technical score (40%)
    3. On-chain score (30%)
    4. Sentiment score (20% news + 10% reddit)
    5. LLM refinement (optional, only for edge cases)
    """
    
    def __init__(
        self,
        gemini_api_key: str = "",
        min_adx: float = DEFAULT_MIN_ADX,
        min_volume_usdt: float = DEFAULT_MIN_VOLUME,
        fng_extreme_fear: int = DEFAULT_FNG_EXTREME_FEAR,
        sell_confidence_threshold: int = DEFAULT_SELL_CONFIDENCE,
        buy_confidence_threshold: int = DEFAULT_BUY_CONFIDENCE,
        risk_per_trade: float = DEFAULT_RISK_PER_TRADE,
        enable_llm: bool = True
    ):
        """
        StrategyEngine başlat.
        
        Args:
            gemini_api_key: Gemini API key (LLM refinement için)
            min_adx: Minimum ADX değeri (trend gücü)
            min_volume_usdt: Minimum 24h hacim (USDT)
            fng_extreme_fear: Aşırı korku eşiği (alım engeli)
            sell_confidence_threshold: Minimum SELL güven skoru
            buy_confidence_threshold: Minimum BUY güven skoru
            risk_per_trade: Trade başına risk oranı
            enable_llm: LLM refinement aktif mi
        """
        self._gemini_key = gemini_api_key
        self._min_adx = min_adx
        self._min_volume = min_volume_usdt
        self._fng_extreme_fear = fng_extreme_fear
        self._sell_threshold = sell_confidence_threshold
        self._buy_threshold = buy_confidence_threshold
        self._risk_per_trade = risk_per_trade
        self._enable_llm = enable_llm and GEMINI_AVAILABLE and gemini_api_key
        
        # Stats
        self._decisions_made = 0
        self._llm_calls = 0
    
    # ─────────────────────────────────────────────────────────────────────────
    # MAIN ENTRY POINTS
    # ─────────────────────────────────────────────────────────────────────────
    async def evaluate_buy_opportunity(
        self,
        symbol: str,
        market_snapshot: Dict[str, Any],
        balance: float = 10000.0
    ) -> Dict[str, Any]:
        """
        BUY fırsatını değerlendir.
        
        Args:
            symbol: Coin sembolü
            market_snapshot: MarketDataEngine.get_full_snapshot() çıktısı
            balance: Mevcut bakiye (quantity hesabı için)
        
        Returns:
            {
                "action": "BUY" | "HOLD",
                "confidence": float (0-100),
                "quantity": float,
                "stop_loss": float,
                "take_profit": float,
                "reason": str,
                "guardrails_passed": bool,
                "scores": { "technical": int, "onchain": int, "sentiment": int },
                "llm_used": bool
            }
        """
        self._decisions_made += 1
        
        result = {
            "action": "HOLD",
            "confidence": 0.0,
            "quantity": 0.0,
            "stop_loss": None,
            "take_profit": None,
            "reason": "",
            "guardrails_passed": False,
            "scores": {"technical": 0, "onchain": 0, "sentiment": 0},
            "llm_used": False,
            "timestamp": datetime.now().isoformat()
        }
        
        # Extract data
        technical = market_snapshot.get("technical", {})
        sentiment = market_snapshot.get("sentiment", {})
        onchain = market_snapshot.get("onchain", {})
        price = market_snapshot.get("price") or technical.get("price")
        
        if not price:
            result["reason"] = "Fiyat verisi yok"
            return result
        
        # ───────────────────────────────────────────────────────────────
        # STEP 1: GUARDRAILS (Hard Filters)
        # ───────────────────────────────────────────────────────────────
        guardrail_result = self._check_buy_guardrails(technical, sentiment)
        
        if not guardrail_result["passed"]:
            result["reason"] = guardrail_result["reason"]
            return result
        
        result["guardrails_passed"] = True
        
        # ───────────────────────────────────────────────────────────────
        # STEP 2: WEIGHTED SCORING
        # ───────────────────────────────────────────────────────────────
        scores = self._calculate_scores(technical, onchain, sentiment)
        result["scores"] = scores
        
        # Weighted total
        weighted_score = (
            scores["technical"] * WEIGHT_TECHNICAL +
            scores["onchain"] * WEIGHT_ONCHAIN +
            scores["sentiment"] * (WEIGHT_NEWS + WEIGHT_REDDIT)
        )
        
        result["confidence"] = round(weighted_score, 1)
        
        # ───────────────────────────────────────────────────────────────
        # STEP 3: DECISION
        # ───────────────────────────────────────────────────────────────
        if weighted_score >= self._buy_threshold:
            result["action"] = "BUY"
            
            # Calculate quantity and SL/TP
            atr = technical.get("atr", 0) or 0
            sl_tp = self._calculate_sl_tp(price, atr)
            
            result["stop_loss"] = sl_tp["stop_loss"]
            result["take_profit"] = sl_tp["take_profit"]
            
            # Position sizing (risk-based)
            result["quantity"] = self._calculate_quantity(
                balance, price, result["stop_loss"]
            )
            
            result["reason"] = self._build_buy_reason(scores, weighted_score)
            
        elif weighted_score >= 50 and self._enable_llm:
            # Edge case - LLM refinement
            llm_result = await self._llm_refine_decision(
                symbol, price, technical, onchain, sentiment, "BUY"
            )
            
            if llm_result:
                result["llm_used"] = True
                self._llm_calls += 1
                
                if llm_result.get("decision") == "BUY":
                    llm_conf = llm_result.get("confidence", 0)
                    if llm_conf >= self._buy_threshold:
                        result["action"] = "BUY"
                        result["confidence"] = llm_conf
                        
                        atr = technical.get("atr", 0) or 0
                        sl_tp = self._calculate_sl_tp(price, atr)
                        result["stop_loss"] = sl_tp["stop_loss"]
                        result["take_profit"] = sl_tp["take_profit"]
                        result["quantity"] = self._calculate_quantity(
                            balance, price, result["stop_loss"]
                        )
                        result["reason"] = llm_result.get("reasoning", "LLM onayı")
                    else:
                        result["reason"] = f"LLM güveni yetersiz ({llm_conf})"
                else:
                    result["reason"] = llm_result.get("reasoning", "LLM HOLD önerdi")
        else:
            result["reason"] = f"Skor yetersiz ({weighted_score:.1f} < {self._buy_threshold})"
        
        return result
    
    async def evaluate_sell_opportunity(
        self,
        position: Dict[str, Any],
        market_snapshot: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        SELL fırsatını değerlendir.
        
        Args:
            position: Açık pozisyon dict (entry_price, quantity, stop_loss, take_profit)
            market_snapshot: MarketDataEngine.get_full_snapshot() çıktısı
        
        Returns:
            {
                "action": "SELL" | "HOLD",
                "confidence": float (0-100),
                "reason": str,
                "pnl_pct": float,
                "exit_type": "SL" | "TP" | "AI" | "HOLD",
                "llm_used": bool
            }
        """
        self._decisions_made += 1
        
        result = {
            "action": "HOLD",
            "confidence": 0.0,
            "reason": "",
            "pnl_pct": 0.0,
            "exit_type": "HOLD",
            "llm_used": False,
            "timestamp": datetime.now().isoformat()
        }
        
        technical = market_snapshot.get("technical", {})
        sentiment = market_snapshot.get("sentiment", {})
        onchain = market_snapshot.get("onchain", {})
        
        current_price = market_snapshot.get("price") or technical.get("price")
        entry_price = position.get("entry_price", 0)
        stop_loss = position.get("stop_loss", 0)
        take_profit = position.get("take_profit", float('inf'))
        
        if not current_price or not entry_price:
            result["reason"] = "Fiyat verisi eksik"
            return result
        
        # Calculate PnL
        pnl_pct = ((current_price - entry_price) / entry_price) * 100
        result["pnl_pct"] = round(pnl_pct, 2)
        
        # ───────────────────────────────────────────────────────────────
        # STEP 1: HARD TRIGGERS (SL/TP)
        # ───────────────────────────────────────────────────────────────
        if stop_loss and current_price <= stop_loss:
            result["action"] = "SELL"
            result["confidence"] = 100
            result["reason"] = f"Stop Loss tetiklendi (${stop_loss:.2f})"
            result["exit_type"] = "SL"
            return result
        
        if take_profit and current_price >= take_profit:
            result["action"] = "SELL"
            result["confidence"] = 100
            result["reason"] = f"Take Profit tetiklendi (${take_profit:.2f})"
            result["exit_type"] = "TP"
            return result
        
        # ───────────────────────────────────────────────────────────────
        # STEP 2: WEIGHTED SCORING (opposite direction)
        # ───────────────────────────────────────────────────────────────
        scores = self._calculate_sell_scores(technical, onchain, sentiment)
        
        weighted_score = (
            scores["technical"] * WEIGHT_TECHNICAL +
            scores["onchain"] * WEIGHT_ONCHAIN +
            scores["sentiment"] * (WEIGHT_NEWS + WEIGHT_REDDIT)
        )
        
        result["confidence"] = round(weighted_score, 1)
        
        # ───────────────────────────────────────────────────────────────
        # STEP 3: DECISION
        # ───────────────────────────────────────────────────────────────
        if weighted_score >= self._sell_threshold:
            result["action"] = "SELL"
            result["exit_type"] = "AI"
            result["reason"] = f"AI satış sinyali (skor: {weighted_score:.1f})"
            
        elif weighted_score >= 50 and self._enable_llm:
            # Edge case - LLM refinement
            symbol = position.get("symbol", "UNKNOWN")
            llm_result = await self._llm_refine_decision(
                symbol, current_price, technical, onchain, sentiment, "SELL",
                has_position=True, pnl_pct=pnl_pct
            )
            
            if llm_result:
                result["llm_used"] = True
                self._llm_calls += 1
                
                if llm_result.get("decision") == "SELL":
                    llm_conf = llm_result.get("confidence", 0)
                    if llm_conf >= self._sell_threshold:
                        result["action"] = "SELL"
                        result["confidence"] = llm_conf
                        result["exit_type"] = "AI"
                        result["reason"] = llm_result.get("reasoning", "LLM satış önerdi")
                    else:
                        result["reason"] = f"LLM güveni yetersiz ({llm_conf})"
                else:
                    result["reason"] = llm_result.get("reasoning", "LLM HOLD önerdi")
        else:
            result["reason"] = f"Skor yetersiz ({weighted_score:.1f} < {self._sell_threshold})"
        
        return result
    
    # ─────────────────────────────────────────────────────────────────────────
    # GUARDRAILS
    # ─────────────────────────────────────────────────────────────────────────
    def _check_buy_guardrails(
        self,
        technical: Dict[str, Any],
        sentiment: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Rule-based ön filtreler."""
        
        # 1. Trend check
        trend = technical.get("trend", "NEUTRAL")
        if trend in ["BEARISH", "NEUTRAL_BEARISH"]:
            if technical.get("trend_strength") == "STRONG":
                return {"passed": False, "reason": f"Güçlü düşüş trendi ({trend})"}
        
        # 2. ADX check (trend strength)
        adx = technical.get("adx")
        if adx is not None and adx < self._min_adx:
            return {"passed": False, "reason": f"ADX düşük ({adx:.1f} < {self._min_adx})"}
        
        # 3. Volume check
        volume = technical.get("volume_24h")
        if volume is not None and volume < self._min_volume:
            vol_m = volume / 1_000_000
            min_m = self._min_volume / 1_000_000
            return {"passed": False, "reason": f"Hacim düşük (${vol_m:.1f}M < ${min_m:.1f}M)"}
        
        # 4. Fear & Greed check
        fng = sentiment.get("fear_greed", {})
        fng_value = fng.get("value") if fng else None
        if fng_value is not None and fng_value <= self._fng_extreme_fear:
            return {"passed": False, "reason": f"Aşırı korku (F&G: {fng_value})"}
        
        return {"passed": True, "reason": "OK"}
    
    # ─────────────────────────────────────────────────────────────────────────
    # SCORING
    # ─────────────────────────────────────────────────────────────────────────
    def _calculate_scores(
        self,
        technical: Dict[str, Any],
        onchain: Dict[str, Any],
        sentiment: Dict[str, Any]
    ) -> Dict[str, int]:
        """BUY için skor hesapla."""
        
        # Technical score (0-100)
        tech_score = 50  # Base
        
        if technical.get("trend_bullish"):
            tech_score += 20
        if technical.get("momentum_positive"):
            tech_score += 15
        
        rsi = technical.get("rsi")
        if rsi:
            if 30 <= rsi <= 50:  # Oversold recovering
                tech_score += 10
            elif rsi > 70:  # Overbought
                tech_score -= 15
        
        adx = technical.get("adx")
        if adx:
            if adx >= 40:
                tech_score += 10
            elif adx >= 25:
                tech_score += 5
        
        tech_score = max(0, min(100, tech_score))
        
        # On-chain score (0-100)
        onchain_score = 50  # Base
        
        signal = onchain.get("signal", "NEUTRAL")
        if signal == "STRONG_SELL_PRESSURE":
            onchain_score -= 30
        elif signal == "MODERATE_SELL_PRESSURE":
            onchain_score -= 15
        elif signal == "LIGHT_SELL_PRESSURE":
            onchain_score -= 5
        elif signal == "NEUTRAL":
            onchain_score += 10  # No whale selling is good
        
        onchain_score = max(0, min(100, onchain_score))
        
        # Sentiment score (0-100)
        sentiment_score = 50  # Base
        
        overall = sentiment.get("overall_sentiment", "NEUTRAL")
        if overall == "EXTREME_GREED":
            sentiment_score -= 10  # Contrarian - too much greed is risky
        elif overall == "GREED":
            sentiment_score += 5
        elif overall == "FEAR":
            sentiment_score += 10  # Contrarian buying
        elif overall == "EXTREME_FEAR":
            sentiment_score -= 5  # Too scared, wait
        
        # Reddit contrarian
        retail = sentiment.get("retail_signal", "NEUTRAL")
        if retail == "EXTREME_EUPHORIA":
            sentiment_score -= 10  # Retail usually wrong at extremes
        elif retail == "EXTREME_PANIC":
            sentiment_score += 10  # Buy when others are fearful
        
        sentiment_score = max(0, min(100, sentiment_score))
        
        return {
            "technical": tech_score,
            "onchain": onchain_score,
            "sentiment": sentiment_score
        }
    
    def _calculate_sell_scores(
        self,
        technical: Dict[str, Any],
        onchain: Dict[str, Any],
        sentiment: Dict[str, Any]
    ) -> Dict[str, int]:
        """SELL için skor hesapla (opposite logic)."""
        
        # Technical score for selling
        tech_score = 50
        
        trend = technical.get("trend", "NEUTRAL")
        if trend in ["BEARISH", "NEUTRAL_BEARISH"]:
            tech_score += 20
        if not technical.get("momentum_positive"):
            tech_score += 15
        
        rsi = technical.get("rsi")
        if rsi and rsi > 70:  # Overbought = sell signal
            tech_score += 15
        
        tech_score = max(0, min(100, tech_score))
        
        # On-chain score for selling
        onchain_score = 50
        
        signal = onchain.get("signal", "NEUTRAL")
        if signal == "STRONG_SELL_PRESSURE":
            onchain_score += 25
        elif signal == "MODERATE_SELL_PRESSURE":
            onchain_score += 15
        
        if onchain.get("whale_alert"):
            onchain_score += 10
        
        onchain_score = max(0, min(100, onchain_score))
        
        # Sentiment score for selling
        sentiment_score = 50
        
        overall = sentiment.get("overall_sentiment", "NEUTRAL")
        if overall == "EXTREME_GREED":
            sentiment_score += 15  # Sell the euphoria
        elif overall == "GREED":
            sentiment_score += 5
        
        retail = sentiment.get("retail_signal", "NEUTRAL")
        if retail == "EXTREME_EUPHORIA":
            sentiment_score += 15  # Sell when retail is euphoric
        
        sentiment_score = max(0, min(100, sentiment_score))
        
        return {
            "technical": tech_score,
            "onchain": onchain_score,
            "sentiment": sentiment_score
        }
    
    # ─────────────────────────────────────────────────────────────────────────
    # POSITION SIZING
    # ─────────────────────────────────────────────────────────────────────────
    def _calculate_sl_tp(
        self,
        price: float,
        atr: float
    ) -> Dict[str, float]:
        """ATR-based SL/TP hesapla."""
        if not atr or atr <= 0:
            # Fallback: %3 SL, %5 TP
            return {
                "stop_loss": price * 0.97,
                "take_profit": price * 1.05
            }
        
        # 2x ATR stop loss, 3x ATR take profit
        return {
            "stop_loss": round(price - (2 * atr), 2),
            "take_profit": round(price + (3 * atr), 2)
        }
    
    def _calculate_quantity(
        self,
        balance: float,
        price: float,
        stop_loss: float
    ) -> float:
        """Risk-based pozisyon boyutu hesapla."""
        if not stop_loss or stop_loss >= price:
            # Fallback: bakiyenin %2'si
            trade_amount = balance * self._risk_per_trade
            return round(trade_amount / price, 6)
        
        # Risk amount
        risk_amount = balance * self._risk_per_trade
        
        # Risk per unit
        risk_per_unit = price - stop_loss
        
        # Quantity
        if risk_per_unit > 0:
            quantity = risk_amount / risk_per_unit
            # Max: bakiyenin %10'u
            max_quantity = (balance * 0.10) / price
            return round(min(quantity, max_quantity), 6)
        
        return round((balance * self._risk_per_trade) / price, 6)
    
    def _build_buy_reason(self, scores: Dict[str, int], total: float) -> str:
        """BUY kararı için açıklama oluştur."""
        parts = []
        
        if scores["technical"] >= 70:
            parts.append("Güçlü teknik")
        elif scores["technical"] >= 60:
            parts.append("Olumlu teknik")
        
        if scores["onchain"] >= 60:
            parts.append("Balina aktivitesi olumlu")
        
        if scores["sentiment"] >= 60:
            parts.append("Sentiment pozitif")
        
        return f"{' + '.join(parts)} (Skor: {total:.1f})" if parts else f"Skor: {total:.1f}"
    
    # ─────────────────────────────────────────────────────────────────────────
    # LLM REFINEMENT
    # ─────────────────────────────────────────────────────────────────────────
    async def _llm_refine_decision(
        self,
        symbol: str,
        price: float,
        technical: Dict[str, Any],
        onchain: Dict[str, Any],
        sentiment: Dict[str, Any],
        context: str = "BUY",
        has_position: bool = False,
        pnl_pct: float = 0.0
    ) -> Optional[Dict[str, Any]]:
        """LLM ile kenar durum kararı al."""
        if not self._enable_llm or not GEMINI_AVAILABLE:
            return None
        
        try:
            genai.configure(api_key=self._gemini_key)
            
            safety_settings = [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
            ]
            
            model = genai.GenerativeModel('models/gemini-2.5-flash', safety_settings=safety_settings)
            
            # Build compact prompt
            tech_summary = technical.get("summary", "Veri yok")
            onchain_signal = onchain.get("signal", "NEUTRAL")
            fng = sentiment.get("fear_greed", {})
            fng_str = f"{fng.get('value', 'N/A')} ({fng.get('classification', 'N/A')})" if fng else "N/A"
            
            position_info = ""
            if has_position:
                position_info = f"\n⚠️ AÇIK POZİSYON: PnL {pnl_pct:+.2f}%"
            
            prompt = f"""Risk-odaklı hedge fon yöneticisi olarak kısa analiz yap.

Coin: {symbol} | Fiyat: ${price:.2f}{position_info}
Teknik: {tech_summary}
On-Chain: {onchain_signal}
F&G: {fng_str}

Bağlam: {context} kararı değerlendiriliyor.

SADECE JSON yanıt ver:
{{"decision": "BUY|SELL|HOLD", "confidence": 0-100, "reasoning": "Max 50 karakter"}}"""

            # Async wrapper
            loop = asyncio.get_event_loop()
            
            def sync_generate():
                return model.generate_content(
                    prompt,
                    generation_config=genai.types.GenerationConfig(
                        temperature=0.3,
                        max_output_tokens=200
                    )
                )
            
            response = await loop.run_in_executor(None, sync_generate)
            
            if response and response.text:
                import json
                import re
                
                text = response.text.strip()
                # Extract JSON
                match = re.search(r'\{[^}]+\}', text)
                if match:
                    return json.loads(match.group())
        
        except Exception as e:
            logger.warning(f"[StrategyEngine] LLM hatası: {e}")
        
        return None
    
    # ─────────────────────────────────────────────────────────────────────────
    # STATS
    # ─────────────────────────────────────────────────────────────────────────
    def get_stats(self) -> Dict[str, Any]:
        """Engine istatistikleri."""
        return {
            "decisions_made": self._decisions_made,
            "llm_calls": self._llm_calls,
            "llm_ratio": self._llm_calls / max(1, self._decisions_made),
            "config": {
                "min_adx": self._min_adx,
                "min_volume": self._min_volume,
                "buy_threshold": self._buy_threshold,
                "sell_threshold": self._sell_threshold,
                "risk_per_trade": self._risk_per_trade,
                "llm_enabled": self._enable_llm
            }
        }
    
    def __repr__(self) -> str:
        llm_status = "enabled" if self._enable_llm else "disabled"
        return f"<StrategyEngine llm={llm_status} decisions={self._decisions_made}>"


# ═══════════════════════════════════════════════════════════════════════════════
# FACTORY FUNCTION
# ═══════════════════════════════════════════════════════════════════════════════
def create_strategy_engine(
    gemini_api_key: str = "",
    **kwargs
) -> StrategyEngine:
    """Factory fonksiyonu."""
    return StrategyEngine(gemini_api_key=gemini_api_key, **kwargs)


# ═══════════════════════════════════════════════════════════════════════════════
# TEST / DEMO
# ═══════════════════════════════════════════════════════════════════════════════
async def demo():
    """Demo - Mock data ile test."""
    print("\n" + "=" * 60)
    print("🧠 STRATEGY ENGINE DEMO")
    print("=" * 60 + "\n")
    
    engine = StrategyEngine(enable_llm=False)
    
    # Mock snapshot
    mock_snapshot = {
        "price": 95000.0,
        "technical": {
            "trend": "BULLISH",
            "trend_bullish": True,
            "momentum_positive": True,
            "adx": 35,
            "atr": 1500,
            "rsi": 55,
            "volume_24h": 50_000_000_000,
            "summary": "TREND: BULLISH | MOMENTUM: POZİTİF | RSI: 55"
        },
        "sentiment": {
            "fear_greed": {"value": 45, "classification": "Fear"},
            "overall_sentiment": "FEAR",
            "retail_signal": "NEUTRAL"
        },
        "onchain": {
            "signal": "NEUTRAL",
            "whale_alert": False
        }
    }
    
    # Test BUY
    print("▶️  BUY Opportunity Test:")
    result = await engine.evaluate_buy_opportunity("BTC", mock_snapshot, balance=10000)
    print(f"   Action: {result['action']}")
    print(f"   Confidence: {result['confidence']}")
    print(f"   Quantity: {result['quantity']}")
    print(f"   Reason: {result['reason']}")
    print(f"   Scores: {result['scores']}")
    
    # Test SELL
    print("\n▶️  SELL Opportunity Test:")
    mock_position = {
        "symbol": "BTC",
        "entry_price": 93000,
        "quantity": 0.01,
        "stop_loss": 90000,
        "take_profit": 100000
    }
    result = await engine.evaluate_sell_opportunity(mock_position, mock_snapshot)
    print(f"   Action: {result['action']}")
    print(f"   Confidence: {result['confidence']}")
    print(f"   PnL%: {result['pnl_pct']}")
    print(f"   Reason: {result['reason']}")
    
    # Stats
    print("\n📋 Engine Stats:")
    stats = engine.get_stats()
    for k, v in stats.items():
        print(f"   {k}: {v}")
    
    print("\n" + "=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(demo())
