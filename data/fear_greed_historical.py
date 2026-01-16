"""
fear_greed_historical.py - Historical Fear & Greed Index Data
==============================================================

Fetches historical Fear & Greed Index from Alternative.me API
for use in backtesting.

Usage:
------
from data.fear_greed_historical import FearGreedHistorical

fg = FearGreedHistorical()
await fg.fetch_history(days=365)
value = fg.get_value_for_date("2025-01-15")
"""

import os
import json
from datetime import datetime, timedelta
from typing import Dict, Optional
import pandas as pd

try:
    import aiohttp
except ImportError:
    aiohttp = None


class FearGreedHistorical:
    """
    Historical Fear & Greed Index data manager.
    """
    
    API_URL = "https://api.alternative.me/fng/?limit={limit}&format=json"
    CACHE_FILE = "data/fear_greed_cache.json"
    
    def __init__(self, cache_dir: str = "data"):
        """Initialize Fear & Greed historical data manager."""
        self.cache_dir = cache_dir
        self.cache_file = os.path.join(cache_dir, "fear_greed_cache.json")
        self.data: Dict[str, int] = {}
        self._load_cache()
    
    def _load_cache(self) -> None:
        """Load cached F&G data from disk."""
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r') as f:
                    self.data = json.load(f)
            except Exception:
                self.data = {}
    
    def _save_cache(self) -> None:
        """Save F&G data to disk cache."""
        os.makedirs(self.cache_dir, exist_ok=True)
        try:
            with open(self.cache_file, 'w') as f:
                json.dump(self.data, f)
        except Exception as e:
            print(f"⚠️ Failed to save F&G cache: {e}")
    
    async def fetch_history(self, days: int = 365) -> bool:
        """
        Fetch historical Fear & Greed data from API.
        
        Args:
            days: Number of days to fetch
        
        Returns:
            True if successful
        """
        if not aiohttp:
            print("⚠️ aiohttp not installed, using cached data only")
            return False
        
        url = self.API_URL.format(limit=days)
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=30) as response:
                    if response.status != 200:
                        print(f"⚠️ F&G API returned {response.status}")
                        return False
                    
                    result = await response.json()
                    
                    if "data" not in result:
                        print("⚠️ Invalid F&G API response")
                        return False
                    
                    for entry in result["data"]:
                        timestamp = int(entry["timestamp"])
                        date_str = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
                        value = int(entry["value"])
                        self.data[date_str] = value
                    
                    self._save_cache()
                    print(f"✅ Fetched {len(result['data'])} days of F&G data")
                    return True
                    
        except Exception as e:
            print(f"⚠️ Failed to fetch F&G data: {e}")
            return False
    
    def fetch_history_sync(self, days: int = 365) -> bool:
        """
        Synchronous version of fetch_history using requests.
        
        Args:
            days: Number of days to fetch
        
        Returns:
            True if successful
        """
        try:
            import requests
        except ImportError:
            print("⚠️ requests not installed")
            return False
        
        url = self.API_URL.format(limit=days)
        
        try:
            response = requests.get(url, timeout=30)
            if response.status_code != 200:
                print(f"⚠️ F&G API returned {response.status_code}")
                return False
            
            result = response.json()
            
            if "data" not in result:
                print("⚠️ Invalid F&G API response")
                return False
            
            for entry in result["data"]:
                timestamp = int(entry["timestamp"])
                date_str = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
                value = int(entry["value"])
                self.data[date_str] = value
            
            self._save_cache()
            print(f"✅ Fetched {len(result['data'])} days of F&G data")
            return True
            
        except Exception as e:
            print(f"⚠️ Failed to fetch F&G data: {e}")
            return False
    
    def get_value_for_date(self, date: str) -> int:
        """
        Get Fear & Greed value for a specific date.
        
        Args:
            date: Date string (YYYY-MM-DD) or datetime
        
        Returns:
            F&G value (0-100) or 50 (neutral) if not found
        """
        if isinstance(date, datetime):
            date_str = date.strftime("%Y-%m-%d")
        elif isinstance(date, pd.Timestamp):
            date_str = date.strftime("%Y-%m-%d")
        else:
            # Parse if timestamp string
            try:
                if " " in str(date):
                    date_str = str(date).split(" ")[0]
                else:
                    date_str = str(date)
            except:
                date_str = str(date)
        
        return self.data.get(date_str, 50)  # Default to neutral
    
    def get_dataframe(self) -> pd.DataFrame:
        """
        Get F&G data as DataFrame.
        
        Returns:
            DataFrame with date and value columns
        """
        if not self.data:
            return pd.DataFrame(columns=["date", "value"])
        
        df = pd.DataFrame([
            {"date": k, "value": v}
            for k, v in self.data.items()
        ])
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        return df


# CLI
if __name__ == "__main__":
    fg = FearGreedHistorical()
    
    print("📥 Fetching Fear & Greed historical data...")
    success = fg.fetch_history_sync(days=365)
    
    if success:
        df = fg.get_dataframe()
        print(f"\n📊 Data range: {df['date'].min()} to {df['date'].max()}")
        print(f"   Average value: {df['value'].mean():.1f}")
        print(f"   Min: {df['value'].min()}, Max: {df['value'].max()}")
    else:
        print("❌ Failed to fetch data")
