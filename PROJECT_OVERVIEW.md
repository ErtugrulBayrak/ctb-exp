# 🤖 CTB-EXP: Kripto Trading Botu Projesi

## Kapsamlı Proje Dokümantasyonu

Bu doküman, projenin ne yaptığını, nasıl yaptığını ve neden yaptığını detaylı şekilde açıklamaktadır. Bir AI veya yeni bir geliştirici bu dokümanı okuyarak projeyi tamamen anlayabilir.

---

## 📋 İÇİNDEKİLER

1. [Proje Özeti](#proje-özeti)
2. [Sistem Mimarisi](#sistem-mimarisi)
3. [Modül Açıklamaları](#modül-açıklamaları)
4. [Trading Stratejisi (Hybrid V2)](#trading-stratejisi-hybrid-v2)
5. [Veri Akışı](#veri-akışı)
6. [Risk Yönetimi](#risk-yönetimi)
7. [Konfigürasyon](#konfigürasyon)
8. [Çalışma Modları](#çalışma-modları)

---

## 🎯 PROJE ÖZETİ

### Ne Yapıyor?

Bu proje, **otomatik kripto para trading botu**dur. Binance borsasında belirlenen coinleri izler, teknik analiz yaparak alım-satım sinyalleri üretir ve bu sinyallere göre pozisyon açıp kapatır.

### Aktif Strateji: Hybrid V2

Bot, **Hybrid Multi-Timeframe V2** stratejisini kullanır. Bu strateji:
- **3 farklı zaman dilimini** birlikte analiz eder (4H, 1H, 15M)
- **Rejim tespiti** ile piyasa koşullarına adapte olur
- **Entry type bazlı exit logic** kullanır

### Temel Özellikler

| Özellik | Açıklama |
|---------|----------|
| **Multi-Timeframe Analiz** | 4 saatlik, 1 saatlik ve 15 dakikalık verileri birlikte kullanır |
| **Rejim Tespiti** | Piyasa rejimini tespit eder (Strong Trend, Weak Trend, Ranging, Volatile) |
| **3 Entry Tipi** | 4H Swing, 1H Momentum, 15M Scalp (şuanda devre dışı) |
| **V2 Exit Logic** | Entry tipine göre özelleştirilmiş çıkış stratejileri |
| **Partial Take-Profit** | Belirlenen % kârda pozisyonun yarısını kapatma |
| **Trailing Stop** | Dinamik trailing stop mekanizması |
| **Paper Trading** | Gerçek para kullanmadan simülasyon modu |
| **Telegram Bildirimleri** | Kritik olaylar için anlık uyarılar ve komutlar |

### İzlenen Coinler (Varsayılan)

```
BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT,
DOGEUSDT, AVAXUSDT, LINKUSDT, MATICUSDT, NEARUSDT, APTUSDT, SUIUSDT
```

---

## 🏗️ SİSTEM MİMARİSİ

### Yüksek Seviye Akış

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              main.py                                     │
│                         (Başlatma & Orkestrasyon)                        │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          loop_controller.py                              │
│                    (Ana Döngü - Her 15 dakikada bir)                     │
│  ┌────────────┐    ┌────────────┐    ┌────────────┐    ┌────────────┐  │
│  │  1. Veri   │───▶│ 2. Analiz  │───▶│ 3. Karar   │───▶│ 4. Uygula  │  │
│  │   Topla    │    │    Yap     │    │    Al      │    │            │  │
│  └────────────┘    └────────────┘    └────────────┘    └────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
         │                  │                 │                  │
         ▼                  ▼                 ▼                  ▼
┌──────────────┐   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│ MarketData   │   │  Strategies  │   │ RiskManager  │   │ Execution    │
│   Engine     │   │  (Hybrid V2) │   │              │   │  Manager     │
└──────────────┘   └──────────────┘   └──────────────┘   └──────────────┘
         │                                                        │
         ▼                                                        ▼
┌──────────────┐                                         ┌──────────────┐
│ ExchangeRouter│                                         │OrderExecutor │
│ (Binance API) │                                         │              │
└──────────────┘                                         └──────────────┘
```

### Modül Bağımlılık Haritası

```
config.py ◄──────────────────────────────────────────────────────────────────┐
     │                                                                        │
     ▼                                                                        │
trade_logger.py ◄────────────────────────────────────────────────────────────┤
     │                                                                        │
     ├────────────────────────────────────────────────────────────────────────┤
     ▼                                                                        │
main.py ──────────┬──────────────────────────────────────────────────────────┤
                  │                                                           │
                  ├───▶ exchange_router.py ◄─────────────────────────────────┤
                  │           │                                               │
                  ├───▶ market_data_engine.py ◄──────────────────────────────┤
                  │           │                                               │
                  ├───▶ strategy_engine.py ◄─────────────────────────────────┤
                  │           │                                               │
                  │           └───▶ strategies/                               │
                  │                     ├── hybrid_multi_tf_v2.py ◄──────────┤
                  │                     ├── regime_detector.py ◄─────────────┤
                  │                     └── timeframe_analyzer.py ◄──────────┤
                  │                                                           │
                  ├───▶ risk_manager.py ◄────────────────────────────────────┤
                  │                                                           │
                  ├───▶ execution_manager.py ◄───────────────────────────────┤
                  │           │                                               │
                  │           └───▶ order_executor.py ◄──────────────────────┤
                  │                                                           │
                  ├───▶ position_manager.py ◄────────────────────────────────┤
                  │                                                           │
                  ├───▶ alert_manager.py ◄───────────────────────────────────┤
                  │                                                           │
                  └───▶ loop_controller.py ◄─────────────────────────────────┘
```

---

## 📦 MODÜL AÇIKLAMALARI

### 1. `main.py` - Giriş Noktası

**Ne Yapar:** Uygulamayı başlatır, tüm bileşenleri oluşturur ve ana döngüyü başlatır.

**Neden Var:** Tek bir giriş noktası sağlayarak tüm bağımlılıkların doğru sırada yüklenmesini garantiler.

**Temel Fonksiyonlar:**
- `print_boot_banner()` - Başlangıç bilgilerini gösterir
- `ensure_safe_to_live()` - Canlı trading güvenlik kontrolü
- `create_order_executor()` - OrderExecutor factory

### 2. `loop_controller.py` - Ana Döngü Orkestratörü

**Ne Yapar:** Her 15 dakikada bir çalışan ana trading döngüsünü yönetir.

**Neden Var:** Tüm trading mantığını merkezi bir yerde koordine eder.

**Ana Döngü Akışı:**
```python
while True:
    1. Açık pozisyonları izle (monitor_positions)
    2. Her coin için:
       a. Piyasa verilerini topla (paralel)
       b. Açık pozisyon varsa → V2 çıkış mantığı (process_sell_logic)
       c. Açık pozisyon yoksa → V2 giriş mantığı (process_buy_logic → _process_buy_hybrid_v2)
    3. Global güvenlik kontrolü
    4. 15 dakika bekle (LOOP_SECONDS)
```

**Önemli Metodlar:**
- `run_once()` - Tek döngü çalıştırır
- `process_buy_logic()` - Hybrid V2 entry kararı
- `_process_buy_hybrid_v2()` - Multi-timeframe sinyal değerlendirmesi
- `process_sell_logic()` - V2 exit logic (backup to watchdog)
- `check_global_safety()` - Risk limitleri kontrolü

### 3. `market_data_engine.py` - Veri Toplama Motoru

**Ne Yapar:** Tüm piyasa verilerini toplar, işler ve önbelleğe alır.

**Neden Var:** Veri toplama mantığını izole ederek tekrar kullanılabilirlik sağlar.

**Veri Kaynakları:**
| Kaynak | Veri | TTL |
|--------|------|-----|
| Binance API | Fiyat, Mum verileri, Hacim | 1-15 sn |
| RSS Feeds | Kripto haberleri | 4 saat |
| Alternative.me | Fear & Greed Index | 90 sn |

**Temel Metodlar:**
- `get_full_snapshot()` - Tüm verileri birleştirir
- `get_v2_snapshot()` - V2 için multi-timeframe snapshot
- `get_technical_snapshot()` - Teknik analiz verileri

### 4. `strategy_engine.py` - Strateji Karar Motoru

**Ne Yapar:** Toplanan verileri analiz ederek BUY/SELL kararları üretir.

**Neden Var:** Karar mantığını merkezi bir yerde toplar, Hybrid V2 stratejisini kullanır.

**V2 Çıktı Formatı:**
```json
{
    "action": "BUY" | "HOLD",
    "confidence": 0-100,
    "entry_type": "4H_SWING" | "1H_MOMENTUM" | "15M_SCALP",
    "reason": "Karar nedeni",
    "stop_loss": 49000.0,
    "take_profit_1": 51500.0,
    "partial_tp_target": 50500.0,
    "quantity": 0.001
}
```

### 5. `strategies/` - Strateji Modülleri

#### 5.1 `hybrid_multi_tf_v2.py` - Ana Strateji

**Ne Yapar:** Multi-timeframe analiz ile 3 farklı entry tipi üretir.

**Entry Tipleri:**

| Tip | Timeframe | Koşullar | Hedefler |
|-----|-----------|----------|----------|
| 4H Swing | 4H ana, 1H teyit | ADX>25, EMA hizası, Weekly teyit | %5 partial, %10 final |
| 1H Momentum | 1H ana, 4H teyit | ADX>20, RSI 55-70, Volume>1.2x | %2 partial, %4 final |
| 15M Scalp | 15M ana (DEVRE DIŞI) | BB squeeze, yüksek volume | %1.5 target |

> **Not:** 15M Scalp şu an devre dışı çünkü 15 dakikalık ana döngü scalping için çok yavaş.

#### 5.2 `regime_detector.py` - Rejim Tespiti

**Ne Yapar:** Piyasa koşullarını sınıflandırır.

**Rejim Tipleri:**
| Rejim | Koşul | İşlem İzni |
|-------|-------|------------|
| STRONG_TREND | ADX >= 30 | Tüm entry tipleri |
| WEAK_TREND | ADX 20-30 | 4H Swing, 1H Momentum |
| RANGING | ADX < 20, ATR < 0.8% | Sadece 4H Swing (dikkatli) |
| VOLATILE | ATR > 3% | 1H Momentum (küçük boyut) |

#### 5.3 `timeframe_analyzer.py` - Timeframe Analizi

**Ne Yapar:** Her timeframe için teknik göstergeleri hesaplar ve skorlar.

**Hesaplanan Göstergeler:**
- EMA20, EMA50, EMA200
- ADX (trend gücü)
- RSI (momentum)
- MACD (crossover tespiti)
- ATR (volatilite)
- Bollinger Bands (squeeze tespiti)

### 6. `risk_manager.py` - Risk Yönetimi

**Ne Yapar:** Pozisyon boyutlandırma, SL/TP hesaplama ve güvenlik kontrollerini yapar.

**Risk Kontrolleri:**
```python
1. Günlük kayıp limiti    → MAX_DAILY_LOSS_PCT (varsayılan: %3 paper, %8 live)
2. Maksimum pozisyon      → MAX_OPEN_POSITIONS (varsayılan: 4)
3. Ardışık stop limiti    → MAX_CONSECUTIVE_STOPS (varsayılan: 3)
4. Minimum hacim          → MIN_VOLUME_GUARDRAIL ($1M)
5. Fear & Greed aşırı     → FNG_EXTREME_FEAR (15)
```

### 7. `execution_manager.py` - İşlem Yürütücü

**Ne Yapar:** Strateji kararlarını gerçek/simüle emirlere dönüştürür.

**Sorumluluklar:**
- V2 alanlarını koruma (`entry_type`, `partial_tp_target`, `take_profit_1`)
- Portföy güncelleme
- Trade loglama
- Telegram bildirimleri
- Duplicate intent kontrolü

### 8. `position_manager.py` - Pozisyon Yönetimi

**Ne Yapar:** Açık pozisyonları izler, V2 exit logic uygular.

**V2 Exit Logic:**
```
check_exit_conditions() → entry_type'a göre yönlendirme:
├── 4H_SWING  → _check_4h_swing_exit()
├── 1H_MOMENTUM → _check_1h_momentum_exit()
├── 15M_SCALP → _check_15m_scalp_exit()
└── V1/UNKNOWN → _check_v1_exit() (fallback)
```

**Watchdog Modu:**
- Ana döngüden bağımsız, 30 saniyede bir kontrol
- SL/TP/Partial TP/Trailing Stop tetiklenince anında işlem
- `_quick_sltp_check()` metodu ile

### 9. `telegram_commands.py` - Telegram Komutları

**Ne Yapar:** Telegram üzerinden bot kontrolü sağlar.

**Komutlar:**
- `/start` - Bot durumu
- `/portfo` - Açık pozisyonlar ve partial_tp durumu
- `/summary` - Günlük özet
- `/help` - Yardım

### 10. `order_executor.py` - Emir Yürütme

**Ne Yapar:** Binance API üzerinden emir oluşturur (gerçek veya simüle).

**Modlar:**
- `dry_run=True` → Simülasyon (varsayılan)
- `dry_run=False` → Gerçek Binance emirleri

### 11. `exchange_router.py` - Borsa Bağlantısı

**Ne Yapar:** Binance bağlantısını merkezi olarak yönetir.

**Özellikler:**
- WebSocket fiyat stream'i
- Circuit breaker (hata koruması)
- Client reconnection mekanizması
- Fiyat cache'i (TTL tabanlı)

---

## 📈 TRADING STRATEJİSİ (HYBRID V2)

### Strateji Felsefesi

**"Multi-Timeframe Alignment + Rejim Adaptasyonu + Tiered Exit"**

Bu strateji şu prensiplere dayanır:
1. **Timeframe hizalaması** - Üst timeframe trendi alt timeframe'i onaylamalı
2. **Rejim adaptasyonu** - Piyasa koşullarına göre strateji ayarla
3. **Entry type bazlı çıkış** - Her trade tipi için özelleştirilmiş hedefler
4. **Kademeli kâr alma** - Partial TP ile riski azalt

### Entry Kuralları

#### 4H Swing Entry
```
[Rejim Kontrolü]
└── STRONG_TREND veya WEAK_TREND    ✓

[Weekly Teyit]
└── EMA50 > EMA200                   ✓ Higher TF confirmation

[4H Timeframe]
├── EMA20 > EMA50 > EMA200           ✓ Trend yapısı
├── ADX >= 25                        ✓ Trend güçlü
└── Price > EMA20                    ✓ Breakout teyidi

[1H Teyit]
└── RSI > 50 veya MACD crossover     ✓ Momentum
```

#### 1H Momentum Entry
```
[Rejim Kontrolü]
└── STRONG_TREND, WEAK_TREND veya VOLATILE   ✓

[4H Trend Teyidi]
└── EMA20 > EMA50                    ✓ Ana trend pozitif

[1H Timeframe]
├── RSI 55-70                        ✓ Güçlü momentum
├── MACD histogram expanding         ✓ Artan momentum
├── ADX >= 20                        ✓ Trend mevcut
└── Volume >= 1.2x average           ✓ Yüksek hacim
```

### Exit Kuralları (V2)

#### 4H Swing Exit
```
[Initial Stop-Loss]
SL = Entry - (2.5 × ATR)

[Partial Take-Profit]
Eğer Price >= Entry × 1.05 (%5):
    → Pozisyonun %50'sini sat
    → Trailing stop aktif et

[Trailing Stop]
Partial TP'den sonra:
    Trail_SL = HighestClose - (2.5 × ATR)
    → Sadece yukarı güncellenir

[Final Target]
Eğer Price >= Entry × 1.10 (%10):
    → Kalan pozisyonu kapat

[Time Exit]
Eğer 10 gün geçti ve kârdaysa:
    → Pozisyonu kapat
```

#### 1H Momentum Exit
```
[Initial Stop-Loss]
SL = Entry - (1.8 × ATR)

[Partial Take-Profit]
Price >= Entry × 1.02 (%2):
    → %50 sat

[Trailing Stop]
Partial TP sonrası aktif

[Final Target]
Price >= Entry × 1.04 (%4):
    → Kapat
```

### Örnek V2 Trade

```
Entry Type:  1H_MOMENTUM
Entry:       $50,000
ATR:         $800
SL:          $50,000 - (1.8 × $800) = $48,560

Senaryo:
1. Fiyat $51,000'e ulaşır (%2)
   → %50 partial TP ($51,000)
   → Trailing stop aktif: $51,000 - (1.8 × $800) = $49,560

2. Fiyat $52,000'e çıkar
   → Trailing güncellenir: $52,000 - $1,440 = $50,560

3. Fiyat $50,800'e düşer
   → Trailing stop tetiklenmez (hâlâ %50,560 üstünde)

4. Fiyat $52,100'e ulaşır (%4.2)
   → Final target hit, kalan %50 kapatılır
   
Sonuç:
- İlk %50: +%2 kâr
- İkinci %50: +%4.2 kâr
- Ortalama: +%3.1 kâr
```

---

## 🔄 VERİ AKIŞI

### V2 Snapshot Yapısı

```python
snapshot = {
    "symbol": "BTCUSDT",
    "price": 90000.0,
    
    # Multi-timeframe veriler
    "tf": {
        "4h": {
            "ema20": 90100.0,
            "ema50": 89500.0,
            "ema200": 85000.0,
            "atr": 1200.0,
            "adx": 28.0,
            "rsi": 58.0,
            "macd": 150.0,
            "macd_signal": 120.0
        },
        "1h": {
            "ema20": 90050.0,
            "ema50": 89800.0,
            "atr": 400.0,
            "adx": 25.0,
            "rsi": 62.0,
            "volume_sma": 50000000,
            "current_volume": 65000000
        },
        "15m": {
            "atr": 150.0,
            "bb_upper": 90200.0,
            "bb_lower": 89800.0
        }
    },
    
    # Rejim bilgisi
    "regime": {
        "type": "STRONG_TREND",
        "confidence": 0.85,
        "adx_4h": 28.0,
        "atr_pct": 1.3
    },
    
    # Sentiment
    "fear_greed": {
        "value": 55,
        "classification": "Greed"
    },
    
    # Hacim
    "volume_24h": 1000000000
}
```

---

## 🛡️ RİSK YÖNETİMİ

### Risk Piramidi

```
           ┌───────────────────┐
           │   Trade Seviyesi  │  ← Entry type bazlı SL/TP
           └─────────┬─────────┘
                     │
           ┌─────────▼─────────┐
           │   Günlük Seviye   │  ← Günlük kayıp limiti
           └─────────┬─────────┘
                     │
           ┌─────────▼─────────┐
           │   Global Seviye   │  ← Maks pozisyon, konsekütif stop
           └─────────┬─────────┘
                     │
           ┌─────────▼─────────┐
           │  Circuit Breaker  │  ← API hata koruması
           └───────────────────┘
```

### Risk Parametreleri

| Parametre | Paper | Live | Açıklama |
|-----------|-------|------|----------|
| `RISK_PER_TRADE` | %0.5 | %2.0 | İşlem başına max risk |
| `MAX_DAILY_LOSS_PCT` | %3 | %8 | Günlük max kayıp |
| `MAX_OPEN_POSITIONS` | 4 | 5 | Eşzamanlı maks pozisyon |
| `MAX_CONSECUTIVE_STOPS` | 3 | 3 | Ardışık stop limiti |
| `COOLDOWN_MINUTES` | 60 | 60 | Stop sonrası bekleme |

### Capital Allocation (V2)

| Timeframe | Allocation | Risk Per Trade |
|-----------|------------|----------------|
| 4H Swing | %50 | %1.5 |
| 1H Momentum | %50 | %1.0 |
| 15M Scalp | %0 (devre dışı) | %0.5 |

---

## ⚙️ KONFİGÜRASYON

### Ortam Değişkenleri (.env)

```env
# Zorunlu API Anahtarları
BINANCE_API_KEY=your_api_key
BINANCE_SECRET_KEY=your_secret_key
GEMINI_API_KEY=your_gemini_key
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# Çalışma Profili
RUN_PROFILE=paper  # paper | live

# Strateji (V2 varsayılan)
STRATEGY_VERSION=HYBRID_V2

# Opsiyonel
MAX_DAILY_LOSS_PCT=3.0
MAX_OPEN_POSITIONS=4
```

### Profil Bazlı Varsayılanlar

| Parametre | Paper Profil | Live Profil |
|-----------|--------------|-------------|
| LIVE_TRADING | False | True |
| RISK_PER_TRADE | %0.5 | %2.0 |
| MAX_OPEN_POSITIONS | 4 | 5 |
| MAX_DAILY_LOSS_PCT | %3 | %8 |
| TELEGRAM_TRADE_NOTIFICATIONS | False | True |

### V2 Strateji Parametreleri

```python
# Rejim Tespiti
REGIME_ADX_STRONG_THRESHOLD = 30.0
REGIME_ADX_WEAK_THRESHOLD = 20.0
REGIME_ATR_PCT_VOLATILE = 3.0

# Capital Allocation
CAPITAL_ALLOCATION_4H = 0.50  # %50
CAPITAL_ALLOCATION_1H = 0.50  # %50
CAPITAL_ALLOCATION_15M = 0.00 # %0 (devre dışı)

# 4H Swing
SWING_4H_MIN_ADX = 25.0
SWING_4H_SL_ATR_MULT = 2.5
SWING_4H_PARTIAL_TP_PCT = 5.0
SWING_4H_FINAL_TARGET_PCT = 10.0

# 1H Momentum
MOMENTUM_1H_MIN_ADX = 20.0
MOMENTUM_1H_MIN_RSI = 55.0
MOMENTUM_1H_MAX_RSI = 70.0
MOMENTUM_1H_PARTIAL_TP_PCT = 2.0
MOMENTUM_1H_FINAL_TARGET_PCT = 4.0

# 15M Scalp (devre dışı)
SCALP_15M_ENABLED = False
```

---

## 🚀 ÇALIŞMA MODLARI

### 1. Paper Trading (Varsayılan)

```bash
RUN_PROFILE=paper python main.py
```

- Gerçek para kullanılmaz
- Simüle edilmiş emirler
- Düşük risk parametreleri
- Trade bildirimleri kapalı

### 2. Live Trading

```bash
RUN_PROFILE=live ALLOW_DANGEROUS_ACTIONS=1 python main.py
```

- Gerçek Binance emirleri
- **DİKKAT: Gerçek para kaybedilebilir!**
- İki güvenlik kilidi gerekli

### 3. Canary Mode

```bash
CANARY_MODE=1 python main.py
```

- Tek sembol (BTCUSDT)
- Minimum risk (%0.25)
- Yeni sürüm doğrulama için

### 4. Safe Mode

```bash
SAFE_MODE=1 python main.py
```

- Sadece veri toplama
- Hiç trade yok
- Strateji izleme

---

## 📁 DOSYA YAPISI

```
project-root/
│
├── main.py                 # Giriş noktası
├── config.py               # Merkezi konfigürasyon
├── trade_logger.py         # Log yönetimi
│
├── loop_controller.py      # Ana döngü orkestratörü
├── market_data_engine.py   # Veri toplama motoru
├── strategy_engine.py      # Strateji karar motoru
│
├── strategies/
│   ├── __init__.py
│   ├── hybrid_multi_tf_v2.py  # ⭐ Ana V2 strateji
│   ├── regime_detector.py     # Rejim tespiti
│   └── timeframe_analyzer.py  # TF analizi
│
├── risk_manager.py         # Risk yönetimi
├── execution_manager.py    # İşlem yürütücü
├── order_executor.py       # Emir yürütme
├── position_manager.py     # Pozisyon yönetimi (V2 exit logic)
├── exchange_router.py      # Borsa bağlantısı
│
├── alert_manager.py        # Uyarı sistemi
├── summary_reporter.py     # Periyodik raporlar
├── telegram_commands.py    # Telegram komutları
├── order_ledger.py         # Emir takip defteri
├── exit_reason.py          # Exit reason enum
├── metrics.py              # Telemetri metrikleri
│
├── backtest.py             # Geriye dönük test
├── debug_suite.py          # Debug araçları
├── reset_paper_trading.py  # Paper trading sıfırlama
│
├── utils/
│   └── io.py               # Atomik dosya işlemleri
│
├── archive/                # Arşivlenmiş V1 dosyaları
│
├── data/
│   ├── portfolio.json      # Portföy durumu
│   ├── trade_log.json      # Trade geçmişi
│   └── alert_state.json    # Alert durumu
│
├── logs/
│   └── trader.log          # Ana log dosyası
│
├── tests/                  # Test dosyaları
│
├── .env                    # Ortam değişkenleri (gitignore'da)
├── .env.example            # Örnek .env
├── requirements.txt        # Python bağımlılıkları
└── README.md               # Proje README
```

---

## 🔍 LOG SİSTEMİ

### Log Formatı

```
[2026-01-13 01:00:00] INFO     [module:function:line] Mesaj
```

### Önemli V2 Log Mesajları

| Log | Anlamı |
|-----|--------|
| `[REGIME: STRONG_TREND]` | Güçlü trend rejimi tespit edildi |
| `[4H_SWING SETUP]` | 4H swing entry koşulları sağlandı |
| `[1H_MOMENTUM SETUP]` | 1H momentum entry koşulları sağlandı |
| `[V2 ENTRY]` | V2 stratejisi ile pozisyon açıldı |
| `[PARTIAL TP HIT]` | Partial take profit tetiklendi |
| `[TRAIL STOP UPDATED]` | Trailing stop güncellendi |
| `[V2 EXIT]` | V2 exit logic ile pozisyon kapatıldı |

---

## 📞 TELEGRAM BİLDİRİMLERİ

### Komutlar

| Komut | Açıklama |
|-------|----------|
| `/start` | Bot durumunu göster |
| `/portfo` | Açık pozisyonlar + partial_tp durumu |
| `/summary` | Günlük performans özeti |
| `/help` | Komut listesi |

### Bildirim Türleri

- Trade açılışı/kapanışı (live modda)
- Günlük kayıp limiti uyarısı
- Partial TP tetiklenmesi
- Circuit breaker durumu
- Kritik hatalar

---

## 🔄 SÜRÜM GEÇMİŞİ

### V2 (Aktif - Hybrid Multi-TF)
- Multi-timeframe analiz (4H, 1H, 15M)
- Rejim tespiti ve adaptasyon
- Entry type bazlı exit logic
- Partial TP ve trailing stop
- 15M scalp devre dışı

### V1 (Arşivlendi)
- Tek timeframe (1H + 15M trigger)
- Basit breakout stratejisi
- Sabit SL/TP oranları
- `/archive` klasöründe

---

*Son güncelleme: 13 Ocak 2026*
