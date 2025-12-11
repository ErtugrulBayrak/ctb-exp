import os
import sys

# ═══════════════════════════════════════════════════════════════════════════════
# WINDOWS TERMINAL UTF-8 ENCODING AYARI
# ═══════════════════════════════════════════════════════════════════════════════
# Sunucularda emoji ve Türkçe karakterlerin düzgün görünmesi için
if sys.platform == 'win32':
    os.system('chcp 65001 >nul 2>&1')  # Windows code page'i UTF-8 yap
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        pass  # Python < 3.7 için

# ═══════════════════════════════════════════════════════════════════════════════
# GRPC/ALTS UYARILARINI TAMAMEN BASTIR (C++ seviyesinde)
# ═══════════════════════════════════════════════════════════════════════════════
# Bu ayarlar TÜM import'lardan ÖNCE yapılmalı
os.environ['GRPC_VERBOSITY'] = 'NONE'
os.environ['GRPC_TRACE'] = ''
os.environ['GRPC_ENABLE_FORK_SUPPORT'] = '0'
os.environ['GLOG_minloglevel'] = '3'
os.environ['GLOG_logtostderr'] = '0'
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
os.environ['ABSL_MIN_LOG_LEVEL'] = '3'

# stderr'i geçici olarak /dev/null'a yönlendir (gRPC yüklenirken)
_original_stderr = sys.stderr
sys.stderr = open(os.devnull, 'w')

# Şimdi gRPC kullanan kütüphaneleri import et
import warnings
warnings.filterwarnings('ignore')

import logging
logging.getLogger('absl').setLevel(logging.CRITICAL)
logging.getLogger('grpc').setLevel(logging.CRITICAL)
logging.getLogger('urllib3').setLevel(logging.CRITICAL)
logging.getLogger('google').setLevel(logging.CRITICAL)


import json
import time
import telegram
import asyncio
import re
import feedparser
from dateutil import parser as dateutil_parser
import google.generativeai as genai
from binance.client import Client
from binance.exceptions import BinanceAPIException
import pandas as pd
import pandas_ta as ta
from newspaper import Article, Config
import praw
import requests
from datetime import datetime, timedelta, timezone

# stderr'i geri yükle (gRPC yüklendi, artık güvenli)
sys.stderr = _original_stderr


# ═══════════════════════════════════════════════════════════════════════════════
# TERMINAL LOG SİSTEMİ - Tüm çıktıları hem terminale hem dosyaya yaz
# ═══════════════════════════════════════════════════════════════════════════════
class TeeLogger:
    """Hem terminale hem dosyaya yazan logger"""
    def __init__(self, log_dir="logs"):
        self.terminal = sys.stdout
        self.log_dir = log_dir
        
        # logs klasörünü oluştur
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
        
        # Timestamp ile dosya adı oluştur
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = os.path.join(log_dir, f"terminal_log_{timestamp}.txt")
        self.file = open(self.log_file, 'w', encoding='utf-8')
        
        print(f"📝 Terminal log dosyası: {self.log_file}")
    
    def write(self, message):
        self.terminal.write(message)
        self.terminal.flush()
        self.file.write(message)
        self.file.flush()
    
    def flush(self):
        self.terminal.flush()
        self.file.flush()
    
    def close(self):
        self.file.close()

# Terminal log'u aktifleştir
ENABLE_TERMINAL_LOG = True  # False yaparak kapatılabilir

if ENABLE_TERMINAL_LOG:
    tee_logger = TeeLogger()
    sys.stdout = tee_logger


# API Anahtarları (config.py'dan import edilir)
from config import SETTINGS
from order_executor import OrderExecutor
from trade_logger import logger as trade_log

# ═══════════════════════════════════════════════════════════════════════════════
# ORDER EXECUTOR FACTORY
# ═══════════════════════════════════════════════════════════════════════════════
def create_order_executor(binance_client=None):
    """
    OrderExecutor factory fonksiyonu.
    LIVE_TRADING moduna göre dry_run ayarlar.
    
    Args:
        binance_client: Binance Client instance (canlı mod için)
    
    Returns:
        OrderExecutor instance
    """
    return OrderExecutor(
        client=binance_client,
        dry_run=not SETTINGS.LIVE_TRADING
    )

# RSS Feed Kaynakları (NewsAPI yerine - gerçek zamanlı)
RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://decrypt.co/feed",
    "https://cryptoslate.com/feed/",
    "https://bitcoinist.com/feed/"
]
GEMINI_API_KEY = SETTINGS.GEMINI_API_KEY
TELEGRAM_BOT_TOKEN = SETTINGS.TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID = SETTINGS.TELEGRAM_CHAT_ID
BINANCE_API_KEY = SETTINGS.BINANCE_API_KEY
BINANCE_SECRET_KEY = SETTINGS.BINANCE_SECRET_KEY

# Reddit ve Etherscan anahtarları (şimdilik hardcoded, config.py'a taşınabilir)
REDDIT_CLIENT_ID = os.getenv('REDDIT_CLIENT_ID', 'G0rIefRfVdRJoJAFsTKuXA')
REDDIT_CLIENT_SECRET = os.getenv('REDDIT_CLIENT_SECRET', 'tINXoJs8U8nmwLeDxw4mNZPwPymNNw')
REDDIT_USER_AGENT = os.getenv('REDDIT_USER_AGENT', 'NewsToMe by Milburn89')
REDDIT_USERNAME = os.getenv('REDDIT_USERNAME', 'Milburn89')
REDDIT_PASSWORD = os.getenv('REDDIT_PASSWORD', 'Nwpss_reddit2')
ETHERSCAN_API_KEY = os.getenv('ETHERSCAN_API_KEY', '1V19JUXS8257WGG4DQQ4YTTYCGBJNRYR9R')

# ═══════════════════════════════════════════════════════════════════════════════
# GÜVENLİK KAPISI - Canlı işlem koruması
# ═══════════════════════════════════════════════════════════════════════════════
def ensure_safe_to_live():
    """
    Başlangıç güvenlik kontrolü.
    LIVE_TRADING modunda ALLOW_DANGEROUS_ACTIONS olmadan çalışmayı engeller.
    """
    if SETTINGS.LIVE_TRADING:
        if not SETTINGS.ALLOW_DANGEROUS_ACTIONS:
            # Canlı mod isteniyor ama güvenlik kilidi açık değil
            print("=" * 60)
            print("🛑 KRİTİK HATA: CANLI İŞLEM MODU ENGELLENDİ!")
            print("=" * 60)
            print()
            print("LIVE_TRADING=1 ayarlı ancak ALLOW_DANGEROUS_ACTIONS=0")
            print()
            print("Canlı işlem modunu etkinleştirmek için .env dosyasına ekleyin:")
            print("   ALLOW_DANGEROUS_ACTIONS=1")
            print()
            print("⚠️  DİKKAT: Bu mod GERÇEK PARA ile işlem yapar!")
            print("⚠️  Yalnızca ne yaptığınızı biliyorsanız etkinleştirin.")
            print("=" * 60)
            sys.exit(1)
        else:
            # Canlı mod ve güvenlik kilidi açık
            print("=" * 60)
            print("🔴🔴🔴 CANLI İŞLEM MODU AKTİF! 🔴🔴🔴")
            print("=" * 60)
            print("⚠️  GERÇEK PARA ile işlem yapılıyor!")
            print("⚠️  Tüm alım/satımlar GERÇEK!")
            print("=" * 60)
    else:
        # Paper trading modu
        print("=" * 60)
        print("🟢 PAPER TRADING MODU (Simülasyon)")
        print("=" * 60)
        print("💰 Sanal bakiye ile güvenli simülasyon yapılıyor.")
        print("📊 Gerçek piyasa verisi kullanılıyor, işlemler sanal.")
        print("=" * 60)
    
    # API anahtarları kontrolü
    if not SETTINGS.is_configured():
        print()
        print(f"⚠️ EKSİK API ANAHTARLARI: {', '.join(SETTINGS.get_missing_keys())}")
        print("   .env dosyasını kontrol edin!")
        print()

# Güvenlik kapısını çalıştır (modül yüklenirken)
ensure_safe_to_live()


ISLENMIS_HABERLER_DOSYASI = "islenmis_haberler.txt"
PORTFOLIO_DOSYASI = "portfolio.json"
TRADE_LOG_DOSYASI = "trade_decisions_log.json"  # AI karar detayları için
BASLANGIC_BAKIYE = SETTINGS.BASLANGIC_BAKIYE  # USDT - artık config'den

# ─────────────────────────────────────────────────────────────────────────────
# HİBRİT TRADER KONFİGÜRASYONU
# ─────────────────────────────────────────────────────────────────────────────
WATCHLIST = ['BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'ADA', 'DOGE', 'AVAX', 'LINK', 'POL']  # MATIC -> POL (Binance güncellendi)
HABER_MAX_SAAT = 4  # 4 saatten eski haberleri filtrele
MIN_HACIM_USDT = 10_000_000  # $10M minimum 24h hacim
MIN_ADX = 25  # Güçlü trend eşiği
FNG_EXTREME_FEAR = 20  # Alım yapma eşiği (Aşırı Korku)

# ─────────────────────────────────────────────────────────────────────────────
# AI AGENT KONFİGÜRASYONU
# ─────────────────────────────────────────────────────────────────────────────
AI_TECH_CONFIDENCE_THRESHOLD = 75   # Teknik tarama için minimum güven skoru
AI_NEWS_CONFIDENCE_THRESHOLD = 80   # Haber tarama için minimum güven skoru
AI_SELL_CONFIDENCE_THRESHOLD = 70   # Satış kararı için minimum güven skoru
AI_MAX_RETRIES = 3                  # API hatalarında tekrar deneme sayısı
AI_RETRY_DELAY = 2                  # Tekrar deneme arasındaki bekleme (saniye)
AI_BATCH_SIZE = 3                   # Batch AI çağrısı için coin sayısı (MAX_TOKENS önlemek için düşürüldü)

# ─────────────────────────────────────────────────────────────────────────────
# TREND FİLTRESİ VE KÂR KORUMA AYARLARI
# ─────────────────────────────────────────────────────────────────────────────
BLOCK_BUY_IN_DOWNTREND = True       # Düşüş trendinde alım engelle (GÜÇLÜ DÜŞÜŞ iken BUY reddet)
PROTECT_PROFITABLE_POSITIONS = True # Kârdaki pozisyonları AI SELL'den koru
MIN_PROFIT_TO_PROTECT = 0.5         # Koruma için minimum kâr yüzdesi (%)
AI_SELL_OVERRIDE_CONFIDENCE = 90    # Bu güven skorunun üstünde kâr korumasını geç

# Telegram Bildirim Ayarları
TELEGRAM_NOTIFY_REDDIT = False      # Reddit analizi bildirimi gönder?
TELEGRAM_NOTIFY_ONCHAIN = False     # On-chain analizi bildirimi gönder?
TELEGRAM_NOTIFY_TRADES = True       # Trade bildirimleri gönder? (SADECE BU AKTİF)
TELEGRAM_NOTIFY_IMPORTANT_NEWS = False  # Önemli haber bildirimleri gönder?


# Loglama yardımcı fonksiyonları
def log(mesaj, seviye="INFO", girinti=0):
    """Yapılandırılmış log çıktısı üretir."""
    zaman = time.strftime("%H:%M:%S")
    prefix = "  " * girinti
    sembol = {"INFO": "•", "OK": "✓", "WARN": "⚠", "ERR": "✗", "DATA": "→"}.get(seviye, "•")
    print(f"[{zaman}] {prefix}{sembol} {mesaj}", flush=True)

def log_bolum(baslik, emoji="📌"):
    """Yeni bir bölüm başlığı yazdırır."""
    print(f"\n{'─'*50}", flush=True)
    print(f"{emoji} {baslik.upper()}", flush=True)
    print(f"{'─'*50}", flush=True)

def log_ozet(veriler):
    """Döngü özeti yazdırır."""
    print(f"\n{'═'*50}", flush=True)
    print("📋 DÖNGÜ ÖZETİ", flush=True)
    print(f"{'═'*50}", flush=True)
    for anahtar, deger in veriler.items():
        print(f"   {anahtar}: {deger}", flush=True)
    print(f"{'═'*50}\n", flush=True)

# ═══════════════════════════════════════════════════════════════════════════════
# PAPER TRADING - PORTFÖY YÖNETİMİ
# ═══════════════════════════════════════════════════════════════════════════════

def load_portfolio():
    """
    Portfolio.json dosyasını yükler.
    Dosya yoksa başlangıç bakiyesiyle yeni portföy oluşturur.
    """
    if not os.path.exists(PORTFOLIO_DOSYASI):
        log("Portföy dosyası bulunamadı, yeni oluşturuluyor...", "INFO")
        portfolio = {
            "balance": BASLANGIC_BAKIYE,
            "positions": [],
            "history": []
        }
        save_portfolio(portfolio)
        return portfolio
    
    try:
        with open(PORTFOLIO_DOSYASI, 'r', encoding='utf-8') as f:
            portfolio = json.load(f)
            # Yapı doğrulama
            if "balance" not in portfolio:
                portfolio["balance"] = BASLANGIC_BAKIYE
            if "positions" not in portfolio:
                portfolio["positions"] = []
            if "history" not in portfolio:
                portfolio["history"] = []
            return portfolio
    except json.JSONDecodeError as e:
        log(f"Portföy JSON hatası: {e}, sıfırlanıyor...", "ERR")
        portfolio = {
            "balance": BASLANGIC_BAKIYE,
            "positions": [],
            "history": []
        }
        save_portfolio(portfolio)
        return portfolio
    except Exception as e:
        log(f"Portföy yükleme hatası: {e}", "ERR")
        return {"balance": BASLANGIC_BAKIYE, "positions": [], "history": []}

def save_portfolio(portfolio):
    """Portföyü JSON dosyasına kaydeder."""
    try:
        with open(PORTFOLIO_DOSYASI, 'w', encoding='utf-8') as f:
            json.dump(portfolio, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        log(f"Portföy kaydetme hatası: {e}", "ERR")
        return False

# ═══════════════════════════════════════════════════════════════════════════════
# TRADE DECISIONS LOG - SİMÜLASYON DEĞERLENDİRME SİSTEMİ
# ═══════════════════════════════════════════════════════════════════════════════

def load_trade_log():
    """Trade decisions log dosyasını yükler."""
    if not os.path.exists(TRADE_LOG_DOSYASI):
        return {"decisions": [], "stats": {"total_buys": 0, "total_sells": 0, "started_at": time.strftime("%Y-%m-%d %H:%M:%S")}}
    try:
        with open(TRADE_LOG_DOSYASI, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        log(f"Trade log yükleme hatası: {e}", "ERR")
        return {"decisions": [], "stats": {"total_buys": 0, "total_sells": 0, "started_at": time.strftime("%Y-%m-%d %H:%M:%S")}}

def save_trade_log(trade_log):
    """Trade decisions log dosyasını kaydeder."""
    try:
        with open(TRADE_LOG_DOSYASI, 'w', encoding='utf-8') as f:
            json.dump(trade_log, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        log(f"Trade log kaydetme hatası: {e}", "ERR")
        return False

def log_trade_decision(action, symbol, price, ai_decision, market_snapshot, position_id=None, trade_details=None):
    """
    Her AI trade kararını detaylı şekilde loglar.
    
    Args:
        action: "BUY", "SELL", "HOLD"
        symbol: Coin sembolü (BTC, ETH, etc.)
        price: İşlem anındaki fiyat
        ai_decision: AI'ın kararı (decision, confidence, reasoning)
        market_snapshot: Piyasa durumu (teknik, on-chain, reddit, fng, haber)
        position_id: Pozisyon ID'si (BUY/SELL için)
        trade_details: Ek işlem detayları (stop_loss, take_profit, cost, pnl, etc.)
    """
    trade_log = load_trade_log()
    
    decision_record = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "action": action,
        "symbol": symbol,
        "price": price,
        "position_id": position_id,
        
        # AI Karar Detayları
        "ai_decision": {
            "decision": ai_decision.get("decision", action) if ai_decision else action,
            "confidence": ai_decision.get("confidence", 0) if ai_decision else 0,
            "reasoning": ai_decision.get("reasoning", "") if ai_decision else ""
        },
        
        # Piyasa Anlık Görüntüsü
        "market_snapshot": {
            "technical": market_snapshot.get("technical", {}),
            "on_chain": market_snapshot.get("on_chain", "Veri yok"),
            "reddit_sentiment": market_snapshot.get("reddit", {}),
            "fear_and_greed": market_snapshot.get("fng", {}),
            "news": market_snapshot.get("news", None)
        },
        
        # İşlem Detayları
        "trade_details": trade_details or {}
    }
    
    trade_log["decisions"].append(decision_record)
    
    # İstatistikleri güncelle
    if action == "BUY":
        trade_log["stats"]["total_buys"] = trade_log["stats"].get("total_buys", 0) + 1
    elif action == "SELL":
        trade_log["stats"]["total_sells"] = trade_log["stats"].get("total_sells", 0) + 1
    
    trade_log["stats"]["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    
    save_trade_log(trade_log)
    log(f"📝 Trade log kaydedildi: {action} {symbol}", "DATA", 1)

def get_open_positions(portfolio):
    """Açık pozisyonları döndürür."""
    return portfolio.get("positions", [])

def open_position(portfolio, symbol, entry_price, quantity, stop_loss, take_profit, haber_baslik="", ai_confidence=0, ai_reasoning=""):
    """
    Yeni pozisyon açar ve portföye ekler.
    Returns: (success, message)
    """
    trade_cost = entry_price * quantity
    
    if trade_cost > portfolio["balance"]:
        return False, f"Yetersiz bakiye: ${portfolio['balance']:.2f} < ${trade_cost:.2f}"
    
    position = {
        "id": f"{symbol}_{int(time.time())}",
        "symbol": symbol,
        "entry_price": entry_price,
        "quantity": quantity,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "trade_cost": trade_cost,
        "entry_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "haber_baslik": haber_baslik[:150] if haber_baslik else "",
        "ai_confidence": ai_confidence,
        "ai_reasoning": ai_reasoning[:200] if ai_reasoning else ""
    }
    
    portfolio["balance"] -= trade_cost
    portfolio["positions"].append(position)
    save_portfolio(portfolio)
    
    return True, position

def close_position(portfolio, position_id, exit_price, reason="Manuel"):
    """
    Pozisyonu kapatır, bakiyeyi günceller ve geçmişe ekler.
    reason: "SL" (Stop Loss), "TP" (Take Profit), "Manuel"
    Returns: (success, profit_loss, closed_position)
    """
    positions = portfolio.get("positions", [])
    position_to_close = None
    position_index = -1
    
    for i, pos in enumerate(positions):
        if pos.get("id") == position_id:
            position_to_close = pos
            position_index = i
            break
    
    if position_to_close is None:
        return False, 0, None
    
    # Kar/zarar hesapla
    entry_price = position_to_close["entry_price"]
    quantity = position_to_close["quantity"]
    exit_value = exit_price * quantity
    entry_value = position_to_close["trade_cost"]
    profit_loss = exit_value - entry_value
    profit_pct = ((exit_price - entry_price) / entry_price) * 100
    
    # Geçmiş kaydı oluştur
    closed_trade = {
        **position_to_close,
        "exit_price": exit_price,
        "exit_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "profit_loss": profit_loss,
        "profit_pct": profit_pct,
        "exit_reason": reason
    }
    
    # Bakiyeyi güncelle
    portfolio["balance"] += exit_value
    
    # Pozisyonu kaldır ve geçmişe ekle
    del portfolio["positions"][position_index]
    portfolio["history"].append(closed_trade)
    save_portfolio(portfolio)
    
    return True, profit_loss, closed_trade

def get_current_price(symbol, binance_client):
    """Binance'ten güncel fiyat çeker."""
    if not binance_client:
        return None
    try:
        ticker = binance_client.get_symbol_ticker(symbol=f"{symbol}USDT")
        return float(ticker['price'])
    except Exception as e:
        log(f"Fiyat çekme hatası ({symbol}): {e}", "ERR", 1)
        return None

def parse_atr_from_teknik(teknik_str):
    """
    Teknik analiz string'inden ATR değerini çıkarır.
    Örnek: "ATR: $245.32" -> 245.32
    """
    if not teknik_str:
        return None
    try:
        match = re.search(r"ATR:\s*\$?([\d,]+\.?\d*)", teknik_str)
        if match:
            return float(match.group(1).replace(",", ""))
    except Exception:
        pass
    return None

def is_bullish_signal(teknik_str):
    """
    Teknik analiz sonucunun bullish olup olmadığını kontrol eder.
    Kriterler: TREND YÜKSELİŞ ve MOMENTUM POZİTİF
    """
    if not teknik_str:
        return False
    
    teknik_upper = teknik_str.upper()
    
    trend_bullish = "TREND: YÜKSELİŞ" in teknik_upper or "TREND: GÜÇLÜ YÜKSELİŞ" in teknik_upper
    momentum_positive = "MOMENTUM: POZİTİF" in teknik_upper or "MOMENTUM: ZAYIF POZİTİF" in teknik_upper
    
    return trend_bullish and momentum_positive

def is_downtrend(teknik_str):
    """
    Teknik analiz sonucunun düşüş trendinde olup olmadığını kontrol eder.
    GÜÇLÜ DÜŞÜŞ trendinde alım engellenir.
    """
    if not teknik_str:
        return False
    
    teknik_upper = teknik_str.upper()
    
    # Sadece GÜÇLÜ DÜŞÜŞ'ü engelle, normal düşüş hala alıma izin verebilir
    return "TREND: GÜÇLÜ DÜŞÜŞ" in teknik_upper

async def sanal_alim_yap(portfolio, symbol, current_price, atr, trade_reason="AI-TECH", trigger_info="", ai_reasoning="", ai_confidence=0, market_snapshot=None):
    """
    Sanal alım yapar (Paper Trading).
    - Stop Loss: Current Price - (2 * ATR)
    - Take Profit: Current Price + (3 * ATR)
    - Risk: Bakiyenin %2'si
    
    trade_reason: "AI-NEWS" (Haber tetikli) veya "AI-TECH" (Teknik tetikli)
    trigger_info: Tetikleyen bilgi (haber başlığı veya teknik sinyal)
    ai_reasoning: AI'ın trade kararı için verdiği gerekçe
    ai_confidence: AI güven skoru (0-100)
    market_snapshot: Piyasa durumu (teknik, on-chain, reddit, fng, haber)
    
    Returns: (success, position_or_message)
    """
    if not atr or atr <= 0:
        return False, "ATR değeri geçersiz"
    
    if current_price <= 0:
        return False, "Geçersiz fiyat"
    
    # ═══════════════════════════════════════════════════════════════════════════
    # TREND FİLTRESİ: Düşüş trendinde alım engelle
    # ═══════════════════════════════════════════════════════════════════════════
    if BLOCK_BUY_IN_DOWNTREND and market_snapshot:
        teknik_summary = market_snapshot.get("technical", {}).get("summary", "")
        if is_downtrend(teknik_summary):
            return False, f"{symbol}: GÜÇLÜ DÜŞÜŞ trendinde alım engellendi (trend filtresi)"
    
    # Aynı coin'de açık pozisyon var mı kontrol et
    for pos in portfolio.get("positions", []):
        if pos.get("symbol") == symbol:
            return False, f"{symbol} için zaten açık pozisyon var"
    
    # SL/TP hesapla (Python'da kalıyor - AI hallucination'ı önlemek için)
    stop_loss = current_price - (2 * atr)
    take_profit = current_price + (3 * atr)
    
    # Stop loss negatif olamaz
    if stop_loss <= 0:
        stop_loss = current_price * 0.95  # %5 SL
        take_profit = current_price * 1.075  # %7.5 TP
    
    # Risk hesapla (%2 bakiye - Python'da kalıyor)
    risk_amount = portfolio["balance"] * 0.02
    
    # Stop loss mesafesi ($ cinsinden)
    sl_distance = current_price - stop_loss
    
    if sl_distance <= 0:
        return False, "Stop Loss mesafesi geçersiz"
    
    # Kaç adet alınabilir (risk/sl_distance)
    quantity = risk_amount / sl_distance
    
    # Minimum işlem kontrolü
    trade_cost = current_price * quantity
    if trade_cost < 10:  # $10 minimum
        return False, f"İşlem değeri çok düşük: ${trade_cost:.2f}"
    
    if trade_cost > portfolio["balance"]:
        # Bakiye yetmiyorsa, alabildiğimiz kadar al
        quantity = (portfolio["balance"] * 0.95) / current_price  # %95'i kullan
        trade_cost = current_price * quantity
    
    # Pozisyon aç (artık ai_confidence ve ai_reasoning da kaydediliyor)
    success, result = open_position(
        portfolio=portfolio,
        symbol=symbol,
        entry_price=current_price,
        quantity=quantity,
        stop_loss=stop_loss,
        take_profit=take_profit,
        haber_baslik=f"[{trade_reason}] {trigger_info[:120]}",
        ai_confidence=ai_confidence,
        ai_reasoning=ai_reasoning
    )
    
    if success:
        position = result
        reason_emoji = "🤖📰" if "NEWS" in trade_reason else "🤖📊"
        reason_text = "AI HABER TETİKLİ" if "NEWS" in trade_reason else "AI TEKNİK TETİKLİ"
        
        log(f"🆕 SANAL ALIM ({reason_text}): {symbol} @ ${current_price:.4f}", "OK")
        log(f"   Miktar: {quantity:.6f} | Değer: ${trade_cost:.2f}", "DATA", 1)
        log(f"   SL: ${stop_loss:.4f} | TP: ${take_profit:.4f}", "DATA", 1)
        if ai_reasoning:
            log(f"   🧠 AI Gerekçe: {ai_reasoning[:80]}...", "DATA", 1)
        
        # 📝 DETAYLI TRADE LOG KAYDI
        ai_decision_data = {
            "decision": "BUY",
            "confidence": ai_confidence,
            "reasoning": ai_reasoning
        }
        
        trade_details = {
            "trade_reason": trade_reason,
            "trigger_info": trigger_info,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "trade_cost": trade_cost,
            "quantity": quantity,
            "atr": atr,
            "sl_pct": ((current_price - stop_loss) / current_price) * 100,
            "tp_pct": ((take_profit - current_price) / current_price) * 100,
            "balance_after": portfolio["balance"]
        }
        
        # Market snapshot yoksa boş dict kullan
        snapshot = market_snapshot or {}
        
        log_trade_decision(
            action="BUY",
            symbol=symbol,
            price=current_price,
            ai_decision=ai_decision_data,
            market_snapshot=snapshot,
            position_id=position.get("id"),
            trade_details=trade_details
        )
        
        # AI Reasoning bölümünü hazırla
        ai_section = ""
        if ai_reasoning:
            ai_section = f"\n<b>🧠 AI Gerekçe:</b>\n<i>{ai_reasoning}</i>\n"
        
        # Telegram bildirimi
        mesaj = (
            f"🆕 <b>SANAL ALIM - {reason_text}</b> {reason_emoji}\n\n"
            f"<b>Coin:</b> {symbol}/USDT\n"
            f"<b>Giriş Fiyatı:</b> ${current_price:.4f}\n"
            f"<b>Miktar:</b> {quantity:.6f}\n"
            f"<b>İşlem Değeri:</b> ${trade_cost:.2f}\n"
            f"{ai_section}\n"
            f"<b>📊 Risk Yönetimi:</b>\n"
            f"• Stop Loss: ${stop_loss:.4f} (-{((current_price-stop_loss)/current_price)*100:.1f}%)\n"
            f"• Take Profit: ${take_profit:.4f} (+{((take_profit-current_price)/current_price)*100:.1f}%)\n"
            f"• Risk/Ödül: 1:1.5\n\n"
            f"<b>💰 Portföy:</b>\n"
            f"• Kalan Bakiye: ${portfolio['balance']:.2f}\n"
            f"• Açık Pozisyon: {len(portfolio['positions'])}\n\n"
            f"<i>Tetikleyen: {trigger_info[:100]}...</i>"
        )
        await telegrama_bildirim_gonder(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, mesaj)
        
        # ═══════════════════════════════════════════════════════════════════
        # LIVE TRADING: Gerçek emir ver
        # ═══════════════════════════════════════════════════════════════════
        if SETTINGS.LIVE_TRADING:
            try:
                # OrderExecutor oluştur (binance_client global olarak mevcut olmalı)
                executor = create_order_executor(binance_client=None)  # Client ana döngüden gelecek
                
                # Gerçek MARKET BUY emri
                live_order = await executor.create_order(
                    symbol=f"{symbol}USDT",
                    side="BUY",
                    quantity=quantity,
                    order_type="MARKET"
                )
                
                # Pozisyona canlı emir bilgisini ekle
                position["live_order_id"] = live_order.get("orderId")
                position["live_order_status"] = "FILLED"
                position["live_client_order_id"] = live_order.get("clientOrderId")
                save_portfolio(portfolio)
                
                log(f"🔴 CANLI EMİR BAŞARILI: {symbol} OrderId={live_order.get('orderId')}", "OK")
                
            except Exception as e:
                # Canlı emir başarısız - loglama yap ama paper pozisyon kalsın
                log(f"❌ CANLI EMİR BAŞARISIZ: {symbol} - {e}", "ERR")
                position["live_order_status"] = "FAILED"
                position["live_order_error"] = str(e)
                save_portfolio(portfolio)
        
        return True, position
    else:
        return False, result



async def ask_gemini_for_trade_decision(market_data, retry_count=0):
    """
    AI Agent: Weighted Decision Matrix for trade decisions.
    
    Weight Distribution:
    - Technical Analysis: 40% - Math doesn't lie
    - On-Chain Data (Whales): 30% - Watch what they do
    - News: 20% - Catalysts
    - Reddit (Retail): 10% - Contrarian indicator
    """
    if not GEMINI_API_KEY:
        return None
    
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]
        model = genai.GenerativeModel('models/gemini-2.5-flash', safety_settings=safety_settings)
        
        symbol = market_data.get('symbol', 'UNKNOWN')
        price = market_data.get('price', 0)
        technical = market_data.get('technical_analysis', 'Veri yok')
        news = market_data.get('news_analysis', None)
        reddit = market_data.get('reddit_sentiment', 'Veri yok')
        on_chain = market_data.get('on_chain_data', 'Veri yok')
        fng = market_data.get('fear_and_greed', {})
        has_position = market_data.get('has_open_position', False)
        
        fng_str = f"F&G: {fng.get('value', 'N/A')} ({fng.get('classification', 'N/A')})" if fng else "F&G: Veri yok"
        news_str = f"Haber: {news.get('duygu', 'Nötr')} - {news.get('ozet_tr', '')[:50]}" if news else "Haber: Tetik yok"
        position_str = "⚠️ AÇIK POZİSYON VAR" if has_position else "Pozisyon yok"
        
        # Build comprehensive Reddit info
        reddit_info = "Retail: Veri yok"
        if isinstance(reddit, dict):
            reddit_signal = reddit.get('retail_signal', 'NEUTRAL')
            reddit_duygu = reddit.get('genel_duygu', 'Nötr')
            fomo = reddit.get('fomo_level', 'N/A')
            fear = reddit.get('fear_level', 'N/A')
            reddit_info = f"Retail: {reddit_signal} | Duygu: {reddit_duygu} | FOMO: {fomo}% | Fear: {fear}%"
        elif isinstance(reddit, str):
            reddit_info = f"Retail: {reddit[:100]}"
        
        prompt = f"""SEN RİSK-ODAKLI BİR HEDGE FON YÖNETİCİSİSİN. Aşağıdaki AĞIRLIKLI KARAR MATRİSİ'ni kullanarak trade kararı ver.

══════════════════════════════════════════════════
📊 AĞIRLIKLI KARAR MATRİSİ (Zorunlu Kullanım)
══════════════════════════════════════════════════
• TEKNİK ANALİZ: %40 Ağırlık - EN YÜKSEK GÜVEN
  → Matematik yalan söylemez - Trend & Momentum

• ON-CHAIN VERİ (Balinalar): %30 Ağırlık
  → Ne yaptıklarını izle, ne söylediklerini değil

• HABER: %20 Ağırlık
  → Katalizör etkisi

• REDDIT (Perakende): %10 Ağırlık - EN DÜŞÜK GÜVEN
  → ⚠️ KONTRARİAN GÖSTERGE: Perakende çoğunlukla yanılır
  → Eğer Retail "Çok Pozitif" ama On-Chain "Satış" ve Teknik "Düşüş" ise → SELL veya HOLD
  → FOMO'ya KAPILMA!

══════════════════════════════════════════════════
📈 ANALİZ EDİLECEK VERİLER
══════════════════════════════════════════════════
Coin: {symbol} | Fiyat: ${price:.4f} | {position_str}
{fng_str}

� TEKNİK (%40): {technical}
� ON-CHAIN (%30): {on_chain}
📰 HABER (%20): {news_str}
🎭 RETAIL (%10): {reddit_info}

══════════════════════════════════════════════════
⚠️ ÇATIŞMA ÇÖZÜMÜ KURALLARI
══════════════════════════════════════════════════
1. Retail POZITIF + On-Chain SATIS + Teknik DÜŞÜŞ = SELL veya HOLD (FOMO yapma!)
2. Retail PANİK + On-Chain ALIM + Teknik YÜKSELİŞ = BUY (Akıllı para tersine gidiyor)
3. On-Chain ve Teknik ÇATIŞIYORSA → HOLD (Net olmadan işlem yapma)
4. Açık pozisyon VARSA → SELL için güçlü kanıt gerekli

SADECE JSON yanit ver (reasoning max 100 char):
{{"decision": "BUY|SELL|HOLD", "confidence": 0-100, "reasoning": "Kisa aciklama"}}"""
        
        import asyncio
        loop = asyncio.get_event_loop()
        
        def sync_generate():
            return model.generate_content(prompt, generation_config=genai.types.GenerationConfig(temperature=0.3, max_output_tokens=2000))
        
        response = await loop.run_in_executor(None, sync_generate)
        
        # Detaylı kontrol
        if not response.candidates:
            raise Exception("Gemini candidates bos")
        
        candidate = response.candidates[0]
        finish_reason = candidate.finish_reason.name if hasattr(candidate.finish_reason, 'name') else str(candidate.finish_reason)
        
        if finish_reason == "MAX_TOKENS":
            raise Exception("Gemini MAX_TOKENS - yanit kesildi")
        elif finish_reason == "SAFETY":
            raise Exception("Gemini SAFETY - icerik engellendi")
        elif not response.parts:
            raise Exception(f"Gemini parts bos - finish: {finish_reason}")
        
        result = extract_json_from_text(response.text.strip())
        if result and result.get('decision') in ['BUY', 'SELL', 'HOLD']:
            result['symbol'] = symbol
            return result
        raise Exception("JSON parse hatasi")
        
    except Exception as e:
        if retry_count < AI_MAX_RETRIES:
            log(f"AI hatasi ({symbol}), tekrar deneniyor ({retry_count + 1}/{AI_MAX_RETRIES})...", "WARN")
            await asyncio.sleep(AI_RETRY_DELAY)
            return await ask_gemini_for_trade_decision(market_data, retry_count + 1)
        log(f"AI Trade Decision hatasi ({symbol}): {e}", "ERR")
        return None


async def ask_gemini_batch_decisions(market_data_list, context_data):
    """
    Batch AI: Weighted Decision Matrix for multiple coins.
    
    Weight Distribution:
    - Technical Analysis: 40%
    - On-Chain Data (Whales): 30%
    - News: 20%
    - Reddit (Retail): 10% - Contrarian indicator
    """
    if not market_data_list or not GEMINI_API_KEY:
        return {}
    
    if len(market_data_list) == 1:
        data = market_data_list[0].copy()
        data.update(context_data)
        result = await ask_gemini_for_trade_decision(data)
        return {data['symbol']: result} if result else {}
    
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]
        model = genai.GenerativeModel('models/gemini-2.5-flash', safety_settings=safety_settings)
        
        # Parse context data
        reddit = context_data.get('reddit_sentiment', {})
        on_chain = context_data.get('on_chain_data', 'Veri yok')
        fng = context_data.get('fear_and_greed', {})
        
        fng_str = f"F&G: {fng.get('value', 'N/A')} ({fng.get('classification', 'N/A')})" if fng else "F&G: Veri yok"
        
        # Build Reddit info for batch
        if isinstance(reddit, dict):
            reddit_signal = reddit.get('retail_signal', 'NEUTRAL')
            fomo = reddit.get('fomo_level', 'N/A')
            fear = reddit.get('fear_level', 'N/A')
            reddit_info = f"Retail Signal: {reddit_signal} | FOMO: {fomo}% | Fear: {fear}%"
        else:
            reddit_info = str(reddit)[:80] if reddit else "Veri yok"
        
        # Build coins list
        coins_text = ""
        for d in market_data_list:
            pos = "⚠️AÇIK" if d.get('has_open_position') else "YOK"
            coins_text += f"{d['symbol']}: ${d.get('price',0):.2f} | Teknik Skor: {d.get('signal_score',0)}/5 | Poz: {pos}\n"
        
        prompt = f"""SEN RİSK-ODAKLI BİR HEDGE FON YÖNETİCİSİSİN. Her coin için AĞIRLIKLI KARAR MATRİSİ kullan.

══════════════════════════════════════════════════
📊 AĞIRLIKLI KARAR MATRİSİ
══════════════════════════════════════════════════
• TEKNİK (%40): Aşağıdaki skor tablosuna bak
• ON-CHAIN (%30): {on_chain[:100] if on_chain else 'Yok'}
• HABER (%20): Mevcut tetik yok
• RETAIL (%10): {reddit_info} - ⚠️ KONTRARİAN GÖSTERGE!

{fng_str}

══════════════════════════════════════════════════
📈 COİNLER (Teknik Skorları)
══════════════════════════════════════════════════
{coins_text}

══════════════════════════════════════════════════
⚠️ ÇATIŞMA KURALLARI
══════════════════════════════════════════════════
• Retail POZİTİF + On-Chain SATIŞ + Teknik<3 = HOLD/SELL
• Retail PANİK + On-Chain ALIM + Teknik>3 = BUY fırsatı
• Teknik Skor <3 olan coinler için dikkatli ol
• Açık pozisyon varsa SELL için güçlü gerekçe gerekli

SADECE JSON yanit ver (reasoning max 80 char):
{{"decisions": [{{"symbol": "X", "decision": "HOLD", "confidence": 60, "reasoning": "Max 80 char"}}]}}"""
        
        import asyncio
        loop = asyncio.get_event_loop()
        
        def sync_generate():
            return model.generate_content(prompt, generation_config=genai.types.GenerationConfig(temperature=0.3, max_output_tokens=8000))
        
        response = await loop.run_in_executor(None, sync_generate)
        
        # Detaylı kontrol
        if not response.candidates:
            log("AI Batch: Gemini candidates bos", "ERR")
            return {}
        
        candidate = response.candidates[0]
        finish_reason = candidate.finish_reason.name if hasattr(candidate.finish_reason, 'name') else str(candidate.finish_reason)
        
        if finish_reason == "MAX_TOKENS":
            log("AI Batch: MAX_TOKENS - yanit kesildi, daha az coin dene", "ERR")
            return {}
        elif finish_reason == "SAFETY":
            log("AI Batch: SAFETY - icerik engellendi", "ERR")
            return {}
        elif not response.parts:
            log(f"AI Batch: Parts bos - finish: {finish_reason}", "ERR")
            return {}
        
        result = extract_json_from_text(response.text.strip())
        if result and 'decisions' in result:
            decisions = {}
            for d in result['decisions']:
                sym = d.get('symbol', '').upper()
                if sym and d.get('decision') in ['BUY', 'SELL', 'HOLD']:
                    decisions[sym] = {'symbol': sym, 'decision': d['decision'].upper(), 
                                     'confidence': int(d.get('confidence', 0)), 
                                     'reasoning': d.get('reasoning', '')[:150]}
            log(f"Batch AI: {len(decisions)} coin analiz edildi", "OK")
            return decisions
        return {}
        
    except Exception as e:
        log(f"AI Batch hatasi: {e}", "ERR")
        return {}

async def execute_ai_sell_decision(portfolio, symbol, current_price, ai_reasoning, binance_client, ai_confidence=0, market_snapshot=None):
    """
    AI SELL kararını uygular - açık pozisyonu kapatır.
    
    Args:
        portfolio: Aktif portföy
        symbol: Coin sembolü  
        current_price: Güncel fiyat
        ai_reasoning: AI'ın satış gerekçesi
        binance_client: Binance client
        ai_confidence: AI güven skoru (0-100)
        market_snapshot: Piyasa durumu (teknik, on-chain, reddit, fng)
    
    Returns: (success, profit_loss, message)
    """
    positions = get_open_positions(portfolio)
    
    # Bu coin için açık pozisyon bul
    target_position = None
    for pos in positions:
        if pos.get('symbol') == symbol:
            target_position = pos
            break
    
    if not target_position:
        return False, 0, f"{symbol} için açık pozisyon bulunamadı"
    
    position_id = target_position.get('id')
    entry_price = target_position.get('entry_price')
    take_profit = target_position.get('take_profit', 0)
    
    # ═══════════════════════════════════════════════════════════════════════════
    # KÂR KORUMA MEKANİZMASI
    # ═══════════════════════════════════════════════════════════════════════════
    if PROTECT_PROFITABLE_POSITIONS and entry_price and current_price:
        current_profit_pct = ((current_price - entry_price) / entry_price) * 100
        
        # Pozisyon kârda ve henüz TP'ye ulaşmamış mı?
        if current_profit_pct >= MIN_PROFIT_TO_PROTECT:
            # Yüksek güvenli AI kararı kâr korumasını geçebilir
            if ai_confidence < AI_SELL_OVERRIDE_CONFIDENCE:
                log(f"🛡️ {symbol}: Kâr koruma aktif! +{current_profit_pct:.2f}% kârda, TP bekliyor (AI güven: {ai_confidence}% < {AI_SELL_OVERRIDE_CONFIDENCE}%)", "WARN")
                return False, 0, f"{symbol}: Kârdaki pozisyon korunuyor (TP'ye ulaşmasını bekle)"
            else:
                log(f"⚠️ {symbol}: Yüksek güvenli AI SELL ({ai_confidence}%) kâr korumasını geçiyor", "WARN")
    
    # Pozisyonu kapat
    success, pnl, closed = close_position(portfolio, position_id, current_price, "AI-SELL")
    
    if success:
        profit_pct = closed.get('profit_pct', 0)
        pnl_emoji = "💰" if pnl > 0 else "🔻"
        
        log(f"{pnl_emoji} AI SELL: {symbol} kapatıldı | PnL: ${pnl:.2f} ({profit_pct:.1f}%)", "OK")
        
        # 📝 DETAYLI TRADE LOG KAYDI
        ai_decision_data = {
            "decision": "SELL",
            "confidence": ai_confidence,
            "reasoning": ai_reasoning
        }
        
        trade_details = {
            "entry_price": entry_price,
            "exit_price": current_price,
            "profit_loss": pnl,
            "profit_pct": profit_pct,
            "quantity": target_position.get('quantity'),
            "trade_cost": target_position.get('trade_cost'),
            "hold_time": closed.get('exit_time', '') + " - " + target_position.get('entry_time', ''),
            "original_stop_loss": target_position.get('stop_loss'),
            "original_take_profit": target_position.get('take_profit'),
            "balance_after": portfolio["balance"]
        }
        
        # Market snapshot yoksa boş dict kullan
        snapshot = market_snapshot or {}
        
        log_trade_decision(
            action="SELL",
            symbol=symbol,
            price=current_price,
            ai_decision=ai_decision_data,
            market_snapshot=snapshot,
            position_id=position_id,
            trade_details=trade_details
        )
        
        if TELEGRAM_NOTIFY_TRADES:
            mesaj = (
                f"🤖 <b>AI SATIŞ KARARI</b> {pnl_emoji}\n\n"
                f"<b>Coin:</b> {symbol}/USDT\n"
                f"<b>Giriş:</b> ${entry_price:.4f}\n"
                f"<b>Çıkış:</b> ${current_price:.4f}\n"
                f"<b>{'Kâr' if pnl > 0 else 'Zarar'}:</b> ${abs(pnl):.2f} ({profit_pct:+.1f}%)\n\n"
                f"<b>🧠 AI Gerekçe:</b>\n<i>{ai_reasoning}</i>\n\n"
                f"<b>💰 Güncel Bakiye:</b> ${portfolio['balance']:.2f}"
            )
            await telegrama_bildirim_gonder(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, mesaj)
        
        # ═══════════════════════════════════════════════════════════════════
        # LIVE TRADING: Gerçek SELL emri ver
        # ═══════════════════════════════════════════════════════════════════
        if SETTINGS.LIVE_TRADING:
            quantity = target_position.get('quantity', 0)
            try:
                executor = create_order_executor(binance_client=binance_client)
                
                live_order = await executor.create_order(
                    symbol=f"{symbol}USDT",
                    side="SELL",
                    quantity=quantity,
                    order_type="MARKET"
                )
                
                # Kapatılan pozisyona canlı satış bilgisini ekle
                closed["live_sell_order_id"] = live_order.get("orderId")
                closed["live_sell_status"] = "FILLED"
                save_portfolio(portfolio)
                
                log(f"🔴 CANLI SATIŞ BAŞARILI: {symbol} OrderId={live_order.get('orderId')}", "OK")
                
            except Exception as e:
                # Canlı satış başarısız - kritik durum!
                log(f"❌ CANLI SATIŞ BAŞARISIZ: {symbol} - {e}", "ERR")
                log(f"⚠️ RECOVERY GEREKLİ: Pozisyon paper'da kapatıldı ama canlı satış yapılamadı!", "ERR")
                
                # History'deki son kapanana flag ekle
                if portfolio.get("history"):
                    portfolio["history"][-1]["live_sell_failed"] = True
                    portfolio["history"][-1]["live_sell_error"] = str(e)
                    portfolio["history"][-1]["recovery_needed"] = True
                    save_portfolio(portfolio)
        
        return True, pnl, closed
    else:
        return False, 0, "Pozisyon kapatılamadı"


async def portfoy_yonet(portfolio, binance_client):
    """
    Açık pozisyonları kontrol eder ve gerekirse kapatır.
    Her döngü başında çağrılır.
    
    Kontroller:
    - Fiyat <= Stop Loss -> Zarar kesimi
    - Fiyat >= Take Profit -> Kar alımı
    
    Returns: (closed_count, total_pnl)
    """
    positions = get_open_positions(portfolio)
    
    if not positions:
        return 0, 0
    
    log_bolum("Portföy Yönetimi (SL/TP Kontrolü)", "💼")
    log(f"Açık pozisyon sayısı: {len(positions)}", "INFO")
    
    closed_count = 0
    total_pnl = 0
    
    for position in positions[:]:  # Copy list to avoid modification during iteration
        symbol = position.get("symbol")
        position_id = position.get("id")
        stop_loss = position.get("stop_loss")
        take_profit = position.get("take_profit")
        entry_price = position.get("entry_price")
        
        # Güncel fiyatı çek
        current_price = get_current_price(symbol, binance_client)
        
        if current_price is None:
            log(f"{symbol}: Fiyat alınamadı, atlanıyor", "WARN", 1)
            continue
        
        log(f"{symbol}: ${current_price:.4f} (SL: ${stop_loss:.4f} | TP: ${take_profit:.4f})", "DATA", 1)
        
        # Stop Loss kontrolü
        if current_price <= stop_loss:
            success, pnl, closed = close_position(portfolio, position_id, current_price, "SL")
            if success:
                closed_count += 1
                total_pnl += pnl
                log(f"🛑 STOP LOSS: {symbol} kapatıldı | PnL: ${pnl:.2f}", "ERR")
                
                mesaj = (
                    f"🛑 <b>ZARAR KESİLDİ (Stop Loss)</b>\n\n"
                    f"<b>Coin:</b> {symbol}/USDT\n"
                    f"<b>Giriş:</b> ${entry_price:.4f}\n"
                    f"<b>Çıkış:</b> ${current_price:.4f}\n"
                    f"<b>Zarar:</b> ${pnl:.2f} ({closed['profit_pct']:.1f}%)\n\n"
                    f"<b>💰 Güncel Bakiye:</b> ${portfolio['balance']:.2f}\n\n"
                    f"<i>{closed.get('haber_baslik', '')}</i>"
                )
                await telegrama_bildirim_gonder(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, mesaj)
                
                # LIVE TRADING: Gerçek SELL emri (SL)
                if SETTINGS.LIVE_TRADING:
                    try:
                        executor = create_order_executor(binance_client=binance_client)
                        quantity = position.get('quantity', 0)
                        live_order = await executor.create_order(
                            symbol=f"{symbol}USDT", side="SELL",
                            quantity=quantity, order_type="MARKET"
                        )
                        log(f"🔴 CANLI SL SATIŞ: {symbol} OrderId={live_order.get('orderId')}", "OK")
                    except Exception as e:
                        log(f"❌ CANLI SL SATIŞ BAŞARISIZ: {symbol} - {e}", "ERR")
                        if portfolio.get("history"):
                            portfolio["history"][-1]["live_sell_failed"] = True
                            save_portfolio(portfolio)
                
                await asyncio.sleep(1)
        
        # Take Profit kontrolü
        elif current_price >= take_profit:
            success, pnl, closed = close_position(portfolio, position_id, current_price, "TP")
            if success:
                closed_count += 1
                total_pnl += pnl
                log(f"💰 TAKE PROFIT: {symbol} kapatıldı | PnL: ${pnl:.2f}", "OK")
                
                mesaj = (
                    f"💰 <b>KÂR ALINDI (Take Profit)</b>\n\n"
                    f"<b>Coin:</b> {symbol}/USDT\n"
                    f"<b>Giriş:</b> ${entry_price:.4f}\n"
                    f"<b>Çıkış:</b> ${current_price:.4f}\n"
                    f"<b>Kâr:</b> +${pnl:.2f} (+{closed['profit_pct']:.1f}%)\n\n"
                    f"<b>💰 Güncel Bakiye:</b> ${portfolio['balance']:.2f}\n\n"
                    f"<i>{closed.get('haber_baslik', '')}</i>"
                )
                await telegrama_bildirim_gonder(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, mesaj)
                
                # LIVE TRADING: Gerçek SELL emri (TP)
                if SETTINGS.LIVE_TRADING:
                    try:
                        executor = create_order_executor(binance_client=binance_client)
                        quantity = position.get('quantity', 0)
                        live_order = await executor.create_order(
                            symbol=f"{symbol}USDT", side="SELL",
                            quantity=quantity, order_type="MARKET"
                        )
                        log(f"🔴 CANLI TP SATIŞ: {symbol} OrderId={live_order.get('orderId')}", "OK")
                    except Exception as e:
                        log(f"❌ CANLI TP SATIŞ BAŞARISIZ: {symbol} - {e}", "ERR")
                        if portfolio.get("history"):
                            portfolio["history"][-1]["live_sell_failed"] = True
                            save_portfolio(portfolio)
                
                await asyncio.sleep(1)
        
        await asyncio.sleep(0.3)  # Rate limiting
    
    if closed_count > 0:
        log(f"Toplam kapatılan: {closed_count} | Toplam PnL: ${total_pnl:.2f}", "OK")
    else:
        log("SL/TP tetiklenmedi, pozisyonlar devam ediyor", "INFO")
    
    return closed_count, total_pnl

def get_portfolio_summary(portfolio):
    """Portföy özeti döndürür."""
    positions = get_open_positions(portfolio)
    history = portfolio.get("history", [])
    
    total_trades = len(history)
    winning_trades = len([h for h in history if h.get("profit_loss", 0) > 0])
    losing_trades = len([h for h in history if h.get("profit_loss", 0) < 0])
    total_pnl = sum(h.get("profit_loss", 0) for h in history)
    
    return {
        "balance": portfolio["balance"],
        "open_positions": len(positions),
        "total_trades": total_trades,
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "win_rate": (winning_trades / total_trades * 100) if total_trades > 0 else 0,
        "total_pnl": total_pnl
    }

def islenmis_haberleri_yukle():
    if not os.path.exists(ISLENMIS_HABERLER_DOSYASI):
        return set()
    try:
        with open(ISLENMIS_HABERLER_DOSYASI, 'r', encoding='utf-8') as f:
            return set(line.strip() for line in f if line.strip())
    except Exception as e:
        print(f"HATA (Veritabanı Okuma): {e}")
        return set()

def haberi_kaydet(haber_linki):
    try:
        with open(ISLENMIS_HABERLER_DOSYASI, 'a', encoding='utf-8') as f:
            f.write(haber_linki + '\n')
    except Exception as e:
        print(f"HATA (Veritabanı Yazma): {e}")

def haber_basligi_uygun_mu(baslik):
    anahtar_kelimeler = ['bitcoin', 'ethereum', 'crypto', 'blockchain', 'binance', 'solana', 'ripple', 'kripto', 'coin', 'token', 'web3', 'nft', 'etf', 'defi', 'metaverse', 'mining', 'staking', 'airdrop']
    return any(kelime in baslik.lower() for kelime in anahtar_kelimeler)

def extract_json_from_text(text):
    """JSON'u metinden çıkar. Hatalı JSON için temizleme dener."""
    if not text:
        return None
    
    # Önce markdown code block içinde ara
    match = re.search(r"```json\s*(\{.*?\})\s*```|```\s*(\{.*?\})\s*```|(\{.*\})", text, re.DOTALL)
    if match:
        json_part = match.group(1) or match.group(2) or match.group(3)
        
        # İlk deneme: Direkt parse
        try:
            return json.loads(json_part)
        except json.JSONDecodeError:
            pass
        
        # İkinci deneme: Yaygın sorunları temizle
        try:
            # Trailing comma'ları kaldır
            cleaned = re.sub(r',\s*}', '}', json_part)
            cleaned = re.sub(r',\s*]', ']', cleaned)
            # Kontrol karakterlerini kaldır
            cleaned = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', cleaned)
            # Tek tırnakları çift tırnağa çevir
            cleaned = cleaned.replace("'", '"')
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass
        
        # Üçüncü deneme: Sadece ilk geçerli JSON objesini bul
        try:
            brace_count = 0
            start_idx = None
            for i, char in enumerate(json_part):
                if char == '{':
                    if start_idx is None:
                        start_idx = i
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0 and start_idx is not None:
                        return json.loads(json_part[start_idx:i+1])
        except json.JSONDecodeError as e:
            print(f"HATA (JSON Ayıklama): {e}")
    
    return None

def haberleri_cek():
    """
    RSS Feed'lerden kripto haberlerini çeker.
    HABER_MAX_SAAT'ten eski haberler filtrelenir.
    Gerçek zamanlı haber akışı sağlar (NewsAPI'nin 24 saat gecikmesi yok).
    """
    haberler = []
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=HABER_MAX_SAAT)
    
    for feed_url in RSS_FEEDS:
        try:
            # Feed kaynağının adını URL'den çıkar
            feed_name = feed_url.split("//")[1].split("/")[0].replace("www.", "").split(".")[0].title()
            
            # Feed'i parse et
            feed = feedparser.parse(feed_url)
            
            if feed.bozo and not feed.entries:
                log(f"{feed_name}: Feed okunamadı", "WARN", 1)
                continue
            
            feed_haber_sayisi = 0
            
            for entry in feed.entries:
                try:
                    # Başlığı al
                    baslik = entry.get('title', '')
                    if not baslik or '[Removed]' in baslik:
                        continue
                    
                    # Linki al
                    link = entry.get('link', '')
                    if not link:
                        continue
                    
                    # Yayın tarihini parse et
                    published_str = entry.get('published') or entry.get('updated') or ''
                    if published_str:
                        try:
                            # dateutil ile esnek tarih parse
                            published_time = dateutil_parser.parse(published_str)
                            
                            # Timezone-aware yap (naive ise UTC varsay)
                            if published_time.tzinfo is None:
                                published_time = published_time.replace(tzinfo=timezone.utc)
                            
                            # Eski haberleri atla
                            if published_time < cutoff_time:
                                continue
                                
                            tarih_str = published_time.isoformat()
                        except (ValueError, TypeError):
                            tarih_str = published_str
                    else:
                        tarih_str = ''
                    
                    haberler.append({
                        'baslik': baslik,
                        'link': link,
                        'kaynak': feed_name,
                        'tarih': tarih_str
                    })
                    feed_haber_sayisi += 1
                    
                except Exception as entry_err:
                    continue  # Tek bir entry hatası diğerlerini etkilemesin
            
            if feed_haber_sayisi > 0:
                log(f"{feed_name}: {feed_haber_sayisi} haber", "DATA", 1)
                
        except Exception as e:
            log(f"RSS hatası ({feed_url[:30]}...): {e}", "WARN", 1)
            continue  # Bir feed hatalı olsa da diğerlerine devam et
    
    # Tarihe göre sırala (en yeni önce)
    haberler.sort(key=lambda x: x.get('tarih', ''), reverse=True)
    
    log(f"Toplam {len(haberler)} taze haber bulundu (son {HABER_MAX_SAAT} saat)", "OK")
    return haberler

def get_haber_icerigi(url):
    try:
        config = Config()
        config.browser_user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        config.request_timeout = 20
        config.verify_ssl = False
        config.fetch_images = False
        config.memoize_articles = False

        article = Article(url, config=config)
        article.download()
        article.parse()

        if not article.text or len(article.text) < 100:
            print(f"UYARI (Newspaper3k - {url}): Yeterli içerik bulunamadı.")
            return None
        return article.text[:7000]
    except Exception as e:
        print(f"HATA (Newspaper3k - {url}): {e}")
        return None

def haberleri_analiz_et(api_key, haber_basligi, haber_icerigi):
    if not api_key:
        print("HATA (Gemini AI): API anahtarı eksik.")
        return None
    try:
        genai.configure(api_key=api_key)
        safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]
        model = genai.GenerativeModel('models/gemini-2.5-flash', safety_settings=safety_settings)

        prompt = f"""
        GÖREV: Aşağıdaki haber başlığını ve metnini analiz et. Çıktın SADECE geçerli bir JSON objesi olmalı.

        Haber Başlığı: "{haber_basligi}"
        Haber Metni: "{haber_icerigi}"

        İstenen JSON Yapısı:
        {{
          "kripto_ile_ilgili_mi": boolean,
          "onem_derecesi": string ('Düşük', 'Orta', 'Yüksek', 'Çok Yüksek'),
          "etkilenen_coinler": array[string] (SADECE Binance ticker sembolleri kullan: BTC, ETH, SOL, XRP, ADA, DOGE, AVAX, LINK gibi 2-5 harfli kısaltmalar. Tam isim YAZMA.),
          "duygu": string ('Çok Pozitif', 'Pozitif', 'Nötr', 'Negatif', 'Çok Negatif'),
          "ozet_tr": string
        }}

        SADECE JSON ÇIKTISI:
        """
        response = model.generate_content(prompt)

        if not response.parts:
            print(f"HATA (Gemini AI): Yanıt alınamadı.")
            return None

        analiz = extract_json_from_text(response.text.strip())
        if analiz:
            # Tüm Türkçe karakter ve yazım varyasyonlarını normalize et
            key_variants = ['önem_derecesi', 'onem_derecisi', 'önem_derecisi']
            for variant in key_variants:
                if variant in analiz and 'onem_derecesi' not in analiz:
                    analiz['onem_derecesi'] = analiz[variant]
                    break
            
            required_keys = ["kripto_ile_ilgili_mi", "onem_derecesi", "etkilenen_coinler", "duygu", "ozet_tr"]
            missing_keys = [k for k in required_keys if k not in analiz]
            
            if not missing_keys:
                return analiz
            
            # Eksik anahtarları logla
            log(f"JSON eksik anahtarlar: {missing_keys}", "WARN", 1)
        else:
            print("HATA (Gemini AI): Yanıttan geçerli JSON ayıklanamadı.")
        return None
    except Exception as e:
        print(f"HATA (Gemini AI): {e}")
        return None

STABLECOINS = ['USDT', 'USDC', 'BUSD', 'DAI', 'TUSD', 'USDP', 'FDUSD']

COIN_MAPPING = {
    'BITCOIN': 'BTC', 'ETHER': 'ETH', 'ETHEREUM': 'ETH', 'RIPPLE': 'XRP',
    'CARDANO': 'ADA', 'DOGECOIN': 'DOGE', 'POLKADOT': 'DOT', 'CHAINLINK': 'LINK',
    'LITECOIN': 'LTC', 'AVALANCHE': 'AVAX', 'POLYGON': 'MATIC', 'STELLAR': 'XLM',
    'COSMOS': 'ATOM', 'MONERO': 'XMR', 'TRON': 'TRX', 'UNISWAP': 'UNI',
    'HEDERA': 'HBAR', 'FILECOIN': 'FIL', 'APTOS': 'APT', 'ARBITRUM': 'ARB',
    'OPTIMISM': 'OP', 'NEAR': 'NEAR', 'INJECTIVE': 'INJ', 'RENDER': 'RENDER',
    'SOLANA': 'SOL', 'TETHER': 'USDT', 'BINANCE': 'BNB', 'SUI': 'SUI',
}

INVALID_TERMS = ['STABLECOINS', 'CRYPTO', 'CRYPTOCURRENCY', 'ALTCOIN', 'ALTCOINS', 
                 'TOKEN', 'TOKENS', 'COIN', 'COINS', 'KRIPTO', 'PARA', 'BIRIMLERI']

def normalize_coin_symbol(coin):
    if not coin or not isinstance(coin, str):
        return None
    coin_upper = coin.upper().strip()
    if len(coin_upper) > 10 or ' ' in coin_upper:
        return None
    if coin_upper in INVALID_TERMS or coin_upper in STABLECOINS:
        return None
    return COIN_MAPPING.get(coin_upper, coin_upper)

def get_teknik_analiz(coin_sembolu, binance_client):
    """
    Gelişmiş teknik analiz: RSI (14), EMA 50/200 (Trend), MACD (Momentum), ADX (Güç), ATR (Risk)
    4 saatlik mumlara dayalı analiz yapar.
    
    Returns: Dictionary with all indicator values and summary string
    {
        'symbol': str,
        'price': float,
        'rsi': float,
        'ema50': float,
        'ema200': float,
        'macd_line': float,
        'signal_line': float,
        'adx': float,
        'atr': float,
        'volume_24h': float,
        'trend_bullish': bool,
        'momentum_positive': bool,
        'strong_trend': bool,
        'volume_ok': bool,
        'signal_score': int,  # 0-5 (kaç kriter karşılandı)
        'summary': str  # Okunabilir özet
    }
    """
    if not binance_client:
        return None
    coin_upper = normalize_coin_symbol(coin_sembolu)
    if not coin_upper:
        return None
    
    try:
        parite = f"{coin_upper}USDT"
        # EMA 200 için daha fazla veri gerekli (en az 200 mum)
        mumlar = binance_client.get_historical_klines(
            parite, 
            Client.KLINE_INTERVAL_4HOUR, 
            "50 days ago UTC"
        )

        if len(mumlar) < 200:
            log(f"{parite}: Yeterli veri yok ({len(mumlar)} mum)", "WARN", 2)
            return None

        # DataFrame oluştur
        df = pd.DataFrame(mumlar, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_asset_volume', 'number_of_trades',
            'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
        ])
        df['close'] = pd.to_numeric(df['close'])
        df['high'] = pd.to_numeric(df['high'])
        df['low'] = pd.to_numeric(df['low'])
        
        son_fiyat = df['close'].iloc[-1]
        
        # Sonuç dictionary'si
        result = {
            'symbol': coin_upper,
            'price': son_fiyat,
            'rsi': None,
            'ema50': None,
            'ema200': None,
            'macd_line': None,
            'signal_line': None,
            'adx': None,
            'atr': None,
            'volume_24h': 0,
            'trend_bullish': False,
            'momentum_positive': False,
            'strong_trend': False,
            'volume_ok': False,
            'signal_score': 0,
            'summary': ""
        }
        
        # ────────── RSI (14) ──────────
        rsi_series = df.ta.rsi(length=14)
        son_rsi = rsi_series.iloc[-1] if rsi_series is not None and not rsi_series.dropna().empty else None
        result['rsi'] = float(son_rsi) if son_rsi is not None and not pd.isna(son_rsi) else None
        
        if result['rsi'] is not None:
            if result['rsi'] > 70:
                rsi_str = f"RSI: {result['rsi']:.1f} (Aşırı Alım 📈)"
            elif result['rsi'] < 30:
                rsi_str = f"RSI: {result['rsi']:.1f} (Aşırı Satım 📉)"
            else:
                rsi_str = f"RSI: {result['rsi']:.1f} (Nötr 📊)"
        else:
            rsi_str = "RSI: Hesaplanamadı"
        
        # ────────── EMA 50 & EMA 200 (Trend) ──────────
        ema_50 = df.ta.ema(length=50)
        ema_200 = df.ta.ema(length=200)
        
        result['ema50'] = float(ema_50.iloc[-1]) if ema_50 is not None and not ema_50.dropna().empty else None
        result['ema200'] = float(ema_200.iloc[-1]) if ema_200 is not None and not ema_200.dropna().empty else None
        
        if result['ema200'] is not None:
            if son_fiyat > result['ema200']:
                result['trend_bullish'] = True
                if result['ema50'] and result['ema50'] > result['ema200']:
                    trend_str = "TREND: GÜÇLÜ YÜKSELİŞ 🐂🐂"
                else:
                    trend_str = "TREND: YÜKSELİŞ 🐂"
            else:
                result['trend_bullish'] = False
                if result['ema50'] and result['ema50'] < result['ema200']:
                    trend_str = "TREND: GÜÇLÜ DÜŞÜŞ 🐻🐻"
                else:
                    trend_str = "TREND: DÜŞÜŞ 🐻"
        else:
            trend_str = "TREND: Hesaplanamadı"
        
        # ────────── MACD (Momentum) ──────────
        macd_df = df.ta.macd(fast=12, slow=26, signal=9)
        
        if macd_df is not None and not macd_df.empty:
            macd_line = macd_df.iloc[-1, 0]  # MACD line
            signal_line = macd_df.iloc[-1, 2]  # Signal line
            macd_hist = macd_df.iloc[-1, 1]  # Histogram
            
            result['macd_line'] = float(macd_line) if not pd.isna(macd_line) else None
            result['signal_line'] = float(signal_line) if not pd.isna(signal_line) else None
            
            if result['macd_line'] is not None and result['signal_line'] is not None:
                if result['macd_line'] > result['signal_line']:
                    result['momentum_positive'] = True
                    if macd_hist > 0:
                        momentum_str = "MOMENTUM: POZİTİF (AL) 🟢"
                    else:
                        momentum_str = "MOMENTUM: ZAYIF POZİTİF 🟡"
                else:
                    result['momentum_positive'] = False
                    if macd_hist < 0:
                        momentum_str = "MOMENTUM: NEGATİF (SAT) 🔴"
                    else:
                        momentum_str = "MOMENTUM: ZAYIF NEGATİF 🟠"
            else:
                momentum_str = "MOMENTUM: Hesaplanamadı"
        else:
            momentum_str = "MOMENTUM: Hesaplanamadı"
        
        # ────────── ATR (14) - Volatilite/Risk ──────────
        atr_series = df.ta.atr(length=14)
        son_atr = atr_series.iloc[-1] if atr_series is not None and not atr_series.dropna().empty else None
        result['atr'] = float(son_atr) if son_atr is not None and not pd.isna(son_atr) else None
        
        if result['atr'] is not None:
            atr_str = f"ATR: ${result['atr']:.2f}"
        else:
            atr_str = "ATR: Hesaplanamadı"
        
        # ────────── ADX (14) - Trend Gücü ──────────
        adx_df = df.ta.adx(length=14)
        
        if adx_df is not None and not adx_df.empty:
            adx_col = [col for col in adx_df.columns if 'ADX' in col and 'DM' not in col]
            if adx_col:
                son_adx = adx_df[adx_col[0]].iloc[-1]
                result['adx'] = float(son_adx) if son_adx is not None and not pd.isna(son_adx) else None
                
                if result['adx'] is not None:
                    result['strong_trend'] = result['adx'] > MIN_ADX
                    if result['adx'] > 25:
                        adx_str = f"TREND GÜCÜ: GÜÇLÜ ({result['adx']:.1f}) 💪"
                    elif result['adx'] < 20:
                        adx_str = f"TREND GÜCÜ: ZAYIF/YATAY ({result['adx']:.1f}) 💤"
                    else:
                        adx_str = f"TREND GÜCÜ: ORTA ({result['adx']:.1f}) 📊"
                else:
                    adx_str = "TREND GÜCÜ: Hesaplanamadı"
            else:
                adx_str = "TREND GÜCÜ: Hesaplanamadı"
        else:
            adx_str = "TREND GÜCÜ: Hesaplanamadı"
        
        # ────────── Hacim/Likidite Kontrolü ──────────
        df['volume'] = pd.to_numeric(df['volume'])
        df['quote_volume'] = pd.to_numeric(df['quote_asset_volume'])
        
        # Son 24 saatlik hacim (6 mum x 4 saat = 24 saat)
        son_24s_hacim = df['quote_volume'].tail(6).sum()
        result['volume_24h'] = float(son_24s_hacim)
        result['volume_ok'] = son_24s_hacim >= MIN_HACIM_USDT
        
        if son_24s_hacim >= MIN_HACIM_USDT:
            if son_24s_hacim >= 50_000_000:
                hacim_str = f"HACİM: ${son_24s_hacim/1_000_000:.1f}M ✅"
            else:
                hacim_str = f"HACİM: ${son_24s_hacim/1_000_000:.1f}M"
        else:
            hacim_str = f"⚠️ DÜŞÜK HACİM: ${son_24s_hacim/1_000_000:.1f}M"
        
        # ────────── Sinyal Skoru Hesapla (0-5) ──────────
        score = 0
        if result['trend_bullish']:
            score += 1
        if result['momentum_positive']:
            score += 1
        if result['strong_trend']:
            score += 1
        if result['volume_ok']:
            score += 1
        if result['rsi'] is not None and 30 <= result['rsi'] <= 70:  # Aşırı alım/satım değil
            score += 1
        result['signal_score'] = score
        
        # ────────── Özet String ──────────
        satir1 = f"{rsi_str} | {trend_str} | {momentum_str}"
        satir2 = f"{atr_str} | {adx_str} | {hacim_str}"
        result['summary'] = f"{satir1}\n{satir2}"
        
        return result

    except BinanceAPIException as e:
        if e.code == -1121:
            log(f"{parite} paritesi Binance'te bulunamadı", "WARN", 2)
        else:
            log(f"Binance API hatası ({coin_sembolu}): {e}", "ERR", 2)
        return None
    except Exception as e:
        log(f"Teknik analiz hatası ({coin_sembolu}): {e}", "ERR", 2)
        return None

def get_fear_and_greed_index():
    """
    Alternative.me API'den Korku ve Açgözlülk Endeksini çeker.
    Piyasa duyarlılığını 0-100 arasında gösterir.
    """
    try:
        response = requests.get("https://api.alternative.me/fng/", timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if data.get("data"):
            fng_data = data["data"][0]
            value = int(fng_data.get("value", 0))
            classification = fng_data.get("value_classification", "Unknown")
            
            # Emoji seçimi
            if value <= 25:
                emoji = "😨"  # Extreme Fear
            elif value <= 45:
                emoji = "😟"  # Fear
            elif value <= 55:
                emoji = "😐"  # Neutral
            elif value <= 75:
                emoji = "😏"  # Greed
            else:
                emoji = "🤑"  # Extreme Greed
            
            return {
                "value": value,
                "classification": classification,
                "emoji": emoji,
                "formatted": f"Korku ve Açgözlülk: {value} ({classification}) {emoji}"
            }
        return None
    except requests.exceptions.Timeout:
        log("Fear & Greed API zaman aşımı", "WARN")
        return None
    except requests.exceptions.RequestException as e:
        log(f"Fear & Greed API hatası: {e}", "ERR")
        return None
    except Exception as e:
        log(f"Fear & Greed hatası: {e}", "ERR")
        return None

def get_reddit_sentiment(gemini_api_key):
    """
    Multi-Subreddit Retail Sentiment Analysis.
    - Monitors: CryptoCurrency, Bitcoin, Ethereum, SatoshiStreetBets, ethtrader
    - Filters posts from last 24 hours only
    - Detects Extreme Euphoria (top signal) or Extreme Panic (bottom signal)
    """
    if not all([REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT, REDDIT_USERNAME, REDDIT_PASSWORD]):
        print("UYARI (Reddit): API bilgileri eksik.")
        return None
    if not gemini_api_key:
        print("HATA (Reddit/Gemini): Gemini API anahtarı eksik.")
        return None

    try:
        log("Reddit API'ye bağlanılıyor...", "INFO")
        reddit = praw.Reddit(
            client_id=REDDIT_CLIENT_ID, client_secret=REDDIT_CLIENT_SECRET,
            user_agent=REDDIT_USER_AGENT, username=REDDIT_USERNAME, password=REDDIT_PASSWORD,
            check_for_async=False
        )
        reddit.user.me()
        log("Reddit'e bağlanıldı, başlıklar çekiliyor...", "OK", 1)

        # Multi-subreddit monitoring
        subreddits = "CryptoCurrency+Bitcoin+Ethereum+SatoshiStreetBets+ethtrader"
        combined_subreddit = reddit.subreddit(subreddits)
        
        # Time filter: Only posts from last 6 hours (fresher data for 30-min loops)
        cutoff_time = time.time() - (6 * 60 * 60)  # 6 hours ago in Unix timestamp
        
        metin_blogu = ""
        fresh_post_count = 0
        total_checked = 0
        
        for submission in combined_subreddit.hot(limit=100):  # Check more posts for filtering
            total_checked += 1
            
            # Time filtering - only include posts from last 24 hours
            post_time = submission.created_utc
            if post_time < cutoff_time:
                continue  # Skip old posts
            
            # Include post title and score for sentiment weight
            upvote_indicator = "🔥" if submission.score > 500 else ""
            metin_blogu += f"{upvote_indicator}{submission.title}. "
            fresh_post_count += 1
            
            if fresh_post_count >= 50:  # Cap at 50 fresh posts
                break

        log(f"Son 24 saatte {fresh_post_count} taze post bulundu (kontrol: {total_checked})", "DATA", 1)

        if not metin_blogu:
            log("Reddit'ten taze başlık bulunamadı", "WARN", 1)
            return {"genel_duygu": "Nötr", "ozet_tr": "Son 24 saatte yeterli veri yok", "retail_signal": "NEUTRAL"}

        genai.configure(api_key=gemini_api_key)
        model = genai.GenerativeModel('models/gemini-2.5-flash')

        prompt = f"""GÖREV: Aşağıdaki Reddit başlıklarını "PERAKENDE YATIRIMCI DUYARLILIĞI" olarak analiz et.

ÖNEMLİ: Aşırı Coşku (Extreme Euphoria) potansiyel zirve sinyali, Aşırı Panik (Extreme Panic) potansiyel dip sinyalidir.

Metin Bloğu (Son 24 saat içindeki Reddit başlıkları):
"{metin_blogu[:6000]}"

Aşağıdaki JSON formatında SADECE yanıt ver:
{{
  "genel_duygu": string ('Aşırı Coşku', 'Çok Pozitif', 'Pozitif', 'Nötr', 'Negatif', 'Çok Negatif', 'Aşırı Panik'),
  "retail_signal": string ('EXTREME_EUPHORIA', 'BULLISH', 'NEUTRAL', 'BEARISH', 'EXTREME_PANIC'),
  "fomo_level": integer (0-100, yüksek = FOMO yoğun),
  "fear_level": integer (0-100, yüksek = korku yoğun),
  "ozet_tr": string (max 150 karakter özet)
}}

SADECE JSON:"""

        response = model.generate_content(prompt)

        if not response.parts:
            log("Gemini yanıt vermedi", "ERR", 1)
            return None

        analiz = extract_json_from_text(response.text.strip())
        if analiz and "genel_duygu" in analiz:
            # Add contrarian signal interpretation
            retail_signal = analiz.get('retail_signal', 'NEUTRAL')
            if retail_signal == 'EXTREME_EUPHORIA':
                analiz['contrarian_warning'] = "⚠️ Aşırı Coşku - Potansiyel zirve, dikkatli ol!"
            elif retail_signal == 'EXTREME_PANIC':
                analiz['contrarian_warning'] = "💡 Aşırı Panik - Potansiyel dip, fırsat olabilir!"
            else:
                analiz['contrarian_warning'] = None
            
            log("Reddit analizi tamamlandı", "OK", 1)
            log(f"Retail Signal: {retail_signal} | FOMO: {analiz.get('fomo_level', 'N/A')}% | Fear: {analiz.get('fear_level', 'N/A')}%", "DATA", 1)
            return analiz
        
        # JSON parse başarısız oldu, basit bir analiz dön
        log("JSON ayıklanamadı, fallback kullanılıyor", "WARN", 1)
        return {"genel_duygu": "Nötr", "ozet_tr": "Reddit analizi yapılamadı", "retail_signal": "NEUTRAL"}

    except praw.exceptions.PRAWException as e:
        log(f"PRAW hatası: {e}", "ERR", 1)
        return None
    except Exception as e:
        log(f"Reddit hatası: {e}", "ERR", 1)
        return None

async def get_borsa_hareketleri():
    """
    Etherscan API V2 kullanarak büyük USDT/USDC girişlerini izler.
    Son 60 dakikada $500K üzeri transferleri tespit eder.
    """
    if not ETHERSCAN_API_KEY:
        print("UYARI (Etherscan): API anahtarı eksik.")
        return []

    # Hedef cüzdan ve kontrat adresleri
    EXCHANGE_WALLETS = {
        "0x28c6c06298d514db089934071355e5743bf21d60": "Binance",
        "0x71660c4005ba85c37ccec55d0c4493e66feef4ff": "Coinbase"
    }
    TOKEN_CONTRACTS = {
        "0xdac17f958d2ee523a2206206994597c13d831ec7": "USDT",
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": "USDC"
    }
    
    MIN_VALUE = 500_000 * (10 ** 6)  # $500K (6 decimals)
    ONE_HOUR_AGO = int(time.time()) - 3600  # Son 60 dakika
    
    hareketler = []
    
    try:
        log("Etherscan API V2 sorgulanıyor...", "INFO")
        
        for wallet_address, exchange_name in EXCHANGE_WALLETS.items():
            for token_address, token_name in TOKEN_CONTRACTS.items():
                try:
                    # V2 API endpoint
                    url = "https://api.etherscan.io/v2/api"
                    params = {
                        "chainid": 1,  # Ethereum Mainnet
                        "module": "account",
                        "action": "tokentx",
                        "contractaddress": token_address,
                        "address": wallet_address,
                        "page": 1,
                        "offset": 100,
                        "sort": "desc",
                        "apikey": ETHERSCAN_API_KEY
                    }
                    
                    response = requests.get(url, params=params, timeout=15)
                    response.raise_for_status()
                    data = response.json()
                    
                    # V2 API yanıt kontrolü
                    if data.get("status") != "1" or not data.get("result"):
                        # Hata mesajını logla
                        if data.get("message") and data.get("message") != "OK":
                            log(f"{exchange_name}/{token_name}: {data.get('message', 'Veri yok')}", "WARN", 1)
                        await asyncio.sleep(0.35)
                        continue
                    
                    for tx in data["result"]:
                        # Sadece INFLOW (borsaya giriş) kontrol et
                        if tx.get("to", "").lower() != wallet_address.lower():
                            continue
                        
                        # Zaman kontrolü (son 60 dakika)
                        tx_time = int(tx.get("timeStamp", 0))
                        if tx_time < ONE_HOUR_AGO:
                            continue
                        
                        # Değer kontrolü ($500K+)
                        value = int(tx.get("value", 0))
                        if value < MIN_VALUE:
                            continue
                        
                        # Değeri okunabilir formata çevir
                        value_formatted = value / (10 ** 6)
                        if value_formatted >= 1_000_000:
                            value_str = f"{value_formatted / 1_000_000:.2f}M"
                        else:
                            value_str = f"{value_formatted / 1_000:.0f}K"
                        
                        hareket = f"🚨 {exchange_name}'e {value_str} {token_name} Girişi!"
                        if hareket not in hareketler:
                            hareketler.append(hareket)
                    
                    await asyncio.sleep(0.35)  # Rate limit: 3 calls/sec
                    
                except requests.exceptions.Timeout:
                    log(f"{exchange_name}/{token_name} zaman aşımı", "WARN", 1)
                    continue
                except requests.exceptions.RequestException as e:
                    log(f"{exchange_name}/{token_name} hatası: {e}", "ERR", 1)
                    continue
        
        if hareketler:
            log(f"{len(hareketler)} büyük borsa girişi tespit edildi", "OK")
        else:
            log("Son 60 dk'da $500K+ transfer bulunamadı", "INFO")
        
        return hareketler
    
    except Exception as e:
        log(f"Etherscan hatası: {e}", "ERR")
        return []

async def telegrama_bildirim_gonder(bot_token, chat_id, mesaj):
    if not bot_token or not chat_id:
        log("Telegram: Bot token veya Chat ID eksik", "ERR")
        return
    try:
        bot = telegram.Bot(token=bot_token)
        if len(mesaj) > 4000:
            mesaj = mesaj[:4000] + "\n\n...(Mesaj kısaltıldı)..."
        await bot.send_message(chat_id=chat_id, text=mesaj, parse_mode='HTML', disable_web_page_preview=True)
        log("Telegram bildirimi gönderildi", "OK", 1)
    except telegram.error.TelegramError as e:
        log(f"Telegram hatası: {e}", "ERR", 1)
    except Exception as e:
        log(f"Telegram hatası: {e}", "ERR", 1)

async def ana_dongu():
    gerekli_anahtarlar = {
        'Gemini': GEMINI_API_KEY, 'Telegram Bot': TELEGRAM_BOT_TOKEN,
        'Telegram Chat': TELEGRAM_CHAT_ID, 'Binance API': BINANCE_API_KEY, 'Binance Secret': BINANCE_SECRET_KEY,
        'Reddit Client ID': REDDIT_CLIENT_ID, 'Reddit Secret': REDDIT_CLIENT_SECRET,
        'Reddit Username': REDDIT_USERNAME, 'Reddit Password': REDDIT_PASSWORD
    }
    eksik = [isim for isim, deger in gerekli_anahtarlar.items() if not deger]
    if eksik:
        print(f"UYARI: Eksik anahtarlar: {', '.join(eksik)}")

    binance_client = None
    if BINANCE_API_KEY and BINANCE_SECRET_KEY:
        try:
            binance_client = Client(BINANCE_API_KEY, BINANCE_SECRET_KEY, requests_params={'timeout': 20})
            binance_client.ping()
            print("✅ Binance API bağlantısı başarılı.")
        except (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout):
            print("❌ HATA: Binance bağlantı zaman aşımı.")
        except BinanceAPIException as e:
            print(f"❌ HATA (Binance): {e}")
        except Exception as e:
            print(f"❌ HATA (Binance): {e}")
    else:
        print("UYARI: Binance API anahtarları eksik.")

    # ──────────────── PORTFÖY İNİCİALİZASYONU ────────────────
    portfolio = load_portfolio()
    portfolio_summary = get_portfolio_summary(portfolio)
    log_bolum("Paper Trading Portföyü Yüklendi", "💰")
    log(f"Bakiye: ${portfolio_summary['balance']:.2f}", "OK")
    log(f"Açık Pozisyon: {portfolio_summary['open_positions']}", "INFO")
    log(f"Toplam İşlem: {portfolio_summary['total_trades']} | Win Rate: {portfolio_summary['win_rate']:.1f}%", "INFO")
    log(f"Toplam PnL: ${portfolio_summary['total_pnl']:.2f}", "DATA")

    while True:
        # Döngü istatistikleri
        dongu_baslangic = time.time()
        istatistik = {
            "Reddit": "–",
            "On-Chain": "–",
            "AI Fırsat": 0,
            "Çekilen Haber": 0,
            "Analiz Edilen": 0,
            "Önemli Haber": 0,
            "Sanal Alım": 0,
            "Telegram Gönderilen": 0,
            "F&G": "–",
            "Portföy": "–"
        }
        
        # Teknik analiz önbelleği (aynı döngüde aynı coin için tekrar API çağrısı yapma)
        teknik_analiz_cache = {}

        print(f"\n{'╔'+'═'*48+'╗'}", flush=True)
        print(f"║{'🤖 AI AGENT DÖNGÜSÜ BAŞLADI':^48}║", flush=True)
        print(f"║{time.strftime('%Y-%m-%d %H:%M:%S'):^48}║", flush=True)
        print(f"{'╚'+'═'*48+'╝'}", flush=True)

        # ──────────────── PORTFÖY YÖNETİMİ (SL/TP) ────────────────
        portfolio = load_portfolio()  # Her döngüde güncel portföyü yükle
        closed_count, pnl = await portfoy_yonet(portfolio, binance_client)
        if closed_count > 0:
            istatistik["Telegram Gönderilen"] += closed_count
        
        portfolio_summary = get_portfolio_summary(portfolio)
        istatistik["Portföy"] = f"${portfolio_summary['balance']:.0f} | {portfolio_summary['open_positions']} açık"

        # ──────────────── FEAR & GREED ENDEKSİ (GLOBAL FİLTRE) ────────────────
        log_bolum("Piyasa Duyarlılığı (Fear & Greed)", "🌡️")
        fng_data = get_fear_and_greed_index()
        fng_str = ""  # Telegram mesajlarına eklenecek
        can_trade = True  # Global ticaret izni
        
        if fng_data:
            istatistik["F&G"] = f"{fng_data['value']} ({fng_data['classification']})"
            log(fng_data['formatted'], "OK")
            fng_str = f"🌡️ <b>Piyasa Duyarlılığı:</b> {fng_data['formatted']}\n\n"
            
            # Aşırı Korku kontrolü (F&G < 20 ise alım yapma)
            if fng_data['value'] < FNG_EXTREME_FEAR:
                can_trade = False
                log(f"⚠️ AŞIRI KORKU ({fng_data['value']}) - Alım yapılmayacak!", "WARN")
        else:
            istatistik["F&G"] = "Alınamadı"
            log("Fear & Greed endeksi alınamadı", "WARN")

        # ──────────────── REDDIT ANALİZİ (AI'a verilecek) ────────────────
        log_bolum("Perakende Duyarlılık Analizi (Reddit)", "🎭")
        reddit_analizi = get_reddit_sentiment(GEMINI_API_KEY)
        reddit_str = "Veri yok"
        if reddit_analizi:
            # Build comprehensive reddit string for AI
            retail_signal = reddit_analizi.get('retail_signal', 'NEUTRAL')
            fomo = reddit_analizi.get('fomo_level', 'N/A')
            fear = reddit_analizi.get('fear_level', 'N/A')
            duygu = reddit_analizi.get('genel_duygu', 'Nötr')
            
            reddit_str = f"Signal: {retail_signal} | Duygu: {duygu} | FOMO: {fomo}% | Fear: {fear}%"
            istatistik["Reddit"] = f"{retail_signal}"
            
            log(f"Duygu: {duygu}", "OK")
            log(f"Retail Signal: {retail_signal} | FOMO: {fomo}% | Fear: {fear}%", "DATA", 1)
            
            # Display contrarian warning if present
            contrarian = reddit_analizi.get('contrarian_warning')
            if contrarian:
                log(contrarian, "WARN", 1)
            
            log(f"Özet: {reddit_analizi.get('ozet_tr', '')[:80]}...", "DATA", 1)
            
            # Telegram bildirimi (opsiyonel - config ile kontrol edilir)
            if TELEGRAM_NOTIFY_REDDIT:
                reddit_mesaj = (
                    f"🎭 <b>Perakende Yatırımcı Duyarlılığı</b>\n"
                    f"<i>(5 Subreddit, Son 24 Saat)</i>\n\n"
                    f"<b>Sinyal:</b> {retail_signal}\n"
                    f"<b>Duygu:</b> {duygu}\n"
                    f"<b>FOMO Seviyesi:</b> {fomo}%\n"
                    f"<b>Korku Seviyesi:</b> {fear}%\n\n"
                    f"<b>Özet:</b> <i>{reddit_analizi.get('ozet_tr', '')}</i>"
                )
                if contrarian:
                    reddit_mesaj += f"\n\n⚠️ {contrarian}"
                await telegrama_bildirim_gonder(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, reddit_mesaj)
                istatistik["Telegram Gönderilen"] += 1
                await asyncio.sleep(1)
        else:
            log("Reddit analizi alınamadı", "WARN")

        # ──────────────── ON-CHAIN ANALİZİ ────────────────
        log_bolum("On-Chain Veri Analizi (Etherscan)", "🔗")
        borsa_hareketleri = await get_borsa_hareketleri()
        onchain_str = "Son 60 dk'da büyük transfer yok"
        if borsa_hareketleri:
            onchain_str = " | ".join(borsa_hareketleri)
            istatistik["On-Chain"] = f"{len(borsa_hareketleri)} hareket"
            for hareket in borsa_hareketleri:
                log(hareket, "DATA", 1)
            
            # Telegram bildirimi (opsiyonel - config ile kontrol edilir)
            if TELEGRAM_NOTIFY_ONCHAIN:
                onchain_mesaj = (
                    f"🔗 <b>On-Chain Analiz: Büyük Borsa Girişleri</b>\n"
                    f"<i>(Son 60 dakika, $500K+ transferler)</i>\n\n"
                )
                for hareket in borsa_hareketleri:
                    onchain_mesaj += f"{hareket}\n"
                onchain_mesaj += "\n<i>⚠️ Büyük girişler potansiyel satış baskısı işareti olabilir.</i>"
                await telegrama_bildirim_gonder(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, onchain_mesaj)
                istatistik["Telegram Gönderilen"] += 1
                await asyncio.sleep(1)
        else:
            istatistik["On-Chain"] = "Hareket yok"
            log("Son 60 dk'da $500K+ transfer yok", "INFO")

        # ══════════════════════════════════════════════════════════════════
        # AI AGENT: TEKNİK TARAYICI (WATCHLIST)
        # ══════════════════════════════════════════════════════════════════
        log_bolum("🤖 AI Agent: Teknik Tarayıcı", "🔍")
        log(f"Watchlist: {', '.join(WATCHLIST)}", "INFO")
        
        ai_firsat_sayisi = 0
        ai_satis_sayisi = 0
        
        # Açık pozisyonları al (SELL kararları için)
        open_positions = get_open_positions(portfolio)
        open_symbols = {pos.get('symbol') for pos in open_positions}
        
        if binance_client:
            # 1. ADIM: Tüm coinler için teknik analiz çek ve önbelleğe al
            market_data_list = []
            
            for coin in WATCHLIST:
                try:
                    if coin in teknik_analiz_cache:
                        ta_result = teknik_analiz_cache[coin]
                    else:
                        ta_result = get_teknik_analiz(coin, binance_client)
                        teknik_analiz_cache[coin] = ta_result
                        await asyncio.sleep(0.2)
                    
                    if ta_result:
                        score = ta_result.get('signal_score', 0)
                        log(f"{coin}: Skor {score}/5", "DATA", 1)
                        
                        market_data_list.append({
                            'symbol': coin,
                            'price': ta_result.get('price', 0),
                            'technical_analysis': ta_result.get('summary', ''),
                            'has_open_position': coin in open_symbols,
                            'atr': ta_result.get('atr'),
                            'signal_score': score
                        })
                except Exception as e:
                    log(f"{coin} teknik analiz hatası: {e}", "ERR", 1)
            
            # 2. ADIM: Batch AI çağrısı yap (5'erli gruplar halinde)
            if market_data_list:
                context_data = {
                    'reddit_sentiment': reddit_str,
                    'on_chain_data': onchain_str,
                    'fear_and_greed': fng_data
                }
                
                ai_decisions = {}
                batch_size = AI_BATCH_SIZE  # Config'den al (5)
                
                # Coinleri 5'erli gruplara böl
                for i in range(0, len(market_data_list), batch_size):
                    batch = market_data_list[i:i+batch_size]
                    log(f"🤖 Batch AI: Grup {i//batch_size + 1} - {len(batch)} coin analiz ediliyor...", "INFO")
                    
                    batch_result = await ask_gemini_batch_decisions(batch, context_data)
                    if batch_result:
                        ai_decisions.update(batch_result)
                    else:
                        # Fallback: Bu gruptaki coinler için tekli çağrı
                        log(f"⚠️ Grup {i//batch_size + 1} başarısız, tekli çağrı deneniyor...", "WARN")
                        for data in batch[:2]:  # Her grup için max 2 coin dene
                            data_copy = data.copy()
                            data_copy.update(context_data)
                            result = await ask_gemini_for_trade_decision(data_copy)
                            if result:
                                ai_decisions[data['symbol']] = result
                            await asyncio.sleep(1)
                    
                    await asyncio.sleep(1)  # Gruplar arası bekleme
                
                log(f"🤖 Toplam AI: {len(ai_decisions)} karar alındı", "OK")
                
                # 3. ADIM: AI kararlarını işle
                for data in market_data_list:
                    coin = data['symbol']
                    ai_decision = ai_decisions.get(coin)
                    
                    if not ai_decision:
                        log(f"⚠️ {coin}: AI karar yok", "WARN", 1)
                        continue
                    
                    decision = ai_decision.get('decision', 'HOLD')
                    confidence = ai_decision.get('confidence', 0)
                    reasoning = ai_decision.get('reasoning', '')
                    
                    log(f"🤖 {coin}: {decision} | Güven: {confidence}% | {reasoning[:40]}...", "OK", 1)
                    
                    # ──── AI-TECH BUY KARARI ────
                    if decision == "BUY" and confidence > AI_TECH_CONFIDENCE_THRESHOLD and can_trade:
                        ai_firsat_sayisi += 1
                        atr = data.get('atr')
                        price = data.get('price')
                        
                        if atr and price and not data.get('has_open_position'):
                            trigger_info = f"AI-TECH: Skor {data.get('signal_score', 0)}/5 | Güven {confidence}%"
                            
                            # Market snapshot hazırla (simülasyon değerlendirmesi için)
                            tech_market_snapshot = {
                                "technical": {
                                    "signal_score": data.get('signal_score'),
                                    "summary": data.get('technical_analysis', ''),
                                    "atr": atr,
                                    "price": price
                                },
                                "on_chain": onchain_str,
                                "reddit": reddit_str if isinstance(reddit_str, dict) else {"raw": reddit_str},
                                "fng": fng_data,
                                "news": None  # Teknik tetikli, haber yok
                            }
                            
                            success, result = await sanal_alim_yap(
                                portfolio, coin, price, atr,
                                trade_reason="AI-TECH",
                                trigger_info=trigger_info,
                                ai_reasoning=reasoning,
                                ai_confidence=confidence,
                                market_snapshot=tech_market_snapshot
                            )
                            
                            if success:
                                istatistik["Sanal Alım"] += 1
                                if TELEGRAM_NOTIFY_TRADES:
                                    istatistik["Telegram Gönderilen"] += 1
                                log(f"✅ {coin}: AI-TECH alım yapıldı! (Güven: {confidence}%)", "OK", 1)
                            else:
                                log(f"❌ {coin}: {result}", "WARN", 1)
                            
                            await asyncio.sleep(0.5)
                    
                    # ──── AI-TECH SELL KARARI ────
                    elif decision == "SELL" and confidence > AI_SELL_CONFIDENCE_THRESHOLD:
                        if data.get('has_open_position'):
                            ai_satis_sayisi += 1
                            price = data.get('price')
                            
                            if price:
                                # Market snapshot hazırla (simülasyon değerlendirmesi için)
                                sell_market_snapshot = {
                                    "technical": {
                                        "signal_score": data.get('signal_score'),
                                        "summary": data.get('technical_analysis', ''),
                                        "price": price
                                    },
                                    "on_chain": onchain_str,
                                    "reddit": reddit_str if isinstance(reddit_str, dict) else {"raw": reddit_str},
                                    "fng": fng_data,
                                    "news": None
                                }
                                
                                success, pnl, _ = await execute_ai_sell_decision(
                                    portfolio, coin, price, reasoning, binance_client,
                                    ai_confidence=confidence,
                                    market_snapshot=sell_market_snapshot
                                )
                                
                                if success:
                                    if TELEGRAM_NOTIFY_TRADES:
                                        istatistik["Telegram Gönderilen"] += 1
                                    log(f"📉 {coin}: AI SELL uygulandı! PnL: ${pnl:.2f}", "OK", 1)
                            
                            await asyncio.sleep(0.5)
            
            istatistik["AI Fırsat"] = ai_firsat_sayisi
            if ai_satis_sayisi > 0:
                istatistik["AI Satış"] = ai_satis_sayisi
        else:
            if not can_trade:
                log("Aşırı Korku nedeniyle AI tarama atlanıyor", "WARN")
            else:
                log("Binance client yok, AI tarama yapılamadı", "ERR")

        # ══════════════════════════════════════════════════════════════════
        # AI AGENT: HABER ANALİZİ (NEWS TETİKLİ ALIM)
        # ══════════════════════════════════════════════════════════════════
        log_bolum("🤖 AI Agent: Haber Tarayıcı", "📰")
        islenmis = islenmis_haberleri_yukle()
        log(f"Veritabanında {len(islenmis)} işlenmiş haber var", "INFO")

        haberler = haberleri_cek()  # RSS Feeds - gerçek zamanlı
        istatistik["Çekilen Haber"] = len(haberler)

        if haberler:
            for haber in haberler:
                if haber['link'] in islenmis or not haber_basligi_uygun_mu(haber['baslik']):
                    if haber['link'] not in islenmis:
                        haberi_kaydet(haber['link'])
                    continue

                baslik_kisaltilmis = haber['baslik'][:55] + "..." if len(haber['baslik']) > 55 else haber['baslik']
                log(f"İşleniyor: {baslik_kisaltilmis}", "INFO")

                icerik = get_haber_icerigi(haber['link'])
                if not icerik:
                    log("İçerik alınamadı, atlanıyor", "WARN", 1)
                    haberi_kaydet(haber['link'])
                    continue

                analiz = haberleri_analiz_et(GEMINI_API_KEY, haber['baslik'], icerik)
                istatistik["Analiz Edilen"] += 1

                if analiz == "KOTA_ASILDI":
                    log("Gemini API kotası aşıldı!", "ERR")
                    break
                if not isinstance(analiz, dict):
                    haberi_kaydet(haber['link'])
                    continue

                onem = analiz.get('onem_derecesi') or analiz.get('önem_derecesi') or 'Bulunamadı'
                duygu = analiz.get('duygu', 'Bilinmiyor')
                log(f"Önem: {onem} | Duygu: {duygu}", "DATA", 1)

                if analiz.get('kripto_ile_ilgili_mi') and onem in ['Yüksek', 'Çok Yüksek']:
                    istatistik["Önemli Haber"] += 1
                    log("🔥 ÖNEMLİ HABER! AI Agent'a danışılıyor...", "OK", 1)

                    teknik_str = ""
                    coinler = analiz.get('etkilenen_coinler', [])
                    if coinler and binance_client and can_trade:
                        teknik_str = "<b>📊 Teknik Analiz (4s):</b>\n"
                        for coin in coinler[:3]:
                            coin_normalized = normalize_coin_symbol(coin)
                            if not coin_normalized:
                                continue
                            
                            # Önbellekten kontrol et
                            if coin_normalized in teknik_analiz_cache:
                                ta_result = teknik_analiz_cache[coin_normalized]
                                log(f"{coin_normalized}/USDT: Önbellekten alındı", "DATA", 2)
                            else:
                                ta_result = get_teknik_analiz(coin_normalized, binance_client)
                                teknik_analiz_cache[coin_normalized] = ta_result
                                await asyncio.sleep(0.5)
                            
                            if ta_result:
                                summary = ta_result.get('summary', '')
                                teknik_str += f"\n<b>• {coin_normalized}/USDT:</b>\n"
                                teknik_str += f"  {summary}\n"
                                log(f"{coin_normalized}: Skor {ta_result.get('signal_score', 0)}/5", "DATA", 2)
                                
                                # ──── AI AGENT'A DANIŞMA (NEWS) ────
                                has_position = coin_normalized in open_symbols
                                market_data = {
                                    'symbol': coin_normalized,
                                    'price': ta_result.get('price', 0),
                                    'technical_analysis': summary,
                                    'news_analysis': analiz,  # Haber analizini dahil et
                                    'reddit_sentiment': reddit_str,
                                    'on_chain_data': onchain_str,
                                    'fear_and_greed': fng_data,
                                    'has_open_position': has_position
                                }
                                
                                log(f"🤖 {coin_normalized}: AI Agent'a (NEWS) danışılıyor...", "INFO", 2)
                                ai_decision = await ask_gemini_for_trade_decision(market_data)
                                
                                if ai_decision:
                                    decision = ai_decision.get('decision', 'HOLD')
                                    confidence = ai_decision.get('confidence', 0)
                                    reasoning = ai_decision.get('reasoning', '')
                                    
                                    log(f"🤖 {coin_normalized}: {decision} | Güven: {confidence}%", "OK", 2)
                                    
                                    # ──── AI-NEWS TETİKLİ ALIM ────
                                    if decision == "BUY" and confidence > AI_NEWS_CONFIDENCE_THRESHOLD:
                                        atr = ta_result.get('atr')
                                        price = ta_result.get('price')
                                        
                                        if atr and price:
                                            trigger_info = f"AI-NEWS: {haber['baslik'][:50]}... | Güven {confidence}%"
                                            
                                            # Market snapshot hazırla (simülasyon değerlendirmesi için)
                                            news_market_snapshot = {
                                                "technical": {
                                                    "signal_score": ta_result.get('signal_score'),
                                                    "summary": summary,
                                                    "atr": atr,
                                                    "price": price
                                                },
                                                "on_chain": onchain_str,
                                                "reddit": reddit_str if isinstance(reddit_str, dict) else {"raw": reddit_str},
                                                "fng": fng_data,
                                                "news": {
                                                    "baslik": haber.get('baslik', ''),
                                                    "kaynak": haber.get('kaynak', ''),
                                                    "link": haber.get('link', ''),
                                                    "duygu": analiz.get('duygu', ''),
                                                    "onem": analiz.get('onem_derecesi') or analiz.get('önem_derecesi', ''),
                                                    "ozet": analiz.get('ozet_tr', '')[:200],
                                                    "etkilenen_coinler": analiz.get('etkilenen_coinler', [])
                                                }
                                            }
                                            
                                            success, result = await sanal_alim_yap(
                                                portfolio, coin_normalized, price, atr,
                                                trade_reason="AI-NEWS",
                                                trigger_info=haber['baslik'],
                                                ai_reasoning=reasoning,
                                                ai_confidence=confidence,
                                                market_snapshot=news_market_snapshot
                                            )
                                            
                                            if success:
                                                istatistik["Sanal Alım"] += 1
                                                istatistik["Telegram Gönderilen"] += 1
                                                log(f"✅ {coin_normalized}: AI-NEWS alım yapıldı! (Güven: {confidence}%)", "OK", 2)
                                            else:
                                                log(f"Alım yapılamadı: {result}", "WARN", 2)
                                            
                                            await asyncio.sleep(1)
                                else:
                                    log(f"⚠️ {coin_normalized}: AI karar alamadı", "WARN", 2)

                    coinler_str = ", ".join(coinler) if coinler else "Belirtilmemiş"
                    
                    # Önemli haber bildirimi (config ile kontrol edilir)
                    if TELEGRAM_NOTIFY_IMPORTANT_NEWS:
                        mesaj = (
                            f"🚨 <b>{onem.upper()} ÖNEMLİ KRİPTO HABERİ</b> 🚨\n\n"
                            f"{fng_str}"
                            f"<b>Başlık:</b> {haber['baslik']}\n"
                            f"<b>Kaynak:</b> {haber['kaynak']}\n\n"
                            f"<b>Haber Analizi:</b>\n"
                            f"• Duygu: {analiz.get('duygu', 'N/A')}\n"
                            f"• Coinler: {coinler_str}\n\n"
                            f"{teknik_str}\n"
                            f"<b>Özet:</b> <i>{analiz.get('ozet_tr', 'Özet alınamadı.')}</i>\n\n"
                            f"<a href='{haber['link']}'>Habere Git</a>"
                        )
                        await telegrama_bildirim_gonder(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, mesaj)
                        istatistik["Telegram Gönderilen"] += 1

                haberi_kaydet(haber['link'])
                await asyncio.sleep(3)

        # ──────────────── DÖNGÜ ÖZETİ ────────────────
        # Portföyü tekrar yükle (döngü içinde yapılan alımları yansıtmak için)
        portfolio = load_portfolio()
        portfolio_summary = get_portfolio_summary(portfolio)
        istatistik["Portföy"] = f"${portfolio_summary['balance']:.0f} | {portfolio_summary['open_positions']} açık"
        
        gecen = time.time() - dongu_baslangic
        bekleme = max(1800 - gecen, 60)
        istatistik["Süre"] = f"{gecen:.1f}s"
        istatistik["Sonraki Döngü"] = f"{bekleme/60:.1f} dk sonra"
        
        log_ozet(istatistik)
        
        await asyncio.sleep(bekleme)

if __name__ == "__main__":
    try:
        asyncio.run(ana_dongu())
    except KeyboardInterrupt:
        print("\nProgram sonlandırıldı.")
    except Exception as e:
        print(f"\n❌ KRİTİK HATA: {e}")
