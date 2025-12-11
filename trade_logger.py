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
LOG_FORMAT = "[%(asctime)s] %(levelname)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
DEFAULT_LEVEL = logging.INFO


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

# Formatter
formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

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


# ═══════════════════════════════════════════════════════════════════════════════
# TEST
# ═══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("🧪 TRADE LOGGER TEST")
    print("=" * 50 + "\n")
    
    # Test logs
    logger.info("Logger initialized successfully")
    logger.warning("This is a warning message")
    logger.error("This is an error message")
    
    # Helper fonksiyonlar
    log("INFO", "Helper function test")
    log_trade("BUY", "BTC", 92500.00, 0.001)
    log_trade("SELL", "ETH", 3500.00, 0.5, pnl=25.50, reason="Take Profit")
    
    print(f"\n✅ Log dosyası: {LOG_FILE}")
    print("=" * 50 + "\n")
