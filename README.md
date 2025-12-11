# NewsToMe - AI-Powered Crypto Trading Bot

Hybrid algorithmic trading bot that combines AI analysis, technical indicators, on-chain data, and sentiment analysis for cryptocurrency trading decisions.

## ⚡ Quick Start

### Requirements
- **Python**: 3.10+
- **OS**: Windows / Linux / macOS

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure Environment
```bash
# Copy example env file
cp .env.example .env

# Or create .env manually with required variables
```

### 3. Required Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `BINANCE_API_KEY` | Binance API key | ✅ Yes |
| `BINANCE_SECRET_KEY` | Binance secret key | ✅ Yes |
| `GEMINI_API_KEY` | Google Gemini AI API key | ✅ Yes |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token for notifications | ✅ Yes |
| `TELEGRAM_CHAT_ID` | Telegram chat ID | ✅ Yes |
| `LIVE_TRADING` | `0` = Paper trading, `1` = Real trading | ⚠️ Default: 0 |
| `ALLOW_DANGEROUS_ACTIONS` | Required `1` to enable live trading | ⚠️ Default: 0 |

### 4. Run in Simulation Mode (Recommended)
```bash
python scraper-v90.py
```
This runs in **paper trading mode** with a virtual $1,000 balance.

### 5. Run in Live Mode (⚠️ CAUTION)
```env
# .env
LIVE_TRADING=1
ALLOW_DANGEROUS_ACTIONS=1
```
```bash
python scraper-v90.py
```

## ⚠️ Important Warnings

> **🔴 NEVER commit API keys to version control!**  
> Add `.env` to your `.gitignore` file.

> **🔴 Always run backtests before enabling `LIVE_TRADING`!**  
> Use `backtest.py` to test strategies on historical data.

> **🔴 Live trading uses REAL MONEY!**  
> Start with small amounts and monitor closely.

## 📁 Project Structure

```
├── scraper-v90.py      # Main trading bot
├── config.py           # Configuration management
├── order_executor.py   # Order execution (live/dry run)
├── trade_logger.py     # Centralized logging
├── backtest.py         # Backtesting framework
├── .env                # Environment variables (DO NOT COMMIT)
├── requirements.txt    # Python dependencies
└── logs/
    └── trader.log      # Rotating log file
```

## 🔧 Configuration

Edit `.env` or use environment variables:

```env
# Mode
LIVE_TRADING=0
ALLOW_DANGEROUS_ACTIONS=0

# API Keys
BINANCE_API_KEY=your_key
BINANCE_SECRET_KEY=your_secret
GEMINI_API_KEY=your_gemini_key
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# Optional: AI Thresholds
AI_TECH_CONFIDENCE_THRESHOLD=75
AI_NEWS_CONFIDENCE_THRESHOLD=80
AI_SELL_CONFIDENCE_THRESHOLD=70
```

## 📊 Features

- **AI-Powered Decisions**: Google Gemini analyzes market conditions
- **Multi-Source Data**: Technical analysis, on-chain data, news, Reddit sentiment
- **Risk Management**: Automatic SL/TP, position sizing, trend filters
- **Profit Protection**: Prevents AI from selling profitable positions prematurely
- **Telegram Alerts**: Real-time notifications for trades
- **Paper Trading**: Test strategies without real money
- **Backtesting**: Historical strategy validation

## 📝 License
MIT License - Use at your own risk.
