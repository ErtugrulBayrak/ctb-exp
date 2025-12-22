"""
Signal ID Deterministic Test
Iki kez backtest calistirip signal_id'lerin ayni olup olmadigini kontrol eder.
"""
import pandas as pd
import numpy as np
from strategies.swing_trend_v1 import SwingTrendV1


def run_backtest_and_collect_signals(run_name: str):
    """Backtest'i calistir ve signal_id'leri topla."""
    np.random.seed(42)  # Deterministic veri icin sabit seed
    n_candles = 20
    
    # Sabit timestamp'ler (deterministik test icin)
    timestamps = pd.date_range('2024-01-01', periods=n_candles, freq='4h')
    
    print(f"\n{'='*60}")
    print(f"[TEST] {run_name}")
    print(f"{'='*60}")
    
    # V1 Strategy instance - HER RUN ICIN YENI INSTANCE (idempotency kontrol)
    strategy = SwingTrendV1()
    
    signals_collected = []
    
    # Her bar icin sinyal evaluasyonu - V1 kosullarini saglayan snapshot'lar
    for idx in range(n_candles):
        # Her 5 bardan birinde entry sinyali uret
        if idx % 5 == 0:
            # BUY sinyali icin uygun snapshot (V1 kosullari saglar)
            # EMA20 > EMA50, breakout, rejim OK
            ts_epoch = int(timestamps[idx].timestamp())
            
            snapshot = {
                "symbol": "BTCUSDT",
                "price": 50000.0 + idx * 100,
                "tf": {
                    "1h": {
                        "ema20": 50200.0 + idx * 100,  # EMA20 > EMA50
                        "ema50": 49500.0 + idx * 100,
                        "ema50_prev": 49400.0 + idx * 100,  # Positive slope
                        "atr": 800.0,
                        "adx": 30.0,  # ADX >= 25 rejim filtresi icin
                        "last_closed_ts": ts_epoch,
                    },
                    "15m": {
                        "close": 50050.0 + idx * 100,  # Close > highest_high = breakout
                        "highest_high": 49900.0 + idx * 100,
                        "highest_close": 49850.0 + idx * 100,
                        "last_closed_ts": ts_epoch,
                    }
                },
                "technical": {
                    "adx": 30.0,
                    "atr": 800.0,
                    "atr_pct": 1.6,  # ATR/price * 100
                },
                "volume_24h": 1_000_000_000,
                "volume_avg": 800_000_000,
                "has_open_position": False,
                "entry_price": 0,
            }
        else:
            # HOLD icin uygun olmayan snapshot (breakout yok)
            ts_epoch = int(timestamps[idx].timestamp())
            
            snapshot = {
                "symbol": "BTCUSDT",
                "price": 49900.0 + idx * 100,
                "tf": {
                    "1h": {
                        "ema20": 50200.0,
                        "ema50": 49500.0,
                        "ema50_prev": 49400.0,
                        "atr": 800.0,
                        "adx": 30.0,
                        "last_closed_ts": ts_epoch,
                    },
                    "15m": {
                        "close": 49800.0,  # Close < highest_high = NO breakout
                        "highest_high": 50000.0,
                        "highest_close": 49950.0,
                        "last_closed_ts": ts_epoch,
                    }
                },
                "technical": {"adx": 30.0, "atr": 800.0, "atr_pct": 1.6},
                "volume_24h": 1_000_000_000,
                "volume_avg": 800_000_000,
                "has_open_position": False,
                "entry_price": 0,
            }
        
        # Evaluate signal
        result = strategy.evaluate_entry(snapshot, balance=10000.0)
        
        if result.action == "BUY":
            sig_id = result.signal_id
            signals_collected.append({
                "bar_idx": idx,
                "timestamp": str(timestamps[idx]),
                "action": result.action,
                "signal_id": sig_id
            })
            print(f"  [{idx:3d}] {result.action:4s} | signal_id: {sig_id}")
        # else:
        #     print(f"  [{idx:3d}] HOLD | Reason: {result.reason[:50]}...")
    
    print(f"\n[INFO] Toplam sinyal: {len(signals_collected)}")
    
    return signals_collected


def main():
    """Iki kez calistir, karsilastir."""
    print("\n" + "="*70)
    print("[TEST] SIGNAL_ID DETERMINISTIC TEST")
    print("="*70)
    
    # Run 1
    run1_signals = run_backtest_and_collect_signals("RUN 1")
    
    # Run 2
    run2_signals = run_backtest_and_collect_signals("RUN 2")
    
    # Karsilastirma
    print("\n" + "="*60)
    print("[RESULTS] KARSILASTIRMA SONUCLARI")
    print("="*60)
    
    if len(run1_signals) != len(run2_signals):
        print(f"[FAIL] FARKLI sinyal sayisi! Run1: {len(run1_signals)}, Run2: {len(run2_signals)}")
    elif len(run1_signals) == 0:
        print(f"[WARN] Hic sinyal uretilmedi! Entry kosullarini kontrol edin.")
    else:
        all_match = True
        for i, (s1, s2) in enumerate(zip(run1_signals, run2_signals)):
            if s1['signal_id'] != s2['signal_id']:
                print(f"[FAIL] UYUSMAZLIK #{i}:")
                print(f"   Run1: {s1['signal_id']}")
                print(f"   Run2: {s2['signal_id']}")
                all_match = False
            else:
                print(f"[OK] Match #{i}: {s1['signal_id']}")
        
        if all_match:
            print(f"\n[SUCCESS] Tum {len(run1_signals)} signal_id deterministik olarak eslesiyor!")
        else:
            print(f"\n[ERROR] Bazi signal_id'ler eslesmiyor!")


if __name__ == "__main__":
    main()
