"""
backtest.py - Minimal Backtesting Framework
============================================

Bu modül geçmiş mum verileri üzerinde strateji testi yapmak için kullanılır.
Basit, senkron ve bağımsız çalışır (LLM veya canlı API gerektirmez).

Kullanım Örneği:
---------------

```python
import pandas as pd
from backtest import Backtester

# Geçmiş mum verilerini yükle
candles = pd.read_csv("btc_4h_candles.csv")

# Backtester oluştur
bt = Backtester(candles, starting_balance=1000.0)

# Basit strateji: RSI < 30 ise BUY, RSI > 70 ise SELL
def my_signal(row):
    if row.get('rsi', 50) < 30:
        return ("BUY", 0.1)  # %10 bakiye ile al
    elif row.get('rsi', 50) > 70:
        return ("SELL", 1.0)  # Tüm pozisyonu sat
    return (None, 0)

# Backtest çalıştır
bt.run_simple_strategy(my_signal)

# Sonuçları al
results = bt.results()
print(f"Final Balance: ${results['ending_balance']:.2f}")
print(f"Total Trades: {results['total_trades']}")
print(f"PnL: ${results['cumulative_pnl']:.2f}")
```

sanal_alim_yap Mantığını Entegre Etme:
-------------------------------------
Mevcut sanal_alim_yap mantığını (teknik analiz, AI kararları) backtest'e 
entegre etmek için:

```python
def trading_signal(row):
    price = row['close']
    
    # Teknik analiz (RSI, EMA, MACD vb.) row'dan alınır
    rsi = row.get('rsi', 50)
    ema_50 = row.get('ema_50', price)
    ema_200 = row.get('ema_200', price)
    
    # Yükseliş trendi + RSI nötr = BUY sinyali
    if ema_50 > ema_200 and rsi < 70:
        atr = row.get('atr', price * 0.02)
        risk_pct = 0.02  # %2 risk
        return ("BUY", risk_pct)
    
    # Düşüş trendi veya aşırı alım = SELL
    if ema_50 < ema_200 or rsi > 75:
        return ("SELL", 1.0)
    
    return (None, 0)
```
"""

import pandas as pd
from dataclasses import dataclass, field
from typing import Callable, List, Dict, Any, Optional, Tuple
from datetime import datetime


@dataclass
class Trade:
    """Tek bir trade kaydı."""
    timestamp: str
    side: str  # "BUY" veya "SELL"
    price: float
    quantity: float
    cost: float
    pnl: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "side": self.side,
            "price": self.price,
            "quantity": self.quantity,
            "cost": self.cost,
            "pnl": self.pnl
        }


class Backtester:
    """
    Minimal backtesting engine.
    
    Geçmiş mum verilerini kullanarak strateji testi yapar.
    Senkron ve bağımsız çalışır.
    
    Attributes:
        candles: pandas DataFrame (columns: timestamp/date, open, high, low, close, volume)
        starting_balance: Başlangıç bakiyesi (USDT)
        balance: Mevcut bakiye
        position: Açık pozisyon miktarı (coin cinsinden)
        position_cost: Pozisyonun toplam maliyeti
        trades: Gerçekleştirilen trade listesi
    """
    
    def __init__(
        self,
        candles: pd.DataFrame,
        starting_balance: float = 1000.0,
        fee_pct: float = 0.001  # %0.1 işlem ücreti
    ):
        """
        Backtester'ı başlat.
        
        Args:
            candles: Mum verileri DataFrame'i. 
                     Gerekli kolonlar: close (veya price)
                     Opsiyonel: timestamp, open, high, low, volume
            starting_balance: Başlangıç bakiyesi. Default: 1000.0
            fee_pct: İşlem ücreti yüzdesi. Default: 0.001 (%0.1)
        """
        self.candles = candles.copy()
        self.starting_balance = starting_balance
        self.fee_pct = fee_pct
        
        # State
        self.balance = starting_balance
        self.position = 0.0  # Coin miktarı
        self.position_avg_price = 0.0  # Ortalama giriş fiyatı
        self.position_cost = 0.0  # Toplam maliyet
        self.trades: List[Trade] = []
        
        # Stats
        self.winning_trades = 0
        self.losing_trades = 0
        self.cumulative_pnl = 0.0
    
    def _get_price(self, row: pd.Series) -> float:
        """Row'dan fiyat al (close, price veya Close)."""
        for col in ['close', 'Close', 'price', 'Price']:
            if col in row.index:
                return float(row[col])
        raise ValueError("DataFrame'de 'close' veya 'price' kolonu bulunamadı!")
    
    def _get_timestamp(self, row: pd.Series) -> str:
        """Row'dan timestamp al."""
        for col in ['timestamp', 'Timestamp', 'date', 'Date', 'time', 'Time']:
            if col in row.index:
                return str(row[col])
        return str(row.name)  # Index'i kullan
    
    def run_simple_strategy(
        self,
        signal_fn: Callable[[pd.Series], Tuple[Optional[str], float]]
    ) -> None:
        """
        Basit strateji backtesti çalıştır.
        
        Kronolojik sırayla her mum için signal_fn çağırır.
        
        Args:
            signal_fn: Sinyal fonksiyonu.
                      Input: pandas Series (mum row'u)
                      Output: (action, fraction)
                        - action: "BUY", "SELL" veya None
                        - fraction: BUY için bakiye yüzdesi (0-1),
                                   SELL için pozisyon yüzdesi (0-1)
        
        Example:
            def signal(row):
                if row['rsi'] < 30:
                    return ("BUY", 0.5)  # Bakiyenin %50'si ile al
                elif row['rsi'] > 70:
                    return ("SELL", 1.0)  # Tüm pozisyonu sat
                return (None, 0)
            
            bt.run_simple_strategy(signal)
        """
        for idx, row in self.candles.iterrows():
            price = self._get_price(row)
            timestamp = self._get_timestamp(row)
            
            # Sinyal al
            action, fraction = signal_fn(row)
            
            if action == "BUY" and fraction > 0:
                self._execute_buy(price, fraction, timestamp)
            elif action == "SELL" and fraction > 0 and self.position > 0:
                self._execute_sell(price, fraction, timestamp)
    
    def _execute_buy(self, price: float, fraction: float, timestamp: str) -> None:
        """BUY emri uygula."""
        # Bakiyenin fraction kadarını kullan
        available = self.balance * min(fraction, 1.0)
        
        if available < 1.0:  # Minimum $1
            return
        
        # Fee düş
        fee = available * self.fee_pct
        net_amount = available - fee
        
        # Coin miktarını hesapla
        quantity = net_amount / price
        
        # Pozisyon güncelle
        total_cost = self.position_cost + available
        total_quantity = self.position + quantity
        
        if total_quantity > 0:
            self.position_avg_price = total_cost / total_quantity
        
        self.position = total_quantity
        self.position_cost = total_cost
        self.balance -= available
        
        # Trade kaydet
        trade = Trade(
            timestamp=timestamp,
            side="BUY",
            price=price,
            quantity=quantity,
            cost=available
        )
        self.trades.append(trade)
    
    def _execute_sell(self, price: float, fraction: float, timestamp: str) -> None:
        """SELL emri uygula."""
        # Pozisyonun fraction kadarını sat
        quantity = self.position * min(fraction, 1.0)
        
        if quantity <= 0:
            return
        
        # Satış geliri
        gross = quantity * price
        fee = gross * self.fee_pct
        net = gross - fee
        
        # PnL hesapla
        cost_basis = (quantity / self.position) * self.position_cost if self.position > 0 else 0
        pnl = net - cost_basis
        
        # Bakiye ve pozisyon güncelle
        self.balance += net
        self.position -= quantity
        self.position_cost -= cost_basis
        
        if self.position < 0.0001:  # Küçük kalan miktarları temizle
            self.position = 0
            self.position_cost = 0
            self.position_avg_price = 0
        
        # Stats güncelle
        self.cumulative_pnl += pnl
        if pnl > 0:
            self.winning_trades += 1
        else:
            self.losing_trades += 1
        
        # Trade kaydet
        trade = Trade(
            timestamp=timestamp,
            side="SELL",
            price=price,
            quantity=quantity,
            cost=0,
            pnl=pnl
        )
        self.trades.append(trade)
    
    def results(self) -> Dict[str, Any]:
        """
        Backtest sonuçlarını döndür.
        
        Returns:
            Dict containing:
                - starting_balance: Başlangıç bakiyesi
                - ending_balance: Son bakiye (pozisyon dahil)
                - cumulative_pnl: Toplam PnL
                - total_trades: Toplam işlem sayısı
                - winning_trades: Kazançlı işlem sayısı
                - losing_trades: Zararlı işlem sayısı
                - win_rate: Kazanma oranı (%)
                - open_position: Açık pozisyon miktarı
                - open_position_value: Açık pozisyon değeri
        """
        # Son fiyat
        if len(self.candles) > 0:
            last_row = self.candles.iloc[-1]
            last_price = self._get_price(last_row)
            open_position_value = self.position * last_price
        else:
            last_price = 0
            open_position_value = 0
        
        ending_balance = self.balance + open_position_value
        total_trades = self.winning_trades + self.losing_trades
        win_rate = (self.winning_trades / total_trades * 100) if total_trades > 0 else 0
        
        return {
            "starting_balance": self.starting_balance,
            "ending_balance": round(ending_balance, 2),
            "cumulative_pnl": round(self.cumulative_pnl, 2),
            "return_pct": round((ending_balance / self.starting_balance - 1) * 100, 2),
            "total_trades": total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": round(win_rate, 1),
            "open_position": round(self.position, 8),
            "open_position_value": round(open_position_value, 2),
            "last_price": last_price
        }
    
    def get_trades(self) -> List[Dict[str, Any]]:
        """Tüm trade'leri liste olarak döndür."""
        return [t.to_dict() for t in self.trades]
    
    def print_summary(self) -> None:
        """Özet sonuçları yazdır."""
        r = self.results()
        
        print("\n" + "=" * 50)
        print("📊 BACKTEST SONUÇLARI")
        print("=" * 50)
        print(f"Başlangıç Bakiye:  ${r['starting_balance']:,.2f}")
        print(f"Bitiş Bakiye:      ${r['ending_balance']:,.2f}")
        print(f"Toplam Getiri:     {r['return_pct']:+.2f}%")
        print(f"Toplam PnL:        ${r['cumulative_pnl']:+,.2f}")
        print("-" * 50)
        print(f"Toplam İşlem:      {r['total_trades']}")
        print(f"Kazançlı:          {r['winning_trades']}")
        print(f"Zararlı:           {r['losing_trades']}")
        print(f"Win Rate:          {r['win_rate']:.1f}%")
        print("-" * 50)
        print(f"Açık Pozisyon:     {r['open_position']:.6f}")
        print(f"Pozisyon Değeri:   ${r['open_position_value']:,.2f}")
        print("=" * 50 + "\n")


# ═══════════════════════════════════════════════════════════════════════════════
# DEMO / UNIT TEST
# ═══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import numpy as np
    
    print("\n" + "=" * 60)
    print("🧪 BACKTESTER DEMO")
    print("=" * 60)
    
    # Sentetik veri oluştur (100 mum, fiyat rastgele yürüyüş)
    np.random.seed(42)
    n_candles = 100
    
    # Fiyat simülasyonu: başlangıç $100, rastgele yürüyüş
    prices = [100.0]
    for i in range(n_candles - 1):
        change = np.random.normal(0, 2)  # Ortalama 0, std 2
        new_price = max(prices[-1] + change, 10)  # Min $10
        prices.append(new_price)
    
    # RSI simülasyonu (basit: fiyat değişimine göre)
    rsi_values = []
    for i in range(n_candles):
        if i == 0:
            rsi_values.append(50)
        else:
            change = prices[i] - prices[i-1]
            prev_rsi = rsi_values[-1]
            # Fiyat artarsa RSI artar, düşerse azalır
            new_rsi = prev_rsi + change * 2
            new_rsi = max(10, min(90, new_rsi))  # 10-90 arası
            rsi_values.append(new_rsi)
    
    # DataFrame oluştur
    candles = pd.DataFrame({
        'timestamp': pd.date_range('2024-01-01', periods=n_candles, freq='4h'),
        'close': prices,
        'rsi': rsi_values
    })
    
    print(f"\n📈 Sentetik veri: {n_candles} mum")
    print(f"   Başlangıç fiyat: ${prices[0]:.2f}")
    print(f"   Bitiş fiyat: ${prices[-1]:.2f}")
    print(f"   Min/Max: ${min(prices):.2f} / ${max(prices):.2f}")
    
    # Backtester oluştur
    bt = Backtester(candles, starting_balance=1000.0)
    
    # Basit RSI stratejisi
    def rsi_strategy(row):
        rsi = row.get('rsi', 50)
        
        if rsi < 35:
            return ("BUY", 0.3)  # RSI düşükse %30 al
        elif rsi > 65:
            return ("SELL", 1.0)  # RSI yüksekse hepsini sat
        return (None, 0)
    
    # Backtest çalıştır
    print("\n🚀 RSI stratejisi çalıştırılıyor...")
    bt.run_simple_strategy(rsi_strategy)
    
    # Sonuçları göster
    bt.print_summary()
    
    # İlk 5 trade'i göster
    trades = bt.get_trades()
    if trades:
        print("📝 İlk 5 Trade:")
        print("-" * 50)
        for t in trades[:5]:
            side_emoji = "🟢" if t['side'] == "BUY" else "🔴"
            pnl_str = f" PnL: ${t['pnl']:.2f}" if t['pnl'] != 0 else ""
            print(f"   {side_emoji} {t['side']} {t['quantity']:.4f} @ ${t['price']:.2f}{pnl_str}")
        if len(trades) > 5:
            print(f"   ... ve {len(trades) - 5} işlem daha")
    
    print("\n✅ Demo tamamlandı!")
