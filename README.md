# CTB-EXP - Crypto Trading Bot

Hybrid Multi-Timeframe V2 stratejisi kullanan otomatik kripto trading botu. Binance borsasında çalışır.

## ⚡ Hızlı Başlangıç

### Gereksinimler
- **Python**: 3.10+
- **OS**: Windows / Linux / macOS

### Kurulum

```bash
# Bağımlılıkları yükle
pip install -r requirements.txt

# .env dosyasını oluştur
cp .env.example .env
# .env dosyasını API anahtarlarınla düzenle
```

### Ortam Değişkenleri

| Değişken | Açıklama | Zorunlu |
|----------|----------|---------|
| `BINANCE_API_KEY` | Binance API key | ✅ |
| `BINANCE_SECRET_KEY` | Binance secret key | ✅ |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token | ✅ |
| `TELEGRAM_CHAT_ID` | Telegram chat ID | ✅ |
| `GEMINI_API_KEY` | Google Gemini API key | ✅ |

### Çalıştırma

```bash
# Paper Trading (varsayılan - önerilen)
python main.py

# Live Trading (⚠️ DİKKAT: Gerçek para!)
RUN_PROFILE=live ALLOW_DANGEROUS_ACTIONS=1 python main.py
```

## 🤖 Strateji

Bot **Hybrid V2** stratejisi kullanır:
- **Multi-Timeframe**: 4H swing + 1H momentum analizi
- **Rejim Adaptasyonu**: Piyasa koşullarına göre ayarlama
- **V2 Exit Logic**: Entry tipine özel çıkış kuralları

> Detaylı bilgi için [PROJECT_OVERVIEW.md](./PROJECT_OVERVIEW.md) dosyasına bakın.

## 📁 Yapı

```
├── main.py              # Giriş noktası
├── loop_controller.py   # Ana trading döngüsü
├── strategies/          # Strateji modülleri
│   └── hybrid_multi_tf_v2.py
├── position_manager.py  # V2 exit logic
├── config.py            # Ayarlar
└── data/                # Portföy ve loglar
```

## ⚠️ Uyarılar

> **🔴 API anahtarlarını asla Git'e commit etmeyin!**

> **🔴 Live trading gerçek para kullanır!** Paper mode ile başlayın.

## 📝 Lisans

MIT License - Riski size ait.
