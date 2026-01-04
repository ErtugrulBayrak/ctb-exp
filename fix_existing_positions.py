"""
fix_existing_positions.py - One-time fix for partial_tp_target NULL issue
===========================================================================

Run this script ONCE to update existing positions in portfolio.json that have
null partial_tp_target values.

Usage:
    python fix_existing_positions.py

This will:
1. Load portfolio.json
2. Find positions with null partial_tp_target
3. Calculate partial_tp_target based on entry_type and config:
   - 1H_MOMENTUM: +2% from entry_price
   - 4H_SWING: +5% from entry_price
4. Save updated portfolio.json
"""

import json
import shutil
from datetime import datetime

# Config values
MOMENTUM_1H_PARTIAL_TP_PCT = 2.0  # %
SWING_4H_PARTIAL_TP_PCT = 5.0     # %


def fix_positions():
    portfolio_file = "portfolio.json"
    
    # Create backup
    backup_file = f"portfolio_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    shutil.copy(portfolio_file, backup_file)
    print(f"✅ Backup created: {backup_file}")
    
    # Load portfolio
    with open(portfolio_file, 'r', encoding='utf-8') as f:
        portfolio = json.load(f)
    
    positions = portfolio.get("positions", [])
    updated_count = 0
    
    print(f"\n📊 Found {len(positions)} open positions")
    print("-" * 50)
    
    for pos in positions:
        symbol = pos.get("symbol", "UNKNOWN")
        entry_price = pos.get("entry_price", 0)
        entry_type = pos.get("entry_type", "UNKNOWN")
        current_partial_tp = pos.get("partial_tp_target")
        
        if current_partial_tp is not None:
            print(f"  {symbol}: partial_tp_target already set (${current_partial_tp:.2f}), skipping")
            continue
        
        # Calculate based on entry_type
        if entry_type == "1H_MOMENTUM":
            partial_tp_pct = MOMENTUM_1H_PARTIAL_TP_PCT
            new_partial_tp = entry_price * (1 + partial_tp_pct / 100)
        elif entry_type == "4H_SWING":
            partial_tp_pct = SWING_4H_PARTIAL_TP_PCT
            new_partial_tp = entry_price * (1 + partial_tp_pct / 100)
        else:
            print(f"  ⚠️ {symbol}: Unknown entry_type '{entry_type}', using 2% default")
            partial_tp_pct = 2.0
            new_partial_tp = entry_price * (1 + partial_tp_pct / 100)
        
        # Update position
        pos["partial_tp_target"] = round(new_partial_tp, 2)
        pos["partial_tp_hit"] = pos.get("partial_tp_hit", False)  # Ensure field exists
        
        print(f"  ✅ {symbol}: entry=${entry_price:.2f} → partial_tp=${new_partial_tp:.2f} (+{partial_tp_pct}%)")
        updated_count += 1
    
    print("-" * 50)
    
    if updated_count > 0:
        # Save updated portfolio
        with open(portfolio_file, 'w', encoding='utf-8') as f:
            json.dump(portfolio, f, indent=2, ensure_ascii=False)
        print(f"\n✅ Updated {updated_count} positions and saved to {portfolio_file}")
    else:
        print(f"\n✅ No positions needed updating")


if __name__ == "__main__":
    print("=" * 60)
    print("🔧 FIX EXISTING POSITIONS - Partial TP Target")
    print("=" * 60)
    
    try:
        fix_positions()
    except FileNotFoundError:
        print("❌ portfolio.json not found!")
    except Exception as e:
        print(f"❌ Error: {e}")
    
    print("\n🏁 Done!")
