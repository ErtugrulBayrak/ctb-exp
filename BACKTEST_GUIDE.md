# HYBRID V2 Backtest Kullanım Kılavuzu

Bu kılavuz, trading botunun HYBRID V2 stratejisini geçmiş veriler üzerinde test etmek için `backtest.py` modülünün nasıl kullanılacağını açıklar.

---

## 🚀 Hızlı Başlangıç

### Adım 1: Geçmiş Veri İndir

```powershell
cd "c:\Users\useit\15-10-proje - GPT-V1"

# BTC 90 günlük veri
python data\fetch_historical.py --symbols BTCUSDT --days 90

# Birden fazla coin (opsiyonel)
python data\fetch_historical.py --symbols BTCUSDT ETHUSDT SOLUSDT --days 60
```

İndirilen dosyalar `data/` klasörüne kaydedilir.

### Adım 2: Backtest Çalıştır

```python
from backtest import Backtester
import pandas as pd

# CSV dosyalarını yükle
df_15m = pd.read_csv('data/BTCUSDT_15m_90d.csv')
df_1h = pd.read_csv('data/BTCUSDT_1h_90d.csv')   # Opsiyonel - otomatik resampling var
df_4h = pd.read_csv('data/BTCUSDT_4h_90d.csv')   # Opsiyonel - otomatik resampling var

# Multi-TF dict oluştur
multi_tf_data = {'15m': df_15m, '1h': df_1h, '4h': df_4h}

# Backtest başlat
bt = Backtester(df_15m, starting_balance=10000.0, fee_pct=0.001)
bt.run_v2_backtest(multi_tf_data, symbol='BTC', starting_balance=10000)
bt.print_v2_summary()
```

> **Not:** Sadece 15m veri yeterli - diğer timeframe'ler otomatik olarak oluşturulur.

---

## 📊 Çıktı Formatı

### Genel Özet

```
==================================================
📊 BACKTEST SONUÇLARI
==================================================
Başlangıç Bakiye:  $10,000.00
Bitiş Bakiye:      $10,850.00
Toplam Getiri:     +8.50%
Toplam PnL:        $+850.00
--------------------------------------------------
Toplam İşlem:      15
Kazançlı:          10
Zararlı:           5
Win Rate:          66.7%
==================================================
```

### V2 Entry Type Breakdown

```
──────────────────────────────────────────────────
📊 V2 ENTRY TYPE BREAKDOWN
──────────────────────────────────────────────────
  4H_SWING:
    Entries: 5 | Wins: 4 | Losses: 1 | Partial TPs: 3
    Win Rate: 80.0% | PnL: $+500.00
  1H_MOMENTUM:
    Entries: 8 | Wins: 5 | Losses: 3 | Partial TPs: 5
    Win Rate: 62.5% | PnL: $+300.00
  15M_SCALP:
    Entries: 2 | Wins: 1 | Losses: 1 | Partial TPs: 0
    Win Rate: 50.0% | PnL: $+50.00
──────────────────────────────────────────────────
  Total Signals: 15
  Signals Skipped: 3

📈 REGIME DISTRIBUTION:
    STRONG_TREND: 450
    WEAK_TREND: 380
    VOLATILE: 120
    RANGING: 50
==================================================
```

---

## 🔍 Trade Analizi

### Tüm Trade'leri Al

```python
trades = bt.get_trades()
for t in trades[:5]:
    print(f"{t['side']} @ ${t['price']:.2f} | PnL: ${t['pnl']:.2f}")
```

### Partial TP'leri Filtrele

```python
partial_trades = [t for t in bt.get_trades() if t['side'] == 'SELL_PARTIAL']
print(f"Partial TPs: {len(partial_trades)}")
```

### Sonuç Dict'ini Al

```python
results = bt.results()
print(f"Return: {results['return_pct']:.2f}%")
print(f"Win Rate: {results['win_rate']:.1f}%")
```

---

## ⚙️ Exit Mantığı (Position Manager ile Senkron)

Backtest, `position_manager.py` ile aynı exit mantığını kullanır:

| Entry Type | Partial TP | Final Target | Trailing Stop | Time Exit |
|------------|------------|--------------|---------------|-----------|
| 4H_SWING | %5'te %50 sat | %10 | ATR×2.5 | 10 gün |
| 1H_MOMENTUM | %2'de %50 sat | %4 | ATR×1.8 | 24 saat |
| 15M_SCALP | Yok | %1.5 | Yok | 4 saat |

> Exit parametreleri `config.py`'den okunur.

---

## ⚙️ Strateji Entry Koşulları

### 4H_SWING (En Sıkı)

- Regime: STRONG_TREND veya WEAK_TREND
- 4h EMA: EMA20 > EMA50 > EMA200
- 4h ADX > 25
- Fiyat 4h EMA20'ye ±%2 yakın (pullback)
- 1h RSI > 50 veya MACD crossover

### 1H_MOMENTUM (Orta)

- Regime: STRONG_TREND, WEAK_TREND veya VOLATILE
- 4h trend aligned (EMA20 > EMA50)
- 1h RSI 55-70 arası
- 1h MACD histogram expanding
- 1h Volume > 1.2× ortalama

### 15M_SCALP (En Gevşek)

- Regime: Sadece STRONG_TREND
- 4h ve 1h trendler aligned
- 15m Bollinger squeeze
- 15m Volume > 2× ortalama

---

## 🔧 Sorun Giderme

### "0 trade" çıkıyorsa

- Sentetik veri strateji koşullarını karşılamıyor (normal)
- Gerçek Binance verisi kullanın
- Regime dağılımını kontrol edin (STRONG_TREND gerekli)

### Import hatası

```powershell
pip install pandas ccxt pandas_ta
```

### Selftest Çalıştır

```powershell
python backtest.py --selftest
```

---

## 📁 Dosya Yapısı

```
15-10-proje - GPT-V1/
├── backtest.py              # Ana backtest modülü
├── config.py                # Exit parametreleri
├── position_manager.py      # Canlı exit mantığı (backtest bunu kopyalar)
├── data/
│   ├── fetch_historical.py  # Veri çekme utility
│   └── BTCUSDT_15m_90d.csv  # İndirilen veriler
└── strategies/
    ├── hybrid_multi_tf_v2.py  # V2 strateji
    └── regime_detector.py     # Regime tespiti
```
