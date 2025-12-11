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


def _get_env_bool(key: str, default: str = "0") -> bool:
    """Ortam değişkenini boolean'a çevir. '1', 'true', 'yes' = True"""
    value = os.getenv(key, default).lower()
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
    LIVE_TRADING: bool = _get_env_bool("LIVE_TRADING", "0")
    ALLOW_DANGEROUS_ACTIONS: bool = _get_env_bool("ALLOW_DANGEROUS_ACTIONS", "0")
    
    # ═══════════════════════════════════════════════════════════════════════════
    # API ANAHTARLARI (Zorunlu - .env'den okunmalı)
    # ═══════════════════════════════════════════════════════════════════════════
    BINANCE_API_KEY: str = os.getenv("BINANCE_API_KEY", "")
    BINANCE_SECRET_KEY: str = os.getenv("BINANCE_SECRET_KEY", "")
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
    
    # ═══════════════════════════════════════════════════════════════════════════
    # AI AGENT EŞİKLERİ
    # ═══════════════════════════════════════════════════════════════════════════
    AI_TECH_CONFIDENCE_THRESHOLD: int = _get_env_int("AI_TECH_CONFIDENCE_THRESHOLD", 75)
    AI_NEWS_CONFIDENCE_THRESHOLD: int = _get_env_int("AI_NEWS_CONFIDENCE_THRESHOLD", 80)
    AI_SELL_CONFIDENCE_THRESHOLD: int = _get_env_int("AI_SELL_CONFIDENCE_THRESHOLD", 70)
    
    # ═══════════════════════════════════════════════════════════════════════════
    # TRADING AYARLARI
    # ═══════════════════════════════════════════════════════════════════════════
    BASLANGIC_BAKIYE: float = _get_env_float("BASLANGIC_BAKIYE", 1000.0)
    MIN_HACIM_USDT: int = _get_env_int("MIN_HACIM_USDT", 10_000_000)
    MIN_ADX: int = _get_env_int("MIN_ADX", 25)
    
    # ═══════════════════════════════════════════════════════════════════════════
    # TELEGRAM BİLDİRİM AYARLARI
    # ═══════════════════════════════════════════════════════════════════════════
    TELEGRAM_NOTIFY_TRADES: bool = _get_env_bool("TELEGRAM_NOTIFY_TRADES", "1")
    TELEGRAM_NOTIFY_REDDIT: bool = _get_env_bool("TELEGRAM_NOTIFY_REDDIT", "0")
    TELEGRAM_NOTIFY_ONCHAIN: bool = _get_env_bool("TELEGRAM_NOTIFY_ONCHAIN", "0")
    TELEGRAM_NOTIFY_IMPORTANT_NEWS: bool = _get_env_bool("TELEGRAM_NOTIFY_IMPORTANT_NEWS", "0")
    
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
    print(f"   MIN_HACIM_USDT:       ${SETTINGS.MIN_HACIM_USDT:,}")
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
    
    print("\n📝 .ENV DOSYASI ŞABLONu:")
    print("-" * 60)
    for var in REQUIRED_ENV_VARS:
        print(f"{var}=your_{var.lower()}_here")
    print("-" * 60)
    print()


# Modül doğrudan çalıştırılırsa ayarları göster
if __name__ == "__main__":
    print_settings_summary()
