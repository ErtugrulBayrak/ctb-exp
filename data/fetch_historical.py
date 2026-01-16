"""
fetch_historical.py - Historical OHLCV Data Fetcher
====================================================

Binance'ten geçmiş mum verilerini çeker ve CSV olarak kaydeder.
Backtest için kullanılır.

Kullanım:
---------
# Komut satırından:
python data/fetch_historical.py --symbols BTCUSDT ETHUSDT --days 90

# Python'dan:
from data.fetch_historical import fetch_ohlcv_csv
await fetch_ohlcv_csv(["BTCUSDT"], timeframes=["4h", "1h", "15m"], days=30)

Gereksinimler:
--------------
pip install ccxt pandas
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import pandas as pd

# CCXT import
try:
    import ccxt.async_support as ccxt
except ImportError:
    print("❌ CCXT kütüphanesi gerekli: pip install ccxt")
    ccxt = None


# ═══════════════════════════════════════════════════════════════════════════════
# TIMEFRAME MAPPINGS
# ═══════════════════════════════════════════════════════════════════════════════

TIMEFRAME_MS = {
    "1m": 60 * 1000,
    "5m": 5 * 60 * 1000,
    "15m": 15 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
    "1d": 24 * 60 * 60 * 1000,
    "1w": 7 * 24 * 60 * 60 * 1000
}


# ═══════════════════════════════════════════════════════════════════════════════
# FETCHER CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class HistoricalDataFetcher:
    """
    CCXT kullanarak Binance'ten geçmiş OHLCV verisi çeker.
    """
    
    def __init__(self, exchange_id: str = "binance"):
        """
        Initialize fetcher.
        
        Args:
            exchange_id: CCXT exchange ID (default: binance)
        """
        if not ccxt:
            raise ImportError("CCXT not installed")
        
        self.exchange_id = exchange_id
        self.exchange = None
    
    async def __aenter__(self):
        """Async context manager entry."""
        self.exchange = getattr(ccxt, self.exchange_id)({
            'enableRateLimit': True,
            'options': {'defaultType': 'future'}  # Use futures for better data
        })
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self.exchange:
            await self.exchange.close()
    
    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        since: int,
        limit: int = 1000
    ) -> List[List]:
        """
        Fetch OHLCV data from exchange.
        
        Args:
            symbol: Trading pair (e.g., "BTCUSDT")
            timeframe: Candle timeframe (e.g., "4h")
            since: Start timestamp in ms
            limit: Max candles per request
        
        Returns:
            List of OHLCV candles
        """
        try:
            ohlcv = await self.exchange.fetch_ohlcv(
                symbol, timeframe, since=since, limit=limit
            )
            return ohlcv
        except Exception as e:
            print(f"⚠️ Fetch error for {symbol} {timeframe}: {e}")
            return []
    
    async def fetch_all_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        days: int = 90
    ) -> pd.DataFrame:
        """
        Fetch all OHLCV data for a period.
        
        Args:
            symbol: Trading pair
            timeframe: Candle timeframe
            days: Number of days to fetch
        
        Returns:
            DataFrame with OHLCV data
        """
        tf_ms = TIMEFRAME_MS.get(timeframe, 60000)
        end_ts = int(datetime.now().timestamp() * 1000)
        start_ts = end_ts - (days * 24 * 60 * 60 * 1000)
        
        all_candles = []
        current_ts = start_ts
        
        print(f"📥 Fetching {symbol} {timeframe} ({days} days)...")
        
        while current_ts < end_ts:
            candles = await self.fetch_ohlcv(symbol, timeframe, current_ts)
            
            if not candles:
                break
            
            all_candles.extend(candles)
            
            # Move to next batch
            current_ts = candles[-1][0] + tf_ms
            
            # Rate limit protection
            await asyncio.sleep(0.1)
        
        if not all_candles:
            return pd.DataFrame()
        
        # Convert to DataFrame
        df = pd.DataFrame(all_candles, columns=[
            "timestamp", "open", "high", "low", "close", "volume"
        ])
        
        # Convert timestamp to datetime
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        
        # Remove duplicates
        df = df.drop_duplicates(subset=["timestamp"]).reset_index(drop=True)
        
        print(f"✅ {symbol} {timeframe}: {len(df)} candles")
        
        return df


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

async def fetch_ohlcv_csv(
    symbols: List[str],
    timeframes: List[str] = ["4h", "1h", "15m"],
    days: int = 90,
    output_dir: str = "data"
) -> Dict[str, Dict[str, str]]:
    """
    Fetch OHLCV data and save to CSV files.
    
    Args:
        symbols: List of trading pairs
        timeframes: List of timeframes to fetch
        days: Number of days of history
        output_dir: Directory to save CSV files
    
    Returns:
        Dict mapping symbol -> timeframe -> csv_path
    """
    os.makedirs(output_dir, exist_ok=True)
    
    result = {}
    
    async with HistoricalDataFetcher() as fetcher:
        for symbol in symbols:
            result[symbol] = {}
            
            for tf in timeframes:
                df = await fetcher.fetch_all_ohlcv(symbol, tf, days)
                
                if df.empty:
                    print(f"⚠️ No data for {symbol} {tf}")
                    continue
                
                # Save to CSV
                filename = f"{symbol.replace('/', '_')}_{tf}_{days}d.csv"
                filepath = os.path.join(output_dir, filename)
                df.to_csv(filepath, index=False)
                
                result[symbol][tf] = filepath
                print(f"💾 Saved: {filepath}")
    
    return result


async def load_multi_tf_data(
    base_symbol: str,
    timeframes: List[str] = ["15m", "1h", "4h"],
    data_dir: str = "data"
) -> Dict[str, pd.DataFrame]:
    """
    Load multi-timeframe CSV data for backtesting.
    
    Args:
        base_symbol: Symbol to load (e.g., "BTCUSDT")
        timeframes: Timeframes to load
        data_dir: Directory containing CSV files
    
    Returns:
        Dict mapping timeframe -> DataFrame
    """
    result = {}
    
    for tf in timeframes:
        # Try to find matching file
        pattern = f"{base_symbol}_{tf}_"
        
        for filename in os.listdir(data_dir):
            if filename.startswith(pattern) and filename.endswith(".csv"):
                filepath = os.path.join(data_dir, filename)
                df = pd.read_csv(filepath)
                
                # Parse timestamp
                if "timestamp" in df.columns:
                    df["timestamp"] = pd.to_datetime(df["timestamp"])
                
                result[tf] = df
                print(f"📂 Loaded: {filepath} ({len(df)} rows)")
                break
    
    return result


def create_synthetic_data(n_bars: int = 1000) -> Dict[str, pd.DataFrame]:
    """
    Create synthetic multi-TF data for testing.
    
    Args:
        n_bars: Number of 15m bars (base timeframe)
    
    Returns:
        Dict mapping timeframe -> DataFrame
    """
    import numpy as np
    
    np.random.seed(42)
    
    # Generate 15m data
    start_price = 95000.0
    prices = [start_price]
    
    for _ in range(n_bars - 1):
        change = np.random.normal(0, 50)  # ~$50 std dev per 15m
        new_price = max(prices[-1] + change, 80000)
        prices.append(new_price)
    
    timestamps = pd.date_range(
        end=datetime.now(),
        periods=n_bars,
        freq="15min"
    )
    
    df_15m = pd.DataFrame({
        "timestamp": timestamps,
        "open": [p * (1 - np.random.uniform(0, 0.001)) for p in prices],
        "high": [p * (1 + np.random.uniform(0, 0.005)) for p in prices],
        "low": [p * (1 - np.random.uniform(0, 0.005)) for p in prices],
        "close": prices,
        "volume": [np.random.uniform(100, 500) for _ in prices]
    })
    
    # Resample to higher timeframes
    def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
        df = df.set_index("timestamp")
        resampled = df.resample(rule).agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum"
        }).dropna().reset_index()
        return resampled
    
    df_1h = resample_ohlcv(df_15m.copy(), "1h")
    df_4h = resample_ohlcv(df_15m.copy(), "4h")
    df_1d = resample_ohlcv(df_15m.copy(), "1D")
    
    print(f"📊 Synthetic data created:")
    print(f"   15m: {len(df_15m)} bars")
    print(f"   1h: {len(df_1h)} bars")
    print(f"   4h: {len(df_4h)} bars")
    print(f"   1d: {len(df_1d)} bars")
    
    return {
        "15m": df_15m,
        "1h": df_1h,
        "4h": df_4h,
        "1d": df_1d
    }


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Fetch historical OHLCV data")
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT"],
                       help="Trading pairs to fetch")
    parser.add_argument("--timeframes", nargs="+", default=["4h", "1h", "15m"],
                       help="Timeframes to fetch")
    parser.add_argument("--days", type=int, default=90,
                       help="Days of history to fetch")
    parser.add_argument("--output", default="data",
                       help="Output directory")
    parser.add_argument("--synthetic", action="store_true",
                       help="Create synthetic data instead of fetching")
    
    args = parser.parse_args()
    
    if args.synthetic:
        print("\n🧪 Creating synthetic data...")
        data = create_synthetic_data(n_bars=1000)
        
        os.makedirs(args.output, exist_ok=True)
        for tf, df in data.items():
            filepath = os.path.join(args.output, f"SYNTHETIC_{tf}_test.csv")
            df.to_csv(filepath, index=False)
            print(f"💾 Saved: {filepath}")
    else:
        print(f"\n📥 Fetching {args.symbols} for {args.days} days...")
        asyncio.run(fetch_ohlcv_csv(
            symbols=args.symbols,
            timeframes=args.timeframes,
            days=args.days,
            output_dir=args.output
        ))
    
    print("\n✅ Done!")
