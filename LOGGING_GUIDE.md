# 📋 Logging Sistemi Dokümantasyonu

Bu döküman, trading bot projesindeki tüm loglama sistemlerini, formatlarını ve kullanım alanlarını açıklar.

---

## 🔧 Merkezi Log Modülü: `trade_logger.py`

### Temel Yapılandırma

| Parametre | Değer | Açıklama |
|-----------|-------|----------|
| **Log Dosyası** | `logs/trader.log` | Ana log dosyası |
| **JSON Log** | `logs/trader.json` | Opsiyonel JSON formatı |
| **Maks Boyut** | 10 MB | Dosya bu boyutu aşınca rotate edilir |
| **Backup Sayısı** | 5 | `trader.log.1`, `trader.log.2`, ... olarak saklanır |
| **Encoding** | UTF-8 | Türkçe karakterler desteklenir |

### Log Formatı

**Normal Mod (INFO+):**
```
[2026-01-16 03:07:26] INFO     [trader] Mesaj
```

**Debug Mod:**
```
[2026-01-16 03:07:26] DEBUG    [trader:function_name:123] Mesaj
```

### Log Seviyeleri

| Seviye | Kullanım |
|--------|----------|
| `DEBUG` | Detaylı teknik bilgi (varsayılan olarak kapalı) |
| `INFO` | Genel işlem akışı, trade sinyalleri |
| `WARNING` | Dikkat gerektiren durumlar, kurtarılabilen hatalar |
| `ERROR` | Kritik hatalar, API başarısızlıkları |
| `CRITICAL` | Sistem durması gereken durumlar |

### Log Seviyesi Değiştirme

```bash
# Ortam değişkeni ile (öncelikli)
LOG_LEVEL=DEBUG python main.py

# Runtime'da (Telegram veya kod ile)
from trade_logger import set_level
set_level("DEBUG")
```

---

## 📦 Helper Fonksiyonlar

`trade_logger.py` modülü şu yardımcı fonksiyonları sunar:

| Fonksiyon | Amaç | Örnek Çıktı |
|-----------|------|-------------|
| `log(level, msg)` | Genel loglama | `[INFO] Mesaj` |
| `log_trade(action, symbol, price, qty)` | Trade logları | `📈 BUY ETHUSDT \| Price: $3278.85` |
| `log_error(module, error)` | Hata logları | `[module] TypeError: ...` |
| `log_api_call(api, endpoint, status)` | API çağrıları | `[API] Binance - klines: ✓` |
| `log_decision(symbol, action, conf, reason)` | Karar logları | `[DECISION] BTC → BUY (85%)` |
| `log_cycle(num, duration, trades, errors)` | Döngü metrikleri | `[CYCLE #1] 12.5s` |
| `log_metric(name, value, unit)` | Performans metrikleri | `[METRIC] latency: 245ms` |
| `log_warning_once(key, msg)` | Tekrarsız uyarılar | (spam önleme) |
| `log_exception(module, exc, traceback)` | Detaylı exception | Exception + traceback |

---

## 🏷️ Log Prefix (Etiket) Referansı

Logları filtrelerken kullanabileceğiniz ana etiketler:

### Strateji Logları

| Prefix | Kaynak | Açıklama |
|--------|--------|----------|
| `[HYBRID V2]` | `strategy_engine.py`, `hybrid_multi_tf_v2.py` | Ana strateji kararları |
| `[HYBRID V2 ENTRY]` | `hybrid_multi_tf_v2.py` | İşlem giriş sinyalleri |
| `[HYBRID V2 DRY RUN]` | `hybrid_multi_tf_v2.py` | Simülasyon mod alımları |
| `[HYBRID V2 SNAPSHOT]` | `market_data_engine.py` | Multi-TF veri özeti |
| `[4H SWING]` | `hybrid_multi_tf_v2.py` | 4 saatlik swing setup kontrolleri |
| `[1H MOM]` | `hybrid_multi_tf_v2.py` | 1 saatlik momentum kontrolleri |
| `[15M SCALP]` | `hybrid_multi_tf_v2.py` | 15 dakikalık scalp kontrolleri |
| `[BUILD SIGNAL]` | `hybrid_multi_tf_v2.py` | Sinyal oluşturma detayları |

### Pozisyon Yönetimi

| Prefix | Kaynak | Açıklama |
|--------|--------|----------|
| `[WATCHDOG]` | `position_manager.py` | SL/TP izleme ve çıkış kararları |
| `[POSITION]` | `position_manager.py` | Pozisyon açma/kapama |
| `[EXIT]` | `position_manager.py` | Çıkış nedenleri |
| `[PARTIAL TP]` | Çeşitli | Kısmi kâr alma operasyonları |

### Veri ve API

| Prefix | Kaynak | Açıklama |
|--------|--------|----------|
| `[MarketDataEngine]` | `market_data_engine.py` | Fiyat ve mum verisi çekme |
| `[CCXTDataProvider]` | `market_data_engine.py` | CCXT kütüphanesi işlemleri |
| `[V2]` | `market_data_engine.py` | Multi-TF indikatör hesaplamaları |
| `[API]` | `trade_logger.py` | Genel API çağrı durumları |
| `[OnChain]` | `market_data_engine.py` | Whale hareketleri verisi |

### Sistem ve Kontrol

| Prefix | Kaynak | Açıklama |
|--------|--------|----------|
| `[BOOT]` | `main.py` | Bot başlangıç bilgileri |
| `[CYCLE]` | `loop_controller.py` | Ana döngü metrikleri |
| `[TG_CMD]` | `telegram_commands.py` | Telegram komut işleyici |
| `[RiskManager]` | `risk_manager.py` | Risk limiti kontrolleri |

---

## 📁 Modül Bazlı Log Detayları

### 1. `main.py` - Boot ve Başlangıç

```
[BOOT] profile=paper live=False dangerous=False universe=12 risk=2.0% max_pos=4 daily_loss=6%
```

Bot başlarken profil ve güvenlik ayarlarını loglar.

### 2. `market_data_engine.py` - Veri Akışı

```
[CCXTDataProvider] Fetched 200 candles for BTCUSDT 4h
[MarketDataEngine] Price from REST API for ETHUSDT: $3278.85
[V2] BTC 1d: ADX=33.6, trend=NEUTRAL
[HYBRID V2 SNAPSHOT] BTC: price=$95282.03, 1d=OK, 4h=OK, 1h=OK, 15m=OK
```

### 3. `hybrid_multi_tf_v2.py` - Strateji Motorları

```
[4H SWING] SOLUSDT: ✅ Setup valid | ADX=27.3 | EMA20=143.09 | RSI_1h=50.5 | conf=0.67
[BUILD SIGNAL] SOLUSDT | 4H_SWING | partial_tp_target=150.42 | take_profit_1=150.42
[HYBRID V2 DRY RUN] SOLUSDT: Would BUY | 4H_SWING
[HYBRID V2 ENTRY] SOLUSDT: 4H_SWING | Confidence=0.67 | R:R=1.76
```

### 4. `position_manager.py` - SL/TP Watchdog

```
[WATCHDOG] SOLUSDT: price=143.26, action=HOLD, reason=Position profitable (0.0%), holding...
[WATCHDOG] ETHUSDT | event=trailing_updated | new_stop=3245.50 | pnl=2.5%
[WATCHDOG] BTCUSDT | event=stop_triggered | exit_price=92000 | pnl=-1.8%
```

### 5. `strategy_engine.py` - Karar Motorları

```
[HYBRID V2] BTCUSDT: Regime=STRONG_TREND (conf=0.80)
[HYBRID V2] BNBUSDT: Regime=RANGING (conf=0.70)
```

---

## 🔍 Log Filtreleme Örnekleri

### PowerShell ile Filtreleme

```powershell
# Sadece trade girişlerini göster
Select-String -Path "logs\trader.log" -Pattern "\[HYBRID V2 ENTRY\]"

# Watchdog olaylarını göster
Select-String -Path "logs\trader.log" -Pattern "\[WATCHDOG\].*event="

# Hataları göster
Select-String -Path "logs\trader.log" -Pattern "ERROR"

# Belirli bir coin'i izle
Select-String -Path "logs\trader.log" -Pattern "SOLUSDT"

# Son 100 satırı göster
Get-Content "logs\trader.log" -Tail 100
```

### Linux/WSL ile Filtreleme

```bash
# Trade sinyallerini izle
grep "\[HYBRID V2 ENTRY\]" logs/trader.log

# Canlı izleme
tail -f logs/trader.log | grep --color "ENTRY\|WATCHDOG"

# Hata sayısı
grep -c ERROR logs/trader.log
```

---

## ⚙️ Konfigürasyon Parametreleri

`.env` dosyasında ayarlanabilir:

| Parametre | Varsayılan | Açıklama |
|-----------|------------|----------|
| `LOG_LEVEL` | `INFO` | Log seviyesi (DEBUG/INFO/WARNING/ERROR) |
| `LOG_MAX_BYTES` | `10000000` | Dosya boyutu limiti (10MB) |
| `LOG_BACKUP_COUNT` | `5` | Backup dosya sayısı |
| `LOG_JSON_ENABLED` | `false` | JSON formatını etkinleştir |

---

## 📊 Log Analizi İpuçları

### Performans İzleme
- `[CYCLE]` loglarından döngü sürelerini takip edin
- `[METRIC]` loglarından API latency'lerini kontrol edin

### Hata Ayıklama
1. `LOG_LEVEL=DEBUG` ile çalıştırın
2. `[4H SWING]`, `[1H MOM]` loglarından strateji ret nedenlerini görün
3. `[WATCHDOG]` loglarından pozisyon durumlarını takip edin

### Trade Takibi
- `[HYBRID V2 ENTRY]` → Giriş zamanı ve fiyatı
- `[WATCHDOG].*event=` → Çıkış nedeni ve sonucu

---

## 🔗 İlişkili Dosyalar

| Dosya | Rol |
|-------|-----|
| [trade_logger.py](file:///c:/Users/useit/15-10-proje%20-%20GPT-V1/trade_logger.py) | Merkezi log modülü |
| [config.py](file:///c:/Users/useit/15-10-proje%20-%20GPT-V1/config.py) | Log parametreleri |
| [main.py](file:///c:/Users/useit/15-10-proje%20-%20GPT-V1/main.py) | Boot log ve başlangıç |
| [position_manager.py](file:///c:/Users/useit/15-10-proje%20-%20GPT-V1/position_manager.py) | Watchdog logları |
| [hybrid_multi_tf_v2.py](file:///c:/Users/useit/15-10-proje%20-%20GPT-V1/strategies/hybrid_multi_tf_v2.py) | Strateji logları |
