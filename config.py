"""
config.py - Merkezi Konfigürasyon Modülü
=========================================

Bu modül tüm API anahtarlarını ve yapılandırma ayarlarını tek bir yerden yönetir.
Güvenlik için tüm hassas bilgiler ortam değişkenlerinden (.env dosyası) okunur.

Kullanım:
    from config import SETTINGS
    
    api_key = SETTINGS.BINANCE_API_KEY
    if SETTINGS.LIVE_TRADING:
        # Gerçek işlem modu
        pass

Gerekli Ortam Değişkenleri (.env dosyasında tanımlanmalı):
----------------------------------------------------------
BINANCE_API_KEY        - Binance API anahtarı
BINANCE_SECRET_KEY     - Binance gizli anahtar
GEMINI_API_KEY         - Google Gemini AI API anahtarı
TELEGRAM_BOT_TOKEN     - Telegram bot token
TELEGRAM_CHAT_ID       - Telegram sohbet ID

Opsiyonel Ortam Değişkenleri (varsayılanlar kullanılır):
--------------------------------------------------------
LIVE_TRADING                  - "1" = gerçek işlem, "0" = paper trading (varsayılan: "0")
ALLOW_DANGEROUS_ACTIONS       - Tehlikeli işlemlere izin ver (varsayılan: "0")
AI_TECH_CONFIDENCE_THRESHOLD  - Teknik tarama güven eşiği (varsayılan: 75)
AI_NEWS_CONFIDENCE_THRESHOLD  - Haber tarama güven eşiği (varsayılan: 80)
AI_SELL_CONFIDENCE_THRESHOLD  - Satış kararı güven eşiği (varsayılan: 70)
USE_NEWS_LLM                  - Haber analizi için LLM kullan (varsayılan: "1")
MAX_DAILY_LOSS_PCT            - Günlük maksimum kayıp yüzdesi (varsayılan: 3.0)
MAX_OPEN_POSITIONS            - Aynı anda maksimum açık pozisyon (varsayılan: 3)
MAX_CONSECUTIVE_LOSSES        - Ardışık maksimum zarar sayısı (varsayılan: 4)
COOLDOWN_MINUTES              - Ardışık zarar sonrası bekleme süresi (dakika) (varsayılan: 120)
BASLANGIC_BAKIYE              - Başlangıç bakiyesi USDT (varsayılan: 1000.0)
MIN_HACIM_USDT                - Minimum 24h hacim (varsayılan: 10000000)
MIN_ADX                       - Güçlü trend ADX eşiği (varsayılan: 25)
TELEGRAM_NOTIFY_TRADES        - Trade bildirimleri gönder (varsayılan: "1")

Reddit API (şimdilik hardcoded, ileride .env'e taşınabilir):
------------------------------------------------------------
REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT, 
REDDIT_USERNAME, REDDIT_PASSWORD, ETHERSCAN_API_KEY
"""

import os
from dataclasses import dataclass
from typing import Optional

# python-dotenv varsa .env dosyasını yükle, yoksa sessizce devam et
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv yüklü değil, sadece os.environ kullanılacak


def _get_env_bool(key: str, default: str | bool = "0") -> bool:
    """Ortam değişkenini boolean'a çevir. '1', 'true', 'yes' = True"""
    default_str = str(default).lower() if not isinstance(default, str) else default.lower()
    value = str(os.getenv(key, default_str)).lower()
    return value in ("1", "true", "yes", "on")


def _get_env_int(key: str, default: int) -> int:
    """Ortam değişkenini integer'a çevir."""
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default


def _get_env_float(key: str, default: float) -> float:
    """Ortam değişkenini float'a çevir."""
    try:
        return float(os.getenv(key, str(default)))
    except ValueError:
        return default


def _get_env_str(key: str, default: str) -> str:
    """Ortam değişkenini string olarak al."""
    return os.getenv(key, default)


def _parse_symbols_env() -> tuple:
    """Parse SYMBOLS from env (comma-separated) or use default."""
    env_val = os.getenv("SYMBOLS", "")
    if env_val:
        return tuple(s.strip().upper() for s in env_val.split(",") if s.strip())
    return ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT")


# ═══════════════════════════════════════════════════════════════════════════════
# RUN PROFILE - Çalışma Modu Presetleri
# ═══════════════════════════════════════════════════════════════════════════════
# Options: "paper" (varsayılan), "live", "backtest"
RUN_PROFILE: str = _get_env_str("RUN_PROFILE", "paper").lower()

# Profile-based default değerler
# Env var set edilmişse env kullan, değilse profile default kullan
_PAPER_DEFAULTS = {
    "LIVE_TRADING": False,
    "ALLOW_DANGEROUS_ACTIONS": False,
    "RISK_PER_TRADE": 0.5,  # %0.5 - düşük risk
    "MAX_OPEN_POSITIONS": 2,
    "MAX_DAILY_LOSS_PCT": 1.0,
    "ALERTS_ENABLED": True,
    "ALERT_SEND_TELEGRAM": True,
    "SUMMARY_SEND_TELEGRAM": True,
    "HOURLY_SUMMARY_ENABLED": False,
    "ALERT_LEVEL_MIN": "WARN",
    "TELEGRAM_TRADE_NOTIFICATIONS": False,  # Spam önleme
}

_LIVE_DEFAULTS = {
    "LIVE_TRADING": True,  # Requires ALLOW_DANGEROUS_ACTIONS=True to work
    "RISK_PER_TRADE": 2.0,
    "MAX_OPEN_POSITIONS": 5,
    "MAX_DAILY_LOSS_PCT": 8.0,
    "ALERTS_ENABLED": True,
    "ALERT_SEND_TELEGRAM": True,
    "SUMMARY_SEND_TELEGRAM": True,
    "HOURLY_SUMMARY_ENABLED": False,
    "ALERT_LEVEL_MIN": "INFO",
    "TELEGRAM_TRADE_NOTIFICATIONS": True,
}

def _get_profile_default(key: str, fallback):
    """Get profile-based default, env var takes priority."""
    if RUN_PROFILE == "paper":
        return _PAPER_DEFAULTS.get(key, fallback)
    elif RUN_PROFILE == "live":
        return _LIVE_DEFAULTS.get(key, fallback)
    return fallback


# ═══════════════════════════════════════════════════════════════════════════════
# UNIVERSE MODE - Sembol Evreni Kısıtlaması
# ═══════════════════════════════════════════════════════════════════════════════
UNIVERSE_MODE: str = _get_env_str("UNIVERSE_MODE", "fixed_list")
SYMBOLS: tuple = _parse_symbols_env()

# ═══════════════════════════════════════════════════════════════════════════════
# PAPER TRADING - Başlangıç ve Test Ayarları
# ═══════════════════════════════════════════════════════════════════════════════
PAPER_START_EQUITY: float = _get_env_float("PAPER_START_EQUITY", 1000.0)
PAPER_SANITY_MODE: bool = _get_env_bool("PAPER_SANITY_MODE", False)


@dataclass(frozen=True)
class Settings:
    """
    Değiştirilemez (immutable) ayarlar.
    Tüm değerler ortam değişkenlerinden okunur, yoksa varsayılanlar kullanılır.
    """
    
    # ═══════════════════════════════════════════════════════════════════════════
    # İŞLEM MODU (Profile-based defaults)
    # ═══════════════════════════════════════════════════════════════════════════
    # True = Gerçek para ile işlem yapar (ÇOK DİKKATLİ KULLANIN!)
    # Paper profile: False, Live profile: True (requires ALLOW_DANGEROUS_ACTIONS)
    LIVE_TRADING: bool = _get_env_bool("LIVE_TRADING", _get_profile_default("LIVE_TRADING", False))
    # True = LIVE_TRADING aktifken işleme izin verir (güvenlik kilidi)
    ALLOW_DANGEROUS_ACTIONS: bool = _get_env_bool("ALLOW_DANGEROUS_ACTIONS", _get_profile_default("ALLOW_DANGEROUS_ACTIONS", False))
    
    # ═══════════════════════════════════════════════════════════════════════════
    # API ANAHTARLARI (Zorunlu - .env'den okunmalı)
    # ═══════════════════════════════════════════════════════════════════════════
    BINANCE_API_KEY: str = os.getenv("BINANCE_API_KEY", "")
    BINANCE_SECRET_KEY: str = os.getenv("BINANCE_SECRET_KEY", "")
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
    
    # Reddit API (sentiment analizi için)
    # REDDIT_ENABLED: Reddit entegrasyonu aktif mi? (API erişimi yoksa False yapın)
    REDDIT_ENABLED: bool = _get_env_bool("REDDIT_ENABLED", False)
    REDDIT_CLIENT_ID: str = os.getenv("REDDIT_CLIENT_ID", "")
    REDDIT_CLIENT_SECRET: str = os.getenv("REDDIT_CLIENT_SECRET", "")
    REDDIT_USER_AGENT: str = os.getenv("REDDIT_USER_AGENT", "CryptoBot/1.0")
    REDDIT_USERNAME: str = os.getenv("REDDIT_USERNAME", "")
    REDDIT_PASSWORD: str = os.getenv("REDDIT_PASSWORD", "")
    
    # Etherscan API (on-chain whale tracking için)
    ETHERSCAN_API_KEY: str = os.getenv("ETHERSCAN_API_KEY", "")
    
    # ═══════════════════════════════════════════════════════════════════════════
    # AI AGENT EŞİKLERİ
    # ═══════════════════════════════════════════════════════════════════════════
    # Teknik analiz için minimum güven skoru (0-100)
    AI_TECH_CONFIDENCE_THRESHOLD: int = 70
    # Haber analizi için minimum güven skoru (0-100)
    AI_NEWS_CONFIDENCE_THRESHOLD: int = 70
    # AI satış kararı için minimum güven skoru (0-100)
    AI_SELL_CONFIDENCE_THRESHOLD: int = 75
    # False ise, bot haber analizi için LLM çağrısı yapmaz
    USE_NEWS_LLM: bool = True
    
    # Strateji LLM Kontrolleri
    # USE_STRATEGY_LLM: False = strateji kararları sadece kurallara dayalı (Gemini sinyal üretimi YOK)
    USE_STRATEGY_LLM: bool = False  # ⚠️ LLM sinyal üretimi KAPALI - sadece Risk Veto aktif
    # STRATEGY_LLM_MODE: "only_on_signal" veya "always" - USE_STRATEGY_LLM=False ise yoksayılır
    STRATEGY_LLM_MODE: str = "always"
    # STRATEGY_LLM_MIN_RULES_CONF: Kurallar güveni bu eşiğin üzerindeyse LLM çağır
    STRATEGY_LLM_MIN_RULES_CONF: int = 65
    
    # Strategy Engine Ağırlıkları
    # Ana karar ağırlıkları (toplam = 1.0)
    STRATEGY_WEIGHT_MATH: float = 0.60  # Matematiksel skorlar (%60 - teknik verilere öncelik)
    STRATEGY_WEIGHT_AI: float = 0.40    # LLM kararı (%40 - AI halüsinasyonlarını azalt)
    
    # Math Layer alt ağırlıkları (toplam = 1.0)
    MATH_WEIGHT_TECHNICAL: float = 0.80  # Teknik göstergeler
    MATH_WEIGHT_ONCHAIN: float = 0.10    # On-chain veri
    MATH_WEIGHT_FNG: float = 0.10        # Fear & Greed Index
    
    # Haber LLM Kontrolleri
    # NEWS_LLM_MODE: "off" = haber LLM'i asla çağırma
    #                "global_summary" = TTL başına bir kez genel haber özeti oluştur
    NEWS_LLM_MODE: str = "global_summary"
    NEWS_LLM_GLOBAL_TTL_SEC: int = _get_env_int("NEWS_LLM_GLOBAL_TTL_SEC", 900)  # 15 dakika
    
    # Market Data Engine Ayarları
    # RSS Feed URL'leri (haber kaynakları)
    RSS_FEED_URLS: tuple = (
        "https://cointelegraph.com/rss",
        "https://decrypt.co/feed",
        "https://www.coindesk.com/arc/outboundfeeds/rss/"
    )
    RSS_MAX_AGE_HOURS: int = 4  # Haberlerin max yaşı (saat)
    
    # Ana döngü süresi (saniye) - her döngü arasında bekleme
    LOOP_SECONDS: int = 900  # 15 dakika
    
    # Cache TTL ayarları (saniye)
    CACHE_TTL_PRICE: float = 1.0  # Fiyat cache
    CACHE_TTL_TECH: float = 15.0  # Teknik göstergeler
    CACHE_TTL_SENTIMENT: float = 90.0  # Sentiment (FnG, Reddit, RSS)
    CACHE_TTL_ONCHAIN: float = 120.0  # On-chain veri
    
    # API Timeout ayarları (saniye)
    API_TIMEOUT_DEFAULT: int = 10  # Genel API timeout
    API_TIMEOUT_FNG: int = 15  # Fear & Greed API
    API_TIMEOUT_ETHERSCAN: int = 10  # Etherscan API
    
    # Global Risk Kontrolleri (Profile-based defaults)
    # Günlük maksimum kayıp yüzdesi - aşılırsa işlemler durur
    # Paper: 1.0%, Live: 8.0%
    MAX_DAILY_LOSS_PCT: float = _get_env_float("MAX_DAILY_LOSS_PCT", _get_profile_default("MAX_DAILY_LOSS_PCT", 8.0))
    # Aynı anda açık tutulabilecek maksimum pozisyon sayısı
    # Paper: 2, Live: 5
    MAX_OPEN_POSITIONS: int = _get_env_int("MAX_OPEN_POSITIONS", _get_profile_default("MAX_OPEN_POSITIONS", 5))
    # Ardışık zarar sayısı - aşılırsa cooldown başlar
    MAX_CONSECUTIVE_LOSSES: int = 5
    # Ardışık zarar sonrası bekleme süresi (dakika)
    COOLDOWN_MINUTES: int = 60
    
    # ADX Eşikleri (Dengeli - güçlü trend iste)
    MIN_ADX_ENTRY: float = 20.0  # Düşürüldü - piyasa koşullarına daha uyumlu
    MIN_ADX_ENTRY_SOFT: float = 18.0
    SOFTEN_ADX_WHEN_CONF_GE: int = 75
    
    # Risk Manager Ayarları (Profile-based)
    # Paper: 0.5%, Live: 2.0%
    RISK_PER_TRADE: float = _get_env_float("RISK_PER_TRADE", _get_profile_default("RISK_PER_TRADE", 2.0)) / 100.0  # İşlem başına max risk
    MIN_VOLUME_GUARDRAIL: int = 1_000_000  # Min 24h volume ($1M)
    FNG_EXTREME_FEAR: int = 15  # Düşürüldü - extreme fear'da da işlem yapabilir
    
    # ═══════════════════════════════════════════════════════════════════════════
    # TRADING AYARLARI
    # ═══════════════════════════════════════════════════════════════════════════
    # Paper trading başlangıç bakiyesi (USDT)
    BASLANGIC_BAKIYE: float = 1000.0
    # İşlem için minimum 24 saatlik hacim (USD)
    MIN_VOLUME_USD: int = 200_000
    # Minimum ADX değeri - trend gücü göstergesi
    MIN_ADX: int = 22
    
    # İzlenecek coinler (USDT bazlı çiftler)
    # Bu listeyi düzenleyerek coin ekle/çıkarabilirsiniz
    WATCHLIST: tuple = (
        "BTCUSDT",
        "ETHUSDT",
        "SOLUSDT",
        "BNBUSDT",
        "XRPUSDT",
        "AVAXUSDT",
        "LINKUSDT"
    )
    
    # Kâr Koruma Ayarları
    # Kârlı pozisyonların erken satılmasını engeller
    PROTECT_PROFITABLE_POSITIONS: bool = True
    MIN_PROFIT_TO_PROTECT: float = 1.5  # %0.5 kâr varsa koru
    AI_SELL_OVERRIDE_CONFIDENCE: int = 90  # AI bu güvenin üstündeyse kâr korumasını geç
    
    # Live Order Retry Ayarları
    LIVE_ORDER_MAX_RETRIES: int = 3  # Başarısız order için max deneme
    LIVE_ORDER_RETRY_DELAY: float = 2.0  # Denemeler arası bekleme (saniye)
    
    # Order Executor Ayarları
    # Slippage ve fee simülasyonu (paper trading için)
    SIMULATED_SLIPPAGE_PCT: float = 0.001  # %0.1 slippage
    SIMULATED_FEE_PCT: float = 0.001  # %0.1 fee (Binance default)
    
    # Rate Limiting - çok hızlı order spam'ini engeller
    ORDER_MIN_INTERVAL_SEC: float = 1.0  # İki order arası minimum bekleme
    
    # SL/TP Watchdog Ayarları
    # Açık pozisyonların SL/TP kontrolünü ana döngüden bağımsız yapar
    SLTP_WATCHDOG_ENABLED: bool = True  # Watchdog aktif mi?
    SLTP_WATCHDOG_INTERVAL_SEC: int = 30  # Kaç saniyede bir kontrol (varsayılan: 30sn)
    
    # LoopController Alarm Eşikleri
    # Telegram uyarısı göndermeden önce kaç ardışık hata beklenecek
    ALARM_PARSE_FAIL_THRESHOLD: int = 15  # LLM parse hata limiti
    ALARM_ADX_BLOCK_THRESHOLD: int = 20   # ADX bloğu limiti
    ALARM_DATA_FAIL_THRESHOLD: int = 5    # Veri çekme hatası limiti
    
    # Logger Ayarları
    LOG_LEVEL: str = "INFO"  # DEBUG, INFO, WARNING, ERROR, CRITICAL
    LOG_JSON_ENABLED: bool = False  # JSON log dosyası oluştur (log analizi için)
    LOG_MAX_BYTES: int = 10_000_000  # 10 MB
    LOG_BACKUP_COUNT: int = 5  # Eski log dosyası sayısı
    
    # ═══════════════════════════════════════════════════════════════════════════
    # V1 STRATEJİ AYARLARI - Rejim Filtreli Swing Trend
    # ═══════════════════════════════════════════════════════════════════════════
    # Strateji modu: "REGIME_SWING_TREND_V1" = V1 stratejisi, "LEGACY" = eski strateji
    STRATEGY_MODE: str = "REGIME_SWING_TREND_V1"
    # Ana sinyal zaman dilimi (trend yapısı, EMA, ADX için)
    SIGNAL_TIMEFRAME: str = "1h"
    # Tetikleme zaman dilimi (breakout teyidi için)
    TRIGGER_TIMEFRAME: str = "15m"
    
    # ─────────────────────────────────────────────────────────────────────────────
    # Rejim Filtresi (trade sayısını düşürmek için zorunlu)
    # ─────────────────────────────────────────────────────────────────────────────
    # Minimum volatilite: ATR(14) / price * 100
    MIN_ATR_PCT: float = 0.3
    # Maximum volatilite (aşırı volatilite filtresi)
    MAX_ATR_PCT: float = 3.0
    # Hacim filtresi için lookback (son N mumun ortalaması)
    MIN_VOLUME_LOOKBACK: int = 10
    # Hacim çarpanı: current_volume >= avg_volume * MIN_VOLUME_MULT için geçer
    MIN_VOLUME_MULT: float = 0.8
    
    # ─────────────────────────────────────────────────────────────────────────────
    # Entry/Exit Ayarları
    # ─────────────────────────────────────────────────────────────────────────────
    # Stop loss ATR çarpanı: SL = entry - SL_ATR_MULT * ATR(14)
    SL_ATR_MULT: float = 1.5
    # Kısmi kâr alma aktif mi?
    PARTIAL_TP_ENABLED: bool = True
    # 1R'de pozisyonun ne kadarı satılacak (0.0-1.0)
    PARTIAL_TP_FRACTION: float = 0.5
    # Trailing stop aktif mi?
    TRAILING_ENABLED: bool = True
    # Trailing için HighestClose lookback
    TRAIL_LOOKBACK: int = 22
    # Trailing stop ATR çarpanı: trail = HighestClose - TRAIL_ATR_MULT * ATR
    TRAIL_ATR_MULT: float = 3.0
    # EMA50 slope hesabı için lookback (kaç bar öncesiyle karşılaştır)
    EMA_SLOPE_LOOKBACK: int = 5
    # Breakout için lookback (HighestHigh/HighestClose)
    BREAKOUT_LOOKBACK: int = 20
    # Trigger timeframe (sinyal tetikleme için kullanılan ana timeframe)
    TRIGGER_TIMEFRAME: str = "15m"
    
    # ─────────────────────────────────────────────────────────────────────────────
    # V1 Risk / Pozisyon Boyutlandırma
    # ─────────────────────────────────────────────────────────────────────────────
    # V1 için işlem başına risk yüzdesi (daha konservatif)
    RISK_PER_TRADE_V1: float = 1.0  # %1 - güvenli varsayılan
    # Volatilite hedefleme: pozisyon boyutunu ATR'ye göre ayarla
    TARGET_ATR_PCT: float = 1.0
    # Volatilite ölçeği sınırları
    MIN_VOL_SCALE: float = 0.5
    MAX_VOL_SCALE: float = 1.5
    
    # ─────────────────────────────────────────────────────────────────────────────
    # V1 Execution Ayarları
    # ─────────────────────────────────────────────────────────────────────────────
    # Emir yürütme modu: "LIMIT_THEN_MARKET" veya "MARKET_ONLY"
    ENTRY_EXECUTION_MODE: str = "LIMIT_THEN_MARKET"
    # LIMIT emir timeout süresi (saniye) - dolmazsa MARKET'e geç
    LIMIT_TIMEOUT_SEC: int = 45
    
    # ─────────────────────────────────────────────────────────────────────────────
    # V1 LLM Kontrolleri - Risk Veto Only
    # ─────────────────────────────────────────────────────────────────────────────
    # V1'de strateji LLM skorlaması kapalı (deterministik kurallar kullanılır)
    USE_STRATEGY_LLM_V1: bool = False
    # Haber/olay bazlı risk veto aktif mi?
    USE_NEWS_LLM_VETO: bool = True
    # Veto için minimum güven skoru (0-100)
    NEWS_VETO_MIN_CONF: int = 70
    # Veto cache süresi (dakika) - aynı coin için tekrar LLM çağırma
    NEWS_VETO_CACHE_MINUTES: int = 10
    # Veto durumunda stop'u sıkılaştır mı?
    NEWS_VETO_TIGHTEN_STOP: bool = False
    # Veto sıkılaştırma çarpanı (SL mesafesini bu oranla çarp)
    NEWS_VETO_TIGHTEN_MULT: float = 0.7
    # Risk keyword prefilter - bu kelimeler yoksa LLM çağırma
    RISK_VETO_KEYWORDS: tuple = (
        "hack", "hacked", "exploit", "exploited", "breach",
        "delist", "delisting", "delisted",
        "withdraw", "withdrawal", "paused", "suspended", "frozen",
        "sec", "regulatory", "investigation", "lawsuit", "sued",
        "rug", "rugpull", "scam", "fraud",
        "crash", "collapse", "insolvent", "bankrupt",
        "vulnerability", "critical", "emergency", "halt"
    )
    
    # ─────────────────────────────────────────────────────────────────────────────
    # V1 Güvenlik Kontrolleri
    # ─────────────────────────────────────────────────────────────────────────────
    # Ardışık stop sayısı limiti - aşılırsa cooldown başlar
    MAX_CONSECUTIVE_STOPS: int = 3
    # Ardışık stop sonrası ek cooldown (dakika) - COOLDOWN_MINUTES'e eklenir
    CONSECUTIVE_STOPS_EXTRA_COOLDOWN: int = 30
    
    # ─────────────────────────────────────────────────────────────────────────────
    # ORDER LEDGER & IDEMPOTENCY (Production-grade)
    # ─────────────────────────────────────────────────────────────────────────────
    # Order ledger aktif mi? (signal_id idempotency kontrolü)
    ORDER_LEDGER_ENABLED: bool = True
    # Canceled/rejected signaller için yeniden deneme izni
    ALLOW_RETRY_SAME_SIGNAL: bool = False
    
    # ─────────────────────────────────────────────────────────────────────────────
    # LLM RATE LIMITING
    # ─────────────────────────────────────────────────────────────────────────────
    # Saat başına maksimum LLM çağrısı (veto + diğer)
    MAX_LLM_CALLS_PER_HOUR: int = 10
    
    # ─────────────────────────────────────────────────────────────────────────────
    # METRICS & TELEMETRY
    # ─────────────────────────────────────────────────────────────────────────────
    # Kaç döngüde bir metrik özeti loglansın
    METRICS_LOG_EVERY_N_CYCLES: int = 20
    # Günlük metrikler dosyaya kaydedilsin mi
    METRICS_PERSIST_DAILY: bool = True
    
    # ═══════════════════════════════════════════════════════════════════════════
    # TELEGRAM BİLDİRİM AYARLARI (Profile-based)
    # ═══════════════════════════════════════════════════════════════════════════
    # Trade işlemleri için bildirim gönder (BUY/SELL)
    # Paper: False (spam önleme), Live: True
    TELEGRAM_NOTIFY_TRADES: bool = _get_env_bool("TELEGRAM_NOTIFY_TRADES", _get_profile_default("TELEGRAM_TRADE_NOTIFICATIONS", True))
    # Reddit sentiment analizi için bildirim gönder
    TELEGRAM_NOTIFY_REDDIT: bool = False
    # On-chain whale hareketleri için bildirim gönder
    TELEGRAM_NOTIFY_ONCHAIN: bool = False
    # Önemli haberler için bildirim gönder
    TELEGRAM_NOTIFY_IMPORTANT_NEWS: bool = False
    
    # ─────────────────────────────────────────────────────────────────────────────
    # SUMMARY REPORTER (Periyodik Özet Raporlama)
    # ─────────────────────────────────────────────────────────────────────────────
    # Günlük özet rapor aktif mi
    DAILY_SUMMARY_ENABLED: bool = True
    # Günlük rapor saati (Europe/Istanbul)
    DAILY_SUMMARY_TIME: str = "23:59"
    # Saatlik özet rapor aktif mi (Paper: False, Live: user-defined)
    HOURLY_SUMMARY_ENABLED: bool = _get_env_bool("HOURLY_SUMMARY_ENABLED", _get_profile_default("HOURLY_SUMMARY_ENABLED", False))
    # Özet raporları Telegram'a gönder (Paper: True, Live: True)
    SUMMARY_SEND_TELEGRAM: bool = _get_env_bool("SUMMARY_SEND_TELEGRAM", _get_profile_default("SUMMARY_SEND_TELEGRAM", False))
    # Özet için özel Telegram chat_id (None = mevcut TELEGRAM_CHAT_ID kullan)
    SUMMARY_TELEGRAM_CHAT_ID: str = None
    # Son rapor zamanını dosyaya kaydet (restart koruması)
    SUMMARY_PERSIST_STATE: bool = True
    
    # ─────────────────────────────────────────────────────────────────────────────
    # ALERT MANAGER (Kritik Olay Bildirimleri)
    # ─────────────────────────────────────────────────────────────────────────────
    # Alert sistemi aktif mi
    ALERTS_ENABLED: bool = _get_env_bool("ALERTS_ENABLED", _get_profile_default("ALERTS_ENABLED", True))
    # Alert'leri Telegram'a gönder (Paper: True, Live: True)
    ALERT_SEND_TELEGRAM: bool = _get_env_bool("ALERT_SEND_TELEGRAM", _get_profile_default("ALERT_SEND_TELEGRAM", False))
    # Alert için özel Telegram chat_id (None = mevcut kullan)
    ALERT_TELEGRAM_CHAT_ID: str = None
    # Aynı alert kodu için tekrar bildirimi engelle (dakika)
    ALERT_THROTTLE_MINUTES: int = 30
    # Throttle state'i dosyaya kaydet (restart koruması)
    ALERT_PERSIST_STATE: bool = True
    # Minimum alert seviyesi (INFO/WARN/CRITICAL)
    # Paper: WARN (INFO spam önleme), Live: INFO
    ALERT_LEVEL_MIN: str = _get_env_str("ALERT_LEVEL_MIN", _get_profile_default("ALERT_LEVEL_MIN", "INFO"))
    
    def is_configured(self) -> bool:
        """Zorunlu API anahtarlarının ayarlanıp ayarlanmadığını kontrol eder."""
        return all([
            self.BINANCE_API_KEY,
            self.BINANCE_SECRET_KEY,
            self.GEMINI_API_KEY,
            self.TELEGRAM_BOT_TOKEN,
            self.TELEGRAM_CHAT_ID
        ])
    
    def get_missing_keys(self) -> list:
        """Eksik zorunlu API anahtarlarını döndürür."""
        missing = []
        if not self.BINANCE_API_KEY:
            missing.append("BINANCE_API_KEY")
        if not self.BINANCE_SECRET_KEY:
            missing.append("BINANCE_SECRET_KEY")
        if not self.GEMINI_API_KEY:
            missing.append("GEMINI_API_KEY")
        if not self.TELEGRAM_BOT_TOKEN:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not self.TELEGRAM_CHAT_ID:
            missing.append("TELEGRAM_CHAT_ID")
        return missing


# Global settings instance
SETTINGS = Settings()


# ═══════════════════════════════════════════════════════════════════════════════
# ZORUNLU ORTAM DEĞİŞKENLERİ LİSTESİ
# ═══════════════════════════════════════════════════════════════════════════════
REQUIRED_ENV_VARS = [
    "BINANCE_API_KEY",
    "BINANCE_SECRET_KEY", 
    "GEMINI_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID"
]

OPTIONAL_ENV_VARS = [
    "LIVE_TRADING",
    "ALLOW_DANGEROUS_ACTIONS",
    "AI_TECH_CONFIDENCE_THRESHOLD",
    "AI_NEWS_CONFIDENCE_THRESHOLD",
    "AI_SELL_CONFIDENCE_THRESHOLD",
    "USE_NEWS_LLM",
    "MAX_DAILY_LOSS_PCT",
    "MAX_OPEN_POSITIONS",
    "MAX_CONSECUTIVE_LOSSES",
    "COOLDOWN_MINUTES",
    "BASLANGIC_BAKIYE",
    "MIN_HACIM_USDT",
    "MIN_ADX",
    "TELEGRAM_NOTIFY_TRADES",
    "TELEGRAM_NOTIFY_REDDIT",
    "TELEGRAM_NOTIFY_ONCHAIN",
    "TELEGRAM_NOTIFY_IMPORTANT_NEWS"
]


def print_settings_summary():
    """Ayarların özetini yazdırır (API anahtarları maskelenir)."""
    def mask(value: str) -> str:
        if not value:
            return "❌ EKSİK"
        if len(value) < 8:
            return "***"
        return f"{value[:4]}...{value[-4:]}"
    
    print("\n" + "=" * 60)
    print("📋 CONFIG.PY - AYARLAR ÖZETİ")
    print("=" * 60)
    
    print("\n🔐 API ANAHTARLARI:")
    print(f"   BINANCE_API_KEY:     {mask(SETTINGS.BINANCE_API_KEY)}")
    print(f"   BINANCE_SECRET_KEY:  {mask(SETTINGS.BINANCE_SECRET_KEY)}")
    print(f"   GEMINI_API_KEY:      {mask(SETTINGS.GEMINI_API_KEY)}")
    print(f"   TELEGRAM_BOT_TOKEN:  {mask(SETTINGS.TELEGRAM_BOT_TOKEN)}")
    print(f"   TELEGRAM_CHAT_ID:    {SETTINGS.TELEGRAM_CHAT_ID or '❌ EKSİK'}")
    
    print("\n⚙️ İŞLEM MODU:")
    print(f"   LIVE_TRADING:              {'🔴 CANLI' if SETTINGS.LIVE_TRADING else '🟢 PAPER'}")
    print(f"   ALLOW_DANGEROUS_ACTIONS:   {'⚠️ AÇIK' if SETTINGS.ALLOW_DANGEROUS_ACTIONS else '✅ KAPALI'}")
    
    print("\n🤖 AI EŞİKLERİ:")
    print(f"   AI_TECH_CONFIDENCE:   {SETTINGS.AI_TECH_CONFIDENCE_THRESHOLD}%")
    print(f"   AI_NEWS_CONFIDENCE:   {SETTINGS.AI_NEWS_CONFIDENCE_THRESHOLD}%")
    print(f"   AI_SELL_CONFIDENCE:   {SETTINGS.AI_SELL_CONFIDENCE_THRESHOLD}%")
    
    print("\n💰 TRADING AYARLARI:")
    print(f"   BASLANGIC_BAKIYE:     ${SETTINGS.BASLANGIC_BAKIYE:,.2f}")
    print(f"   MIN_VOLUME_USD:       ${SETTINGS.MIN_VOLUME_USD:,}")
    print(f"   MIN_ADX:              {SETTINGS.MIN_ADX}")
    
    print("\n📱 TELEGRAM BİLDİRİMLERİ:")
    print(f"   Trades:          {'✅' if SETTINGS.TELEGRAM_NOTIFY_TRADES else '❌'}")
    print(f"   Reddit:          {'✅' if SETTINGS.TELEGRAM_NOTIFY_REDDIT else '❌'}")
    print(f"   On-Chain:        {'✅' if SETTINGS.TELEGRAM_NOTIFY_ONCHAIN else '❌'}")
    print(f"   Important News:  {'✅' if SETTINGS.TELEGRAM_NOTIFY_IMPORTANT_NEWS else '❌'}")
    
    print("\n" + "-" * 60)
    
    missing = SETTINGS.get_missing_keys()
    if missing:
        print(f"⚠️ EKSİK ZORUNLU DEĞİŞKENLER: {', '.join(missing)}")
        print("   Bu değişkenleri .env dosyasına ekleyin!")
    else:
        print("✅ Tüm zorunlu API anahtarları ayarlanmış.")
    

# Modül doğrudan çalıştırılırsa ayarları göster
if __name__ == "__main__":
    print_settings_summary()
