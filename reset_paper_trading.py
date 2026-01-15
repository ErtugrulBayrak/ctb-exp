"""
reset_paper_trading.py - Paper Trading Data Reset Script
=========================================================

This script resets all data files for a fresh paper trading start.
Run this before starting a new paper trading session to clear:
- Portfolio (positions, balance, history)
- Trade logs
- Order ledger
- Metrics and state files
- Terminal logs (optional)

Usage:
    python reset_paper_trading.py           # Reset with prompts
    python reset_paper_trading.py --force   # Reset without prompts
    python reset_paper_trading.py --all     # Also clear log files
"""

import os
import json
from datetime import datetime
from pathlib import Path

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

# Base directory (same as this script)
BASE_DIR = Path(__file__).parent

# Starting balance for paper trading
DEFAULT_STARTING_BALANCE = 1000.0

# Files to reset with their default content
RESET_FILES = {
    "portfolio.json": {
        "balance": DEFAULT_STARTING_BALANCE,
        "positions": [],
        "history": []
    },
    "trade_log.json": {
        "stats": {
            "total_buys": 0,
            "total_sells": 0,
            "wins": 0,
            "losses": 0
        },
        "decisions": [],
        "trades": []
    },
    "data/order_ledger.json": {},
    "data/alert_state.json": {
        "last_alert_times": {},
        "throttle_counts": {}
    },
    "data/summary_state.json": {
        "cycle_count": 0,
        "run_id": None
    }
}

# Optional log files to clear (only with --all flag)
LOG_PATTERNS = [
    "logs/trader.log",
    "logs/terminal_log_*.txt",
    "logs/diagnostics_*.json"
]

# ═══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def reset_file(relative_path: str, default_content: dict) -> bool:
    """Reset a file to its default content."""
    file_path = BASE_DIR / relative_path
    
    # Create directory if needed
    file_path.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        # Write default content
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(default_content, f, indent=2, ensure_ascii=False)
        
        print(f"  ✅ Reset: {relative_path}")
        return True
    except Exception as e:
        print(f"  ❌ Failed: {relative_path} - {e}")
        return False

def clear_log_files() -> int:
    """Clear all log files. Returns count of files deleted."""
    import glob
    
    deleted = 0
    
    for pattern in LOG_PATTERNS:
        full_pattern = str(BASE_DIR / pattern)
        for file_path in glob.glob(full_pattern):
            try:
                os.remove(file_path)
                print(f"  🗑️ Deleted: {os.path.basename(file_path)}")
                deleted += 1
            except Exception as e:
                print(f"  ⚠️ Could not delete {file_path}: {e}")
    
    # Create empty trader.log
    trader_log = BASE_DIR / "logs" / "trader.log"
    trader_log.parent.mkdir(parents=True, exist_ok=True)
    with open(trader_log, 'w', encoding='utf-8') as f:
        f.write(f"# Log reset at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    print(f"  ✅ Created fresh: trader.log")
    
    return deleted

def get_current_state() -> dict:
    """Get current portfolio state for display."""
    portfolio_path = BASE_DIR / "portfolio.json"
    
    if not portfolio_path.exists():
        return None
    
    try:
        with open(portfolio_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return None

# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    import sys
    
    force_mode = "--force" in sys.argv or "-f" in sys.argv
    clear_logs = "--all" in sys.argv or "-a" in sys.argv
    custom_balance = None
    
    # Check for custom balance
    for arg in sys.argv[1:]:
        if arg.startswith("--balance="):
            try:
                custom_balance = float(arg.split("=")[1])
            except ValueError:
                print(f"⚠️ Invalid balance value: {arg}")
    
    print("\n" + "=" * 60)
    print("🔄 PAPER TRADING RESET SCRIPT")
    print("=" * 60)
    
    # Show current state
    current = get_current_state()
    if current:
        print(f"\n📊 Current State:")
        print(f"   Balance: ${current.get('balance', 0):.2f}")
        print(f"   Open Positions: {len(current.get('positions', []))}")
        print(f"   Trade History: {len(current.get('history', []))} trades")
    
    # Show what will be reset
    starting_balance = custom_balance or DEFAULT_STARTING_BALANCE
    print(f"\n📋 Will Reset To:")
    print(f"   Starting Balance: ${starting_balance:.2f}")
    print(f"   Positions: Empty")
    print(f"   History: Cleared")
    print(f"   Logs: {'Will be cleared' if clear_logs else 'Preserved (use --all to clear)'}")
    
    # Confirm if not force mode
    if not force_mode:
        print("\n" + "-" * 60)
        response = input("⚠️ This will DELETE all trading data. Continue? (y/N): ").strip().lower()
        if response not in ['y', 'yes']:
            print("❌ Cancelled.")
            return
    
    # Update balance if custom
    if custom_balance:
        RESET_FILES["portfolio.json"]["balance"] = custom_balance
    
    print("\n" + "-" * 60)
    print("🔄 Resetting files...")
    print("-" * 60)
    
    # Reset all data files (no backup)
    success_count = 0
    for file_path, default_content in RESET_FILES.items():
        if reset_file(file_path, default_content):
            success_count += 1
    
    # Clear logs if requested
    if clear_logs:
        print("\n" + "-" * 60)
        print("🗑️ Clearing log files...")
        print("-" * 60)
        deleted = clear_log_files()
        print(f"   Deleted {deleted} log files")
    
    print("\n" + "=" * 60)
    print(f"✅ RESET COMPLETE - {success_count}/{len(RESET_FILES)} files reset")
    print(f"💰 Starting Balance: ${starting_balance:.2f}")
    print("=" * 60)
    print("\n🚀 Ready to start fresh paper trading!\n")

if __name__ == "__main__":
    main()
