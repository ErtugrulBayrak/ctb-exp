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
    result = await engine.evaluate_buy_opportunity(snapshot)
    # → {"action": "BUY", "confidence": 75, "quantity": 0.001, "reason": "..."}
    
    # Sell opportunity  
    result = await engine.evaluate_sell_opportunity(position, snapshot)
    # → {"action": "SELL", "confidence": 80, "reason": "..."}
"""

import asyncio
import time
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from config import SETTINGS
from llm_utils import safe_json_loads, validate_decision, build_retry_prompt

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
        enable_llm: bool = True,
        deterministic: bool = False
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
            enable_llm: LLM refinement aktif mi (Live mode default)
            deterministic: Deterministic mode (Backtest mode). If True, LLM is disabled.
        """
        self._gemini_key = gemini_api_key
        self._min_adx = min_adx
        self._min_volume = min_volume_usdt
        self._fng_extreme_fear = fng_extreme_fear
        self._sell_threshold = sell_confidence_threshold
        self._buy_threshold = buy_confidence_threshold
        self._risk_per_trade = risk_per_trade
        self._deterministic = deterministic
        self._enable_llm = enable_llm and GEMINI_AVAILABLE and gemini_api_key and not deterministic
        
        # Stats
        self._decisions_made = 0
        self._llm_calls = 0
        
        # LLM Metrics (Expanded)
        self.llm_metrics = {
            "strategy_calls": 0,
            "strategy_failures": 0,
            "strategy_fallbacks": 0,
            "strategy_latency_ema_ms": 0.0,
            # New granular metrics
            "api_fail": 0,
            "parse_fail": 0,
            "schema_fail": 0,
            "retry_count": 0,
            "retry_success": 0
        }
    
    def get_llm_metrics(self) -> Dict[str, Any]:
        """Return current LLM metrics dictionary."""
        return self.llm_metrics.copy()
    
    def _update_latency_ema(self, key: str, new_value: float, alpha: float = 0.2):
        """Update EMA for latency tracking."""
        old = self.llm_metrics.get(key, 0.0)
        self.llm_metrics[key] = alpha * new_value + (1 - alpha) * old
    
    # ─────────────────────────────────────────────────────────────────────────
    # MAIN ENTRY POINTS
    # ─────────────────────────────────────────────────────────────────────────
    async def evaluate_opportunity(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Genel fırsat değerlendiricisi (Dispatcher).
        LoopController tarafından kullanılır.
        """
        has_pos = market_data.get('has_open_position', False)
        
        if has_pos:
            # Create a minimal position dict if not provided
            # Note: Ideally market_data should contain position details
            position = {
                "symbol": market_data.get("symbol"),
                "entry_price": market_data.get("entry_price"), # Might be missing
                "stop_loss": market_data.get("stop_loss"),
                "take_profit": market_data.get("take_profit")
            }
            return await self.evaluate_sell_opportunity(position, market_data)
        else:
            return await self.evaluate_buy_opportunity(market_data)

    async def evaluate_buy_opportunity(
        self,
        market_snapshot: Dict[str, Any],
        balance: float = 10000.0
    ) -> Dict[str, Any]:
        """
        BUY fırsatını değerlendir (Base Signal Only).
        
        RiskManager tarafından zenginleştirilecek (Quantity, SL/TP).
        
        Outputs Base Schema:
            {
                "action": "BUY" | "HOLD",
                "confidence": float (0-100),
                "reason": str,
                "metadata": dict
            }
        """
        self._decisions_made += 1
        
        # 1. Rule-Based Decision
        decision = self._build_rule_based_buy_decision(market_snapshot)
        decision["metadata"]["source"] = "RULES"
        
        # 2. LLM Refinement (Based on SETTINGS)
        should_call_llm = False
        
        if self._enable_llm and not self._deterministic and SETTINGS.USE_STRATEGY_LLM:
            action = decision.get("action")
            conf = decision.get("confidence", 0)
            
            if SETTINGS.STRATEGY_LLM_MODE == "always":
                should_call_llm = True
            elif SETTINGS.STRATEGY_LLM_MODE == "only_on_signal":
                # Call LLM only if RULES produced BUY/SELL with sufficient confidence
                if action in ("BUY", "SELL") and conf >= SETTINGS.STRATEGY_LLM_MIN_RULES_CONF:
                    should_call_llm = True
        
        if should_call_llm:
            symbol = market_snapshot.get("symbol", "UNKNOWN")
            price = decision.get("entry_price")
            technical = market_snapshot.get("technical", {})
            onchain = market_snapshot.get("onchain", {})
            sentiment = market_snapshot.get("sentiment", {})
            news_summary = market_snapshot.get("news") or market_snapshot.get("news_analysis") or {}
            
            # Metrics: Start Timer
            self.llm_metrics["strategy_calls"] += 1
            start_time = time.perf_counter()
            
            llm_result = await self._llm_decision_pipeline(
                symbol=symbol,
                price=price,
                technical=technical,
                onchain=onchain,
                sentiment=sentiment,
                news_summary=news_summary,
                context="BUY"
            )
            
            # Metrics: End Timer
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            self._update_latency_ema("strategy_latency_ema_ms", elapsed_ms)
            
            if llm_result:
                self._llm_calls += 1
                decision["metadata"]["llm_used"] = True
                decision["metadata"]["source"] = "LLM"
                
                llm_action = llm_result.get("decision", "HOLD")
                llm_conf = llm_result.get("confidence", 0)
                llm_reason = llm_result.get("reason", "")[:60]
                sl_bias = llm_result.get("sl_bias", "neutral")
                tp_bias = llm_result.get("tp_bias", "neutral")
                
                # Log the parsed LLM decision
                symbol = market_snapshot.get("symbol", "UNKNOWN")
                logger.info(f"[LLM DECISION] {symbol}: decision={llm_action} conf={llm_conf} sl={sl_bias} tp={tp_bias} reason={llm_reason}")
                
                if llm_action == "BUY" and llm_conf >= self._buy_threshold:
                    decision["action"] = "BUY"
                    decision["confidence"] = llm_conf
                    decision["reason"] = f"LLM: {llm_reason}"
                elif llm_action != "BUY":
                    decision["action"] = "HOLD"
                    decision["reason"] = f"LLM Rejected: {llm_reason}"
            else:
                # LLM failed, count as failure/fallback
                self.llm_metrics["strategy_failures"] += 1
                self.llm_metrics["strategy_fallbacks"] += 1
        
        return decision

    def _build_rule_based_buy_decision(
        self,
        market_snapshot: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Saf kural tabanlı BUY sinyali (No Sizing/SL/TP)."""
        
        result = {
            "action": "HOLD",
            "confidence": 0.0,
            "entry_price": 0.0,
            "reason": "",
            "metadata": {
                "scores": {},
                "weighted_score": 0.0,
                "llm_used": False,
                "timestamp": datetime.now().isoformat()
            }
        }
        
        technical = market_snapshot.get("technical", {})
        sentiment = market_snapshot.get("sentiment", {})
        onchain = market_snapshot.get("onchain", {})
        
        price = market_snapshot.get("price") or technical.get("price")
        result["entry_price"] = price if price else 0.0
        
        if not price:
            result["reason"] = "Fiyat verisi yok"
            return result
            
        # 1. Scoring (Guardrails moved to RiskManager)
        scores = self._calculate_scores(technical, onchain, sentiment)
        result["metadata"]["scores"] = scores
        
        weighted_score = (
            scores["technical"] * WEIGHT_TECHNICAL +
            scores["onchain"] * WEIGHT_ONCHAIN +
            scores["sentiment"] * (WEIGHT_NEWS + WEIGHT_REDDIT)
        )
        result["metadata"]["weighted_score"] = round(weighted_score, 1)
        result["confidence"] = result["metadata"]["weighted_score"]
        
        # 2. Decision based on Score
        if weighted_score >= self._buy_threshold:
            result["action"] = "BUY"
            result["reason"] = self._build_buy_reason(scores, weighted_score)
        else:
            result["reason"] = f"Skor yetersiz ({weighted_score:.1f} < {self._buy_threshold})"
            
        return result

    async def evaluate_sell_opportunity(
        self,
        position: Dict[str, Any],
        market_snapshot: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        SELL fırsatını değerlendir (Base Signal Only).
        
        RiskManager tarafından nihai çıkış kararı verilecek.
        """
        self._decisions_made += 1
        
        # 1. Rule-Based Decision
        decision = self._build_rule_based_sell_decision(position, market_snapshot)
        decision["metadata"]["source"] = "RULES"
        
        # 2. LLM Refinement (Based on SETTINGS)
        should_call_llm = False
        
        if self._enable_llm and not self._deterministic and SETTINGS.USE_STRATEGY_LLM:
            action = decision.get("action")
            conf = decision.get("confidence", 0)
            
            if SETTINGS.STRATEGY_LLM_MODE == "always":
                should_call_llm = True
            elif SETTINGS.STRATEGY_LLM_MODE == "only_on_signal":
                # Call LLM only if RULES produced BUY/SELL with sufficient confidence
                if action in ("BUY", "SELL") and conf >= SETTINGS.STRATEGY_LLM_MIN_RULES_CONF:
                    should_call_llm = True
        
        if should_call_llm:
            symbol = position.get("symbol", market_snapshot.get("symbol", "UNKNOWN"))
            current_price = decision.get("entry_price")
            technical = market_snapshot.get("technical", {})
            onchain = market_snapshot.get("onchain", {})
            sentiment = market_snapshot.get("sentiment", {})
            pnl_pct = decision["metadata"].get("pnl_pct", 0)
            news_summary = market_snapshot.get("news") or market_snapshot.get("news_analysis") or {}
            
            # Metrics: Start Timer
            self.llm_metrics["strategy_calls"] += 1
            start_time = time.perf_counter()
            
            llm_result = await self._llm_decision_pipeline(
                symbol=symbol,
                price=current_price,
                technical=technical,
                onchain=onchain,
                sentiment=sentiment,
                news_summary=news_summary,
                context="SELL",
                has_position=True,
                pnl_pct=pnl_pct
            )
            
            # Metrics: End Timer
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            self._update_latency_ema("strategy_latency_ema_ms", elapsed_ms)
            
            if llm_result:
                self._llm_calls += 1
                decision["metadata"]["llm_used"] = True
                decision["metadata"]["source"] = "LLM"
                
                llm_action = llm_result.get("decision", "HOLD")
                llm_conf = llm_result.get("confidence", 0)
                llm_reason = llm_result.get("reason", "")[:60]
                sl_bias = llm_result.get("sl_bias", "neutral")
                tp_bias = llm_result.get("tp_bias", "neutral")
                
                # Log the parsed LLM decision
                sym = position.get("symbol", market_snapshot.get("symbol", "UNKNOWN"))
                logger.info(f"[LLM DECISION] {sym}: decision={llm_action} conf={llm_conf} sl={sl_bias} tp={tp_bias} reason={llm_reason}")
                
                if llm_action == "SELL" and llm_conf >= self._sell_threshold:
                    decision["action"] = "SELL"
                    decision["confidence"] = llm_conf
                    decision["reason"] = f"LLM: {llm_reason}"
                elif decision["action"] == "SELL" and llm_action == "HOLD":
                    decision["action"] = "HOLD"
                    decision["reason"] = f"LLM Veto: {llm_reason}"
            else:
                # LLM failed, count as failure/fallback
                self.llm_metrics["strategy_failures"] += 1
                self.llm_metrics["strategy_fallbacks"] += 1
                        
        return decision

    def _build_rule_based_sell_decision(
        self,
        position: Dict[str, Any],
        market_snapshot: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Saf kural tabanlı SELL sinyali."""
        
        result = {
            "action": "HOLD",
            "confidence": 0.0,
            "entry_price": 0.0,
            "reason": "",
            "metadata": {
                "pnl_pct": 0.0,
                "exit_type": "HOLD",
                "scores": {},
                "weighted_score": 0.0,
                "llm_used": False,
                "timestamp": datetime.now().isoformat()
            }
        }
        
        technical = market_snapshot.get("technical", {})
        sentiment = market_snapshot.get("sentiment", {})
        onchain = market_snapshot.get("onchain", {})
        
        current_price = market_snapshot.get("price") or technical.get("price")
        result["entry_price"] = current_price if current_price else 0.0
        
        if not current_price:
            result["reason"] = "Fiyat verisi eksik"
            return result
            
        # PnL Calculation
        pos_entry = position.get("entry_price")
        if pos_entry:
            pnl_pct = ((current_price - pos_entry) / pos_entry) * 100
            result["metadata"]["pnl_pct"] = round(pnl_pct, 2)
            
        # 1. Hard Triggers (Signal Only)
        pos_sl = position.get("stop_loss")
        pos_tp = position.get("take_profit")
        
        if pos_sl and current_price <= pos_sl:
            result["action"] = "SELL"
            result["confidence"] = 100.0
            result["reason"] = f"Stop Loss Sinyali (${pos_sl:.2f})"
            result["metadata"]["exit_type"] = "SL"
            return result
            
        if pos_tp and current_price >= pos_tp:
            result["action"] = "SELL"
            result["confidence"] = 100.0
            result["reason"] = f"Take Profit Sinyali (${pos_tp:.2f})"
            result["metadata"]["exit_type"] = "TP"
            return result
            
        # 2. Scoring
        scores = self._calculate_sell_scores(technical, onchain, sentiment)
        result["metadata"]["scores"] = scores
        
        weighted_score = (
            scores["technical"] * WEIGHT_TECHNICAL +
            scores["onchain"] * WEIGHT_ONCHAIN +
            scores["sentiment"] * (WEIGHT_NEWS + WEIGHT_REDDIT)
        )
        result["metadata"]["weighted_score"] = round(weighted_score, 1)
        result["confidence"] = result["metadata"]["weighted_score"]
        
        if weighted_score >= self._sell_threshold:
            result["action"] = "SELL"
            result["metadata"]["exit_type"] = "AI"
            result["reason"] = f"AI satış sinyali (skor: {weighted_score:.1f})"
        else:
             result["reason"] = f"Skor yetersiz ({weighted_score:.1f} < {self._sell_threshold})"
        
        return result
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
            
            logger.info("[LLM DEBUG] Gemini çağrısı başlatılıyor.")
            logger.info(f"[LLM DEBUG] Gönderilen Prompt:\n{prompt}")

            def sync_generate():
                return model.generate_content(
                    prompt,
                    generation_config=genai.types.GenerationConfig(
                        temperature=0.3,
                        max_output_tokens=200
                    )
                )
            
            response = await loop.run_in_executor(None, sync_generate)
            
            logger.info("[LLM DEBUG] Gemini API ham yanıtı alındı.")
            try:
                logger.info(f"[LLM RAW RESPONSE] {response.text}")
            except Exception as e:
                logger.warning(f"[LLM RAW RESPONSE LOG ERROR] {e}")
            
            logger.info("[LLM STATUS] Gemini çağrısı başarıyla tamamlandı.")
            
            if response and response.text:
                import json
                import re
                
                text = response.text.strip()
                # Extract JSON
                match = re.search(r'\{[^}]+\}', text)
                if match:
                    return json.loads(match.group())
        
        except Exception as e:
            logger.error("[LLM STATUS] Gemini çağrısı başarısız oldu → FALLBACK tetiklendi.", exc_info=True)
            logger.warning(f"[StrategyEngine] LLM hatası: {e}")
        
        return None

    # ─────────────────────────────────────────────────────────────────────────────
    # LLM ADVANCED DECISION PIPELINE
    # ─────────────────────────────────────────────────────────────────────────────
    async def _llm_evaluate_market(
        self,
        symbol: str,
        price: float,
        technical: Dict[str, Any],
        onchain: Dict[str, Any],
        sentiment: Dict[str, Any],
        news_summary: Any
    ) -> Optional[str]:
        """Stage 1: Long-form market evaluation (internal only)."""
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
            prompt = f"""Act as a professional quant PM. Produce a concise internal analysis (no JSON, no final decision).
Sections:
- Trend evaluation
- Momentum evaluation
- Volume evaluation
- Sentiment (Reddit, Fear & Greed)
- On-chain signals
- News effects
- Whale movements
- Overall bias (bullish / bearish / neutral)

Market Data:
Symbol: {symbol}
Price: {price}
Technical: {technical}
On-chain: {onchain}
Sentiment: {sentiment}
News summary: {news_summary}
"""
            logger.info("[LLM DEBUG] Gemini çağrısı başlatılıyor.")
            logger.info(f"[LLM DEBUG] Gönderilen Prompt:\n{prompt}")
            
            loop = asyncio.get_event_loop()
            def sync_generate():
                return model.generate_content(
                    prompt,
                    generation_config=genai.types.GenerationConfig(
                        temperature=0.2,
                        max_output_tokens=400
                    )
                )
            response = await loop.run_in_executor(None, sync_generate)
            
            logger.info("[LLM DEBUG] Gemini API ham yanıtı alındı.")
            try:
                logger.info(f"[LLM RAW RESPONSE] {response.text}")
            except Exception as e:
                logger.warning(f"[LLM RAW RESPONSE LOG ERROR] {e}")
            logger.info("[LLM STATUS] Gemini çağrısı başarıyla tamamlandı.")
            if response and response.text:
                return response.text.strip()
        except Exception as e:
            logger.error("[LLM STATUS] Gemini çağrısı başarısız oldu → FALLBACK tetiklendi.", exc_info=True)
            logger.warning(f"[StrategyEngine] LLM evaluation hatası: {e}")
        return None

    async def _llm_decision_pass(self, evaluation_text: str) -> Optional[Dict[str, Any]]:
        """Stage 2: Convert evaluation into structured JSON decision."""
        if not self._enable_llm or not GEMINI_AVAILABLE:
            return None
        
        prompt = f"""You are a trading risk analyst. Convert the evaluation into STRICT JSON.
Rules:
- Output ONLY valid JSON, no prose, no markdown.
- Start with {{ and end with }}
- decision ∈ BUY, SELL, HOLD
- confidence integer 0-100
- sl_bias/tp_bias ∈ tighter|looser|neutral
- reason max 60 chars

Examples:
{{"decision": "BUY", "confidence": 82, "sl_bias": "tighter", "tp_bias": "looser", "reason": "Strong trend + bullish sentiment"}}
{{"decision": "SELL", "confidence": 75, "sl_bias": "tighter", "tp_bias": "neutral", "reason": "Momentum reversal + weak volume"}}

Evaluation:
\"\"\"{evaluation_text[:2000]}\"\"\""""
        
        max_attempts = 2
        
        for attempt in range(max_attempts):
            current_prompt = prompt if attempt == 0 else build_retry_prompt(prompt)
            
            if attempt > 0:
                self.llm_metrics["retry_count"] += 1
                logger.info(f"[LLM RETRY] Attempt {attempt + 1}/{max_attempts}")
            
            try:
                genai.configure(api_key=self._gemini_key)
                model = genai.GenerativeModel('models/gemini-2.5-flash')
                
                loop = asyncio.get_event_loop()
                def sync_generate():
                    return model.generate_content(
                        current_prompt,
                        generation_config=genai.types.GenerationConfig(
                            temperature=0.05,  # Very low for consistent JSON
                            max_output_tokens=400
                        )
                    )
                response = await loop.run_in_executor(None, sync_generate)
                
                if not response or not response.text:
                    self.llm_metrics["api_fail"] += 1
                    continue
                
                raw = response.text.strip()
                logger.debug(f"[LLM RAW] {raw[:200]}...")
                
                # Parse using llm_utils
                parsed, parse_error = safe_json_loads(raw)
                
                if parsed is None:
                    self.llm_metrics["parse_fail"] += 1
                    logger.warning(f"[LLM PARSE FAIL] {parse_error}")
                    continue
                
                # Validate using llm_utils
                validated = validate_decision(parsed)
                
                if validated is None:
                    self.llm_metrics["schema_fail"] += 1
                    logger.warning(f"[LLM SCHEMA FAIL] Invalid decision structure: {list(parsed.keys())}")
                    continue
                
                # Success!
                if attempt > 0:
                    self.llm_metrics["retry_success"] += 1
                
                return validated
                
            except Exception as e:
                self.llm_metrics["api_fail"] += 1
                logger.warning(f"[LLM API ERROR] {str(e)[:100]}")
        
        # All attempts failed
        self.llm_metrics["strategy_failures"] += 1
        self.llm_metrics["strategy_fallbacks"] += 1
        return None

    async def _llm_self_consistency_check(
        self,
        evaluation_text: str,
        first_decision: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Stage 3: Ensure decision aligns with evaluation."""
        if not self._enable_llm or not GEMINI_AVAILABLE:
            return first_decision
        prompt = f"""You are verifying decision consistency.
Evaluation:
\"\"\"{evaluation_text}\"\"\"

Prior decision:
{json.dumps(first_decision)}

If consistent, return the SAME JSON. If inconsistent, return corrected JSON with:
{{"decision": "BUY|SELL|HOLD", "confidence": 0-100, "sl_bias": "tighter|looser|neutral", "tp_bias": "tighter|looser|neutral", "reason": "max 60 chars"}}

Examples:
{{"decision": "BUY", "confidence": 82, "sl_bias": "tighter", "tp_bias": "looser", "reason": "Strong trend + bullish sentiment"}}
{{"decision": "SELL", "confidence": 75, "sl_bias": "tighter", "tp_bias": "neutral", "reason": "Momentum reversal + weak volume"}}"""
        try:
            genai.configure(api_key=self._gemini_key)
            model = genai.GenerativeModel('models/gemini-2.5-flash')
            logger.info("[LLM DEBUG] Gemini çağrısı başlatılıyor.")
            logger.info(f"[LLM DEBUG] Gönderilen Prompt:\n{prompt}")

            loop = asyncio.get_event_loop()
            def sync_generate():
                return model.generate_content(
                    prompt,
                    generation_config=genai.types.GenerationConfig(
                        temperature=0.1,
                        max_output_tokens=200
                    )
                )
            response = await loop.run_in_executor(None, sync_generate)

            logger.info("[LLM DEBUG] Gemini API ham yanıtı alındı.")
            try:
                logger.info(f"[LLM RAW RESPONSE] {response.text}")
            except Exception as e:
                logger.warning(f"[LLM RAW RESPONSE LOG ERROR] {e}")
            logger.info("[LLM STATUS] Gemini çağrısı başarıyla tamamlandı.")
            if response and response.text:
                text = response.text.strip()
                match = re.search(r'\{.*\}', text, re.DOTALL)
                if match:
                    return json.loads(match.group())
        except Exception as e:
            logger.error("[LLM STATUS] Gemini çağrısı başarısız oldu → FALLBACK tetiklendi.", exc_info=True)
            logger.warning(f"[StrategyEngine] LLM consistency hatası: {e}")
        return first_decision

    def _calibrate_confidence(self, raw_conf: int) -> int:
        """Stabilize LLM confidence outputs."""
        conf = raw_conf or 0
        if conf < 10:
            conf = 20
        if conf > 90:
            allow_high = False
            ctx = getattr(self, "_last_llm_context", {})
            if ctx.get("decision") == "BUY" and ctx.get("trend") in ("BULLISH", "STRONG_BULLISH"):
                allow_high = True
            conf = conf if allow_high else 90
        conf = conf * 0.7 + 30
        conf = max(0, min(int(round(conf)), 100))
        return conf

    def _validate_decision_json(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Validate structure and enforce allowed values."""
        if not isinstance(data, dict):
            return None
        decision = str(data.get("decision", "HOLD")).upper()
        if decision not in ("BUY", "SELL", "HOLD"):
            return None
        try:
            conf = int(data.get("confidence", 0))
        except (TypeError, ValueError):
            return None
        conf = max(0, min(conf, 100))
        sl_bias = str(data.get("sl_bias", "neutral")).lower()
        tp_bias = str(data.get("tp_bias", "neutral")).lower()
        if sl_bias not in ("tighter", "looser", "neutral"):
            sl_bias = "neutral"
        if tp_bias not in ("tighter", "looser", "neutral"):
            tp_bias = "neutral"
        reason = str(data.get("reason", ""))[:60]
        return {
            "decision": decision,
            "confidence": conf,
            "sl_bias": sl_bias,
            "tp_bias": tp_bias,
            "reason": reason
        }

    async def _llm_decision_pipeline(
        self,
        symbol: str,
        price: float,
        technical: Dict[str, Any],
        onchain: Dict[str, Any],
        sentiment: Dict[str, Any],
        news_summary: Any,
        context: str = "BUY",
        has_position: bool = False,
        pnl_pct: float = 0.0
    ) -> Optional[Dict[str, Any]]:
        """Run 3-stage LLM pipeline with validation and calibration."""
        fallback = {
            "decision": "HOLD",
            "confidence": 0,
            "reason": "LLM fallback",
            "sl_bias": "neutral",
            "tp_bias": "neutral"
        }
        if not self._enable_llm or not GEMINI_AVAILABLE:
            return fallback
        try:
            evaluation_text = await self._llm_evaluate_market(
                symbol, price, technical, onchain, sentiment, news_summary
            )
            if not evaluation_text:
                return fallback
            first_decision = await self._llm_decision_pass(evaluation_text)
            if not first_decision:
                return fallback
            final_decision = await self._llm_self_consistency_check(evaluation_text, first_decision)
            validated = self._validate_decision_json(final_decision)
            if not validated:
                return fallback
            self._last_llm_context = {"decision": validated.get("decision"), "trend": technical.get("trend")}
            validated["confidence"] = self._calibrate_confidence(validated.get("confidence", 0))
            return validated
        except Exception as e:
            logger.warning(f"[StrategyEngine] LLM pipeline hatası: {e}")
            return fallback
    
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
    result = await engine.evaluate_buy_opportunity(mock_snapshot, balance=10000)
    print(f"   Action: {result['action']}")
    print(f"   Confidence: {result['confidence']}")
    # print(f"   Quantity: {result['quantity']}") # Removed from Engine
    print(f"   Reason: {result['reason']}")
    print(f"   Scores: {result['metadata']['scores']}")
    
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
    print(f"   PnL%: {result['metadata']['pnl_pct']}")
    print(f"   Reason: {result['reason']}")
    
    # Stats
    print("\n📋 Engine Stats:")
    stats = engine.get_stats()
    for k, v in stats.items():
        print(f"   {k}: {v}")
    
    print("\n" + "=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(demo())
