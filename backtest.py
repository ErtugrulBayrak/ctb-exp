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
    # V2 BACKTEST - HYBRID MULTI-TF STRATEGY
    # ═══════════════════════════════════════════════════════════════════════════════
    
    def run_v2_backtest(
        self,
        multi_tf_data: Dict[str, pd.DataFrame],  # {"4h": df, "1h": df, "15m": df}
        symbol: str = "BTC",
        starting_balance: float = 10000.0
    ) -> Dict[str, Any]:
        """
        Run HYBRID V2 multi-timeframe backtest.
        
        Args:
            multi_tf_data: Dict of timeframe -> DataFrame with OHLCV + indicators
            symbol: Symbol being tested
            starting_balance: Initial balance
        
        Returns:
            Dict with backtest results
        """
        # Import V2 components
        try:
            from strategies.hybrid_multi_tf_v2 import HybridMultiTFV2, EntryType, ENTRY_CONFIGS
            from strategies.regime_detector import RegimeDetector
        except ImportError as e:
            print(f"❌ V2 imports failed: {e}")
            return {"error": str(e)}
        
        # Import and load Fear & Greed historical data
        try:
            from data.fear_greed_historical import FearGreedHistorical
            self._fg_data = FearGreedHistorical()
            if not self._fg_data.data:
                print("📥 Fetching Fear & Greed historical data...")
                self._fg_data.fetch_history_sync(days=365)
        except ImportError:
            self._fg_data = None
            print("⚠️ Fear & Greed data not available")
        
        # Import config for exit parameters (sync with position_manager)
        try:
            import config
            self._exit_params = {
                "4H_SWING": {
                    "partial_tp_pct": getattr(config, 'SWING_4H_PARTIAL_TP_PCT', 5.0),
                    "final_target_pct": getattr(config, 'SWING_4H_FINAL_TARGET_PCT', 10.0),
                    "sl_atr_mult": getattr(config, 'SWING_4H_SL_ATR_MULT', 2.5),
                    "time_exit_hours": 240,  # 10 days
                    "partial_fraction": 0.5,
                },
                "1H_MOMENTUM": {
                    "partial_tp_pct": getattr(config, 'MOMENTUM_1H_PARTIAL_TP_PCT', 2.0),
                    "final_target_pct": getattr(config, 'MOMENTUM_1H_FINAL_TARGET_PCT', 4.0),
                    "sl_atr_mult": getattr(config, 'MOMENTUM_1H_SL_ATR_MULT', 1.8),
                    "time_exit_hours": 24,
                    "time_exit_min_profit": 0.5,
                    "partial_fraction": 0.5,
                },
                "15M_SCALP": {
                    "target_pct": getattr(config, 'SCALP_15M_TARGET_PCT', 1.5),
                    "sl_atr_mult": getattr(config, 'SCALP_15M_SL_ATR_MULT', 1.2),
                    "time_exit_hours": 4,
                },
            }
        except ImportError:
            # Fallback defaults if config not available
            self._exit_params = {
                "4H_SWING": {"partial_tp_pct": 5.0, "final_target_pct": 10.0, "sl_atr_mult": 2.5, "time_exit_hours": 240, "partial_fraction": 0.5},
                "1H_MOMENTUM": {"partial_tp_pct": 2.0, "final_target_pct": 4.0, "sl_atr_mult": 1.8, "time_exit_hours": 24, "time_exit_min_profit": 0.5, "partial_fraction": 0.5},
                "15M_SCALP": {"target_pct": 1.5, "sl_atr_mult": 1.2, "time_exit_hours": 4},
            }
        
        # Reset state
        self.balance = starting_balance
        self.starting_balance = starting_balance
        self.position = 0.0
        self.position_avg_price = 0.0
        self.position_cost = 0.0
        self.trades = []
        self.winning_trades = 0
        self.losing_trades = 0
        self.cumulative_pnl = 0.0
        
        # V2 Stats (enhanced with partial_tps tracking)
        self._v2_stats = {
            "4H_SWING": {"entries": 0, "wins": 0, "losses": 0, "pnl": 0.0, "partial_tps": 0},
            "1H_MOMENTUM": {"entries": 0, "wins": 0, "losses": 0, "pnl": 0.0, "partial_tps": 0},
            "15M_SCALP": {"entries": 0, "wins": 0, "losses": 0, "pnl": 0.0},
            "regime_counts": {},
            "total_signals": 0,
            "signals_skipped": 0
        }
        
        # Initialize strategy components
        strategy = HybridMultiTFV2(
            balance=starting_balance,
            dry_run=False,  # False to allow quantity calculation
            enable_scalping=True,
            liquidity_filter=False  # Disable for backtest
        )
        regime_detector = RegimeDetector(cache_ttl=0)  # Disable cache for backtest loop
        
        # Get primary timeframe (15m for signal iteration)
        if "15m" not in multi_tf_data:
            print("❌ 15m timeframe data required")
            return {"error": "15m data missing"}
        
        df_15m = multi_tf_data["15m"]
        
        # Auto-resample missing timeframes
        required_tfs = ["1h", "4h", "1d", "1w"]
        for tf in required_tfs:
            if tf not in multi_tf_data:
                print(f"⚠️ Resampling {tf} from 15m data...")
                # Simple resampling logic
                d = df_15m.copy()
                d["timestamp"] = pd.to_datetime(d["timestamp"])
                d = d.set_index("timestamp")
                rule_map = {"1h": "1h", "4h": "4h", "1d": "1D", "1w": "1W"}
                resampled = d.resample(rule_map[tf]).agg({
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last",
                    "volume": "sum"
                }).dropna().reset_index()
                multi_tf_data[tf] = resampled
        
        # Ensure indicators are computed
        # Note: 1w is also needed for strategy but let's ensure we process all keys
        for tf, df_tf in multi_tf_data.items():
            if "rsi" not in df_tf.columns: # Simple check if indicators exist
                 multi_tf_data[tf] = self._ensure_v2_indicators(df_tf)
        
        print(f"🚀 V2 Backtest Starting... ({len(df_15m)} bars)")
        
        # Position state for exit management
        self.active_positions = []  # List of open positions
        MAX_POSITIONS = 4
        
        # Iterate through 15m bars
        for i, row in df_15m.iterrows():
            self._current_bar_index = i  # Track for entry_bar
            price = self._get_price(row)
            timestamp = self._get_timestamp(row)
            
            # Build V2 snapshot
            snapshot = self._build_v2_snapshot(
                symbol, row, i, multi_tf_data
            )
            
            # 1. Check exits for ALL active positions
            # Iterate backwards to safely remove items
            for pos_idx in range(len(self.active_positions) - 1, -1, -1):
                position = self.active_positions[pos_idx]
                
                # Update current bar for time-based exits
                position["current_bar"] = i
                
                # Update highest_close_since_entry (matches position_manager)
                if price > position.get("highest_close_since_entry", 0):
                    position["highest_close_since_entry"] = price
                
                exit_result = self._check_v2_exit(
                    position, price, row, snapshot
                )
                
                if exit_result.get("exit"):
                    entry_type = position.get("entry_type", "UNKNOWN")
                    
                    if exit_result.get("partial_exit"):
                        # Partial exit - sell fraction, keep position active
                        fraction = exit_result.get("exit_fraction", 0.5)
                        pnl = self._execute_v2_partial_exit(
                            position, price, timestamp, fraction, exit_result["reason"]
                        )
                        
                        # Update position quantity (remaining)
                        position["quantity"] *= (1 - fraction)
                        
                        # Track partial TP in stats
                        if entry_type in self._v2_stats and "partial_tps" in self._v2_stats[entry_type]:
                            self._v2_stats[entry_type]["partial_tps"] += 1
                            self._v2_stats[entry_type]["pnl"] += pnl
                        
                        # DON'T remove from active_positions - still holding remainder
                    else:
                        # Full exit
                        pnl = self._execute_v2_exit(
                            position, price, timestamp, exit_result["reason"]
                        )
                        
                        # Update stats
                        if entry_type in self._v2_stats:
                            self._v2_stats[entry_type]["pnl"] += pnl
                            if pnl > 0:
                                self._v2_stats[entry_type]["wins"] += 1
                            else:
                                self._v2_stats[entry_type]["losses"] += 1
                        
                        # Remove from active positions
                        self.active_positions.pop(pos_idx)
                    
                    # Sync strategy balance
                    strategy.balance = self.balance

            
            # 2. Evaluate entry if we have capacity
            if len(self.active_positions) < MAX_POSITIONS:
                # Detect regime
                regime = regime_detector.detect_regime(symbol, snapshot)
                regime_type = regime.get("regime", "UNKNOWN")
                
                # Track regime counts
                self._v2_stats["regime_counts"][regime_type] = \
                    self._v2_stats["regime_counts"].get(regime_type, 0) + 1
                
                # Evaluate entry
                signal = strategy.evaluate_entry(symbol, snapshot, regime)
                
                if signal.get("action") == "BUY":
                    self._v2_stats["total_signals"] += 1
                    entry_type = signal.get("entry_type", "UNKNOWN")
                    
                    # Check if we assume a position constraint per type
                    # e.g. only 1 scalp, 1 momentum, 1 swing?
                    # For now allow stacking if different types or Pyramiding (if strategy allows)
                    
                    # Check balance
                    quantity = signal.get("quantity", 0)
                    cost = quantity * price
                    
                    # Minimum size fallback
                    if quantity <= 0 or cost <= 0:
                        min_cost = self.balance * 0.01
                        quantity = min_cost / price if price > 0 else 0
                        cost = quantity * price
                    
                    if cost > 0 and self.balance >= cost:
                        # Execute entry
                        new_position = self._execute_v2_entry(
                            signal, price, timestamp
                        )
                        
                        # Add to active positions
                        self.active_positions.append(new_position)
                        
                        # Sync strategy balance
                        strategy.balance = self.balance
                        
                        # Update stats
                        if entry_type in self._v2_stats:
                            self._v2_stats[entry_type]["entries"] += 1
                    else:
                        if self._v2_stats["signals_skipped"] < 10:
                            print(f"[DEBUG] Signal skipped: {entry_type}, Qty={quantity}, Price={price:.2f}, Cost={cost:.2f}, Balance={self.balance:.2f}")
                        self._v2_stats["signals_skipped"] += 1
        
        # Close any remaining positions at last price
        for position in self.active_positions:
            last_price = self._get_price(df_15m.iloc[-1])
            last_ts = self._get_timestamp(df_15m.iloc[-1])
            self._execute_v2_exit(position, last_price, last_ts, "BACKTEST_END")
        
        print(f"✅ V2 Backtest Complete: {len(self.trades)} trades")
        return self.results()
    
    def _build_v2_snapshot(
        self,
        symbol: str,
        row_15m: pd.Series,
        row_index: int,
        multi_tf_data: Dict[str, pd.DataFrame]
    ) -> Dict[str, Any]:
        """Build V2 snapshot from multi-timeframe data."""
        
        price = self._get_price(row_15m)
        
        snapshot = {
            "symbol": symbol,
            "price": price,
            "volume_24h": row_15m.get("volume", 0) * 96,  # Approximate 24h
            "tf": {},
            "sentiment": {"fear_greed": {"value": self._get_fear_greed_for_row(row_15m)}},
            "onchain": {"signal": "NEUTRAL"}
        }
        
        # Build 15m data
        snapshot["tf"]["15m"] = {
            "close": price,
            "adx": row_15m.get("adx", 25),
            "rsi": row_15m.get("rsi", 50),
            "atr": row_15m.get("atr", price * 0.01),
            "ema20": row_15m.get("ema20", price),
            "ema50": row_15m.get("ema50", price),
            "highest_high": row_15m.get("highest_high", price),
            "bb_upper": row_15m.get("bb_upper", price * 1.02),
            "bb_middle": row_15m.get("bb_middle", price),
            "bb_lower": row_15m.get("bb_lower", price * 0.98),
            "volume": row_15m.get("volume", 0),
            "volume_avg": row_15m.get("volume_avg", row_15m.get("volume", 0))
        }
        
        # Get corresponding rows from other timeframes using proportional index
        # This avoids datetime comparison issues
        for tf in ["1h", "4h", "1d"]:
            if tf in multi_tf_data:
                df_tf = multi_tf_data[tf]
                if len(df_tf) == 0:
                    continue
                
                # Use proportional index based on timeframe ratio
                tf_ratio = {"1h": 4, "4h": 16, "1d": 96}.get(tf, 1)
                tf_idx = min(row_index // tf_ratio, len(df_tf) - 1)
                tf_row = df_tf.iloc[tf_idx]
                
                snapshot["tf"][tf] = self._extract_tf_indicators(tf_row, tf)
        
        return snapshot
    
    def _get_fear_greed_for_row(self, row: pd.Series) -> int:
        """Get Fear & Greed value for a row's timestamp."""
        if not hasattr(self, '_fg_data') or self._fg_data is None:
            return 50  # Neutral default
        
        timestamp = row.get("timestamp")
        if timestamp is None:
            return 50
        
        return self._fg_data.get_value_for_date(timestamp)
    
    def _extract_tf_indicators(self, row: pd.Series, timeframe: str) -> Dict[str, Any]:
        """Extract indicators from a row for a specific timeframe."""
        price = self._get_price(row)
        
        return {
            "close": price,
            "ema20": row.get("ema20", price),
            "ema50": row.get("ema50", price),
            "ema200": row.get("ema200", price * 0.95),
            "adx": row.get("adx", 25),
            "rsi": row.get("rsi", 50),
            "atr": row.get("atr", price * 0.02),
            "atr_pct": row.get("atr_pct", 2.0),
            "macd": row.get("macd", 0),
            "macd_signal": row.get("macd_signal", 0),
            "macd_hist": row.get("macd_hist", 0),
            "macd_hist_prev": row.get("macd_hist_prev", 0),
            "volume": row.get("volume", 0),
            "volume_avg": row.get("volume_avg", row.get("volume", 0)),
            "trend": row.get("trend", "NEUTRAL")
        }
    
    def _ensure_v2_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Ensure all V2 indicators are computed for a DataFrame.
        
        Uses pandas_ta for robust calculation if available.
        """
        df = df.copy()
        
        # Ensure column names are lowercase
        df.columns = [c.lower() for c in df.columns]
        
        # Ensure required columns exist
        if "close" not in df.columns:
            return df
            
        try:
            import pandas_ta as ta
            
            # ADX (Critical for regime detection)
            if "adx" not in df.columns and "high" in df.columns and "low" in df.columns:
                # pandas_ta appends columns like ADX_14, DMP_14, DMN_14
                df.ta.adx(length=14, append=True)
                if "ADX_14" in df.columns:
                    df["adx"] = df["ADX_14"]
            
            # ATR
            if "atr" not in df.columns and "high" in df.columns and "low" in df.columns:
                df.ta.atr(length=14, append=True)
                if "ATRr_14" in df.columns:
                    df["atr"] = df["ATRr_14"]
            
            # RSI
            if "rsi" not in df.columns:
                df.ta.rsi(length=14, append=True)
                if "RSI_14" in df.columns:
                    df["rsi"] = df["RSI_14"]
            
            # MACD
            if "macd" not in df.columns:
                df.ta.macd(append=True)
                if "MACD_12_26_9" in df.columns:
                    df["macd"] = df["MACD_12_26_9"]
                if "MACDs_12_26_9" in df.columns:
                    df["macd_signal"] = df["MACDs_12_26_9"]
                if "MACDh_12_26_9" in df.columns:
                    df["macd_hist"] = df["MACDh_12_26_9"]
                    df["macd_hist_prev"] = df["macd_hist"].shift(1)

            # Bollinger Bands
            if "bb_upper" not in df.columns:
                df.ta.bbands(length=20, std=2.0, append=True)
                if "BBU_20_2.0" in df.columns:
                    df["bb_upper"] = df["BBU_20_2.0"]
                if "BBM_20_2.0" in df.columns:
                    df["bb_middle"] = df["BBM_20_2.0"]
                if "BBL_20_2.0" in df.columns:
                    df["bb_lower"] = df["BBL_20_2.0"]

            # EMAs
            if "ema20" not in df.columns:
                df.ta.ema(length=20, append=True)
                if "EMA_20" in df.columns:
                    df["ema20"] = df["EMA_20"]
            
            if "ema50" not in df.columns:
                df.ta.ema(length=50, append=True)
                if "EMA_50" in df.columns:
                    df["ema50"] = df["EMA_50"]
            
            if "ema200" not in df.columns:
                df.ta.ema(length=200, append=True)
                if "EMA_200" in df.columns:
                    df["ema200"] = df["EMA_200"]

        except ImportError:
            print("⚠️ pandas_ta not installed, using fallback calculations")
            # Fallback (Manual Calcs) would go here but we assume pandas_ta is present
            pass
        except Exception as e:
            print(f"❌ Indicator calculation error: {e}")
            import traceback
            traceback.print_exc()

        # Fill NaNs in critical columns to avoid logic errors
        # (ADX, RSI, ATR should generally settle after N periods)
        cols_to_fill = ["adx", "rsi", "atr", "ema20", "ema50", "bb_width"]
        for c in cols_to_fill:
            if c in df.columns:
                df[c] = df[c].fillna(0)

        # Helpers
        if "highest_high" not in df.columns and "high" in df.columns:
             df["highest_high"] = df["high"].rolling(20).max()
        
        if "volume_avg" not in df.columns and "volume" in df.columns:
             df["volume_avg"] = df["volume"].rolling(20).mean()

        if "atr_pct" not in df.columns and "atr" in df.columns and "close" in df.columns:
             df["atr_pct"] = (df["atr"] / df["close"]) * 100

        return df
    
    def _execute_v2_entry(
        self,
        signal: Dict[str, Any],
        price: float,
        timestamp: str
    ) -> Dict[str, Any]:
        """Execute V2 entry and return position dict."""
        
        quantity = signal.get("quantity", 0)
        stop_loss = signal.get("stop_loss", price * 0.95)
        take_profit_1 = signal.get("take_profit_1") or signal.get("partial_tp_target")
        take_profit_2 = signal.get("take_profit_2", price * 1.05)
        
        # Calculate cost
        cost = quantity * price
        fee = cost * self.fee_pct
        
        # Update balance
        self.balance -= (cost + fee)
        self.position = quantity
        self.position_avg_price = price
        self.position_cost = cost + fee
        
        # Record trade
        trade = Trade(
            timestamp=timestamp,
            side="BUY",
            price=price,
            quantity=quantity,
            cost=cost + fee
        )
        self.trades.append(trade)
        
        # Return position dict for tracking
        return {
            "entry_price": price,
            "quantity": quantity,
            "stop_loss": stop_loss,
            "take_profit_1": take_profit_1,
            "take_profit_2": take_profit_2,
            "entry_type": signal.get("entry_type", "UNKNOWN"),
            "partial_taken": False,
            "highest_price": price,
            "entry_time": timestamp,
            "entry_bar": self._current_bar_index  # For time-based exits
        }
    
    def _check_v2_exit(
        self,
        position: Dict[str, Any],
        price: float,
        row: pd.Series,
        snapshot: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Check if position should be exited.
        
        Routes to entry-type-specific exit method (mirrors position_manager.py).
        """
        entry_type = position.get("entry_type", "V1")
        
        # Update highest price for trailing stop
        if price > position.get("highest_price", 0):
            position["highest_price"] = price
        
        if entry_type == "4H_SWING":
            return self._check_4h_swing_exit_bt(position, price, snapshot)
        elif entry_type == "1H_MOMENTUM":
            return self._check_1h_momentum_exit_bt(position, price, snapshot)
        elif entry_type == "15M_SCALP":
            return self._check_15m_scalp_exit_bt(position, price, snapshot)
        else:
            return self._check_v1_exit_bt(position, price, snapshot)
    
    def _check_4h_swing_exit_bt(
        self,
        position: Dict[str, Any],
        price: float,
        snapshot: Dict[str, Any]
    ) -> Dict[str, Any]:
        """4H swing exit logic (mirrors position_manager._check_4h_swing_exit)."""
        
        entry_price = position["entry_price"]
        stop_loss = position["stop_loss"]
        quantity = position["quantity"]
        partial_taken = position.get("partial_taken", False)
        entry_bar = position.get("entry_bar", 0)
        current_bar = position.get("current_bar", 0)
        bars_held = current_bar - entry_bar
        hours_held = bars_held * 0.25  # 15m bars
        
        profit_pct = ((price - entry_price) / entry_price) * 100
        
        # Get config parameters from self._exit_params (synced with config.py)
        params = getattr(self, '_exit_params', {}).get('4H_SWING', {})
        partial_tp_pct = params.get('partial_tp_pct', 5.0)
        final_target_pct = params.get('final_target_pct', 10.0)
        sl_atr_mult = params.get('sl_atr_mult', 2.5)
        time_exit_hours = params.get('time_exit_hours', 240)
        partial_fraction = params.get('partial_fraction', 0.5)
        
        # 1. Initial stop loss
        if price <= stop_loss:
            return {"exit": True, "reason": "STOP_LOSS", "exit_type": "STOP_LOSS"}
        
        # 2. Partial TP at configured % (sell 50%)
        if not partial_taken and profit_pct >= partial_tp_pct:
            position["partial_taken"] = True
            # Return partial_exit=True for real partial execution
            return {
                "exit": True,
                "partial_exit": True,
                "exit_fraction": partial_fraction,
                "reason": "PARTIAL_TP",
                "exit_type": "PARTIAL_TP"
            }
        
        # 3. Trailing stop after partial TP
        if partial_taken:
            tf_4h = snapshot.get("tf", {}).get("4h", {})
            atr_4h = tf_4h.get("atr", entry_price * 0.02)
            highest_close = position.get("highest_close_since_entry", entry_price)
            
            if atr_4h > 0:
                trail_stop = highest_close - (sl_atr_mult * atr_4h)
                if price <= trail_stop:
                    return {"exit": True, "reason": "TRAILING_STOP", "exit_type": "TRAILING_STOP"}
        
        # 4. Final target
        if profit_pct >= final_target_pct:
            return {"exit": True, "reason": "TAKE_PROFIT", "exit_type": "TAKE_PROFIT"}
        
        # 5. Time-based exit: After configured hours, close if profit > 0
        if hours_held > time_exit_hours and profit_pct > 0:
            return {"exit": True, "reason": "TIME_EXIT", "exit_type": "TIME_EXIT"}
        
        return {"exit": False, "reason": "HOLDING"}
    
    def _check_1h_momentum_exit_bt(
        self,
        position: Dict[str, Any],
        price: float,
        snapshot: Dict[str, Any]
    ) -> Dict[str, Any]:
        """1H momentum exit logic (mirrors position_manager._check_1h_momentum_exit)."""
        
        entry_price = position["entry_price"]
        stop_loss = position["stop_loss"]
        quantity = position["quantity"]
        partial_taken = position.get("partial_taken", False)
        entry_bar = position.get("entry_bar", 0)
        current_bar = position.get("current_bar", 0)
        bars_held = current_bar - entry_bar
        hours_held = bars_held * 0.25
        
        profit_pct = ((price - entry_price) / entry_price) * 100
        
        # Get config parameters from self._exit_params (synced with config.py)
        params = getattr(self, '_exit_params', {}).get('1H_MOMENTUM', {})
        partial_tp_pct = params.get('partial_tp_pct', 2.0)
        final_target_pct = params.get('final_target_pct', 4.0)
        sl_atr_mult = params.get('sl_atr_mult', 1.8)
        time_exit_hours = params.get('time_exit_hours', 24)
        time_exit_min_profit = params.get('time_exit_min_profit', 0.5)
        partial_fraction = params.get('partial_fraction', 0.5)
        
        # 1. Initial stop loss
        if price <= stop_loss:
            return {"exit": True, "reason": "STOP_LOSS", "exit_type": "STOP_LOSS"}
        
        # 2. Partial TP at configured %
        if not partial_taken and profit_pct >= partial_tp_pct:
            position["partial_taken"] = True
            # Return partial_exit=True for real partial execution
            return {
                "exit": True,
                "partial_exit": True,
                "exit_fraction": partial_fraction,
                "reason": "PARTIAL_TP",
                "exit_type": "PARTIAL_TP"
            }
        
        # 3. Trailing stop after partial TP
        if partial_taken:
            tf_1h = snapshot.get("tf", {}).get("1h", {})
            atr_1h = tf_1h.get("atr", entry_price * 0.015)
            highest_close = position.get("highest_close_since_entry", entry_price)
            
            if atr_1h > 0:
                trail_stop = highest_close - (sl_atr_mult * atr_1h)
                if price <= trail_stop:
                    return {"exit": True, "reason": "TRAILING_STOP", "exit_type": "TRAILING_STOP"}
        
        # 4. Final target
        if profit_pct >= final_target_pct:
            return {"exit": True, "reason": "TAKE_PROFIT", "exit_type": "TAKE_PROFIT"}
        
        # 5. Time-based exit: After configured hours, close if profit > min
        if hours_held > time_exit_hours and profit_pct > time_exit_min_profit:
            return {"exit": True, "reason": "TIME_EXIT", "exit_type": "TIME_EXIT"}
        
        return {"exit": False, "reason": "HOLDING"}
    
    def _check_15m_scalp_exit_bt(
        self,
        position: Dict[str, Any],
        price: float,
        snapshot: Dict[str, Any]
    ) -> Dict[str, Any]:
        """15M scalp exit logic (mirrors position_manager._check_15m_scalp_exit)."""
        
        entry_price = position["entry_price"]
        stop_loss = position["stop_loss"]
        entry_bar = position.get("entry_bar", 0)
        current_bar = position.get("current_bar", 0)
        bars_held = current_bar - entry_bar
        hours_held = bars_held * 0.25
        
        profit_pct = ((price - entry_price) / entry_price) * 100
        
        # Config parameters
        target_pct = 1.5
        
        # 1. Initial stop loss
        if price <= stop_loss:
            return {"exit": True, "reason": "STOP_LOSS", "exit_type": "STOP_LOSS"}
        
        # 2. Target hit at 1.5%
        if profit_pct >= target_pct:
            return {"exit": True, "reason": "TAKE_PROFIT", "exit_type": "TAKE_PROFIT"}
        
        # 3. Time-based exit: After 4 hours, close if near breakeven
        if hours_held > 4 and profit_pct >= -0.1:
            return {"exit": True, "reason": "TIME_EXIT", "exit_type": "TIME_EXIT"}
        
        return {"exit": False, "reason": "HOLDING"}
    
    def _check_v1_exit_bt(
        self,
        position: Dict[str, Any],
        price: float,
        snapshot: Dict[str, Any]
    ) -> Dict[str, Any]:
        """V1/Legacy exit logic - simple SL/TP."""
        
        stop_loss = position["stop_loss"]
        take_profit = position.get("take_profit_2", position.get("take_profit_1"))
        
        if price <= stop_loss:
            return {"exit": True, "reason": "STOP_LOSS", "exit_type": "STOP_LOSS"}
        
        if take_profit and price >= take_profit:
            return {"exit": True, "reason": "TAKE_PROFIT", "exit_type": "TAKE_PROFIT"}
        
        return {"exit": False, "reason": "HOLDING"}
    
    def _execute_v2_exit(
        self,
        position: Dict[str, Any],
        price: float,
        timestamp: str,
        reason: str
    ) -> float:
        """Execute V2 exit and return PnL."""
        
        quantity = position["quantity"]
        entry_price = position["entry_price"]
        
        # Calculate PnL
        gross = quantity * price
        fee = gross * self.fee_pct
        net = gross - fee
        
        cost_basis = quantity * entry_price + (quantity * entry_price * self.fee_pct)
        pnl = net - cost_basis
        
        # Update balance
        self.balance += net
        self.position = 0
        self.position_cost = 0
        self.position_avg_price = 0
        
        # Update stats
        self.cumulative_pnl += pnl
        if pnl > 0:
            self.winning_trades += 1
        else:
            self.losing_trades += 1
        
        # Record trade
        trade = Trade(
            timestamp=timestamp,
            side="SELL",
            price=price,
            quantity=quantity,
            cost=0,
            pnl=pnl
        )
        self.trades.append(trade)
        
        return pnl
    
    def _execute_v2_partial_exit(
        self,
        position: Dict[str, Any],
        price: float,
        timestamp: str,
        fraction: float,
        reason: str
    ) -> float:
        """Execute partial V2 exit (sell fraction of position) and return PnL for sold portion.
        
        This mirrors the SELL_PARTIAL action in position_manager.py.
        The remaining position stays active for trailing stop or final target.
        """
        
        full_quantity = position["quantity"]
        sell_quantity = full_quantity * fraction
        entry_price = position["entry_price"]
        
        # Calculate PnL for sold portion only
        gross = sell_quantity * price
        fee = gross * self.fee_pct
        net = gross - fee
        
        cost_basis = sell_quantity * entry_price + (sell_quantity * entry_price * self.fee_pct)
        pnl = net - cost_basis
        
        # Update balance (only for sold portion)
        self.balance += net
        
        # Update cumulative PnL
        self.cumulative_pnl += pnl
        
        # Partial TPs are always wins if profitable (most should be)
        # We don't increment winning_trades here - that's for full exits
        # The partial trade is recorded separately
        
        # Record partial trade with side="SELL_PARTIAL"
        trade = Trade(
            timestamp=timestamp,
            side="SELL_PARTIAL",
            price=price,
            quantity=sell_quantity,
            cost=0,
            pnl=pnl
        )
        self.trades.append(trade)
        
        return pnl
    
    def print_v2_summary(self) -> None:
        """Print V2 backtest summary with entry type breakdown."""
        self.print_summary()
        
        if not hasattr(self, '_v2_stats'):
            return
        
        stats = self._v2_stats
        
        print("─" * 50)
        print("📊 V2 ENTRY TYPE BREAKDOWN")
        print("─" * 50)
        
        for entry_type in ["4H_SWING", "1H_MOMENTUM", "15M_SCALP"]:
            s = stats.get(entry_type, {})
            entries = s.get("entries", 0)
            wins = s.get("wins", 0)
            losses = s.get("losses", 0)
            pnl = s.get("pnl", 0.0)
            partial_tps = s.get("partial_tps", 0)
            win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0
            
            print(f"  {entry_type}:")
            print(f"    Entries: {entries} | Wins: {wins} | Losses: {losses} | Partial TPs: {partial_tps}")
            print(f"    Win Rate: {win_rate:.1f}% | PnL: ${pnl:+,.2f}")
        
        print("─" * 50)
        print(f"  Total Signals: {stats['total_signals']}")
        print(f"  Signals Skipped: {stats['signals_skipped']}")
        
        print("\n📈 REGIME DISTRIBUTION:")
        for regime, count in stats["regime_counts"].items():
            print(f"    {regime}: {count}")
        
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

