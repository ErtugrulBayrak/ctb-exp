"""
news_veto.py - LLM Haber/Olay Risk Veto Modülü
==============================================

LLM kullanarak haber/olay bazlı risk veto kararları alır.
V1 stratejisinde LLM sadece bu amaçla kullanılır.

Veto Tetikleyicileri:
- Borsa delist
- Withdrawals paused
- Hack/Security breach
- Regulatory action
- Major protocol failure

Kullanım:
    from strategies.news_veto import NewsVeto
    
    nv = NewsVeto(gemini_api_key="...")
    result = nv.check_veto(symbol, news_summary)
    if result.veto:
        logger.warning(f"Entry vetoed: {result.reason}")
"""

import json
import time
from typing import Dict, Any, Optional, List
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
    from config import SETTINGS
except ImportError:
    class MockSettings:
        USE_NEWS_LLM_VETO = True
        NEWS_VETO_MIN_CONF = 70
        NEWS_VETO_TIGHTEN_STOP = False
        NEWS_VETO_TIGHTEN_MULT = 0.7
        GEMINI_API_KEY = ""
    SETTINGS = MockSettings()

# Gemini import
try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    genai = None

# LLM utils import
try:
    from llm_utils import safe_json_loads, strip_code_fences
except ImportError:
    def strip_code_fences(text: str) -> str:
        return text.strip()
    
    def safe_json_loads(text: str):
        try:
            return json.loads(text), None
        except json.JSONDecodeError as e:
            return None, str(e)

# Metrics import for rate limiting
try:
    from metrics import (
        can_call_llm, 
        record_llm_call, 
        record_llm_rate_limited,
        increment as metrics_increment
    )
    METRICS_AVAILABLE = True
except ImportError:
    can_call_llm = lambda: True
    record_llm_call = lambda: None
    record_llm_rate_limited = lambda: None
    metrics_increment = lambda *a, **k: None
    METRICS_AVAILABLE = False


@dataclass
class VetoResult:
    """Veto sonucu."""
    veto: bool
    confidence: int  # 0-100
    reason: str
    tags: List[str]
    raw_response: Optional[str] = None


# Veto prompt template
VETO_PROMPT_TEMPLATE = """Sen bir kripto para risk analisti olarak görev yapıyorsun.
Aşağıdaki haber özetini analiz et ve bu coin için yeni pozisyon açmanın riskli olup olmadığına karar ver.

COIN: {symbol}

HABER ÖZETİ:
{news_summary}

YÜKSEK RİSK TETİKLEYİCİLERİ (bunlardan biri varsa veto=true):
- Borsa delisting
- Withdrawals/deposits kapatıldı
- Hack veya güvenlik ihlali
- Yasal/düzenleyici soruşturma
- Major protokol hatası veya exploit
- Proje ekibinin kaçması (rug pull şüphesi)
- %50+ fiyat düşüşü haberi

SADECE aşağıdaki JSON formatında yanıt ver, başka hiçbir şey yazma:
{{"veto": boolean, "confidence": 0-100, "reason": "kısa açıklama", "tags": ["tag1", "tag2"]}}

Örnek yanıtlar:
{{"veto": true, "confidence": 85, "reason": "SEC soruşturması haberi", "tags": ["regulatory", "sec"]}}
{{"veto": false, "confidence": 90, "reason": "Normal piyasa haberleri", "tags": ["neutral"]}}
"""


class NewsVeto:
    """
    LLM Haber/Olay Risk Veto Sistemi.
    
    Entry öncesi haberleri analiz eder ve riskli durumlarda veto verir.
    Veto sadece entry'yi engeller, mevcut pozisyonları kapatmaz.
    
    Özellikler:
    - Keyword prefilter: Risk keyword yoksa LLM çağrılmaz
    - Hash caching: Aynı içerik için tekrar LLM çağrılmaz
    - Parse fail → veto=False (güvenli fallback)
    """
    
    def __init__(
        self,
        gemini_api_key: str = None,
        min_confidence: int = None,
        tighten_stop: bool = None,
        tighten_mult: float = None
    ):
        """
        NewsVeto başlat.
        
        Args:
            gemini_api_key: Gemini API key
            min_confidence: Veto için minimum güven (varsayılan: 70)
            tighten_stop: Veto durumunda stop sıkılaştır
            tighten_mult: Stop sıkılaştırma çarpanı
        """
        self.gemini_api_key = gemini_api_key or getattr(SETTINGS, 'GEMINI_API_KEY', '')
        self.min_confidence = min_confidence if min_confidence is not None else getattr(SETTINGS, 'NEWS_VETO_MIN_CONF', 70)
        self.tighten_stop = tighten_stop if tighten_stop is not None else getattr(SETTINGS, 'NEWS_VETO_TIGHTEN_STOP', False)
        self.tighten_mult = tighten_mult if tighten_mult is not None else getattr(SETTINGS, 'NEWS_VETO_TIGHTEN_MULT', 0.7)
        
        # Risk keywords from config
        self.risk_keywords = getattr(SETTINGS, 'RISK_VETO_KEYWORDS', (
            "hack", "delist", "exploit", "breach", "withdraw", "paused", 
            "suspended", "sec", "regulatory", "rug", "scam", "crash"
        ))
        
        # Cache TTL from config
        self._cache_ttl = getattr(SETTINGS, 'NEWS_VETO_CACHE_MINUTES', 10) * 60
        
        # Gemini model
        self._model = None
        if GEMINI_AVAILABLE and self.gemini_api_key:
            try:
                genai.configure(api_key=self.gemini_api_key)
                self._model = genai.GenerativeModel('gemini-1.5-flash')
            except Exception as e:
                logger.warning(f"[NEWS_VETO] Gemini init failed: {e}")
        
        # Hash-based cache: {cache_key: (timestamp, result)}
        self._cache: Dict[str, tuple] = {}
        
        # Telemetry
        self.metrics = {
            "checks": 0,
            "llm_calls": 0,
            "cache_hits": 0,
            "keyword_skips": 0,
            "veto_true": 0,
            "parse_fails": 0
        }
    
    def _compute_cache_key(self, symbol: str, text: str) -> str:
        """Deterministic cache key: (symbol, time_bucket, text_hash)"""
        import hashlib
        # 10-min time bucket
        time_bucket = int(time.time() / (self._cache_ttl)) * self._cache_ttl
        # Normalize text
        normalized = text.lower().strip()[:1000]
        text_hash = hashlib.md5(normalized.encode()).hexdigest()[:8]
        return f"{symbol}_{time_bucket}_{text_hash}"
    
    def _has_risk_keywords(self, text: str) -> bool:
        """Check if text contains any risk keywords."""
        if not text:
            return False
        text_lower = text.lower()
        return any(kw in text_lower for kw in self.risk_keywords)
    
    def check_veto(
        self,
        symbol: str,
        news_summary: str = "",
        reddit_summary: str = "",
        use_cache: bool = True
    ) -> VetoResult:
        """
        Haber + Reddit bazlı veto kontrolü.
        
        Args:
            symbol: Coin sembolü (örn: BTCUSDT)
            news_summary: Son haberler özeti
            reddit_summary: Son reddit postları özeti
            use_cache: Cache kullan
        
        Returns:
            VetoResult dataclass
        """
        self.metrics["checks"] += 1
        
        # Veto devre dışı mı?
        if not getattr(SETTINGS, 'USE_NEWS_LLM_VETO', True):
            return VetoResult(
                veto=False,
                confidence=0,
                reason="NEWS_VETO_DISABLED",
                tags=[]
            )
        
        # Bundle text
        bundle = f"{news_summary}\n{reddit_summary}".strip()
        
        # Haber özeti yoksa geç
        if not bundle or len(bundle) < 20:
            return VetoResult(
                veto=False,
                confidence=0,
                reason="NO_NEWS_DATA",
                tags=[]
            )
        
        # Cache key hesapla
        cache_key = self._compute_cache_key(symbol, bundle)
        
        # Cache kontrolü
        if use_cache and cache_key in self._cache:
            cached_ts, cached_result = self._cache[cache_key]
            if time.time() - cached_ts < self._cache_ttl:
                self.metrics["cache_hits"] += 1
                logger.debug(f"[NEWS_VETO] Cache hit for {symbol}")
                return cached_result
        
        # KEYWORD PREFILTER - risk keyword yoksa LLM çağırma
        if not self._has_risk_keywords(bundle):
            self.metrics["keyword_skips"] += 1
            result = VetoResult(
                veto=False,
                confidence=0,
                reason="NO_RISK_KEYWORDS",
                tags=["prefilter_skip"]
            )
            self._cache[cache_key] = (time.time(), result)
            logger.debug(f"[NEWS_VETO] Keyword prefilter: no risk keywords for {symbol}")
            return result
        
        # LLM kullanılamıyorsa güvenli fallback
        if not self._model:
            logger.debug("[NEWS_VETO] LLM not available, safe fallback")
            return VetoResult(
                veto=False,
                confidence=0,
                reason="LLM_NOT_AVAILABLE",
                tags=[]
            )
        
        # ═══════════════════════════════════════════════════════════════════════
        # LLM RATE LIMIT CHECK
        # ═══════════════════════════════════════════════════════════════════════
        if not can_call_llm():
            record_llm_rate_limited()
            logger.warning(
                f"[NEWS_VETO] {symbol} | LLM rate limited | "
                f"veto_fallback=False | reason=MAX_LLM_CALLS_PER_HOUR_EXCEEDED"
            )
            # Emit alert (throttled)
            try:
                from alert_manager import get_alert_manager, AlertLevel, AlertCode
                get_alert_manager().emit(
                    AlertCode.LLM_RATE_LIMITED, AlertLevel.WARN,
                    "LLM rate limited", symbol=symbol
                )
            except: pass
            return VetoResult(
                veto=False,
                confidence=0,
                reason="LLM_RATE_LIMITED",
                tags=["rate_limited"]
            )
        
        # LLM çağrısı
        self.metrics["llm_calls"] += 1
        record_llm_call()  # Centralized metrics
        metrics_increment("veto_checked_count")
        
        result = self._call_veto_llm(symbol, bundle)
        
        # Telemetry update
        if result.veto:
            self.metrics["veto_true"] += 1
            metrics_increment("veto_true_count")
            # Emit alert (throttled)
            try:
                from alert_manager import get_alert_manager, AlertLevel, AlertCode
                get_alert_manager().emit(
                    AlertCode.NEWS_VETO_TRUE, AlertLevel.WARN,
                    "Entry vetoed by news analysis", symbol=symbol, reason=result.reason[:50]
                )
            except: pass
        if "error" in result.tags or "PARSE" in result.reason:
            self.metrics["parse_fails"] += 1
        
        # Log detailed veto decision
        logger.info(
            f"[NEWS_VETO] {symbol} | veto_result={result.veto} | "
            f"confidence={result.confidence} | reason={result.reason[:50]}"
        )
        
        # Cache'e kaydet
        self._cache[cache_key] = (time.time(), result)
        
        return result
    
    def get_metrics(self) -> Dict[str, int]:
        """Veto telemetri metriklerini döndür."""
        return dict(self.metrics)
    
    def _call_veto_llm(self, symbol: str, news_summary: str) -> VetoResult:
        """
        LLM ile veto kararı al.
        """
        prompt = VETO_PROMPT_TEMPLATE.format(
            symbol=symbol.replace("USDT", ""),
            news_summary=news_summary[:2000]  # Max 2000 karakter
        )
        
        try:
            response = self._model.generate_content(
                prompt,
                generation_config={
                    "temperature": 0.1,
                    "max_output_tokens": 200
                }
            )
            
            raw_text = response.text if response.text else ""
            
            # Parse response
            return self._parse_veto_response(raw_text)
            
        except Exception as e:
            logger.warning(f"[NEWS_VETO] LLM call failed for {symbol}: {e}")
            return VetoResult(
                veto=False,
                confidence=0,
                reason=f"LLM_CALL_FAILED: {str(e)[:50]}",
                tags=["error"],
                raw_response=None
            )
    
    def _parse_veto_response(self, raw_text: str) -> VetoResult:
        """
        LLM yanıtını parse et.
        
        Parse fail durumunda güvenli fallback: veto=False
        """
        if not raw_text:
            return VetoResult(
                veto=False,
                confidence=0,
                reason="LLM_EMPTY_RESPONSE",
                tags=["error"],
                raw_response=""
            )
        
        # Code fence temizle
        cleaned = strip_code_fences(raw_text)
        
        # JSON parse
        parsed, error = safe_json_loads(cleaned)
        
        if error or not parsed:
            logger.warning(f"[NEWS_VETO] Parse failed: {error}")
            return VetoResult(
                veto=False,
                confidence=0,
                reason="LLM_PARSE_FAIL",
                tags=["error"],
                raw_response=raw_text[:200]
            )
        
        # Validate schema
        try:
            veto = bool(parsed.get("veto", False))
            confidence = int(parsed.get("confidence", 0))
            reason = str(parsed.get("reason", ""))[:100]
            tags = parsed.get("tags", [])
            if not isinstance(tags, list):
                tags = []
            tags = [str(t)[:20] for t in tags[:5]]
            
            # Güven eşiği kontrolü
            if veto and confidence < self.min_confidence:
                logger.info(
                    f"[NEWS_VETO] Veto confidence ({confidence}) < min ({self.min_confidence}), ignoring"
                )
                veto = False
            
            result = VetoResult(
                veto=veto,
                confidence=confidence,
                reason=reason,
                tags=tags,
                raw_response=raw_text[:200]
            )
            
            if veto:
                logger.warning(
                    f"[NEWS_VETO] ⚠️ VETO ACTIVE | "
                    f"Conf={confidence} | Reason={reason} | Tags={tags}"
                )
            
            return result
            
        except Exception as e:
            logger.warning(f"[NEWS_VETO] Validation failed: {e}")
            return VetoResult(
                veto=False,
                confidence=0,
                reason=f"VALIDATION_FAIL: {str(e)[:50]}",
                tags=["error"],
                raw_response=raw_text[:200]
            )
    
    def get_stop_adjustment(self, veto_result: VetoResult) -> Optional[float]:
        """
        Veto durumunda stop sıkılaştırma çarpanı döndür.
        
        Args:
            veto_result: Veto sonucu
        
        Returns:
            Stop çarpanı (örn: 0.7 = stop mesafesini %30 kısalt) veya None
        """
        if not self.tighten_stop:
            return None
        
        if not veto_result.veto:
            return None
        
        # Güven seviyesine göre sıkılaştırma
        # Yüksek güven = daha sıkı stop
        if veto_result.confidence >= 90:
            return self.tighten_mult * 0.8  # Daha sıkı
        elif veto_result.confidence >= 80:
            return self.tighten_mult * 0.9
        else:
            return self.tighten_mult


# ═══════════════════════════════════════════════════════════════════════════════
# VALIDATION FUNCTION FOR llm_utils.py
# ═══════════════════════════════════════════════════════════════════════════════

def validate_news_veto(obj: Any) -> Optional[Dict]:
    """
    Validate news veto response schema.
    
    Expected: {"veto": bool, "confidence": int(0-100), "reason": str, "tags": [str]}
    
    Args:
        obj: Parsed JSON object
    
    Returns:
        Validated dict or None
    """
    if not isinstance(obj, dict):
        return None
    
    try:
        result = {
            "veto": bool(obj.get("veto", False)),
            "confidence": max(0, min(100, int(obj.get("confidence", 0)))),
            "reason": str(obj.get("reason", ""))[:100],
            "tags": []
        }
        
        tags = obj.get("tags", [])
        if isinstance(tags, list):
            result["tags"] = [str(t)[:20] for t in tags[:5]]
        
        return result
        
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# TEST / DEMO
# ═══════════════════════════════════════════════════════════════════════════════

def demo():
    """NewsVeto demo (LLM olmadan)."""
    print("\n" + "=" * 60)
    print("📰 NEWS VETO DEMO")
    print("=" * 60)
    
    # Parse test
    print("\n📋 Parse Tests:")
    
    test_responses = [
        '{"veto": true, "confidence": 85, "reason": "SEC investigation", "tags": ["regulatory"]}',
        '{"veto": false, "confidence": 90, "reason": "Normal market news", "tags": ["neutral"]}',
        'Invalid JSON',
        '',
    ]
    
    nv = NewsVeto()
    
    for i, raw in enumerate(test_responses):
        result = nv._parse_veto_response(raw)
        print(f"\n   Test {i+1}:")
        print(f"   Input: {raw[:50]}...")
        print(f"   Veto: {result.veto}, Conf: {result.confidence}, Reason: {result.reason}")
    
    # Validation test
    print("\n📋 Validation Tests:")
    
    test_objects = [
        {"veto": True, "confidence": 80, "reason": "Hack detected", "tags": ["security"]},
        {"veto": "yes", "confidence": "50"},  # Wrong types
        "not a dict",
        None
    ]
    
    for i, obj in enumerate(test_objects):
        validated = validate_news_veto(obj)
        print(f"\n   Test {i+1}:")
        print(f"   Input: {obj}")
        print(f"   Valid: {validated is not None}")
        if validated:
            print(f"   Result: {validated}")
    
    print("\n" + "=" * 60)
    print("✅ Demo tamamlandı!")
    print("=" * 60)


if __name__ == "__main__":
    demo()
