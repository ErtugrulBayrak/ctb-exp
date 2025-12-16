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


@dataclass(frozen=True)
class Settings:
    """
    Değiştirilemez (immutable) ayarlar.
    Tüm değerler ortam değişkenlerinden okunur, yoksa varsayılanlar kullanılır.
    """
    
    # ═══════════════════════════════════════════════════════════════════════════
    # İŞLEM MODU
    # ═══════════════════════════════════════════════════════════════════════════
    # True = Gerçek para ile işlem yapar (ÇOK DİKKATLİ KULLANIN!)
    LIVE_TRADING: bool = False
    # True = LIVE_TRADING aktifken işleme izin verir (güvenlik kilidi)
    ALLOW_DANGEROUS_ACTIONS: bool = False
    
    # ═══════════════════════════════════════════════════════════════════════════
    # API ANAHTARLARI (Zorunlu - .env'den okunmalı)
    # ═══════════════════════════════════════════════════════════════════════════
    BINANCE_API_KEY: str = os.getenv("BINANCE_API_KEY", "")
    BINANCE_SECRET_KEY: str = os.getenv("BINANCE_SECRET_KEY", "")
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
    
    # Reddit API (sentiment analizi için)
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
    AI_TECH_CONFIDENCE_THRESHOLD: int = 75
    # Haber analizi için minimum güven skoru (0-100)
    AI_NEWS_CONFIDENCE_THRESHOLD: int = 80
    # AI satış kararı için minimum güven skoru (0-100)
    AI_SELL_CONFIDENCE_THRESHOLD: int = 70
    # False ise, bot haber analizi için LLM çağrısı yapmaz
    USE_NEWS_LLM: bool = True
    
    # Strateji LLM Kontrolleri
    # USE_STRATEGY_LLM: False ise, strateji kararları sadece kurallara dayalıdır (Gemini çağrısı yok)
    USE_STRATEGY_LLM: bool = True
    # STRATEGY_LLM_MODE: "only_on_signal" = RULES BUY/SELL derse LLM çağır
    #                    "always" = her döngüde her sembol için LLM çağır (pahalı)
    STRATEGY_LLM_MODE: str = "always"
    # STRATEGY_LLM_MIN_RULES_CONF: Kurallar güveni bu eşiğin üzerindeyse LLM çağır
    STRATEGY_LLM_MIN_RULES_CONF: int = 65
    
    # Strategy Engine Ağırlıkları
    # Ana karar ağırlıkları (toplam = 1.0)
    STRATEGY_WEIGHT_MATH: float = 0.35  # Matematiksel skorlar (%35)
    STRATEGY_WEIGHT_AI: float = 0.65    # LLM kararı (%65)
    
    # Math Layer alt ağırlıkları (toplam = 1.0)
    MATH_WEIGHT_TECHNICAL: float = 0.70  # Teknik göstergeler
    MATH_WEIGHT_ONCHAIN: float = 0.15    # On-chain veri
    MATH_WEIGHT_FNG: float = 0.15        # Fear & Greed Index
    
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
    
    # Cache TTL ayarları (saniye)
    CACHE_TTL_PRICE: float = 1.0  # Fiyat cache
    CACHE_TTL_TECH: float = 15.0  # Teknik göstergeler
    CACHE_TTL_SENTIMENT: float = 90.0  # Sentiment (FnG, Reddit, RSS)
    CACHE_TTL_ONCHAIN: float = 120.0  # On-chain veri
    
    # API Timeout ayarları (saniye)
    API_TIMEOUT_DEFAULT: int = 10  # Genel API timeout
    API_TIMEOUT_FNG: int = 15  # Fear & Greed API
    API_TIMEOUT_ETHERSCAN: int = 10  # Etherscan API
    
    # Global Risk Kontrolleri
    # Günlük maksimum kayıp yüzdesi - aşılırsa işlemler durur
    MAX_DAILY_LOSS_PCT: float = 8.0
    # Aynı anda açık tutulabilecek maksimum pozisyon sayısı
    MAX_OPEN_POSITIONS: int = 10
    # Ardışık zarar sayısı - aşılırsa cooldown başlar
    MAX_CONSECUTIVE_LOSSES: int = 5
    # Ardışık zarar sonrası bekleme süresi (dakika)
    COOLDOWN_MINUTES: int = 60
    
    # ADX Eşikleri (Yarı-agresif varsayılanlar)
    MIN_ADX_ENTRY: float = 22.0
    MIN_ADX_ENTRY_SOFT: float = 18.0
    SOFTEN_ADX_WHEN_CONF_GE: int = 75
    
    # Risk Manager Ayarları
    RISK_PER_TRADE: float = 0.02  # İşlem başına max risk (%2)
    MIN_VOLUME_GUARDRAIL: int = 1_000_000  # Min 24h volume ($1M)
    FNG_EXTREME_FEAR: int = 20  # Bu değerin altında alım yapma
    
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
    MIN_PROFIT_TO_PROTECT: float = 0.5  # %0.5 kâr varsa koru
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
    # TELEGRAM BİLDİRİM AYARLARI
    # ═══════════════════════════════════════════════════════════════════════════
    # Trade işlemleri için bildirim gönder (BUY/SELL)
    TELEGRAM_NOTIFY_TRADES: bool = True
    # Reddit sentiment analizi için bildirim gönder
    TELEGRAM_NOTIFY_REDDIT: bool = False
    # On-chain whale hareketleri için bildirim gönder
    TELEGRAM_NOTIFY_ONCHAIN: bool = False
    # Önemli haberler için bildirim gönder
    TELEGRAM_NOTIFY_IMPORTANT_NEWS: bool = False
    
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
