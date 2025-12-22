import os
import sys

# ═══════════════════════════════════════════════════════════════════════════════
# HEADLESS / SERVICE MODE DETECTION
# ═══════════════════════════════════════════════════════════════════════════════
# Windows servis veya terminal-less ortamlarda güvenli çalışma
def _is_terminal_available():
    """Check if stdout/stderr are connected to a real terminal."""
    try:
        # Try to check if stdout is a TTY
        if hasattr(sys.stdout, 'isatty'):
            return sys.stdout.isatty()
        # Fallback: try to write to stdout
        sys.stdout.write('')
        sys.stdout.flush()
        return True
    except (AttributeError, OSError, PermissionError):
        return False

_HAS_TERMINAL = _is_terminal_available()

# ═══════════════════════════════════════════════════════════════════════════════
# WINDOWS TERMINAL UTF-8 ENCODING AYARI
# ═══════════════════════════════════════════════════════════════════════════════
# Sunucularda emoji ve Türkçe karakterlerin düzgün görünmesi için
if sys.platform == 'win32' and _HAS_TERMINAL:
    try:
        os.system('chcp 65001 >nul 2>&1')  # Windows code page'i UTF-8 yap
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, OSError, PermissionError):
        pass  # Terminal unavailable or Python < 3.7

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
# Sadece terminal varsa yapılır
_original_stderr = sys.stderr
try:
    sys.stderr = open(os.devnull, 'w')
except (OSError, PermissionError):
    pass  # Headless mode - stderr redirect not possible

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
from binance.client import Client
from binance.exceptions import BinanceAPIException
import requests
from datetime import datetime, timedelta, timezone

# stderr'i geri yükle (gRPC yüklendi, artık güvenli)
try:
    sys.stderr = _original_stderr
except (OSError, PermissionError):
    pass  # Headless mode


# ═══════════════════════════════════════════════════════════════════════════════
# TERMINAL LOG SİSTEMİ - Tüm çıktıları hem terminale hem dosyaya yaz
# ═══════════════════════════════════════════════════════════════════════════════
class TeeLogger:
    """Hem terminale hem dosyaya yazan logger (headless-safe)"""
    def __init__(self, log_dir="logs"):
        self.terminal = sys.stdout
        self.log_dir = log_dir
        self._terminal_works = _HAS_TERMINAL
        
        # logs klasörünü oluştur
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
        
        # Timestamp ile dosya adı oluştur
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_file = os.path.join(log_dir, f"terminal_log_{timestamp}.txt")
        self.file = open(self.log_file, 'w', encoding='utf-8')
        
        # Only print if terminal is available
        if self._terminal_works:
            try:
                print(f"📝 Terminal log dosyası: {self.log_file}")
            except (OSError, PermissionError):
                self._terminal_works = False
    
    def write(self, message):
        # Write to file always
        try:
            self.file.write(message)
            self.file.flush()
        except Exception:
            pass
        
        # Write to terminal only if available
        if self._terminal_works:
            try:
                self.terminal.write(message)
                self.terminal.flush()
            except (OSError, PermissionError):
                self._terminal_works = False
    
    def flush(self):
        try:
            self.file.flush()
        except Exception:
            pass
        if self._terminal_works:
            try:
                self.terminal.flush()
            except (OSError, PermissionError):
                self._terminal_works = False
    
    def close(self):
        try:
            self.file.close()
        except Exception:
            pass

# Terminal log'u aktifleştir
ENABLE_TERMINAL_LOG = True  # False yaparak kapatılabilir

tee_logger = None
if ENABLE_TERMINAL_LOG:
    try:
        tee_logger = TeeLogger()
        sys.stdout = tee_logger
        
        # ═══════════════════════════════════════════════════════════════════════════
        # LOGGING MODÜLÜNÜ TEE LOGGER'A YÖNLENDİR
        # ═══════════════════════════════════════════════════════════════════════════
        # Tüm modüllerdeki logger.info() çağrıları da log dosyasına yazılsın
        import logging
        
        class TeeHandler(logging.Handler):
            """Logging çıktılarını TeeLogger'a yönlendiren handler"""
            def __init__(self, tee_logger_instance):
                super().__init__()
                self.tee_logger = tee_logger_instance
            
            def emit(self, record):
                try:
                    msg = self.format(record) + '\n'
                    self.tee_logger.write(msg)
                except Exception:
                    pass
        
        # Root logger'a TeeHandler ekle
        root_logger = logging.getLogger()
        tee_handler = TeeHandler(tee_logger)
        tee_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
        tee_handler.setLevel(logging.INFO)
        root_logger.addHandler(tee_handler)
        root_logger.setLevel(logging.INFO)
        
        # NOTE: Alt modüllere ayrı handler EKLEME - propagation otomatik olarak 
        # logları root logger'a iletir. Handler eklemek duplikasyona neden olur.
        
    except (OSError, PermissionError):
        pass  # Headless mode - TeeLogger cannot be initialized


# API Anahtarları (config.py'dan import edilir)
from config import SETTINGS, RUN_PROFILE, UNIVERSE_MODE, SYMBOLS, PAPER_START_EQUITY, PAPER_SANITY_MODE
from order_executor import OrderExecutor
from loop_controller import LoopController


# ═══════════════════════════════════════════════════════════════════════════════
# BOOT BANNER - Tek satır özetli başlangıç bildirimi
# ═══════════════════════════════════════════════════════════════════════════════
def print_boot_banner():
    """
    Başlangıçta tek satır net banner yaz.
    Format: [BOOT] profile=X live=X dangerous=X universe=N risk=X% max_pos=N daily_loss=X%
    """
    symbol_count = len(SYMBOLS) if UNIVERSE_MODE == "fixed_list" else len(SETTINGS.WATCHLIST)
    risk_pct = SETTINGS.RISK_PER_TRADE * 100  # Convert back to percentage
    
    banner = (
        f"[BOOT] profile={RUN_PROFILE} "
        f"live={SETTINGS.LIVE_TRADING} "
        f"dangerous={SETTINGS.ALLOW_DANGEROUS_ACTIONS} "
        f"universe={symbol_count} "
        f"risk={risk_pct:.1f}% "
        f"max_pos={SETTINGS.MAX_OPEN_POSITIONS} "
        f"daily_loss={SETTINGS.MAX_DAILY_LOSS_PCT:.1f}%"
    )
    print(banner, flush=True)
    
    # PAPER_SANITY_MODE uyarısı
    if PAPER_SANITY_MODE and RUN_PROFILE == "paper":
        print("⚠️  [SANITY MODE] MIN_ADX_ENTRY forced to 15 for paper test", flush=True)


# ═══════════════════════════════════════════════════════════════════════════════
# MODULAR ARCHITECTURE IMPORTS
# ═══════════════════════════════════════════════════════════════════════════════
from exchange_router import ExchangeRouter
from market_data_engine import MarketDataEngine
from strategy_engine import StrategyEngine
from execution_manager import ExecutionManager
from position_manager import PositionManager
from risk_manager import RiskManager
from loop_controller import LoopController


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

# API Anahtarları (config.py'den)
GEMINI_API_KEY = SETTINGS.GEMINI_API_KEY
TELEGRAM_BOT_TOKEN = SETTINGS.TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID = SETTINGS.TELEGRAM_CHAT_ID
BINANCE_API_KEY = SETTINGS.BINANCE_API_KEY
BINANCE_SECRET_KEY = SETTINGS.BINANCE_SECRET_KEY

# Reddit ve Etherscan (config.py'den)
REDDIT_CLIENT_ID = SETTINGS.REDDIT_CLIENT_ID
REDDIT_CLIENT_SECRET = SETTINGS.REDDIT_CLIENT_SECRET
REDDIT_USER_AGENT = SETTINGS.REDDIT_USER_AGENT
REDDIT_USERNAME = SETTINGS.REDDIT_USERNAME
REDDIT_PASSWORD = SETTINGS.REDDIT_PASSWORD
ETHERSCAN_API_KEY = SETTINGS.ETHERSCAN_API_KEY

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

# Boot banner'u yaz (profile ve ayarlar özeti)
print_boot_banner()


ISLENMIS_HABERLER_DOSYASI = "islenmis_haberler.txt"
PORTFOLIO_DOSYASI = "portfolio.json"
TRADE_LOG_DOSYASI = "trade_decisions_log.json"  # AI karar detayları için
# Paper mod için PAPER_START_EQUITY kullan, yoksa config'den BASLANGIC_BAKIYE
BASLANGIC_BAKIYE = PAPER_START_EQUITY if RUN_PROFILE == "paper" else SETTINGS.BASLANGIC_BAKIYE

# ─────────────────────────────────────────────────────────────────────────────
# HİBRİT TRADER KONFİGÜRASYONU (config.py'den okunuyor)
# ─────────────────────────────────────────────────────────────────────────────
# UNIVERSE_MODE=fixed_list ise SYMBOLS kullan, değilse mevcut watchlist
WATCHLIST = list(SYMBOLS) if UNIVERSE_MODE == "fixed_list" else list(SETTINGS.WATCHLIST)
HABER_MAX_SAAT = getattr(SETTINGS, 'RSS_MAX_AGE_HOURS', 4)

# Telegram Bildirim Ayarları (config.py'den)
TELEGRAM_NOTIFY_REDDIT = SETTINGS.TELEGRAM_NOTIFY_REDDIT
TELEGRAM_NOTIFY_ONCHAIN = SETTINGS.TELEGRAM_NOTIFY_ONCHAIN
TELEGRAM_NOTIFY_TRADES = SETTINGS.TELEGRAM_NOTIFY_TRADES
TELEGRAM_NOTIFY_IMPORTANT_NEWS = SETTINGS.TELEGRAM_NOTIFY_IMPORTANT_NEWS


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
    """Portföyü JSON dosyasına atomik olarak kaydeder."""
    try:
        from utils.io import write_atomic_json
        return write_atomic_json(PORTFOLIO_DOSYASI, portfolio)
    except ImportError:
        # Fallback if utils.io not available
        try:
            with open(PORTFOLIO_DOSYASI, 'w', encoding='utf-8') as f:
                json.dump(portfolio, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            log(f"Portföy kaydetme hatası: {e}", "ERR")
            return False


# ═══════════════════════════════════════════════════════════════════════════════
# TELEGRAM HELPER
# ═══════════════════════════════════════════════════════════════════════════════

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

    log(f"News LLM analysis is {'ENABLED' if SETTINGS.USE_NEWS_LLM else 'DISABLED'}", "INFO")
    log(f"[GLOBAL RISK] Daily Loss Limit = {SETTINGS.MAX_DAILY_LOSS_PCT}%", "INFO")
    log(f"[GLOBAL RISK] Max Open Positions = {SETTINGS.MAX_OPEN_POSITIONS}", "INFO")
    log(f"[GLOBAL RISK] Max Consecutive Losses = {SETTINGS.MAX_CONSECUTIVE_LOSSES}", "INFO")
    log(f"[GLOBAL RISK] Cooldown = {SETTINGS.COOLDOWN_MINUTES} minutes", "INFO")

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

    # ──────────────── MODULAR ARCHITECTURE INITIALIZATION ────────────────
    log_bolum("Modüler Mimari Başlatılıyor", "🔧")
    try:
        # Initialize ExchangeRouter with credentials (binance_client optional)
        router = ExchangeRouter(
            api_key=SETTINGS.BINANCE_API_KEY, 
            api_secret=SETTINGS.BINANCE_SECRET_KEY, 
            symbols=set(WATCHLIST)  # Tüm watchlist coinleri için WebSocket stream
        )
        if binance_client:
            router._client = binance_client  # Use existing client
        log("ExchangeRouter başlatıldı", "OK")
        
        # Initialize MarketDataEngine
        market_data_engine = MarketDataEngine(
            exchange_router=router,
            etherscan_api_key=ETHERSCAN_API_KEY,
            reddit_credentials={
                "client_id": REDDIT_CLIENT_ID,
                "client_secret": REDDIT_CLIENT_SECRET,
                "user_agent": REDDIT_USER_AGENT,
                "username": REDDIT_USERNAME,
                "password": REDDIT_PASSWORD
            }
        )
        log("MarketDataEngine başlatıldı", "OK")
        
        # Initialize StrategyEngine
        strategy_engine = StrategyEngine(
            gemini_api_key=SETTINGS.GEMINI_API_KEY,
            enable_llm=SETTINGS.USE_STRATEGY_LLM,
            deterministic=False
        )
        log(f"StrategyEngine başlatıldı (LLM={SETTINGS.USE_STRATEGY_LLM})", "OK")
        

        # Initialize OrderExecutor
        order_executor = create_order_executor(binance_client)
        log(f"OrderExecutor başlatıldı (dry_run={not SETTINGS.LIVE_TRADING})", "OK")

        # Initialize RiskManager
        risk_manager = RiskManager()
        log("RiskManager başlatıldı", "OK")
        
    except Exception as e:
        log(f"Modüler mimari başlatma hatası: {e}", "ERR")
        # Fallback - modüller olmadan devam et
        router = None
        market_data_engine = None
        strategy_engine = None
        order_executor = None

    # ──────────────── PORTFÖY İNİCİALİZASYONU ────────────────
    portfolio = load_portfolio()
    # ──────────────── EXECUTION MANAGER İNİCİALİZASYONU ────────────────
    execution_manager = ExecutionManager(
        portfolio=portfolio,
        strategy_engine=strategy_engine,
        market_data_engine=market_data_engine,
        executor=order_executor,
        telegram_config={
            "bot_token": TELEGRAM_BOT_TOKEN,
            "chat_id": TELEGRAM_CHAT_ID,
            "notify_trades": TELEGRAM_NOTIFY_TRADES
        },
        save_portfolio_fn=save_portfolio,
        log_fn=log,
        telegram_fn=telegrama_bildirim_gonder
    )
    log("ExecutionManager başlatıldı", "OK")

    # ──────────────── POSITION MANAGER INITIALIZATION ────────────────
    position_manager = PositionManager(
        portfolio=portfolio,
        market_data_engine=market_data_engine,
        strategy_engine=strategy_engine,
        executor=order_executor,
        execution_manager=execution_manager,
        save_portfolio_fn=save_portfolio,
        telegram_fn=telegrama_bildirim_gonder,
        telegram_config={
            "bot_token": TELEGRAM_BOT_TOKEN,
            "chat_id": TELEGRAM_CHAT_ID
        }
    )
    log("PositionManager başlatıldı", "OK")

    portfolio_summary = position_manager.get_portfolio_summary()
    log_bolum("Paper Trading Portföyü Yüklendi", "💰")
    log(f"Bakiye: ${portfolio_summary['balance']:.2f}", "OK")
    log(f"Açık Pozisyon: {portfolio_summary['open_positions']}", "INFO")
    log(f"Toplam İşlem: {portfolio_summary['total_trades']} | Win Rate: {portfolio_summary['win_rate']:.1f}%", "INFO")
    log(f"Toplam PnL: ${portfolio_summary['total_pnl']:.2f}", "DATA")

    # ──────────────── LOOP CONTROLLER İNİCİALİZASYONU ────────────────
    loop_controller = LoopController(
        watchlist=WATCHLIST,
        market_data_engine=market_data_engine,
        strategy_engine=strategy_engine,
        execution_manager=execution_manager,
        position_manager=position_manager,
        exchange_router=router,
        risk_manager=risk_manager,
        telegram_fn=telegrama_bildirim_gonder,
        telegram_config={
            "bot_token": TELEGRAM_BOT_TOKEN,
            "chat_id": TELEGRAM_CHAT_ID,
            "notify_trades": TELEGRAM_NOTIFY_TRADES
        }
    )

    # ──────────────── SİSTEM HAZIR BİLDİRİMİ ────────────────
    # Döngü başlamadan önce Telegram'a "sistem hazır" mesajı gönder
    try:
        startup_msg = (
            " <b>SİSTEM BAŞLATILDI</b> \n\n"
            f"📊 <b>Mod:</b> {'🔴 CANLI İŞLEM' if SETTINGS.LIVE_TRADING else '🟢 Paper Trading'}\n"
            f"💰 <b>Bakiye:</b> ${portfolio_summary['balance']:.2f}\n"
            f"📈 <b>Açık Pozisyon:</b> {portfolio_summary['open_positions']}\n"
            f"🎯 <b>Watchlist:</b> {', '.join(WATCHLIST)}\n"
            f"⏱️ <b>Döngü Süresi:</b> {SETTINGS.LOOP_SECONDS}s\n\n"
            "✅ Tüm modüller başarıyla yüklendi.\n"
            f"<i>Başlangıç: {time.strftime('%Y-%m-%d %H:%M:%S')}</i>"
        )
        await telegrama_bildirim_gonder(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, startup_msg)
        log("Sistem başlangıç bildirimi gönderildi", "OK")
    except Exception as e:
        log(f"Başlangıç bildirimi gönderilemedi: {e}", "WARN")

    # ──────────────── ANA DÖNGÜYÜ BAŞLAT ────────────────
    await loop_controller.run()

if __name__ == "__main__":
    try:
        asyncio.run(ana_dongu())
    except KeyboardInterrupt:
        print("\nProgram sonlandırıldı.")
    except Exception as e:
        print(f"\n❌ KRİTİK HATA: {e}")
