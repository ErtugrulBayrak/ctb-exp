# 🤖 CBT - Kripto Trading Bot Projesi

## Proje Genel Bakış

**CBT (Crypto Bot Trader)**, yapay zeka destekli çoklu veri kaynağı kullanan tam otomatik bir kripto para trading botudur. Bot, teknik analiz, on-chain veriler, haber analizi ve sosyal medya sentiment'ini birleştirerek alım-satım kararları verir.

> [!IMPORTANT]
> Bu proje hem **Paper Trading** (simülasyon) hem de **Live Trading** (gerçek işlem) modlarını destekler. Gerçek para ile işlem yapmadan önce tüm ayarları dikkatle kontrol edin.

---

## 🏗️ Mimari Genel Görünüm

```mermaid
flowchart TB
    subgraph Veri_Kaynaklari["📡 Veri Kaynakları"]
        BINANCE["Binance API"]
        RSS["RSS Haber Akışı"]
        REDDIT["Reddit API"]
        ETHERSCAN["Etherscan API"]
        FNG["Fear & Greed Index"]
    end
    
    subgraph Cekirdek_Motor["⚙️ Çekirdek Motor"]
        MDE["MarketDataEngine"]
        SE["StrategyEngine"]
        RM["RiskManager"]
        PM["PositionManager"]
        EM["ExecutionManager"]
    end
    
    subgraph Altyapi["🔧 Altyapı"]
        ER["ExchangeRouter"]
        OE["OrderExecutor"]
        LC["LoopController"]
        TL["TradeLogger"]
    end
    
    subgraph Cikti["📤 Çıktılar"]
        TELEGRAM["Telegram Bildirimleri"]
        LOGS["Log Dosyaları"]
        PORTFOLIO["Portfolio JSON"]
    end
    
    BINANCE --> ER
    RSS --> MDE
    REDDIT --> MDE
    ETHERSCAN --> MDE
    FNG --> MDE
    
    ER --> MDE
    MDE --> SE
    SE --> RM
    RM --> EM
    EM --> PM
    PM --> OE
    
    LC -->|"Orkestrasyon"| MDE
    LC -->|"Orkestrasyon"| SE
    LC -->|"Orkestrasyon"| EM
    
    OE --> ER
    EM --> TELEGRAM
    TL --> LOGS
    PM --> PORTFOLIO
```

---

## 📁 Dosya Yapısı

| Dosya | Satır | Açıklama |
|-------|-------|----------|
| [main.py](file:///c:/Users/useit/15-10-proje/main.py) | 572 | Ana giriş noktası, tüm modülleri başlatır |
| [config.py](file:///c:/Users/useit/15-10-proje/config.py) | 380 | Merkezi konfigürasyon (60+ parametre) |
| [market_data_engine.py](file:///c:/Users/useit/15-10-proje/market_data_engine.py) | 1859 | Veri toplama ve işleme motoru |
| [strategy_engine.py](file:///c:/Users/useit/15-10-proje/strategy_engine.py) | 1597 | Karar motoru (Math + AI) |
| [execution_manager.py](file:///c:/Users/useit/15-10-proje/execution_manager.py) | 768 | İşlem yürütme yöneticisi |
| [loop_controller.py](file:///c:/Users/useit/15-10-proje/loop_controller.py) | 553 | Ana döngü orkestrasyonu |
| [position_manager.py](file:///c:/Users/useit/15-10-proje/position_manager.py) | 482 | SL/TP izleme ve pozisyon yönetimi |
| [risk_manager.py](file:///c:/Users/useit/15-10-proje/risk_manager.py) | 407 | Risk kontrolü ve pozisyon boyutlandırma |
| [exchange_router.py](file:///c:/Users/useit/15-10-proje/exchange_router.py) | 757 | Binance bağlantı yönetimi |
| [order_executor.py](file:///c:/Users/useit/15-10-proje/order_executor.py) | 665 | Emir oluşturma ve yürütme |
| [backtest.py](file:///c:/Users/useit/15-10-proje/backtest.py) | 576 | Geçmiş veri testi |
| [trade_logger.py](file:///c:/Users/useit/15-10-proje/trade_logger.py) | 387 | Merkezi loglama sistemi |
| [llm_utils.py](file:///c:/Users/useit/15-10-proje/llm_utils.py) | 361 | LLM yanıt ayrıştırma |
| [debug_suite.py](file:///c:/Users/useit/15-10-proje/debug_suite.py) | 1250 | Sistem diagnostik |

---

## 🔄 Ana İşlem Döngüsü

```mermaid
flowchart TD
    START([🚀 Bot Başlat]) --> INIT[Modülleri Başlat]
    INIT --> SAFETY{Güvenlik<br/>Kontrolü}
    SAFETY -->|LIVE + Onay| LOOP
    SAFETY -->|Paper| LOOP
    SAFETY -->|LIVE + Onay Yok| ABORT([❌ Çıkış])
    
    subgraph LOOP["♾️ Ana Döngü (Her 15 dakikada)"]
        direction TB
        CHECK_POS[1. Açık Pozisyonları Kontrol Et]
        CHECK_POS --> FETCH[2. Piyasa Verilerini Topla]
        FETCH --> ANALYZE[3. Her Coin İçin Analiz]
        ANALYZE --> BUY_LOGIC{BUY Mantığı}
        BUY_LOGIC --> SELL_LOGIC{SELL Mantığı}
        SELL_LOGIC --> METRICS[4. Metrikleri Logla]
        METRICS --> SLEEP[5. 15 dk Bekle]
    end
    
    LOOP --> LOOP
    
    subgraph WATCHDOG["🐕 SL/TP Watchdog (Her 30 sn)"]
        W1[Fiyat Kontrol]
        W1 --> W2{SL/TP<br/>Tetiklendi?}
        W2 -->|Evet| W3[Pozisyon Kapat]
        W2 -->|Hayır| W1
    end
```

---

## 📊 Karar Verme Süreci

### Ağırlıklı Skor Modeli

Bot, **Math (60%)** ve **AI (40%)** bileşenlerini birleştiren hibrit bir karar sistemi kullanır:

```mermaid
flowchart LR
    subgraph MATH["📐 Math Layer (60%)"]
        TECH["Teknik Analiz<br/>(80%)"]
        ONCHAIN["On-Chain Veri<br/>(10%)"]
        FNG2["Fear & Greed<br/>(10%)"]
    end
    
    subgraph AI["🤖 AI Layer (40%)"]
        LLM["Gemini LLM<br/>Karar Verici"]
    end
    
    TECH --> MATH_SCORE["Math Skor<br/>(0-100)"]
    ONCHAIN --> MATH_SCORE
    FNG2 --> MATH_SCORE
    
    LLM --> AI_SCORE["AI Skor<br/>(0-100)"]
    
    MATH_SCORE --> FINAL["Final Skor<br/>= Math×0.60 + AI×0.40"]
    AI_SCORE --> FINAL
    
    FINAL --> DECISION{Skor ≥ 70?}
    DECISION -->|Evet| BUY["🟢 BUY"]
    DECISION -->|Hayır| HOLD["⚪ HOLD"]
```

### BUY Karar Akışı

```mermaid
flowchart TD
    START([Yeni Döngü]) --> GLOBAL_CHECK{Global<br/>Güvenlik?}
    GLOBAL_CHECK -->|Max Daily Loss| BLOCK1([❌ Bloklandı])
    GLOBAL_CHECK -->|Max Positions| BLOCK2([❌ Bloklandı])
    GLOBAL_CHECK -->|Cooldown Active| BLOCK3([❌ Bloklandı])
    GLOBAL_CHECK -->|OK| SYMBOL_LOOP
    
    SYMBOL_LOOP[Her Sembol İçin] --> FETCH_SNAP[Market Snapshot Al]
    FETCH_SNAP --> GUARDRAILS{Guardrails<br/>Kontrolü}
    
    GUARDRAILS -->|ADX < 20| SKIP1([⏭️ Atla])
    GUARDRAILS -->|Volume < $1M| SKIP2([⏭️ Atla])
    GUARDRAILS -->|F&G < 15| SKIP3([⏭️ Atla])
    GUARDRAILS -->|OK| CALC_MATH
    
    CALC_MATH[Math Skor Hesapla] --> LLM_CALL{LLM<br/>Çağır?}
    LLM_CALL -->|Rules Conf ≥ 65| CALL_LLM[Gemini API Çağrısı]
    LLM_CALL -->|Rules Conf < 65| FALLBACK[Sadece Math Kullan]
    
    CALL_LLM --> COMBINE[Skorları Birleştir]
    FALLBACK --> COMBINE
    
    COMBINE --> THRESHOLD{Final ≥ 70?}
    THRESHOLD -->|Hayır| HOLD([⚪ HOLD])
    THRESHOLD -->|Evet| RISK_CHECK
    
    RISK_CHECK[RiskManager Kontrolü] --> CALC_SIZE[Pozisyon Boyutu<br/>SL/TP Hesapla]
    CALC_SIZE --> EXECUTE[ExecutionManager<br/>BUY Yürüt]
    EXECUTE --> NOTIFY[📱 Telegram Bildir]
```

### SELL Karar Akışı

```mermaid
flowchart TD
    START([Açık Pozisyon]) --> CHECK_SLTP{SL/TP<br/>Tetiklendi?}
    CHECK_SLTP -->|SL Hit| CLOSE_SL[❌ Stop Loss Kapat]
    CHECK_SLTP -->|TP Hit| CLOSE_TP[✅ Take Profit Kapat]
    CHECK_SLTP -->|Hayır| AI_EVAL
    
    AI_EVAL[AI SELL Değerlendirmesi] --> AI_SELL{AI Güven<br/>≥ 75%?}
    AI_SELL -->|Hayır| HOLD([⚪ Tut])
    AI_SELL -->|Evet| PROFIT_PROTECT
    
    PROFIT_PROTECT{Kârlı Pozisyon<br/>Koruması?}
    PROFIT_PROTECT -->|Kâr ≥ 1.5%<br/>AI < 90%| HOLD
    PROFIT_PROTECT -->|Zarar veya<br/>AI ≥ 90%| CLOSE_AI[🤖 AI Satış]
    
    CLOSE_SL --> UPDATE[Portföy Güncelle]
    CLOSE_TP --> UPDATE
    CLOSE_AI --> UPDATE
    UPDATE --> LOG[Log & Telegram]
```

---

## 🔌 Modül Detayları

### 1. MarketDataEngine

Tüm dış veri kaynaklarından veri toplayan ve önbellekleyen merkezi veri motoru.

```mermaid
flowchart TB
    subgraph INPUTS["Veri Kaynakları"]
        B_API["Binance REST/WS"]
        RSS_FEED["RSS Haberleri"]
        REDDIT_API["Reddit PRAW"]
        ETH_API["Etherscan API"]
        FNG_API["Fear & Greed API"]
    end
    
    subgraph MDE["MarketDataEngine"]
        CACHE["🗃️ Cache Katmanı<br/>(TTL Bazlı)"]
        
        subgraph METHODS["Metotlar"]
            GET_PRICE["get_current_price()"]
            BUILD_SNAP["build_snapshot()"]
            GET_TECH["_get_technical_data()"]
            GET_ONCHAIN["_get_onchain_signals()"]
            GET_NEWS["get_global_news_summary()"]
            GET_REDDIT["get_crypto_reddit_summary()"]
            GET_FNG["_get_fear_greed()"]
        end
    end
    
    B_API --> GET_PRICE
    B_API --> GET_TECH
    ETH_API --> GET_ONCHAIN
    RSS_FEED --> GET_NEWS
    REDDIT_API --> GET_REDDIT
    FNG_API --> GET_FNG
    
    GET_PRICE --> CACHE
    GET_TECH --> CACHE
    GET_ONCHAIN --> CACHE
    GET_NEWS --> CACHE
    GET_REDDIT --> CACHE
    GET_FNG --> CACHE
    
    CACHE --> BUILD_SNAP
    BUILD_SNAP --> SNAPSHOT["📦 Market Snapshot"]
```

**Market Snapshot Yapısı:**
```python
{
    "symbol": "BTCUSDT",
    "price": 43500.0,
    "technical": {
        "rsi": 45.2,
        "macd": {"macd": 50, "signal": 45, "hist": 5},
        "ema": {"ema_50": 42000, "ema_200": 40000},
        "adx": 28.5,
        "atr": 1200.0,
        "volume_24h": 25000000000,
        "bb_upper": 44000,
        "bb_lower": 42000
    },
    "onchain": {
        "signal": "BULLISH",
        "whale_alert": False
    },
    "sentiment": {
        "fear_greed": {"value": 52, "classification": "Neutral"},
        "reddit_summary": "...",
        "news_summary": "..."
    }
}
```

---

### 2. StrategyEngine

Math + AI hibrit karar motoru.

```mermaid
flowchart TB
    subgraph INPUT["Girdiler"]
        SNAP["Market Snapshot"]
        POS["Mevcut Pozisyon<br/>(SELL için)"]
    end
    
    subgraph SE["StrategyEngine"]
        direction TB
        
        subgraph MATH_LAYER["📐 Math Layer"]
            CALC_TECH["Teknik Skor<br/>(RSI, MACD, EMA, ADX)"]
            CALC_OC["On-Chain Skor"]
            CALC_FNG["F&G Skor"]
            MATH_AGG["Ağırlıklı Toplam"]
        end
        
        subgraph AI_LAYER["🤖 AI Layer"]
            PROMPT["Prompt Oluştur"]
            GEMINI["Gemini API Çağrısı"]
            PARSE["JSON Parse & Validate"]
        end
        
        COMBINE["Final Skor Hesapla"]
        OUTPUT["Karar Çıktısı"]
    end
    
    SNAP --> CALC_TECH & CALC_OC & CALC_FNG
    CALC_TECH --> MATH_AGG
    CALC_OC --> MATH_AGG
    CALC_FNG --> MATH_AGG
    
    SNAP --> PROMPT
    PROMPT --> GEMINI
    GEMINI --> PARSE
    
    MATH_AGG --> COMBINE
    PARSE --> COMBINE
    COMBINE --> OUTPUT
```

**Karar Çıktı Şeması:**
```python
{
    "action": "BUY" | "SELL" | "HOLD",
    "confidence": 75,  # 0-100
    "reason": "RSI oversold + positive MACD crossover",
    "metadata": {
        "math_score": 72,
        "ai_score": 78,
        "sl_bias": "neutral",
        "tp_bias": "neutral"
    }
}
```

---

### 3. RiskManager

Pozisyon boyutlandırma ve güvenlik kontrolleri.

```mermaid
flowchart TD
    subgraph INPUTS["Girdiler"]
        SNAP["Market Snapshot"]
        DECISION["Base Karar"]
        PORTFOLIO["Portföy Durumu"]
    end
    
    subgraph RM["RiskManager"]
        GUARD["Guardrails Kontrolü"]
        GUARD --> G1{ADX ≥ MIN?}
        GUARD --> G2{Volume ≥ MIN?}
        GUARD --> G3{F&G ≥ 15?}
        
        SLTP["SL/TP Hesapla"]
        SLTP --> SL["Stop Loss<br/>= Price - (ATR × 1.5)"]
        SLTP --> TP["Take Profit<br/>= Price + (ATR × 2.5)"]
        
        SIZE["Pozisyon Boyutu"]
        SIZE --> QTY["Quantity<br/>= (Balance × 2%) / Risk"]
    end
    
    SNAP --> GUARD
    DECISION --> GUARD
    
    G1 & G2 & G3 -->|Hepsi OK| SLTP
    SNAP --> SLTP
    SLTP --> SIZE
    PORTFOLIO --> SIZE
    
    SIZE --> OUTPUT["Risk-Onaylı Karar"]
```

---

### 4. LoopController

Ana döngü orkestrasyonu ve alarm sistemi.

```mermaid
flowchart TB
    subgraph LC["LoopController"]
        RUN["run()"]
        RUN --> ONCE["run_once()"]
        
        ONCE --> MON["monitor_positions()"]
        MON --> SNAP_ALL["Tüm Snapshot'ları Al"]
        SNAP_ALL --> BUY_LOOP["Her Sembol İçin<br/>process_buy_logic()"]
        BUY_LOOP --> SELL_LOOP["Her Pozisyon İçin<br/>process_sell_logic()"]
        
        ALARM["_check_alarms()"]
        ALARM --> A1["Parse Fail > 15?"]
        ALARM --> A2["ADX Block > 20?"]
        ALARM --> A3["Data Fail > 5?"]
        A1 & A2 & A3 -->|Evet| TELE["📱 Telegram Alert"]
    end
    
    subgraph SAFETY["check_global_safety()"]
        S1["Max Daily Loss?"]
        S2["Max Positions?"]
        S3["Cooldown Active?"]
    end
    
    BUY_LOOP --> SAFETY
```

---

### 5. ExecutionManager

İşlem yürütme ve kayıt.

```mermaid
flowchart TB
    subgraph EM["ExecutionManager"]
        BUY_FLOW["execute_buy_flow()"]
        SELL_FLOW["execute_sell_flow()"]
        
        BUY_FLOW --> OPEN_POS["open_position()"]
        OPEN_POS --> EXEC_BUY["OrderExecutor.create_order()"]
        EXEC_BUY --> LOG_BUY["_log_trade_decision()"]
        LOG_BUY --> TELE_BUY["📱 Telegram"]
        
        SELL_FLOW --> CLOSE_POS["close_position()"]
        CLOSE_POS --> EXEC_SELL["OrderExecutor.create_order()"]
        EXEC_SELL --> LOG_SELL["_log_trade_decision()"]
        LOG_SELL --> TELE_SELL["📱 Telegram"]
    end
    
    subgraph FILES["Dosyalar"]
        PORT["portfolio.json"]
        TRADE["trade_log.json"]
    end
    
    OPEN_POS --> PORT
    CLOSE_POS --> PORT
    LOG_BUY --> TRADE
    LOG_SELL --> TRADE
```

---

### 6. ExchangeRouter

WebSocket ve REST API bağlantı yönetimi.

```mermaid
flowchart TB
    subgraph ER["ExchangeRouter"]
        CLIENT["Binance Client"]
        WS["WebSocket Manager"]
        CACHE["Price Cache"]
        
        subgraph METHODS["Metotlar"]
            GET_PRICE["get_price()"]
            GET_ASYNC["get_price_async()"]
            FETCH_24H["fetch_24h_ticker()"]
            START["start_streams()"]
            STOP["stop_streams()"]
        end
    end
    
    subgraph BINANCE["Binance"]
        REST["REST API"]
        WSS["WebSocket Streams"]
    end
    
    START --> WS
    WS <-->|"Fiyat Stream"| WSS
    WS --> CACHE
    GET_PRICE --> CACHE
    GET_ASYNC --> CACHE
    GET_ASYNC -->|Cache Miss| REST
    FETCH_24H --> REST
```

---

### 7. OrderExecutor

Emir oluşturma (Gerçek + Simülasyon).

```mermaid
flowchart TD
    subgraph OE["OrderExecutor"]
        CREATE["create_order()"]
        
        CREATE --> MODE{dry_run?}
        MODE -->|True| SIM["Simülasyon"]
        MODE -->|False| LIVE["Gerçek API"]
        
        SIM --> SLIP["Slippage Simüle"]
        SIM --> FEE["Fee Simüle"]
        SLIP & FEE --> FAKE_RESP["Fake Response"]
        
        LIVE --> RETRY["Retry Logic<br/>(max 3)"]
        RETRY --> API["Binance API"]
        API --> REAL_RESP["Real Response"]
    end
    
    FAKE_RESP & REAL_RESP --> OUTPUT["Order Response"]
```

---

## ⚙️ Konfigürasyon Parametreleri

### 🔐 API Anahtarları (Zorunlu)

| Parametre | Açıklama |
|-----------|----------|
| `BINANCE_API_KEY` | Binance API anahtarı |
| `BINANCE_SECRET_KEY` | Binance gizli anahtar |
| `GEMINI_API_KEY` | Google Gemini AI API anahtarı |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `TELEGRAM_CHAT_ID` | Telegram sohbet ID |

### 🔄 İşlem Modu

| Parametre | Varsayılan | Açıklama |
|-----------|------------|----------|
| `LIVE_TRADING` | `False` | `True` = Gerçek para ile işlem |
| `ALLOW_DANGEROUS_ACTIONS` | `False` | LIVE modda güvenlik kilidi |

### 🤖 AI Eşikleri

| Parametre | Varsayılan | Açıklama |
|-----------|------------|----------|
| `AI_TECH_CONFIDENCE_THRESHOLD` | 70 | BUY için minimum güven |
| `AI_NEWS_CONFIDENCE_THRESHOLD` | 70 | Haber analizi minimum güven |
| `AI_SELL_CONFIDENCE_THRESHOLD` | 75 | SELL için minimum güven |
| `USE_STRATEGY_LLM` | `True` | Strateji LLM aktif mi? |
| `STRATEGY_LLM_MODE` | `"always"` | `"always"` veya `"only_on_signal"` |
| `STRATEGY_LLM_MIN_RULES_CONF` | 65 | LLM çağrısı için minimum kural güveni |

### 📊 Ağırlıklar

| Parametre | Varsayılan | Açıklama |
|-----------|------------|----------|
| `STRATEGY_WEIGHT_MATH` | 0.60 | Math katmanı ağırlığı |
| `STRATEGY_WEIGHT_AI` | 0.40 | AI katmanı ağırlığı |
| `MATH_WEIGHT_TECHNICAL` | 0.80 | Teknik analiz ağırlığı |
| `MATH_WEIGHT_ONCHAIN` | 0.10 | On-chain veri ağırlığı |
| `MATH_WEIGHT_FNG` | 0.10 | Fear & Greed ağırlığı |

### 🛡️ Risk Kontrolleri

| Parametre | Varsayılan | Açıklama |
|-----------|------------|----------|
| `MAX_DAILY_LOSS_PCT` | 8.0% | Günlük maksimum kayıp |
| `MAX_OPEN_POSITIONS` | 5 | Maksimum açık pozisyon |
| `MAX_CONSECUTIVE_LOSSES` | 5 | Ardışık maksimum zarar |
| `COOLDOWN_MINUTES` | 60 | Zarar sonrası bekleme (dk) |
| `RISK_PER_TRADE` | 2.0% | İşlem başına maksimum risk |
| `MIN_VOLUME_GUARDRAIL` | $1M | Minimum 24h hacim |

### 📈 Teknik Eşikler

| Parametre | Varsayılan | Açıklama |
|-----------|------------|----------|
| `MIN_ADX_ENTRY` | 20.0 | Minimum ADX değeri |
| `MIN_ADX_ENTRY_SOFT` | 18.0 | Yüksek güvende yumuşatılmış ADX |
| `FNG_EXTREME_FEAR` | 15 | Extreme fear eşiği |

### 💰 Trading Ayarları

| Parametre | Varsayılan | Açıklama |
|-----------|------------|----------|
| `BASLANGIC_BAKIYE` | $1,000 | Paper trading başlangıç bakiyesi |
| `WATCHLIST` | BTC, ETH, SOL, BNB, XRP, AVAX, LINK | İzlenecek coinler |
| `LOOP_SECONDS` | 900 (15 dk) | Ana döngü süresi |

### 🔒 Kâr Koruma

| Parametre | Varsayılan | Açıklama |
|-----------|------------|----------|
| `PROTECT_PROFITABLE_POSITIONS` | `True` | Kârlı pozisyon koruması |
| `MIN_PROFIT_TO_PROTECT` | 1.5% | Koruma için minimum kâr |
| `AI_SELL_OVERRIDE_CONFIDENCE` | 90% | Korumayı geçen AI güveni |

### ⏱️ Cache & Timeout

| Parametre | Varsayılan | Açıklama |
|-----------|------------|----------|
| `CACHE_TTL_PRICE` | 1 sn | Fiyat cache süresi |
| `CACHE_TTL_TECH` | 15 sn | Teknik veri cache |
| `CACHE_TTL_SENTIMENT` | 90 sn | Sentiment cache |
| `CACHE_TTL_ONCHAIN` | 120 sn | On-chain cache |
| `API_TIMEOUT_DEFAULT` | 10 sn | Genel API timeout |

### 🐕 SL/TP Watchdog

| Parametre | Varsayılan | Açıklama |
|-----------|------------|----------|
| `SLTP_WATCHDOG_ENABLED` | `True` | Watchdog aktif mi? |
| `SLTP_WATCHDOG_INTERVAL_SEC` | 30 sn | Kontrol aralığı |

### 📱 Telegram Bildirimleri

| Parametre | Varsayılan | Açıklama |
|-----------|------------|----------|
| `TELEGRAM_NOTIFY_TRADES` | `True` | Trade bildirimleri |
| `TELEGRAM_NOTIFY_REDDIT` | `False` | Reddit sentiment |
| `TELEGRAM_NOTIFY_ONCHAIN` | `False` | Whale hareketleri |
| `TELEGRAM_NOTIFY_IMPORTANT_NEWS` | `False` | Önemli haberler |

---

## 📊 Veri Akış Diyagramı

```mermaid
flowchart LR
    subgraph EXTERNAL["🌐 Dış Kaynaklar"]
        B["Binance"]
        R["Reddit"]
        E["Etherscan"]
        F["Fear & Greed"]
        RSS["RSS Feeds"]
    end
    
    subgraph COLLECTION["📥 Veri Toplama"]
        MDE["MarketDataEngine"]
    end
    
    subgraph PROCESSING["⚙️ İşleme"]
        SE["StrategyEngine"]
        RM["RiskManager"]
    end
    
    subgraph EXECUTION["📤 Yürütme"]
        EM["ExecutionManager"]
        OE["OrderExecutor"]
    end
    
    subgraph STORAGE["💾 Depolama"]
        PJ["portfolio.json"]
        TL["trade_log.json"]
        LOGS["logs/"]
    end
    
    B --> MDE
    R --> MDE
    E --> MDE
    F --> MDE
    RSS --> MDE
    
    MDE -->|Market Snapshot| SE
    SE -->|Decision| RM
    RM -->|Risk-Approved| EM
    EM --> OE
    
    OE -->|Orders| B
    EM --> PJ
    EM --> TL
    EM --> LOGS
```

---

## 🧪 Yardımcı Araçlar

### Backtest Modülü

```python
from backtest import Backtester
import pandas as pd

# Geçmiş veri yükle
candles = pd.read_csv("btc_1h_2024.csv")

# Backtester başlat
bt = Backtester(candles, starting_balance=1000.0)

# Strateji engine ile test
await bt.run_backtest(strategy_engine, risk_manager)

# Sonuçları görüntüle
bt.print_summary()
```

### Debug Suite

```bash
# Tüm kontrolleri çalıştır
python debug_suite.py

# Sadece belirli kontroller
python debug_suite.py --check imports env binance

# Router testi dahil
python debug_suite.py --with-router
```

---

## 🚀 Hızlı Başlangıç

1. **Bağımlılıkları Kur:**
   ```bash
   pip install -r requirements.txt
   ```

2. **API Anahtarlarını Ayarla (`.env` dosyası):**
   ```env
   BINANCE_API_KEY=your_key
   BINANCE_SECRET_KEY=your_secret
   GEMINI_API_KEY=your_gemini_key
   TELEGRAM_BOT_TOKEN=your_bot_token
   TELEGRAM_CHAT_ID=your_chat_id
   ```

3. **Paper Trading Başlat:**
   ```bash
   python main.py
   ```

4. **(Opsiyonel) Live Trading:**
   ```env
   LIVE_TRADING=1
   ALLOW_DANGEROUS_ACTIONS=1
   ```

> [!CAUTION]
> Live Trading modunu aktifleştirmeden önce Paper Trading ile yeterli süre test yapın ve tüm risk parametrelerini dikkatlice ayarlayın.

---

## 📝 Özet Şeması

```mermaid
mindmap
  root((CBT Bot))
    Veri Kaynakları
      Binance API
      RSS Haberleri
      Reddit API
      Etherscan
      Fear & Greed
    Karar Motoru
      Math Layer 60%
        Teknik 80%
        On-Chain 10%
        Sentiment 10%
      AI Layer 40%
        Gemini LLM
    Risk Yönetimi
      Max Daily Loss
      Max Positions
      Cooldown
      SL/TP
    Yürütme
      Paper Trading
      Live Trading
      Order Retry
    Çıktılar
      Telegram
      Logs
      Portfolio JSON
```

---

*Bu döküman, CBT projesi v1.0 için oluşturulmuştur. Son güncelleme: Aralık 2024*
