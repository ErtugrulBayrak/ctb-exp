"""
trade_logger.py - Merkezi Loglama Modülü
========================================

Bu modül tüm trader bileşenleri için merkezi loglama sağlar.
RotatingFileHandler ile log dosyaları otomatik döndürülür.

Kullanım:
--------
    from trade_logger import logger

    logger.info("İşlem başarılı")
    logger.warning("Dikkat gerektiren durum")
    logger.error("Hata oluştu")

    # Veya helper fonksiyon ile:
    from trade_logger import log

    log("INFO", "Mesaj")
    log("ERROR", "Hata mesajı")

Log Dosyası:
-----------
    logs/trader.log (max 10MB, 5 backup)
"""

import os
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime


# ═══════════════════════════════════════════════════════════════════════════════
# KONFIGÜRASYON
# ═══════════════════════════════════════════════════════════════════════════════
LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, "trader.log")
MAX_BYTES = 10_000_000  # 10 MB
BACKUP_COUNT = 5

# Detaylı format: modül adı ve satır numarası ile
LOG_FORMAT = "[%(asctime)s] %(levelname)-8s [%(name)s] %(message)s"
LOG_FORMAT_DEBUG = "[%(asctime)s] %(levelname)-8s [%(name)s:%(funcName)s:%(lineno)d] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Ortam değişkeninden log seviyesi al (DEBUG için: LOG_LEVEL=DEBUG)
LOG_LEVEL_MAP = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL
}
_env_level = os.environ.get("LOG_LEVEL", "INFO").upper()
DEFAULT_LEVEL = LOG_LEVEL_MAP.get(_env_level, logging.INFO)


# ═══════════════════════════════════════════════════════════════════════════════
# LOG KLASÖRÜ OLUŞTUR
# ═══════════════════════════════════════════════════════════════════════════════
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)


# ═══════════════════════════════════════════════════════════════════════════════
# LOGGER KONFIGÜRASYONU
# ═══════════════════════════════════════════════════════════════════════════════
logger = logging.getLogger("trader")
logger.setLevel(DEFAULT_LEVEL)

# Formatter - DEBUG modunda detaylı format kullan
_active_format = LOG_FORMAT_DEBUG if DEFAULT_LEVEL == logging.DEBUG else LOG_FORMAT
formatter = logging.Formatter(_active_format, datefmt=DATE_FORMAT)

# ─────────────────────────────────────────────────────────────────────────────
# File Handler (Rotating)
# ─────────────────────────────────────────────────────────────────────────────
file_handler = RotatingFileHandler(
    LOG_FILE,
    maxBytes=MAX_BYTES,
    backupCount=BACKUP_COUNT,
    encoding='utf-8'
)
file_handler.setLevel(DEFAULT_LEVEL)
file_handler.setFormatter(formatter)

# ─────────────────────────────────────────────────────────────────────────────
# Console Handler
# ─────────────────────────────────────────────────────────────────────────────
console_handler = logging.StreamHandler()
console_handler.setLevel(DEFAULT_LEVEL)
console_handler.setFormatter(formatter)

# Handler'ları ekle (duplicate önle)
if not logger.handlers:
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

# Propagation'ı kapat (parent logger'a gönderme)
logger.propagate = False


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER FONKSIYONLAR
# ═══════════════════════════════════════════════════════════════════════════════
def log(level: str, msg: str) -> None:
    """
    Hem konsola hem dosyaya log yaz.
    
    Args:
        level: "INFO", "WARNING", "ERROR", "DEBUG", "CRITICAL"
        msg: Log mesajı
    
    Example:
        log("INFO", "Trade başarılı")
        log("ERROR", "API hatası")
    """
    level = level.upper()
    
    if level == "INFO":
        logger.info(msg)
    elif level == "WARNING" or level == "WARN":
        logger.warning(msg)
    elif level == "ERROR" or level == "ERR":
        logger.error(msg)
    elif level == "DEBUG":
        logger.debug(msg)
    elif level == "CRITICAL":
        logger.critical(msg)
    else:
        logger.info(msg)


def log_trade(action: str, symbol: str, price: float, quantity: float, **kwargs) -> None:
    """
    Trade işlemini logla.
    
    Args:
        action: "BUY" veya "SELL"
        symbol: Coin sembolü
        price: İşlem fiyatı
        quantity: İşlem miktarı
        **kwargs: Ek bilgiler (pnl, reason, vb.)
    """
    pnl = kwargs.get('pnl', 0)
    reason = kwargs.get('reason', '')
    
    if action == "BUY":
        msg = f"📈 BUY {symbol} | Price: ${price:.4f} | Qty: {quantity:.6f}"
    else:
        pnl_str = f" | PnL: ${pnl:+.2f}" if pnl != 0 else ""
        msg = f"📉 SELL {symbol} | Price: ${price:.4f} | Qty: {quantity:.6f}{pnl_str}"
    
    if reason:
        msg += f" | {reason}"
    
    logger.info(msg)


def log_error(module: str, error: Exception) -> None:
    """
    Hata logla.
    
    Args:
        module: Hatanın oluştuğu modül/fonksiyon adı
        error: Exception objesi
    """
    logger.error(f"[{module}] {type(error).__name__}: {error}")


def log_api_call(api_name: str, endpoint: str, status: str = "OK") -> None:
    """
    API çağrısını logla.
    
    Args:
        api_name: API adı (Binance, Gemini, vb.)
        endpoint: Endpoint veya işlem
        status: Durum (OK, FAIL, TIMEOUT)
    """
    if status == "OK":
        logger.debug(f"[API] {api_name} - {endpoint}: ✓")
    else:
        logger.warning(f"[API] {api_name} - {endpoint}: {status}")


def log_decision(symbol: str, action: str, confidence: float, reason: str) -> None:
    """
    AI karar verme sürecini logla - canlı debug için kritik.
    
    Args:
        symbol: Coin sembolü
        action: BUY/SELL/HOLD
        confidence: 0-100 arası güven skoru
        reason: Kararın sebebi
    """
    level = logging.INFO if action != "HOLD" else logging.DEBUG
    logger.log(level, f"[DECISION] {symbol} → {action} (conf: {confidence:.1f}%) | {reason}")


def log_cycle(cycle_num: int, duration_sec: float, trades: int = 0, errors: int = 0) -> None:
    """
    Döngü metriklerini logla.
    
    Args:
        cycle_num: Döngü numarası
        duration_sec: Döngü süresi (saniye)
        trades: Bu döngüde yapılan işlem sayısı
        errors: Bu döngüde oluşan hata sayısı
    """
    if errors > 0:
        logger.warning(f"[CYCLE #{cycle_num}] {duration_sec:.2f}s | trades: {trades} | errors: {errors}")
    else:
        logger.info(f"[CYCLE #{cycle_num}] {duration_sec:.2f}s | trades: {trades}")


def log_metric(name: str, value: float, unit: str = "") -> None:
    """
    Performans metriklerini logla (DEBUG seviyesinde).
    
    Args:
        name: Metrik adı
        value: Değer
        unit: Birim (ms, $, %, vb.)
    """
    unit_str = f" {unit}" if unit else ""
    logger.debug(f"[METRIC] {name}: {value:.4f}{unit_str}")


def log_warning_once(key: str, msg: str, _cache: dict = {}) -> None:
    """
    Aynı uyarıyı sadece bir kez logla (spam önleme).
    
    Args:
        key: Uyarı için benzersiz anahtar
        msg: Uyarı mesajı
    """
    if key not in _cache:
        _cache[key] = True
        logger.warning(msg)


def log_exception(module: str, exc: Exception, include_traceback: bool = False) -> None:
    """
    Exception'ı detaylı logla.
    
    Args:
        module: Modül adı
        exc: Exception objesi
        include_traceback: Traceback dahil edilsin mi
    """
    import traceback
    msg = f"[{module}] {type(exc).__name__}: {exc}"
    if include_traceback:
        tb = traceback.format_exc()
        logger.error(f"{msg}\n{tb}")
    else:
        logger.error(msg)


def set_level(level: str) -> None:
    """
    Runtime'da log seviyesini değiştir.
    
    Args:
        level: "DEBUG", "INFO", "WARNING", "ERROR"
    
    Example:
        set_level("DEBUG")  # Detaylı loglamayı aç
    """
    lvl = LOG_LEVEL_MAP.get(level.upper(), logging.INFO)
    logger.setLevel(lvl)
    for handler in logger.handlers:
        handler.setLevel(lvl)
    logger.info(f"Log level changed to: {level.upper()}")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST
# ═══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("🧪 TRADE LOGGER TEST")
    print(f"   Current Level: {logging.getLevelName(DEFAULT_LEVEL)}")
    print("=" * 60 + "\n")
    
    # Tüm log seviyelerini test et
    logger.debug("DEBUG: Detaylı bilgi (sadece LOG_LEVEL=DEBUG ile görünür)")
    logger.info("INFO: Genel bilgi mesajı")
    logger.warning("WARNING: Dikkat gerektiren durum")
    logger.error("ERROR: Hata oluştu")
    
    # Helper fonksiyonlar
    print("\n--- Helper Functions ---")
    log("INFO", "log() helper function test")
    log_trade("BUY", "BTC", 92500.00, 0.001)
    log_trade("SELL", "ETH", 3500.00, 0.5, pnl=25.50, reason="Take Profit")
    log_decision("BTC", "BUY", 85.5, "Strong RSI + MACD crossover")
    log_decision("ETH", "HOLD", 45.0, "Mixed signals")
    log_cycle(1, 12.5, trades=1, errors=0)
    log_cycle(2, 15.2, trades=0, errors=2)
    log_metric("api_latency", 245.5, "ms")
    log_api_call("Binance", "klines", "OK")
    log_api_call("Gemini", "generate", "TIMEOUT")
    log_warning_once("test_key", "Bu uyarı sadece bir kez görünür")
    log_warning_once("test_key", "Bu tekrar görünmez")
    
    print(f"\n✅ Log dosyası: {LOG_FILE}")
    print(f"📝 DEBUG için: LOG_LEVEL=DEBUG python trade_logger.py")
    print("=" * 60 + "\n")
