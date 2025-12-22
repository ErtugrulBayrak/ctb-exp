# CTB-EXP - AI-Powered Crypto Trading Bot

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
python main.py
```
This runs in **paper trading mode** with a virtual $1,000 balance.

### 5. Run in Live Mode (⚠️ CAUTION)
```env
# .env
LIVE_TRADING=1
ALLOW_DANGEROUS_ACTIONS=1
```
```bash
python main.py
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
├── main.py                 # Entry point - bot initialization
├── config.py               # Configuration & settings management
├── loop_controller.py      # Main trading loop orchestration
├── market_data_engine.py   # Market data fetching & aggregation
├── strategy_engine.py      # AI-powered trading decision engine
├── execution_manager.py    # Trade execution flow management
├── position_manager.py     # Open positions & portfolio tracking
├── risk_manager.py         # Risk controls & kill switches
├── order_executor.py       # Order execution (live/paper)
├── order_ledger.py         # Order history tracking
├── alert_manager.py        # Telegram alert system
├── summary_reporter.py     # Performance reporting
├── trade_logger.py         # Centralized logging
├── llm_utils.py            # LLM response parsing utilities
├── metrics.py              # Performance metrics tracking
├── backtest.py             # Backtesting framework
├── exchange_router.py      # Exchange API routing
├── exit_reason.py          # Exit reason definitions
│
├── strategies/             # Trading strategies
│   ├── swing_trend_v1.py   # Main swing trading strategy
│   ├── regime_filter.py    # Market regime detection
│   └── news_veto.py        # News-based trade veto system
│
├── utils/                  # Utility modules
│   └── io.py               # Safe I/O operations
│
├── data/                   # Runtime data files
│   └── summary_state.json  # Bot state persistence
│
├── logs/                   # Log files
│   └── trader.log          # Rotating log file
│
├── .env                    # Environment variables (DO NOT COMMIT)
├── .env.example            # Example environment file
├── portfolio.json          # Virtual portfolio state
└── requirements.txt        # Python dependencies
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
- **Risk Management**: Automatic SL/TP, position sizing, daily loss limits
- **Alert System**: Real-time Telegram notifications for critical events
- **Regime Detection**: ADX-based market regime filtering
- **News Veto**: LLM-powered news analysis to block risky trades
- **Paper Trading**: Test strategies without real money
- **Backtesting**: Historical strategy validation

## 🧪 Testing

```bash
# Run backtests
python backtest.py

# Run debug suite
python debug_suite.py
```

## 📝 License
MIT License - Use at your own risk.
