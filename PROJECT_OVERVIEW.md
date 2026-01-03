# 🤖 CTB-EXP: Kripto Trading Botu Projesi

## Kapsamlı Proje Dokümantasyonu

Bu doküman, projenin ne yaptığını, nasıl yaptığını ve neden yaptığını detaylı şekilde açıklamaktadır. Bir AI veya yeni bir geliştirici bu dokümanı okuyarak projeyi tamamen anlayabilir.

---

## 📋 İÇİNDEKİLER

1. [Proje Özeti](#proje-özeti)
2. [Sistem Mimarisi](#sistem-mimarisi)
3. [Modül Açıklamaları](#modül-açıklamaları)
4. [Trading Stratejisi (V1)](#trading-stratejisi-v1)
5. [Veri Akışı](#veri-akışı)
6. [Risk Yönetimi](#risk-yönetimi)
7. [Konfigürasyon](#konfigürasyon)
8. [Çalışma Modları](#çalışma-modları)

---

## 🎯 PROJE ÖZETİ

### Ne Yapıyor?

Bu proje, **otomatik kripto para trading botu**dur. Binance borsasında belirlenen coinleri izler, teknik analiz yaparak alım-satım sinyalleri üretir ve bu sinyallere göre pozisyon açıp kapatır.

### Temel Özellikler

| Özellik | Açıklama |
|---------|----------|
| **Multi-Timeframe Analiz** | 1 saatlik ve 15 dakikalık zaman dilimlerini birlikte kullanır |
| **Rejim Filtresi** | Düşük trendli piyasalarda işlem yapmayı engeller |
| **Risk Veto Sistemi** | LLM ile haberleri analiz ederek riskli işlemleri engeller |
| **Otomatik Stop-Loss** | ATR bazlı dinamik stop-loss hesaplama |
| **Partial Take-Profit** | 1R kârda pozisyonun yarısını kapatma |
| **Trailing Stop** | Chandelier trailing stop mekanizması |
| **Paper Trading** | Gerçek para kullanmadan simülasyon modu |
| **Telegram Bildirimleri** | Kritik olaylar için anlık uyarılar |

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
│   Engine     │   │  (V1/Legacy) │   │              │   │  Manager     │
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
                  │                     ├── regime_filter.py ◄───────────────┤
                  │                     ├── swing_trend_v1.py ◄──────────────┤
                  │                     └── news_veto.py ◄───────────────────┤
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
    1. Whale hareketlerini kontrol et (on-chain)
    2. Her coin için:
       a. Piyasa verilerini topla
       b. Açık pozisyon varsa → Satış mantığı
       c. Açık pozisyon yoksa → Alım mantığı
    3. Global güvenlik kontrolü
    4. 15 dakika bekle
```

**Önemli Metodlar:**
- `run_once()` - Tek döngü çalıştırır
- `process_buy_logic()` - BUY karar süreci
- `process_sell_logic()` - SELL karar süreci
- `_process_buy_v1()` - V1 stratejisi alım mantığı
- `check_global_safety()` - Risk limitleri kontrolü

### 3. `market_data_engine.py` - Veri Toplama Motoru

**Ne Yapar:** Tüm piyasa verilerini toplar, işler ve önbelleğe alır.

**Neden Var:** Veri toplama mantığını izole ederek tekrar kullanılabilirlik sağlar.

**Veri Kaynakları:**
| Kaynak | Veri | TTL |
|--------|------|-----|
| Binance API | Fiyat, Mum verileri, Hacim | 1-15 sn |
| Etherscan | Whale hareketleri | 2 dk |
| RSS Feeds | Kripto haberleri | 4 saat |
| Alternative.me | Fear & Greed Index | 90 sn |

**Temel Metodlar:**
- `get_full_snapshot()` - Tüm verileri birleştirir
- `get_v1_timeframe_data()` - Multi-timeframe göstergeler
- `get_technical_snapshot()` - Teknik analiz verileri
- `_fetch_whale_movements()` - On-chain whale takibi

### 4. `strategy_engine.py` - Strateji Karar Motoru

**Ne Yapar:** Toplanan verileri analiz ederek BUY/SELL kararları üretir.

**Neden Var:** Karar mantığını merkezi bir yerde toplar, farklı stratejileri destekler.

**Karar Formülü (Legacy):**
```
Final Score = (Math Score × 0.35) + (AI Score × 0.65)

Math Score = (Tech × 0.70) + (OnChain × 0.15) + (F&G × 0.15)
```

**Çıktı Formatı:**
```json
{
    "action": "BUY" | "HOLD" | "SELL",
    "confidence": 0-100,
    "reason": "Karar nedeni",
    "stop_loss": 49000.0,
    "take_profit": 52000.0,
    "quantity": 0.001
}
```

### 5. `strategies/` - Strateji Modülleri

#### 5.1 `regime_filter.py` - Rejim Filtresi

**Ne Yapar:** Piyasa koşullarını kontrol ederek düşük kaliteli ortamlarda trade'i engeller.

**Filtreler:**
| Filtre | Koşul | Varsayılan |
|--------|-------|------------|
| ADX | >= MIN_ADX_ENTRY | 10.0 |
| ATR% | >= MIN_ATR_PCT | 0.10% |
| Volume | >= Ortalama × 0.8 | - |

**Neden Var:** "Kötü piyasada trade yapma" prensibini uygular.

#### 5.2 `swing_trend_v1.py` - Ana Strateji

**Ne Yapar:** Long-only swing trading stratejisi uygular.

**Entry Koşulları (Tümü sağlanmalı):**
```
1. Rejim filtresi geçilmeli            → ADX >= 10, ATR% >= 0.10%
2. Trend yapısı pozitif olmalı         → EMA20(1h) > EMA50(1h)
3. EMA50 yukarı eğimli olmalı          → EMA50 > EMA50_prev
4. Breakout gerçekleşmeli              → Close(15m) > HighestHigh(20)
```

**Exit Mekanizmaları:**
```
1. Initial Stop-Loss  → Entry - (SL_ATR_MULT × ATR)
2. Partial TP        → 1R'de pozisyonun %50'sini sat
3. Trailing Stop     → HighestClose - (TRAIL_ATR_MULT × ATR)
```

#### 5.3 `news_veto.py` - Haber Risk Veto

**Ne Yapar:** LLM (Gemini) kullanarak haberleri analiz eder, riskli durumlarda entry'yi engeller.

**Veto Tetikleyicileri:**
- Borsa delist
- Hack/Exploit haberleri
- SEC/Regülasyon soruşturmaları
- Kritik teknik açıklar

**Neden Var:** Beklenmedik negatif gelişmelere karşı koruma sağlar.

### 6. `risk_manager.py` - Risk Yönetimi

**Ne Yapar:** Pozisyon boyutlandırma, SL/TP hesaplama ve güvenlik kontrollerini yapar.

**Risk Kontrolleri:**
```python
1. Günlük kayıp limiti    → MAX_DAILY_LOSS_PCT (varsayılan: %3)
2. Maksimum pozisyon      → MAX_OPEN_POSITIONS (varsayılan: 2)
3. Ardışık stop limiti    → MAX_CONSECUTIVE_STOPS (varsayılan: 3)
4. Minimum hacim          → MIN_VOLUME_GUARDRAIL ($1M)
5. Fear & Greed aşırı     → FNG_EXTREME_FEAR (15)
```

**Pozisyon Boyutlandırma:**
```
1. Risk USD = Bakiye × RISK_PER_TRADE
2. Stop Distance = Entry - SL
3. Quantity = Risk USD / Stop Distance
4. Volatilite ölçekleme uygula
```

### 7. `execution_manager.py` - İşlem Yürütücü

**Ne Yapar:** Strateji kararlarını gerçek/simüle emirlere dönüştürür.

**Sorumluluklar:**
- Portföy güncelleme
- Trade loglama
- Telegram bildirimleri
- Duplicate intent kontrolü
- Order ledger entegrasyonu

**İşlem Akışı:**
```
Decision → Validate → OrderExecutor → Portfolio Update → Log → Notify
```

### 8. `order_executor.py` - Emir Yürütme

**Ne Yapar:** Binance API üzerinden emir oluşturur (gerçek veya simüle).

**Modlar:**
- `dry_run=True` → Simülasyon (varsayılan)
- `dry_run=False` → Gerçek Binance emirleri

**Özellikler:**
- Retry mekanizması (exponential backoff)
- Slippage ve fee simülasyonu
- Rate limiting
- LIMIT order timeout

### 9. `exchange_router.py` - Borsa Bağlantısı

**Ne Yapar:** Binance bağlantısını merkezi olarak yönetir.

**Özellikler:**
- WebSocket fiyat stream'i
- Circuit breaker (hata koruması)
- Fiyat cache'i (TTL tabanlı)
- Heartbeat izleme

**Circuit Breaker Durumları:**
```
CLOSED    → Normal çalışma
OPEN      → 5 dk bekleme (hatalar çok)
HALF_OPEN → Deneme yapılıyor
```

### 10. `position_manager.py` - Pozisyon Yönetimi

**Ne Yapar:** Açık pozisyonları izler, SL/TP tetiklenince kapatır.

**Watchdog Modu:**
- Ana döngüden bağımsız, 30 saniyede bir kontrol
- SL/TP tetiklenince anında kapatma
- V1 için partial TP ve trailing stop yönetimi

### 11. `alert_manager.py` - Uyarı Sistemi

**Ne Yapar:** Kritik olaylarda operatöre bildirim gönderir.

**Alert Seviyeleri:**
- `INFO` - Bilgilendirme
- `WARN` - Uyarı
- `CRITICAL` - Kritik

**Alert Kodları:**
```python
DAILY_LOSS_LIMIT_HIT     → Günlük kayıp limiti aşıldı
CONSECUTIVE_STOPS_HIT    → Ardışık stop limiti
ORDER_REJECTED           → Emir reddedildi
LLM_RATE_LIMITED         → LLM rate limit
NEWS_VETO_TRUE           → Haber veto aktif
```

### 12. `backtest.py` - Geriye Dönük Test

**Ne Yapar:** Geçmiş veriler üzerinde strateji testi yapar.

**Özellikler:**
- Senkron çalışma (LLM gerektirmez)
- V1 strateji desteği (partial TP, trailing stop)
- PnL hesaplama
- Trade log çıktısı

---

## 📈 TRADING STRATEJİSİ (V1)

### Strateji Felsefesi

**"Trend Takibi + Breakout + Risk Yönetimi"**

Bu strateji şu prensiplere dayanır:
1. **Trend ile işlem yap** - EMA yapısı pozitif olmalı
2. **Breakout teyidi bekle** - Yanlış sinyalleri filtrele
3. **Kârı koru** - Partial TP ile riski azalt
4. **Kayıpları sınırla** - ATR bazlı stop-loss

### Entry Kuralları

```
[1h Timeframe - Trend Yapısı]
├── EMA20 > EMA50           ✓ Uptrend yapısı
├── EMA50 > EMA50_prev      ✓ Momentum pozitif
└── ADX >= 10               ✓ Trend güçlü

[15m Timeframe - Tetikleme]
└── Close > HighestHigh(20) ✓ Breakout teyidi

[Rejim Filtresi]
├── ADX >= 10               ✓ Trend var
├── ATR% >= 0.10%           ✓ Volatilite yeterli
└── Volume >= Avg × 0.8     ✓ Hacim normal
```

### Exit Kuralları

```
[Initial Stop-Loss]
SL = Entry - (1.5 × ATR)

[Partial Take-Profit]
Eğer Price >= Entry + 1R:
    → Pozisyonun %50'sini sat
    
    1R = Entry + (Entry - SL) = Entry + Stop_Distance

[Trailing Stop]
Partial TP'den sonra:
    Trail_SL = HighestClose - (3.0 × ATR)
    → Sadece yukarı güncellenir (never loosen)
```

### Örnek Trade

```
Entry:     $50,000
ATR:       $800
SL:        $50,000 - (1.5 × $800) = $48,800
1R:        $50,000 + ($50,000 - $48,800) = $51,200

Senaryo 1: Fiyat $51,200'e ulaşır
  → %50 satılır ($51,200'de)
  → Kalan %50 için trailing başlar

Senaryo 2: Fiyat $48,800'e düşer
  → Tüm pozisyon kapatılır (SL)
  → Kayıp: 1.5 × ATR = $1,200 (pozisyon başına)
```

---

## 🔄 VERİ AKIŞI

### Ana Döngü Veri Akışı

```
┌────────────────────────────────────────────────────────────────────────┐
│                           HER 15 DAKİKA                                 │
└────────────────────────────────────────────────────────────────────────┘
                                  │
       ┌──────────────────────────┼──────────────────────────┐
       │                          │                          │
       ▼                          ▼                          ▼
┌──────────────┐         ┌──────────────┐         ┌──────────────┐
│   Binance    │         │  Etherscan   │         │  RSS Feeds   │
│  (Fiyat,     │         │  (Whale      │         │  (Haberler)  │
│   Mumlar)    │         │  Hareketleri)│         │              │
└──────────────┘         └──────────────┘         └──────────────┘
       │                          │                          │
       └──────────────────────────┼──────────────────────────┘
                                  │
                                  ▼
                    ┌────────────────────────┐
                    │  MarketDataEngine      │
                    │  get_full_snapshot()   │
                    └────────────────────────┘
                                  │
                                  ▼
                    ┌────────────────────────┐
                    │      Snapshot          │
                    │  {                     │
                    │    symbol, price,      │
                    │    tf: {1h, 15m},      │
                    │    technical,          │
                    │    onchain,            │
                    │    volume_24h          │
                    │  }                     │
                    └────────────────────────┘
                                  │
           ┌──────────────────────┼──────────────────────┐
           │                      │                      │
           ▼                      ▼                      ▼
    ┌─────────────┐       ┌─────────────┐       ┌─────────────┐
    │ RegimeFilter│       │SwingTrendV1 │       │  NewsVeto   │
    │    check()  │──────▶│evaluate_entry│──────▶│ check_veto()│
    └─────────────┘       └─────────────┘       └─────────────┘
                                  │
                                  ▼
                    ┌────────────────────────┐
                    │    EntrySignal         │
                    │  {                     │
                    │    action: BUY/HOLD,   │
                    │    confidence,         │
                    │    stop_loss,          │
                    │    take_profit,        │
                    │    quantity            │
                    │  }                     │
                    └────────────────────────┘
                                  │
                                  ▼
                    ┌────────────────────────┐
                    │   RiskManager          │
                    │ evaluate_entry_risk()  │
                    └────────────────────────┘
                                  │
                                  ▼
                    ┌────────────────────────┐
                    │  ExecutionManager      │
                    │  execute_buy_flow()    │
                    └────────────────────────┘
                                  │
                                  ▼
                    ┌────────────────────────┐
                    │   OrderExecutor        │
                    │   create_order()       │
                    └────────────────────────┘
```

### Snapshot Veri Yapısı

```python
snapshot = {
    "symbol": "BTCUSDT",
    "price": 90000.0,
    
    # Multi-timeframe teknik göstergeler
    "tf": {
        "1h": {
            "ema20": 90100.0,
            "ema50": 89500.0,
            "ema50_prev": 89400.0,
            "atr": 800.0,
            "adx": 25.0,
            "last_closed_ts": 1704240000
        },
        "15m": {
            "close": 90050.0,
            "highest_high": 89900.0,
            "highest_close": 89850.0,
            "atr": 200.0
        }
    },
    
    # Eski format (geriye uyumluluk)
    "technical": {
        "rsi": 55.0,
        "macd": 100.0,
        "ema_50": 89500.0,
        "ema_200": 85000.0,
        "adx": 25.0,
        "atr": 800.0
    },
    
    # On-chain verileri
    "onchain": {
        "whale_signal": "NEUTRAL",
        "whale_movements": 0,
        "whale_inflow": 0.0
    },
    
    # Hacim verileri
    "volume_24h": 1000000000,
    "volume_avg": 800000000,
    
    # Sentiment
    "fear_greed": {
        "value": 45,
        "classification": "Fear"
    }
}
```

---

## 🛡️ RİSK YÖNETİMİ

### Risk Piramidi

```
           ┌───────────────────┐
           │   Trade Seviyesi  │  ← Pozisyon boyutu, SL/TP
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

| Parametre | Varsayılan | Açıklama |
|-----------|------------|----------|
| `RISK_PER_TRADE` | %0.5 (paper) / %2 (live) | İşlem başına max risk |
| `MAX_DAILY_LOSS_PCT` | %3 | Günlük max kayıp |
| `MAX_OPEN_POSITIONS` | 2 (paper) / 5 (live) | Eşzamanlı maks pozisyon |
| `MAX_CONSECUTIVE_STOPS` | 3 | Ardışık stop limiti |
| `COOLDOWN_MINUTES` | 60 | Ardışık stop sonrası bekleme |

### Pozisyon Boyutlandırma Formülü

```python
# Temel Risk Hesabı
risk_usd = balance * RISK_PER_TRADE  # örn: $1000 * 0.5% = $5
stop_distance = entry_price - stop_loss  # örn: $50,000 - $48,800 = $1,200
base_qty = risk_usd / stop_distance  # örn: $5 / $1,200 = 0.00417 BTC

# Volatilite Ölçekleme (V1)
atr_pct = (atr / price) * 100  # örn: ($800 / $50,000) * 100 = 1.6%
vol_scale = clamp(TARGET_ATR_PCT / atr_pct, 0.5, 1.5)
final_qty = base_qty * vol_scale

# Max %10 kap
max_qty = (balance * 0.10) / price
final_qty = min(final_qty, max_qty)
```

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

# Opsiyonel Ayarlar
MAX_DAILY_LOSS_PCT=3.0
MAX_OPEN_POSITIONS=2
SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT
```

### Profil Bazlı Varsayılanlar

| Parametre | Paper Profil | Live Profil |
|-----------|--------------|-------------|
| LIVE_TRADING | False | True |
| RISK_PER_TRADE | %0.5 | %2.0 |
| MAX_OPEN_POSITIONS | 2 | 5 |
| MAX_DAILY_LOSS_PCT | %3 | %8 |
| TELEGRAM_TRADE_NOTIFICATIONS | False | True |

### Strateji Parametreleri (config.py)

```python
# ADX Eşikleri
MIN_ADX_ENTRY = 10.0             # Minimum ADX (düşürüldü: 14 → 10)
MIN_ADX_ENTRY_SOFT = 8.0         # Soft ADX (düşürüldü: 13 → 8)

# ATR Eşikleri (sembol bazlı)
MIN_ATR_PCT = 0.10               # Genel fallback
MIN_ATR_PCT_BY_SYMBOL = {
    "BTCUSDT": 0.08,             # BTC için özel
    "ETHUSDT": 0.10              # ETH için özel
}

# SL/TP Çarpanları
SL_ATR_MULT = 1.5                # SL = Entry - (1.5 × ATR)
PARTIAL_TP_FRACTION = 0.5        # 1R'de %50 sat
TRAIL_ATR_MULT = 3.0             # Trailing = HighestClose - (3 × ATR)
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
- İki güvenlik kilidi gerekli:
  - `RUN_PROFILE=live`
  - `ALLOW_DANGEROUS_ACTIONS=1`

### 3. Canary Mode

```bash
CANARY_MODE=1 python main.py
```

- Tek sembol (BTCUSDT)
- Minimum risk (%0.25)
- Tek pozisyon
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
│   ├── regime_filter.py    # Rejim filtresi
│   ├── swing_trend_v1.py   # V1 ana strateji
│   └── news_veto.py        # LLM haber veto
│
├── risk_manager.py         # Risk yönetimi
├── execution_manager.py    # İşlem yürütücü
├── order_executor.py       # Emir yürütme
├── position_manager.py     # Pozisyon yönetimi
├── exchange_router.py      # Borsa bağlantısı
│
├── alert_manager.py        # Uyarı sistemi
├── summary_reporter.py     # Periyodik raporlar
├── order_ledger.py         # Emir takip defteri
├── metrics.py              # Telemetri metrikleri
│
├── backtest.py             # Geriye dönük test
├── debug_suite.py          # Debug araçları
│
├── utils/
│   ├── __init__.py
│   └── io.py               # Atomik dosya işlemleri
│
├── data/
│   ├── portfolio.json      # Portföy durumu
│   ├── trade_log.json      # Trade geçmişi
│   └── alert_state.json    # Alert durumu
│
├── logs/
│   └── trader.log          # Ana log dosyası
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
[2026-01-03 01:00:00] INFO     [module:function:line] Mesaj
```

### Önemli Log Mesajları

| Log | Anlamı |
|-----|--------|
| `[REGIME PASS]` | Rejim filtresi geçildi |
| `[REGIME BLOCK]` | Rejim filtresi engelledi |
| `[TREND OK]` | Trend yapısı pozitif |
| `[TREND BLOCK]` | Trend yapısı negatif |
| `[BREAKOUT OK]` | Breakout gerçekleşti |
| `[BREAKOUT BLOCK]` | Breakout yok |
| `[V1 ENTRY]` | V1 alım sinyali |
| `[NEWS VETO]` | Haber veto aktif |

---

## 📞 İLETİŞİM & DESTEK

### Telegram Bildirimleri

Bot şu durumlarda bildirim gönderir:
- Trade açıldığında/kapandığında
- Günlük kayıp limiti aşıldığında
- Circuit breaker açıldığında
- Kritik hatalar oluştuğunda

### Log Dosyaları

```
logs/trader.log      # Ana log (son 10MB)
logs/terminal.log    # Terminal çıktısı
data/trade_log.json  # Trade geçmişi
```

---

*Bu doküman otomatik olarak oluşturulmuştur. Son güncelleme: 2026-01-03*
