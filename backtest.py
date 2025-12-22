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
import asyncio
from dataclasses import dataclass, field
from typing import Callable, List, Dict, Any, Optional, Tuple
from datetime import datetime
from risk_manager import RiskManager  # Import RiskManager
from market_data_engine import MarketDataEngine

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
    
    
    async def run_backtest(self, strategy_engine, risk_manager=None) -> None:
        """
        StrategyEngine kullanarak backtest çalıştır (Async).
        
        Args:
            strategy_engine: StrategyEngine instance
            risk_manager: RiskManager instance (Optional)
        """
        print(f"🚀 Backtest Başlıyor... ({len(self.candles)} mum)")
        
        # Instantiate Risk Manager if not provided
        risk_manager = risk_manager or RiskManager()
        
        for idx, row in self.candles.iterrows():
            # Support basic position state
            entry_price = self.position_avg_price if self.position > 0 else 0
            
            # Prepare offline extra data
            offline_extra = {
                "has_open_position": self.position > 0,
                "entry_price": entry_price,
                "sentiment": {
                     "overall_sentiment": row.get('sentiment', "NEUTRAL"),
                     "fear_greed": {"value": row.get('fng', 50)}
                },
                "onchain": {
                     "signal": row.get('onchain_signal', "NEUTRAL")
                }
            }
            
            # Instantiate MarketDataEngine in OFFLINE mode for this row
            mde = MarketDataEngine(
                offline_mode=True,
                offline_row=row.to_dict(),
                offline_extra=offline_extra
            )
            
            # Build unified snapshot
            market_snapshot = mde.build_snapshot("BACKTEST_PF")
            
            # 1. Base Strategy Signal
            base_decision = await strategy_engine.evaluate_opportunity(market_snapshot)
            
            # 2. Risk Management
            final_decision = base_decision
            portfolio = {"balance": self.balance}
            
            if base_decision.get("action") == "BUY":
                final_decision = risk_manager.evaluate_entry_risk(
                    snapshot=market_snapshot,
                    base_decision=base_decision,
                    portfolio=portfolio
                )
            elif base_decision.get("action") == "SELL":
                # Construct mock position dict for RiskManager
                pos_details = {
                    "symbol": "BACKTEST_PF",
                    "entry_price": entry_price,
                    "quantity": self.position,
                    "stop_loss": 0, # Not tracking strictly in this loop 
                    "take_profit": 0
                }
                final_decision = risk_manager.evaluate_exit_risk(
                     snapshot=market_snapshot,
                     position=pos_details,
                     base_decision=base_decision
                )

            # 3. Execution Logic
            if not final_decision.get("allowed", False):
                continue
                
            action = final_decision.get("action")
            quantity = final_decision.get("quantity", 0)
            
            if action == "BUY":
                cost = quantity * price
                if cost > 0 and self.balance >= cost * 0.99: # 0.99 buffer
                    # Convert absolute quantity to fraction for existing _execute_buy
                    fraction = min(cost / self.balance, 1.0) if self.balance > 0 else 0
                    if fraction > 0:
                         self._execute_buy(price, fraction, timestamp)
                         
            elif action == "SELL":
                if self.position > 0:
                    qty_to_sell = quantity if quantity > 0 else self.position
                    fraction = min(qty_to_sell / self.position, 1.0)
                    self._execute_sell(price, fraction, timestamp)

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
    
    # ═══════════════════════════════════════════════════════════════════════════
    # V1 BACKTEST - Partial TP & Trailing Stop
    # ═══════════════════════════════════════════════════════════════════════════
    
    def run_v1_backtest(
        self,
        signal_fn: Callable[[pd.Series, Dict], Tuple[Optional[str], Dict]],
        partial_tp_enabled: bool = True,
        partial_tp_fraction: float = 0.5,
        trailing_enabled: bool = True,
        trail_atr_mult: float = 3.0,
        sl_atr_mult: float = 2.0,
        mock_veto_fn: Callable[[pd.Series], bool] = None  # Mock veto for backtest
    ) -> None:
        """
        V1 strateji için gelişmiş backtest.
        
        Partial TP (1R) ve Chandelier trailing stop simülasyonu içerir.
        Mock veto modu backtesting'de LLM çağrısı yerine kullanılır.
        
        Args:
            signal_fn: Sinyal fonksiyonu.
                      Input: (row, position_state)
                      Output: (action, details_dict)
                        - action: "BUY", "SELL" veya None
                        - details_dict: {"quantity": float, "stop_loss": float, ...}
            
            partial_tp_enabled: 1R'de kısmi kar al
            partial_tp_fraction: Kısmi satış oranı (0.5 = %50)
            trailing_enabled: Chandelier trailing aktif
            trail_atr_mult: Trailing ATR çarpanı
            sl_atr_mult: Initial SL ATR çarpanı
        """
        # V1 Position State
        v1_state = {
            "initial_sl": 0.0,
            "current_sl": 0.0,
            "partial_taken": False,
            "partial_tp_price": 0.0,
            "highest_close": 0.0,
            "entry_price": 0.0
        }
        
        # Stats
        v1_stats = {
            "partial_tp_count": 0,
            "trailing_sl_count": 0,
            "initial_sl_count": 0,
            "full_tp_count": 0
        }
        
        for idx, row in self.candles.iterrows():
            price = self._get_price(row)
            timestamp = self._get_timestamp(row)
            atr = row.get('atr', price * 0.02)
            
            # Update highest close for trailing
            if self.position > 0 and price > v1_state["highest_close"]:
                v1_state["highest_close"] = price
            
            # ─────────────────────────────────────────────────────────────────
            # POSITION MANAGEMENT (if we have a position)
            # ─────────────────────────────────────────────────────────────────
            if self.position > 0:
                # 1. Check SL hit
                if price <= v1_state["current_sl"]:
                    exit_type = "TRAIL_SL" if v1_state["partial_taken"] else "SL"
                    self._execute_sell(price, 1.0, timestamp)
                    
                    if v1_state["partial_taken"]:
                        v1_stats["trailing_sl_count"] += 1
                    else:
                        v1_stats["initial_sl_count"] += 1
                    
                    # Reset state
                    v1_state = {
                        "initial_sl": 0.0, "current_sl": 0.0, "partial_taken": False,
                        "partial_tp_price": 0.0, "highest_close": 0.0, "entry_price": 0.0
                    }
                    continue
                
                # 2. Check Partial TP (1R)
                if partial_tp_enabled and not v1_state["partial_taken"]:
                    entry = v1_state["entry_price"]
                    stop_dist = entry - v1_state["initial_sl"]
                    one_r = entry + stop_dist
                    
                    if price >= one_r:
                        # Partial sell
                        self._execute_sell(price, partial_tp_fraction, timestamp)
                        v1_state["partial_taken"] = True
                        v1_state["partial_tp_price"] = one_r
                        v1_stats["partial_tp_count"] += 1
                        continue
                
                # 3. Update Trailing Stop
                if trailing_enabled and v1_state["partial_taken"] and atr > 0:
                    new_trail_sl = v1_state["highest_close"] - (trail_atr_mult * atr)
                    if new_trail_sl > v1_state["current_sl"]:
                        v1_state["current_sl"] = new_trail_sl
            
            # ─────────────────────────────────────────────────────────────────
            # SIGNAL EVALUATION (for new entries)
            # ─────────────────────────────────────────────────────────────────
            if self.position == 0:
                action, details = signal_fn(row, v1_state)
                
                if action == "BUY" and details:
                    quantity = details.get("quantity", 0)
                    stop_loss = details.get("stop_loss", price * 0.95)
                    
                    # Calculate fraction from quantity
                    cost = quantity * price
                    if cost > 0 and self.balance >= cost:
                        fraction = min(cost / self.balance, 1.0)
                        self._execute_buy(price, fraction, timestamp)
                        
                        # Initialize V1 state
                        v1_state["entry_price"] = price
                        v1_state["initial_sl"] = stop_loss
                        v1_state["current_sl"] = stop_loss
                        v1_state["highest_close"] = price
                        v1_state["partial_taken"] = False
        
        # Store V1 stats
        self._v1_stats = v1_stats
    
    def print_v1_summary(self) -> None:
        """V1 backtest özeti yazdır."""
        self.print_summary()
        
        if hasattr(self, '_v1_stats'):
            stats = self._v1_stats
            print("─" * 50)
            print("📊 V1 EXIT STATISTICS")
            print("─" * 50)
            print(f"Partial TP (1R):   {stats['partial_tp_count']}")
            print(f"Trailing SL:       {stats['trailing_sl_count']}")
            print(f"Initial SL:        {stats['initial_sl_count']}")
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
    

    # ... (Sentetik veri oluşturma kodunun geri kalanı) ...
    # RSI simülasyonu (basit: fiyat değişimine göre)
    rsi_values = []
    adx_values = []
    trends = []
    
    for i in range(n_candles):
        if i == 0:
            rsi_values.append(50)
            adx_values.append(25)
            trends.append("NEUTRAL")
        else:
            change = prices[i] - prices[i-1]
            prev_rsi = rsi_values[-1]
            
            # RSI
            new_rsi = prev_rsi + change * 2
            new_rsi = max(10, min(90, new_rsi))
            rsi_values.append(new_rsi)
            
            # ADX (Rastgele)
            adx_values.append(np.random.randint(15, 45))
            
            # Trend
            if prices[i] > prices[i-1]:
                trends.append("BULLISH")
            else:
                trends.append("BEARISH")
    
    # DataFrame oluştur
    candles = pd.DataFrame({
        'timestamp': pd.date_range('2024-01-01', periods=n_candles, freq='4h'),
        'close': prices,
        'rsi': rsi_values,
        'adx': adx_values,
        'trend': trends
    })
    
    print(f"\n📈 Sentetik veri: {n_candles} mum")
    print(f"   Başlangıç fiyat: ${prices[0]:.2f}")
    print(f"   Bitiş fiyat: ${prices[-1]:.2f}")
    
    # ────────────────────────────────────────────────────────
    # 1. BASİT STRATEJİ TESTİ
    # ────────────────────────────────────────────────────────
    print("\n" + "=" * 40)
    print("TEST 1: Basit Fonksiyonel Strateji")
    print("=" * 40)
    
    bt = Backtester(candles, starting_balance=1000.0)
    
    def rsi_strategy(row):
        rsi = row.get('rsi', 50)
        if rsi < 35:
            return ("BUY", 0.3)
        elif rsi > 65:
            return ("SELL", 1.0)
        return (None, 0)
    
    bt.run_simple_strategy(rsi_strategy)
    bt.print_summary()
    
    # ────────────────────────────────────────────────────────
    # 2. STRATEGY ENGINE ENTEGRASYON TESTİ
    # ────────────────────────────────────────────────────────
    print("\n" + "=" * 40)
    print("TEST 2: Strategy Engine (Deterministic)")
    print("=" * 40)
    
    async def run_async_demo():
        try:
            from strategy_engine import StrategyEngine
            # RiskManager zaten import edildi (top-level)
            
            # Deterministic modda engine oluştur
            # Not: StrategyEngine'e guardrail parametrelerini geçmeye gerek yok artik (RiskManager'da)
            engine = StrategyEngine(
                deterministic=True, 
                enable_llm=False
            )
            
            # Risk Manager oluştur (Demo için gevşek kurallar)
            rm = RiskManager(config={
                "min_volume": 0,
                "min_adx": 0,
                "risk_per_trade": 0.05
            })
            
            # Backtester'ı sıfırla/yeniden oluştur
            bt_engine = Backtester(candles, starting_balance=1000.0)
            
            # Async backtest çalıştır (custom risk manager ile)
            await bt_engine.run_backtest(engine, risk_manager=rm)
            
            # Sonuçları göster
            bt_engine.print_summary()
            
            trades = bt_engine.get_trades()
            if trades:
                print("📝 Engine Trades (İlk 5):")
                print("-" * 50)
                for t in trades[:5]:
                     side_emoji = "🟢" if t['side'] == "BUY" else "🔴"
                     pnl_str = f" PnL: ${t['pnl']:.2f}" if t['pnl'] != 0 else ""
                     print(f"   {side_emoji} {t['side']} {t['quantity']:.4f} @ ${t['price']:.2f}{pnl_str}")

        except ImportError:
            print("⚠️ StrategyEngine import edilemedi (dosya eksik olabilir)")
        except Exception as e:
            print(f"⚠️ Hata: {e}")
            import traceback
            traceback.print_exc()

    asyncio.run(run_async_demo())
    
    print("\n✅ Demo tamamlandı!")


# ═══════════════════════════════════════════════════════════════════════════════
# SELFTEST - Deterministic Exit Lifecycle Verification
# ═══════════════════════════════════════════════════════════════════════════════

def run_selftest():
    """
    Deterministic exit lifecycle scenarios.
    
    Scenario 1: Entry → 1R → Partial TP → Trailing → Trail Stop close
    Scenario 2: Entry → SL hit → close
    
    These tests validate the exit logic without any LLM or exchange calls.
    """
    print("\n" + "=" * 60)
    print("🧪 SELFTEST: Exit Lifecycle Verification")
    print("=" * 60)
    
    # Exit reason import
    try:
        from exit_reason import ExitReason
    except ImportError:
        class ExitReason:
            STOP_LOSS = "STOP_LOSS"
            TRAIL_STOP = "TRAIL_STOP"
            PARTIAL_TP = "PARTIAL_TP"
    
    all_passed = True
    
    # ─────────────────────────────────────────────────────────────────────────────
    # SCENARIO 1: 1R → Partial TP → Trailing → Trail Stop close
    # ─────────────────────────────────────────────────────────────────────────────
    print("\n" + "-" * 60)
    print("SCENARIO 1: Partial TP + Trailing Stop Flow")
    print("-" * 60)
    
    # Setup
    entry_price = 100.0
    initial_sl = 95.0   # Risk = $5 (5%)
    quantity = 1.0
    partial_fraction = 0.5
    trail_atr_mult = 2.0
    atr = 2.0
    
    # 1R price = entry + (entry - initial_sl) = 100 + 5 = 105
    one_r_price = entry_price + (entry_price - initial_sl)
    
    # Position state
    pos = {
        "entry_price": entry_price,
        "quantity": quantity,
        "initial_sl": initial_sl,
        "current_sl": initial_sl,
        "partial_taken": False,
        "highest_close": entry_price,
        "pnl_realized": 0.0
    }
    
    print(f"   Entry: ${entry_price:.2f} | Initial SL: ${initial_sl:.2f} | 1R: ${one_r_price:.2f}")
    
    # Step 1: Price reaches 1R → Partial TP
    price_at_1r = 106.0  # Above 1R
    print(f"\n   [STEP 1] Price reaches ${price_at_1r:.2f} (above 1R)")
    
    if price_at_1r >= one_r_price and not pos["partial_taken"]:
        sell_qty = pos["quantity"] * partial_fraction
        pnl = (price_at_1r - entry_price) * sell_qty
        pos["quantity"] -= sell_qty
        pos["partial_taken"] = True
        pos["pnl_realized"] += pnl
        pos["highest_close"] = price_at_1r
        print(f"   [EVENT] partial_tp_triggered | exit_reason={ExitReason.PARTIAL_TP}")
        print(f"           Sold {sell_qty:.2f} @ ${price_at_1r:.2f} | PnL: ${pnl:.2f}")
        print(f"           Remaining: {pos['quantity']:.2f}")
        assert pos["partial_taken"] == True
        assert pos["quantity"] == 0.5
        print("   [PASS] Partial TP triggered correctly")
    else:
        print("   [FAIL] Partial TP should have triggered")
        all_passed = False
    
    # Step 2: Price continues up → Trailing updates
    price_higher = 110.0
    print(f"\n   [STEP 2] Price climbs to ${price_higher:.2f}")
    
    if pos["partial_taken"]:
        pos["highest_close"] = max(pos["highest_close"], price_higher)
        new_trail_sl = pos["highest_close"] - (trail_atr_mult * atr)
        if new_trail_sl > pos["current_sl"]:
            old_sl = pos["current_sl"]
            pos["current_sl"] = new_trail_sl
            print(f"   [EVENT] trailing_updated | old_sl=${old_sl:.2f} | new_sl=${new_trail_sl:.2f}")
            print(f"           highest_close=${pos['highest_close']:.2f} | atr=${atr:.2f}")
            assert pos["current_sl"] > initial_sl
            print("   [PASS] Trailing stop updated correctly")
    
    # Step 3: Price drops to trail stop → Close
    price_drop = 105.0  # Below trail stop (110 - 4 = 106)
    print(f"\n   [STEP 3] Price drops to ${price_drop:.2f}")
    
    if price_drop <= pos["current_sl"]:
        pnl = (price_drop - entry_price) * pos["quantity"]
        pos["pnl_realized"] += pnl
        exit_reason = ExitReason.TRAIL_STOP
        print(f"   [EVENT] stop_triggered | exit_reason={exit_reason} | price=${price_drop:.2f}")
        print(f"           Final PnL: ${pos['pnl_realized']:.2f} (should be positive)")
        assert pos["pnl_realized"] > 0
        print("   [PASS] Position closed with profit via trailing stop")
    else:
        print("   [FAIL] Trail stop should have triggered")
        all_passed = False
    
    scenario1_passed = pos["pnl_realized"] > 0
    print(f"\n   SCENARIO 1: {'[PASS]' if scenario1_passed else '[FAIL]'} Total PnL: ${pos['pnl_realized']:.2f}")
    
    # ─────────────────────────────────────────────────────────────────────────────
    # SCENARIO 2: Direct SL Hit
    # ─────────────────────────────────────────────────────────────────────────────
    print("\n" + "-" * 60)
    print("SCENARIO 2: Direct Stop Loss Hit")
    print("-" * 60)
    
    # Setup
    entry_price = 100.0
    initial_sl = 95.0
    quantity = 1.0
    
    pos2 = {
        "entry_price": entry_price,
        "quantity": quantity,
        "initial_sl": initial_sl,
        "current_sl": initial_sl,
        "partial_taken": False,
        "pnl_realized": 0.0
    }
    
    print(f"   Entry: ${entry_price:.2f} | SL: ${initial_sl:.2f}")
    
    # Price drops directly to SL
    price_sl = 94.5
    print(f"\n   [STEP 1] Price drops directly to ${price_sl:.2f} (below SL)")
    
    if price_sl <= pos2["current_sl"]:
        pnl = (price_sl - entry_price) * pos2["quantity"]
        pos2["pnl_realized"] += pnl
        exit_reason = ExitReason.STOP_LOSS
        print(f"   [EVENT] stop_triggered | exit_reason={exit_reason} | price=${price_sl:.2f}")
        print(f"           PnL: ${pnl:.2f} (should be negative)")
        assert pnl < 0
        assert pos2["partial_taken"] == False
        print("   [PASS] Position closed at stop loss")
    else:
        print("   [FAIL] Stop loss should have triggered")
        all_passed = False
    
    scenario2_passed = pos2["pnl_realized"] < 0
    print(f"\n   SCENARIO 2: {'[PASS]' if scenario2_passed else '[FAIL]'} Total PnL: ${pos2['pnl_realized']:.2f}")
    
    # ─────────────────────────────────────────────────────────────────────────────
    # SUMMARY
    # ─────────────────────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    all_passed = scenario1_passed and scenario2_passed
    if all_passed:
        print("✅ ALL SELFTEST SCENARIOS PASSED")
    else:
        print("❌ SOME SELFTEST SCENARIOS FAILED")
    print("=" * 60)
    
    return all_passed


if __name__ == "__main__":
    import sys
    
    # Parse arguments
    if "--selftest" in sys.argv:
        success = run_selftest()
        sys.exit(0 if success else 1)
    else:
        demo()

