"""
swing_trend_v1.py - V1 Swing Trend Strateji Modülü
====================================================

Long-only swing trading stratejisi.

Entry Koşulları (1h üzerinden üret, 15m ile tetikle):
1. Trend yapısı: EMA20(1h) > EMA50(1h) ve EMA50 slope > 0
2. Breakout: Close(15m) > HighestHigh(20, 15m)
3. Rejim filtresi geçilmeli

Exit Mantığı:
1. İlk stop: SL = entry - SL_ATR_MULT * ATR(14, 1h)
2. Kısmi kâr alma: 1R'de pozisyonun %50'sini sat
3. Trailing stop: Chandelier (HighestClose - TRAIL_ATR_MULT * ATR)

Kullanım:
    from strategies.swing_trend_v1 import SwingTrendV1
    
    strategy = SwingTrendV1()
    decision = strategy.evaluate_entry(snapshot, balance)
    exit_update = strategy.update_exit(position, snapshot)
"""

import time
import uuid
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass, field

# Logger import
try:
    from trade_logger import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)

# Config import
try:
    from config import SETTINGS
except ImportError:
    class MockSettings:
        SL_ATR_MULT = 1.5
        PARTIAL_TP_ENABLED = True
        PARTIAL_TP_FRACTION = 0.5
        TRAILING_ENABLED = True
        TRAIL_LOOKBACK = 22
        TRAIL_ATR_MULT = 3.0
        EMA_SLOPE_LOOKBACK = 5
        BREAKOUT_LOOKBACK = 20
        RISK_PER_TRADE_V1 = 1.0
        TARGET_ATR_PCT = 1.0
        MIN_VOL_SCALE = 0.5
        MAX_VOL_SCALE = 1.5
    SETTINGS = MockSettings()

# Regime filter import
try:
    from strategies.regime_filter import RegimeFilter
except ImportError:
    RegimeFilter = None


@dataclass
class EntrySignal:
    """Entry sinyal sonucu."""
    action: str  # "BUY" veya "HOLD"
    confidence: int  # 0-100
    reason: str
    stop_loss: float
    take_profit: float  # 1R seviyesi
    quantity: float
    signal_id: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExitUpdate:
    """Exit güncelleme sonucu."""
    action: str  # "PARTIAL_TP", "TRAILING_UPDATE", "SL_HIT", "TRAILING_HIT", "HOLD"
    new_sl: Optional[float] = None
    sell_quantity: Optional[float] = None
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class SwingTrendV1:
    """
    V1 Swing Trend Stratejisi - Long-only, deterministik.
    
    Entry: Trend yapısı + Breakout + Rejim filtresi
    Exit: ATR-based SL + 1R Partial TP + Chandelier Trailing
    """
    
    def __init__(
        self,
        sl_atr_mult: float = None,
        partial_tp_enabled: bool = None,
        partial_tp_fraction: float = None,
        trailing_enabled: bool = None,
        trail_lookback: int = None,
        trail_atr_mult: float = None,
        ema_slope_lookback: int = None,
        breakout_lookback: int = None,
        risk_pct: float = None,
        target_atr_pct: float = None,
        min_vol_scale: float = None,
        max_vol_scale: float = None
    ):
        """
        SwingTrendV1 başlat.
        
        Tüm parametreler opsiyoneldir, config'den varsayılanlar kullanılır.
        """
        self.sl_atr_mult = sl_atr_mult if sl_atr_mult is not None else getattr(SETTINGS, 'SL_ATR_MULT', 1.5)
        self.partial_tp_enabled = partial_tp_enabled if partial_tp_enabled is not None else getattr(SETTINGS, 'PARTIAL_TP_ENABLED', True)
        self.partial_tp_fraction = partial_tp_fraction if partial_tp_fraction is not None else getattr(SETTINGS, 'PARTIAL_TP_FRACTION', 0.5)
        self.trailing_enabled = trailing_enabled if trailing_enabled is not None else getattr(SETTINGS, 'TRAILING_ENABLED', True)
        self.trail_lookback = trail_lookback if trail_lookback is not None else getattr(SETTINGS, 'TRAIL_LOOKBACK', 22)
        self.trail_atr_mult = trail_atr_mult if trail_atr_mult is not None else getattr(SETTINGS, 'TRAIL_ATR_MULT', 3.0)
        self.ema_slope_lookback = ema_slope_lookback if ema_slope_lookback is not None else getattr(SETTINGS, 'EMA_SLOPE_LOOKBACK', 5)
        self.breakout_lookback = breakout_lookback if breakout_lookback is not None else getattr(SETTINGS, 'BREAKOUT_LOOKBACK', 20)
        self.risk_pct = risk_pct if risk_pct is not None else getattr(SETTINGS, 'RISK_PER_TRADE_V1', 1.0) / 100.0
        self.target_atr_pct = target_atr_pct if target_atr_pct is not None else getattr(SETTINGS, 'TARGET_ATR_PCT', 1.0)
        self.min_vol_scale = min_vol_scale if min_vol_scale is not None else getattr(SETTINGS, 'MIN_VOL_SCALE', 0.5)
        self.max_vol_scale = max_vol_scale if max_vol_scale is not None else getattr(SETTINGS, 'MAX_VOL_SCALE', 1.5)
        
        # Signal tracking for idempotency
        self._last_signals: Dict[str, str] = {}  # symbol -> signal_id
        
        # Regime filter
        self.regime_filter = RegimeFilter() if RegimeFilter else None
    
    def evaluate_entry(
        self,
        snapshot: Dict[str, Any],
        balance: float = 10000.0
    ) -> EntrySignal:
        """
        Entry fırsatını değerlendir.
        
        Args:
            snapshot: Piyasa snapshot'ı (tf.1h, tf.15m, price, technical vb.)
            balance: Kullanılabilir bakiye
        
        Returns:
            EntrySignal dataclass
        """
        symbol = snapshot.get("symbol", "UNKNOWN")
        price = snapshot.get("price", 0.0)
        
        # Varsayılan HOLD sonucu
        hold_signal = EntrySignal(
            action="HOLD",
            confidence=0,
            reason="No entry signal",
            stop_loss=0.0,
            take_profit=0.0,
            quantity=0.0,
            signal_id="",
            metadata={"symbol": symbol, "price": price}
        )
        
        if not price or price <= 0:
            hold_signal.reason = "Fiyat verisi eksik"
            return hold_signal
        
        # ─────────────────────────────────────────────────────────────────────────
        # 1. Rejim Filtresi
        # ─────────────────────────────────────────────────────────────────────────
        if self.regime_filter:
            regime_passed, regime_result = self.regime_filter.check(snapshot)
            if not regime_passed:
                hold_signal.reason = f"Rejim filtresi: {regime_result.reason}"
                hold_signal.metadata["blocked_by_regime"] = True
                hold_signal.metadata["regime_details"] = {
                    "adx": regime_result.adx_value,
                    "atr_pct": regime_result.atr_pct,
                    "volume_ratio": regime_result.volume_ratio
                }
                return hold_signal
        
        # ─────────────────────────────────────────────────────────────────────────
        # 2. Trend Yapısı Kontrolü (1h timeframe)
        # ─────────────────────────────────────────────────────────────────────────
        tf_1h = snapshot.get("tf", {}).get("1h", {})
        technical = snapshot.get("technical", {})
        
        ema20 = tf_1h.get("ema20", technical.get("ema20"))
        ema50 = tf_1h.get("ema50", technical.get("ema50"))
        ema50_prev = tf_1h.get("ema50_prev", technical.get("ema50_prev"))
        
        # EMA değerleri yoksa fallback
        if not ema20 or not ema50:
            hold_signal.reason = "EMA verileri eksik"
            hold_signal.metadata["missing_data"] = ["ema20", "ema50"]
            return hold_signal
        
        # Trend yapısı: EMA20 > EMA50
        trend_ok = ema20 > ema50
        if not trend_ok:
            hold_signal.reason = f"Trend yapısı negatif: EMA20({ema20:.2f}) <= EMA50({ema50:.2f})"
            hold_signal.metadata["trend_ok"] = False
            return hold_signal
        
        # EMA50 slope kontrolü
        ema50_slope_ok = True
        if ema50_prev and ema50_prev > 0:
            ema50_slope = ema50 - ema50_prev
            ema50_slope_ok = ema50_slope > 0
            if not ema50_slope_ok:
                hold_signal.reason = f"EMA50 slope negatif: {ema50_slope:.4f}"
                hold_signal.metadata["ema50_slope"] = ema50_slope
                return hold_signal
        
        # ─────────────────────────────────────────────────────────────────────────
        # 3. Breakout Kontrolü (15m timeframe)
        # ─────────────────────────────────────────────────────────────────────────
        tf_15m = snapshot.get("tf", {}).get("15m", {})
        
        highest_high = tf_15m.get("highest_high", technical.get("highest_high"))
        highest_close = tf_15m.get("highest_close", technical.get("highest_close"))
        close_15m = tf_15m.get("close", price)
        
        breakout_ok = False
        breakout_type = None
        
        if highest_high and close_15m > highest_high:
            breakout_ok = True
            breakout_type = "highest_high"
        elif highest_close and close_15m > highest_close:
            breakout_ok = True
            breakout_type = "highest_close"
        
        if not breakout_ok:
            hold_signal.reason = f"Breakout yok: Close({close_15m:.2f}) <= HH({highest_high or 0:.2f})"
            hold_signal.metadata["breakout_ok"] = False
            return hold_signal
        
        # ─────────────────────────────────────────────────────────────────────────
        # 4. Idempotency Kontrolü (Deterministic signal_id)
        # ─────────────────────────────────────────────────────────────────────────
        trigger_tf = getattr(SETTINGS, 'TRIGGER_TIMEFRAME', '15m')
        last_closed_ts = tf_15m.get("last_closed_ts", 0)
        signal_id_src = "trigger_last_closed_ts"
        
        # Fallback chain
        if not last_closed_ts:
            # Try 1h as fallback
            last_closed_ts = tf_1h.get("last_closed_ts", 0)
            signal_id_src = "candle_fallback_1h"
        
        if not last_closed_ts:
            # Last resort: wall-clock fallback
            last_closed_ts = int(time.time() // 3600) * 3600
            signal_id_src = "wall_clock_fallback"
            logger.warning(f"[V1] {symbol}: signal_id_fallback=wall_clock (no candle timestamp available)")
        
        signal_id = f"{symbol}_{trigger_tf}_{last_closed_ts}"
        
        if symbol in self._last_signals and self._last_signals[symbol] == signal_id:
            hold_signal.reason = "Duplicate signal engellendi (idempotency)"
            hold_signal.metadata["duplicate"] = True
            hold_signal.metadata["signal_id"] = signal_id
            hold_signal.metadata["signal_id_src"] = signal_id_src
            logger.debug(f"[V1 IDEM] {symbol}: Duplicate blocked | signal_id={signal_id} | src={signal_id_src}")
            return hold_signal
        
        # ─────────────────────────────────────────────────────────────────────────
        # 5. SL/TP Hesaplama
        # ─────────────────────────────────────────────────────────────────────────
        atr = tf_1h.get("atr", technical.get("atr", price * 0.02))  # Fallback %2
        if not atr:
            atr = price * 0.02
        
        stop_loss = price - (self.sl_atr_mult * atr)
        stop_distance = price - stop_loss
        take_profit = price + stop_distance  # 1R = entry + stop_distance
        
        # ─────────────────────────────────────────────────────────────────────────
        # 6. Pozisyon Boyutu (Volatilite Hedeflemeli)
        # ─────────────────────────────────────────────────────────────────────────
        quantity = self._calculate_quantity(
            balance=balance,
            price=price,
            stop_loss=stop_loss,
            atr=atr
        )
        
        if quantity <= 0:
            hold_signal.reason = "Pozisyon boyutu hesaplanamadı"
            return hold_signal
        
        # ─────────────────────────────────────────────────────────────────────────
        # 7. BUY Sinyali Oluştur
        # ─────────────────────────────────────────────────────────────────────────
        self._last_signals[symbol] = signal_id
        
        confidence = 75  # Deterministik strateji için sabit güven
        
        # ADX'e göre güven ayarla
        adx = tf_1h.get("adx", technical.get("adx", 20))
        if adx and adx >= 30:
            confidence = min(90, confidence + 10)
        elif adx and adx >= 25:
            confidence = min(85, confidence + 5)
        
        logger.info(
            f"[V1 ENTRY] {symbol}: BUY signal | "
            f"Price={price:.2f} | SL={stop_loss:.2f} | TP={take_profit:.2f} | "
            f"Qty={quantity:.6f} | Conf={confidence} | "
            f"EMA20={ema20:.2f} > EMA50={ema50:.2f} | "
            f"Breakout={breakout_type}"
        )
        
        return EntrySignal(
            action="BUY",
            confidence=confidence,
            reason=f"V1 Entry: Trend OK (EMA20>EMA50), Breakout ({breakout_type})",
            stop_loss=stop_loss,
            take_profit=take_profit,
            quantity=quantity,
            signal_id=signal_id,
            metadata={
                "symbol": symbol,
                "price": price,
                "ema20": ema20,
                "ema50": ema50,
                "breakout_type": breakout_type,
                "atr": atr,
                "adx": adx,
                "stop_distance": stop_distance,
                "risk_pct": self.risk_pct * 100
            }
        )
    
    def _calculate_quantity(
        self,
        balance: float,
        price: float,
        stop_loss: float,
        atr: float
    ) -> float:
        """
        Volatilite hedeflemeli pozisyon boyutu hesapla.
        
        Formula:
        1. size_usd = balance * risk_pct
        2. stop_dist = price - stop_loss
        3. qty = size_usd / stop_dist
        4. atr_pct = atr / price * 100
        5. vol_scale = clamp(target_atr_pct / atr_pct, min_scale, max_scale)
        6. qty *= vol_scale
        """
        if balance <= 0 or price <= 0:
            return 0.0
        
        stop_dist = price - stop_loss
        if stop_dist <= 0:
            stop_dist = price * 0.02  # Fallback %2
        
        # Risk bazlı boyut
        size_usd = balance * self.risk_pct
        qty = size_usd / stop_dist
        
        # Volatilite ölçekleme
        if atr and price > 0:
            atr_pct = (atr / price) * 100
            if atr_pct > 0:
                vol_scale = self.target_atr_pct / atr_pct
                vol_scale = max(self.min_vol_scale, min(self.max_vol_scale, vol_scale))
                qty *= vol_scale
        
        # Max %10 cap
        max_qty = (balance * 0.10) / price
        qty = min(qty, max_qty)
        
        return round(qty, 8)
    
    def update_exit(
        self,
        position: Dict[str, Any],
        snapshot: Dict[str, Any]
    ) -> ExitUpdate:
        """
        Açık pozisyonun exit durumunu güncelle.
        
        Kontroller:
        1. SL tetiklendi mi?
        2. Partial TP (1R) tetiklendi mi?
        3. Trailing stop güncellenmeli mi?
        4. Trailing stop tetiklendi mi?
        
        Args:
            position: Açık pozisyon verisi
            snapshot: Güncel piyasa snapshot'ı
        
        Returns:
            ExitUpdate dataclass
        """
        current_price = snapshot.get("price", 0.0)
        entry_price = position.get("entry_price", position.get("giris_fiyati", 0.0))
        current_sl = position.get("current_sl", position.get("stop_loss", 0.0))
        initial_sl = position.get("initial_sl", current_sl)
        partial_taken = position.get("partial_taken", False)
        partial_tp_price = position.get("partial_tp_price", 0.0)
        quantity = position.get("quantity", position.get("miktar", 0.0))
        
        if not current_price or current_price <= 0:
            return ExitUpdate(action="HOLD", reason="Fiyat verisi eksik")
        
        # ─────────────────────────────────────────────────────────────────────────
        # 1. SL Kontrolü
        # ─────────────────────────────────────────────────────────────────────────
        if current_sl and current_price <= current_sl:
            return ExitUpdate(
                action="SL_HIT",
                reason=f"Stop Loss tetiklendi: {current_price:.2f} <= {current_sl:.2f}",
                metadata={"exit_price": current_price, "sl_price": current_sl}
            )
        
        # ─────────────────────────────────────────────────────────────────────────
        # 2. Partial TP Kontrolü (1R)
        # ─────────────────────────────────────────────────────────────────────────
        if self.partial_tp_enabled and not partial_taken:
            # 1R hesapla: entry + (entry - initial_sl)
            if entry_price and initial_sl:
                stop_distance = entry_price - initial_sl
                one_r_price = entry_price + stop_distance
                
                if current_price >= one_r_price:
                    sell_qty = quantity * self.partial_tp_fraction
                    return ExitUpdate(
                        action="PARTIAL_TP",
                        sell_quantity=sell_qty,
                        reason=f"1R seviyesi ({one_r_price:.2f}) aşıldı, partial TP",
                        metadata={
                            "one_r_price": one_r_price,
                            "current_price": current_price,
                            "sell_fraction": self.partial_tp_fraction
                        }
                    )
        
        # ─────────────────────────────────────────────────────────────────────────
        # 3. Trailing Stop Güncelleme
        # ─────────────────────────────────────────────────────────────────────────
        if self.trailing_enabled and partial_taken:
            tf_1h = snapshot.get("tf", {}).get("1h", {})
            technical = snapshot.get("technical", {})
            
            highest_close = tf_1h.get("highest_close_trail", snapshot.get("highest_close"))
            atr = tf_1h.get("atr", technical.get("atr", 0.0))
            
            if highest_close and atr:
                new_trail_sl = highest_close - (self.trail_atr_mult * atr)
                
                # Trailing sadece yukarı güncellenir (never loosen)
                if new_trail_sl > current_sl:
                    # Trailing tetik kontrolü
                    if current_price <= new_trail_sl:
                        return ExitUpdate(
                            action="TRAILING_HIT",
                            new_sl=new_trail_sl,
                            reason=f"Trailing stop tetiklendi: {current_price:.2f} <= {new_trail_sl:.2f}",
                            metadata={
                                "highest_close": highest_close,
                                "atr": atr,
                                "old_sl": current_sl,
                                "new_sl": new_trail_sl
                            }
                        )
                    else:
                        return ExitUpdate(
                            action="TRAILING_UPDATE",
                            new_sl=new_trail_sl,
                            reason=f"Trailing stop güncellendi: {current_sl:.2f} -> {new_trail_sl:.2f}",
                            metadata={
                                "highest_close": highest_close,
                                "atr": atr,
                                "old_sl": current_sl,
                                "new_sl": new_trail_sl
                            }
                        )
        
        # ─────────────────────────────────────────────────────────────────────────
        # 4. Trailing Stop Tetik (güncelleme olmadan)
        # ─────────────────────────────────────────────────────────────────────────
        if self.trailing_enabled and partial_taken and current_sl:
            if current_price <= current_sl:
                return ExitUpdate(
                    action="TRAILING_HIT",
                    reason=f"Trailing stop tetiklendi: {current_price:.2f} <= {current_sl:.2f}",
                    metadata={"exit_price": current_price, "trailing_sl": current_sl}
                )
        
        return ExitUpdate(action="HOLD", reason="Pozisyon devam ediyor")
    
    def calculate_position_fields(
        self,
        entry_price: float,
        quantity: float,
        atr: float
    ) -> Dict[str, Any]:
        """
        Yeni pozisyon için V1 alanlarını hesapla.
        
        Bu alanlar pozisyon açıldığında portfolio'ya eklenir.
        
        Args:
            entry_price: Giriş fiyatı
            quantity: Pozisyon miktarı
            atr: ATR değeri
        
        Returns:
            V1 position fields dict
        """
        initial_sl = entry_price - (self.sl_atr_mult * atr)
        stop_distance = entry_price - initial_sl
        partial_tp_price = entry_price + stop_distance  # 1R
        
        return {
            "initial_sl": initial_sl,
            "current_sl": initial_sl,
            "partial_taken": False,
            "partial_tp_price": partial_tp_price,
            "trailing_enabled": self.trailing_enabled,
            "highest_close_since_entry": entry_price,
            "last_trailing_update_ts": None,
            "strategy_id": "SWING_TREND_V1",
            "signal_id": f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
        }


# ═══════════════════════════════════════════════════════════════════════════════
# TEST / DEMO
# ═══════════════════════════════════════════════════════════════════════════════

def demo():
    """SwingTrendV1 demo."""
    print("\n" + "=" * 60)
    print("📈 SWING TREND V1 DEMO")
    print("=" * 60)
    
    strategy = SwingTrendV1()
    
    # Test 1: Valid entry signal
    snapshot_buy = {
        "symbol": "BTCUSDT",
        "price": 50000.0,
        "tf": {
            "1h": {
                "ema20": 50200.0,
                "ema50": 49500.0,
                "ema50_prev": 49400.0,
                "atr": 800.0,
                "adx": 28.0
            },
            "15m": {
                "close": 50050.0,
                "highest_high": 49900.0,
                "highest_close": 49850.0
            }
        },
        "technical": {
            "adx": 28.0,
            "atr": 800.0
        },
        "volume_24h": 1_000_000_000,
        "volume_avg": 800_000_000
    }
    
    signal = strategy.evaluate_entry(snapshot_buy, balance=10000.0)
    print(f"\n✅ Test 1 (BUY Signal):")
    print(f"   Action: {signal.action}")
    print(f"   Confidence: {signal.confidence}")
    print(f"   SL: ${signal.stop_loss:.2f}")
    print(f"   TP (1R): ${signal.take_profit:.2f}")
    print(f"   Quantity: {signal.quantity:.6f}")
    print(f"   Reason: {signal.reason}")
    
    # Test 2: No breakout
    snapshot_no_breakout = {
        "symbol": "ETHUSDT",
        "price": 3000.0,
        "tf": {
            "1h": {
                "ema20": 3050.0,
                "ema50": 2950.0,
                "ema50_prev": 2940.0,
                "atr": 50.0,
                "adx": 25.0
            },
            "15m": {
                "close": 3000.0,
                "highest_high": 3050.0,  # Close < HH
                "highest_close": 3020.0
            }
        },
        "technical": {"adx": 25.0, "atr": 50.0}
    }
    
    signal2 = strategy.evaluate_entry(snapshot_no_breakout, balance=10000.0)
    print(f"\n❌ Test 2 (No Breakout):")
    print(f"   Action: {signal2.action}")
    print(f"   Reason: {signal2.reason}")
    
    # Test 3: Exit update - partial TP
    position = {
        "entry_price": 50000.0,
        "quantity": 0.1,
        "initial_sl": 48800.0,
        "current_sl": 48800.0,
        "partial_taken": False
    }
    
    snapshot_1r = {
        "price": 51300.0  # > 1R (51200)
    }
    
    exit_update = strategy.update_exit(position, snapshot_1r)
    print(f"\n📊 Test 3 (Partial TP):")
    print(f"   Action: {exit_update.action}")
    print(f"   Sell Qty: {exit_update.sell_quantity}")
    print(f"   Reason: {exit_update.reason}")
    
    print("\n" + "=" * 60)
    print("✅ Demo tamamlandı!")
    print("=" * 60)


if __name__ == "__main__":
    demo()
