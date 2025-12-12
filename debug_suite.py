#!/usr/bin/env python
"""
debug_suite.py - Diagnostics Script for Trading Bot Stack
==========================================================

Validates the entire bot stack end-to-end WITHOUT placing any orders.

Usage:
    python debug_suite.py --mode deep --symbols BTCUSDT,ETHUSDT
    python debug_suite.py --mode quick --skip-llm 1
    python debug_suite.py --test-router 1

Checks:
    1.  CHECK_IMPORTS          - Required dependencies
    2.  CHECK_ENV_KEYS         - API key validation (masked)
    3.  CHECK_LOGGER_MISUSE    - Scan for bare logger(...) calls
    4.  CHECK_FILE_IO          - logs/ directory permissions
    5.  CHECK_BINANCE_PUBLIC   - Ping, server time, drift
    6.  CHECK_BINANCE_MARKET   - Klines to DataFrame
    7.  CHECK_EXCHANGE_ROUTER  - (optional) Start/stop health
    8.  CHECK_ETHERSCAN        - Whale/on-chain fetch
    9.  CHECK_RSS_NEWS         - RSS feed fetch
    10. CHECK_ARTICLE_CONTENT  - (deep mode) Content extraction
    11. CHECK_LLM_NEWS         - LLM news analysis
    12. CHECK_LLM_STRATEGY     - LLM strategy evaluation

Output:
    - Terminal table: CHECK_NAME | STATUS | duration_ms | message
    - JSON artifact: logs/diagnostics_YYYYMMDD_HHMMSS.json
    - Exit code: 0 (no FAIL), 1 (any FAIL)
"""

import argparse
import asyncio
import glob
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Use standard logging for this script (avoid trade_logger recursion)
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
script_logger = logging.getLogger("debug_suite")


# ═══════════════════════════════════════════════════════════════════════════════
# CHECK RESULT DATACLASS
# ═══════════════════════════════════════════════════════════════════════════════
@dataclass
class CheckResult:
    """Result of a diagnostic check."""
    name: str
    status: str  # PASS, WARN, FAIL
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# UTILITY FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════
def mask_secret(value: str) -> str:
    """Mask a secret value, showing first 4 and last 4 chars."""
    if not value:
        return "❌ MISSING"
    if len(value) < 10:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def run_timed(func):
    """Decorator to time a check function."""
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        result = func(*args, **kwargs)
        elapsed_ms = (time.perf_counter() - start) * 1000
        result.duration_ms = round(elapsed_ms, 2)
        return result
    return wrapper


# ═══════════════════════════════════════════════════════════════════════════════
# CHECK IMPLEMENTATIONS
# ═══════════════════════════════════════════════════════════════════════════════

@run_timed
def check_imports() -> CheckResult:
    """
    CHECK_IMPORTS: Validate required Python dependencies.
    FAIL if requests/pandas/binance missing. WARN for optional ones.
    """
    required = ["requests", "pandas", "binance"]
    optional = ["feedparser", "dateutil", "newspaper", "google.generativeai"]
    
    missing_required = []
    missing_optional = []
    imported = []
    
    for mod in required:
        try:
            __import__(mod)
            imported.append(mod)
        except ImportError:
            missing_required.append(mod)
    
    for mod in optional:
        try:
            __import__(mod)
            imported.append(mod)
        except ImportError:
            missing_optional.append(mod)
    
    details = {
        "imported": imported,
        "missing_required": missing_required,
        "missing_optional": missing_optional
    }
    
    if missing_required:
        return CheckResult(
            name="CHECK_IMPORTS",
            status="FAIL",
            message=f"Missing required: {', '.join(missing_required)}",
            details=details
        )
    elif missing_optional:
        return CheckResult(
            name="CHECK_IMPORTS",
            status="WARN",
            message=f"Optional missing: {', '.join(missing_optional)}",
            details=details
        )
    else:
        return CheckResult(
            name="CHECK_IMPORTS",
            status="PASS",
            message=f"All {len(imported)} modules imported",
            details=details
        )


@run_timed
def check_env_keys(test_router: bool = False) -> CheckResult:
    """
    CHECK_ENV_KEYS: Validate API keys from config.py SETTINGS.
    Shows masked values. FAIL if Binance keys missing and router test enabled.
    """
    try:
        from config import SETTINGS
        settings_loaded = True
    except ImportError:
        return CheckResult(
            name="CHECK_ENV_KEYS",
            status="FAIL",
            message="Cannot import config.py SETTINGS",
            details={"error": "ImportError"}
        )
    
    keys_status = {}
    missing_critical = []
    missing_optional = []
    
    # Check each key
    key_checks = [
        ("BINANCE_API_KEY", SETTINGS.BINANCE_API_KEY, True),
        ("BINANCE_SECRET_KEY", SETTINGS.BINANCE_SECRET_KEY, True),
        ("GEMINI_API_KEY", SETTINGS.GEMINI_API_KEY, False),
        ("TELEGRAM_BOT_TOKEN", SETTINGS.TELEGRAM_BOT_TOKEN, False),
        ("TELEGRAM_CHAT_ID", SETTINGS.TELEGRAM_CHAT_ID, False),
    ]
    
    # Check ETHERSCAN_API_KEY from env
    etherscan_key = os.getenv("ETHERSCAN_API_KEY", "")
    key_checks.append(("ETHERSCAN_API_KEY", etherscan_key, False))
    
    for key_name, value, is_critical in key_checks:
        masked = mask_secret(value) if value else "❌ MISSING"
        keys_status[key_name] = masked
        
        if not value:
            if is_critical:
                missing_critical.append(key_name)
            else:
                missing_optional.append(key_name)
    
    details = {
        "keys": keys_status,
        "missing_critical": missing_critical,
        "missing_optional": missing_optional,
        "live_trading": SETTINGS.LIVE_TRADING
    }
    
    # FAIL if Binance keys missing AND router test enabled
    if test_router and missing_critical:
        return CheckResult(
            name="CHECK_ENV_KEYS",
            status="FAIL",
            message=f"Missing for router test: {', '.join(missing_critical)}",
            details=details
        )
    elif missing_critical:
        return CheckResult(
            name="CHECK_ENV_KEYS",
            status="WARN",
            message=f"Critical keys missing: {', '.join(missing_critical)}",
            details=details
        )
    elif missing_optional:
        return CheckResult(
            name="CHECK_ENV_KEYS",
            status="WARN",
            message=f"Optional keys missing: {', '.join(missing_optional)}",
            details=details
        )
    else:
        return CheckResult(
            name="CHECK_ENV_KEYS",
            status="PASS",
            message="All API keys configured",
            details=details
        )


@run_timed
def check_logger_misuse_scan(project_dir: str) -> CheckResult:
    r"""
    CHECK_LOGGER_MISUSE_SCAN: Scan all *.py files for bare logger(...) calls.
    This is CRITICAL for catching the TypeError issue.
    
    Pattern: \blogger\s*\(
    Exclude: logger.info(, logger.error(, logger.warning(, logger.debug(, logger.critical(
    """
    # Pattern to find bare logger( calls
    bare_logger_pattern = re.compile(r'\blogger\s*\(')
    # Patterns for legitimate method calls
    legit_patterns = [
        re.compile(r'\blogger\.(info|error|warning|debug|critical|exception|warn)\s*\('),
        re.compile(r'\blogger\.(setLevel|addHandler|removeHandler|handlers)\s*\('),
        re.compile(r'#.*\blogger\s*\('),  # Comments
        re.compile(r'["\'].*\blogger\s*\(.*["\']'),  # Inside strings
    ]
    
    violations = []
    files_scanned = 0
    
    py_files = glob.glob(os.path.join(project_dir, "*.py"))
    
    for filepath in py_files:
        filename = os.path.basename(filepath)
        # Skip this script itself
        if filename == "debug_suite.py":
            continue
        
        files_scanned += 1
        
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
            
            for line_num, line in enumerate(lines, 1):
                # Skip comments and strings first
                stripped = line.strip()
                if stripped.startswith('#'):
                    continue
                
                # Check for bare logger( pattern
                if bare_logger_pattern.search(line):
                    # Check if it's a legitimate method call
                    is_legit = False
                    for legit in legit_patterns:
                        if legit.search(line):
                            is_legit = True
                            break
                    
                    if not is_legit:
                        # Additional check: is "logger" followed by .method?
                        # Match "logger(" but not "logger.something("
                        if re.search(r'\blogger\s*\([^)]*\)', line) and not re.search(r'\blogger\.\w+\s*\(', line):
                            violations.append({
                                "file": filename,
                                "line": line_num,
                                "snippet": stripped[:100]
                            })
        except Exception as e:
            script_logger.warning(f"Could not scan {filename}: {e}")
    
    details = {
        "files_scanned": files_scanned,
        "violations_count": len(violations),
        "violations": violations[:20]  # Limit output
    }
    
    if violations:
        return CheckResult(
            name="CHECK_LOGGER_MISUSE",
            status="FAIL",
            message=f"Found {len(violations)} bare logger() calls",
            details=details
        )
    else:
        return CheckResult(
            name="CHECK_LOGGER_MISUSE",
            status="PASS",
            message=f"No logger misuse in {files_scanned} files",
            details=details
        )


@run_timed
def check_file_io() -> CheckResult:
    """
    CHECK_FILE_IO: Verify logs/ directory exists and is writable.
    Also test portfolio JSON access.
    """
    logs_dir = "logs"
    test_file = os.path.join(logs_dir, "_diag_test.tmp")
    portfolio_file = "portfolio.json"
    
    issues = []
    checks_passed = []
    
    # Check/create logs directory
    try:
        if not os.path.exists(logs_dir):
            os.makedirs(logs_dir)
            checks_passed.append("logs/ created")
        else:
            checks_passed.append("logs/ exists")
    except Exception as e:
        issues.append(f"Cannot create logs/: {e}")
    
    # Test write to logs/
    try:
        with open(test_file, 'w') as f:
            f.write("diagnostic test")
        os.remove(test_file)
        checks_passed.append("logs/ writable")
    except Exception as e:
        issues.append(f"Cannot write to logs/: {e}")
    
    # Test portfolio.json read
    try:
        if os.path.exists(portfolio_file):
            with open(portfolio_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            checks_passed.append(f"portfolio.json readable ({len(data)} keys)")
        else:
            checks_passed.append("portfolio.json not found (OK if new)")
    except Exception as e:
        issues.append(f"Portfolio read error: {e}")
    
    details = {
        "checks_passed": checks_passed,
        "issues": issues
    }
    
    if issues:
        return CheckResult(
            name="CHECK_FILE_IO",
            status="FAIL",
            message="; ".join(issues),
            details=details
        )
    else:
        return CheckResult(
            name="CHECK_FILE_IO",
            status="PASS",
            message=f"{len(checks_passed)} I/O checks passed",
            details=details
        )


@run_timed
def check_binance_public(timeout: int = 8) -> CheckResult:
    """
    CHECK_BINANCE_PUBLIC: Test Binance public endpoints (no auth required).
    - client.ping()
    - client.get_server_time()
    - Compute local time drift
    """
    try:
        from binance.client import Client
    except ImportError:
        return CheckResult(
            name="CHECK_BINANCE_PUBLIC",
            status="FAIL",
            message="python-binance not installed",
            details={"error": "ImportError"}
        )
    
    details = {}
    
    try:
        # Create unauthenticated client
        client = Client("", "")
        
        # Ping test
        ping_start = time.time()
        client.ping()
        ping_ms = (time.time() - ping_start) * 1000
        details["ping_ms"] = round(ping_ms, 2)
        
        # Server time
        server_time_data = client.get_server_time()
        server_time_ms = server_time_data.get("serverTime", 0)
        local_time_ms = int(time.time() * 1000)
        drift_ms = abs(local_time_ms - server_time_ms)
        drift_seconds = drift_ms / 1000
        
        details["server_time_ms"] = server_time_ms
        details["local_time_ms"] = local_time_ms
        details["drift_seconds"] = round(drift_seconds, 3)
        
        if drift_seconds > 2:
            return CheckResult(
                name="CHECK_BINANCE_PUBLIC",
                status="WARN",
                message=f"Time drift {drift_seconds:.1f}s (>2s threshold)",
                details=details
            )
        else:
            return CheckResult(
                name="CHECK_BINANCE_PUBLIC",
                status="PASS",
                message=f"Ping {ping_ms:.0f}ms, drift {drift_seconds:.2f}s",
                details=details
            )
    
    except Exception as e:
        return CheckResult(
            name="CHECK_BINANCE_PUBLIC",
            status="FAIL",
            message=f"Binance connection failed: {e}",
            details={"error": str(e)}
        )


@run_timed
def check_binance_market_data(symbols: List[str], timeout: int = 8) -> CheckResult:
    """
    CHECK_BINANCE_MARKET_DATA: Fetch klines and convert to DataFrame.
    """
    try:
        from binance.client import Client
        import pandas as pd
    except ImportError as e:
        return CheckResult(
            name="CHECK_BINANCE_MARKET",
            status="FAIL",
            message=f"Missing dependency: {e}",
            details={"error": str(e)}
        )
    
    client = Client("", "")
    results = {}
    failures = []
    
    for symbol in symbols:
        symbol_upper = symbol.upper()
        try:
            # Get ticker
            ticker = client.get_symbol_ticker(symbol=symbol_upper)
            current_price = float(ticker.get("price", 0))
            
            # Get klines
            klines = client.get_klines(
                symbol=symbol_upper,
                interval=Client.KLINE_INTERVAL_4HOUR,
                limit=200
            )
            
            if not klines:
                failures.append(f"{symbol_upper}: No klines returned")
                continue
            
            # Convert to DataFrame
            columns = [
                'open_time', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'quote_asset_volume', 'number_of_trades',
                'taker_buy_base_volume', 'taker_buy_quote_volume', 'ignore'
            ]
            df = pd.DataFrame(klines, columns=columns)
            
            # Convert numeric columns
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            
            last_close = float(df['close'].iloc[-1])
            
            results[symbol_upper] = {
                "price": current_price,
                "kline_count": len(klines),
                "df_rows": len(df),
                "last_close": last_close,
                "numeric_ok": isinstance(last_close, float)
            }
            
        except Exception as e:
            failures.append(f"{symbol_upper}: {e}")
    
    details = {
        "symbols_tested": results,
        "failures": failures
    }
    
    if failures and not results:
        return CheckResult(
            name="CHECK_BINANCE_MARKET",
            status="FAIL",
            message="All symbol checks failed",
            details=details
        )
    elif failures:
        return CheckResult(
            name="CHECK_BINANCE_MARKET",
            status="WARN",
            message=f"{len(results)} OK, {len(failures)} failed",
            details=details
        )
    else:
        return CheckResult(
            name="CHECK_BINANCE_MARKET",
            status="PASS",
            message=f"All {len(results)} symbols OK",
            details=details
        )


@run_timed
def check_exchange_router_sync(symbols: List[str]) -> CheckResult:
    """
    CHECK_EXCHANGE_ROUTER: Instantiate, start, fetch price, stop.
    This is a sync wrapper for the async check.
    """
    return asyncio.run(_check_exchange_router_async(symbols))


async def _check_exchange_router_async(symbols: List[str]) -> CheckResult:
    """Async implementation of exchange router check."""
    try:
        from config import SETTINGS
        from exchange_router import ExchangeRouter
    except ImportError as e:
        return CheckResult(
            name="CHECK_EXCHANGE_ROUTER",
            status="FAIL",
            message=f"Cannot import: {e}",
            details={"error": str(e)}
        )
    
    if not SETTINGS.BINANCE_API_KEY or not SETTINGS.BINANCE_SECRET_KEY:
        return CheckResult(
            name="CHECK_EXCHANGE_ROUTER",
            status="WARN",
            message="Binance credentials missing, skipping router test",
            details={}
        )
    
    router = None
    try:
        router = ExchangeRouter(
            api_key=SETTINGS.BINANCE_API_KEY,
            api_secret=SETTINGS.BINANCE_SECRET_KEY,
            symbols=set(symbols)
        )
        
        # Start router
        started = await router.start()
        if not started:
            return CheckResult(
                name="CHECK_EXCHANGE_ROUTER",
                status="FAIL",
                message="Router failed to start",
                details={}
            )
        
        # Wait for WS to connect
        await asyncio.sleep(2)
        
        # Fetch price for first symbol using async method
        test_symbol = symbols[0].upper()
        price = await router.get_price_async(test_symbol)
        
        # Stop router
        await router.stop()
        
        if price is None:
            return CheckResult(
                name="CHECK_EXCHANGE_ROUTER",
                status="WARN",
                message=f"Router started OK but no price for {test_symbol}",
                details={"test_symbol": test_symbol, "price": None}
            )
        
        return CheckResult(
            name="CHECK_EXCHANGE_ROUTER",
            status="PASS",
            message=f"Router OK, {test_symbol}=${price:.2f}",
            details={"test_symbol": test_symbol, "price": price}
        )
    
    except Exception as e:
        if router:
            try:
                await router.stop()
            except:
                pass
        return CheckResult(
            name="CHECK_EXCHANGE_ROUTER",
            status="FAIL",
            message=f"Router error: {e}",
            details={"error": str(e)}
        )


@run_timed
def check_etherscan_onchain(timeout: int = 8) -> CheckResult:
    """
    CHECK_ETHERSCAN_ONCHAIN: Test Etherscan API for whale movements.
    """
    etherscan_key = os.getenv("ETHERSCAN_API_KEY", "")
    
    if not etherscan_key:
        return CheckResult(
            name="CHECK_ETHERSCAN",
            status="WARN",
            message="ETHERSCAN_API_KEY not set",
            details={"reason": "key_missing"}
        )
    
    try:
        import requests
    except ImportError:
        return CheckResult(
            name="CHECK_ETHERSCAN",
            status="FAIL",
            message="requests module not available",
            details={}
        )
    
    # Test a simple Etherscan API call
    test_wallet = "0x28c6c06298d514db089934071355e5743bf21d60"  # Binance
    test_contract = "0xdac17f958d2ee523a2206206994597c13d831ec7"  # USDT
    
    url = (
        f"https://api.etherscan.io/v2/api"
        f"?chainid=1"
        f"&module=account"
        f"&action=tokentx"
        f"&contractaddress={test_contract}"
        f"&address={test_wallet}"
        f"&startblock=0"
        f"&endblock=99999999"
        f"&sort=desc"
        f"&apikey={etherscan_key}"
    )
    
    try:
        response = requests.get(url, timeout=timeout)
        data = response.json()
        
        status = data.get("status", "0")
        message = data.get("message", "")
        result = data.get("result", [])
        
        details = {
            "api_status": status,
            "api_message": message,
            "tx_count": len(result) if isinstance(result, list) else 0
        }
        
        if status == "1":
            return CheckResult(
                name="CHECK_ETHERSCAN",
                status="PASS",
                message=f"API OK, {len(result)} txs returned",
                details=details
            )
        else:
            return CheckResult(
                name="CHECK_ETHERSCAN",
                status="WARN",
                message=f"API returned status={status}: {message}",
                details=details
            )
    
    except requests.exceptions.Timeout:
        return CheckResult(
            name="CHECK_ETHERSCAN",
            status="WARN",
            message=f"Etherscan API timeout ({timeout}s)",
            details={"error": "timeout"}
        )
    except Exception as e:
        return CheckResult(
            name="CHECK_ETHERSCAN",
            status="FAIL",
            message=f"Etherscan error: {e}",
            details={"error": str(e)}
        )


@run_timed
def check_rss_news() -> CheckResult:
    """
    CHECK_RSS_NEWS: Test RSS feed fetching via MarketDataEngine.
    """
    try:
        from market_data_engine import MarketDataEngine
    except ImportError:
        return CheckResult(
            name="CHECK_RSS_NEWS",
            status="FAIL",
            message="Cannot import MarketDataEngine",
            details={}
        )
    
    try:
        engine = MarketDataEngine()
        snapshot = engine.get_news_snapshot(max_age_hours=6, force_refresh=True)
        
        articles = snapshot.get("articles", [])
        article_count = snapshot.get("article_count", 0)
        
        details = {
            "article_count": article_count,
            "sample_titles": [a.get("baslik", "")[:50] for a in articles[:3]]
        }
        
        if article_count == 0:
            return CheckResult(
                name="CHECK_RSS_NEWS",
                status="WARN",
                message="No recent articles found (RSS may be empty)",
                details=details
            )
        else:
            # Validate structure
            if articles and all(k in articles[0] for k in ["baslik", "link", "kaynak"]):
                return CheckResult(
                    name="CHECK_RSS_NEWS",
                    status="PASS",
                    message=f"{article_count} articles fetched",
                    details=details
                )
            else:
                return CheckResult(
                    name="CHECK_RSS_NEWS",
                    status="WARN",
                    message=f"{article_count} articles, structure incomplete",
                    details=details
                )
    
    except Exception as e:
        return CheckResult(
            name="CHECK_RSS_NEWS",
            status="FAIL",
            message=f"RSS fetch error: {e}",
            details={"error": str(e)}
        )


@run_timed
def check_article_content() -> CheckResult:
    """
    CHECK_ARTICLE_CONTENT: Extract content from up to 3 article URLs.
    """
    try:
        from market_data_engine import MarketDataEngine
    except ImportError:
        return CheckResult(
            name="CHECK_ARTICLE_CONTENT",
            status="FAIL",
            message="Cannot import MarketDataEngine",
            details={}
        )
    
    try:
        engine = MarketDataEngine()
        snapshot = engine.get_news_snapshot(max_age_hours=12, force_refresh=True)
        articles = snapshot.get("articles", [])
        
        if not articles:
            return CheckResult(
                name="CHECK_ARTICLE_CONTENT",
                status="WARN",
                message="No articles to test content extraction",
                details={}
            )
        
        # Test up to 3 URLs
        test_urls = [a.get("link", "") for a in articles[:3] if a.get("link")]
        results = []
        successful = 0
        
        for url in test_urls:
            try:
                content = engine._get_article_content(url)
                if content and len(content) >= 300:
                    successful += 1
                    results.append({"url": url[:50], "chars": len(content), "ok": True})
                else:
                    results.append({"url": url[:50], "chars": len(content) if content else 0, "ok": False})
            except Exception as e:
                results.append({"url": url[:50], "error": str(e)[:50], "ok": False})
        
        details = {
            "tested": len(test_urls),
            "successful": successful,
            "results": results
        }
        
        if successful > 0:
            return CheckResult(
                name="CHECK_ARTICLE_CONTENT",
                status="PASS",
                message=f"{successful}/{len(test_urls)} articles extracted (>=300 chars)",
                details=details
            )
        else:
            return CheckResult(
                name="CHECK_ARTICLE_CONTENT",
                status="WARN",
                message="No articles extracted successfully (paywalls?)",
                details=details
            )
    
    except Exception as e:
        return CheckResult(
            name="CHECK_ARTICLE_CONTENT",
            status="FAIL",
            message=f"Content extraction error: {e}",
            details={"error": str(e)}
        )


@run_timed
def check_llm_news() -> CheckResult:
    """
    CHECK_LLM_NEWS: Test Gemini news analysis with sample data.
    """
    try:
        from config import SETTINGS
    except ImportError:
        return CheckResult(
            name="CHECK_LLM_NEWS",
            status="FAIL",
            message="Cannot import SETTINGS",
            details={}
        )
    
    if not SETTINGS.GEMINI_API_KEY:
        return CheckResult(
            name="CHECK_LLM_NEWS",
            status="WARN",
            message="GEMINI_API_KEY not set",
            details={}
        )
    
    try:
        from market_data_engine import MarketDataEngine
    except ImportError:
        return CheckResult(
            name="CHECK_LLM_NEWS",
            status="FAIL",
            message="Cannot import MarketDataEngine",
            details={}
        )
    
    try:
        engine = MarketDataEngine()
        
        # Sample test data
        test_title = "Bitcoin ETF inflows surge to record $500M daily"
        test_content = "Institutional investors are pouring money into Bitcoin ETFs, with daily inflows reaching $500 million. Analysts suggest this represents growing mainstream adoption of cryptocurrency as an asset class."
        
        result = engine._analyze_news_with_llm(
            gemini_api_key=SETTINGS.GEMINI_API_KEY,
            haber_basligi=test_title,
            haber_icerigi=test_content
        )
        
        if result is None:
            return CheckResult(
                name="CHECK_LLM_NEWS",
                status="WARN",
                message="LLM returned None (USE_NEWS_LLM disabled or parse error)",
                details={"raw_result": None}
            )
        
        required_keys = ["kripto_ile_ilgili_mi", "onem_derecesi", "etkilenen_coinler", "duygu", "ozet_tr"]
        missing_keys = [k for k in required_keys if k not in result]
        
        details = {
            "result_keys": list(result.keys()),
            "missing_keys": missing_keys,
            "sample_output": {k: result.get(k) for k in required_keys if k in result}
        }
        
        if missing_keys:
            return CheckResult(
                name="CHECK_LLM_NEWS",
                status="WARN",
                message=f"LLM output missing keys: {missing_keys}",
                details=details
            )
        else:
            return CheckResult(
                name="CHECK_LLM_NEWS",
                status="PASS",
                message="LLM news analysis OK",
                details=details
            )
    
    except Exception as e:
        return CheckResult(
            name="CHECK_LLM_NEWS",
            status="FAIL",
            message=f"LLM news error: {e}",
            details={"error": str(e)}
        )


@run_timed  
def check_llm_strategy_sync(symbols: List[str]) -> CheckResult:
    """
    CHECK_LLM_STRATEGY: Test StrategyEngine.evaluate_buy_opportunity.
    Sync wrapper for async implementation.
    """
    return asyncio.run(_check_llm_strategy_async(symbols))


async def _check_llm_strategy_async(symbols: List[str]) -> CheckResult:
    """Async implementation of LLM strategy check."""
    try:
        from config import SETTINGS
    except ImportError:
        return CheckResult(
            name="CHECK_LLM_STRATEGY",
            status="FAIL",
            message="Cannot import SETTINGS",
            details={}
        )
    
    if not SETTINGS.GEMINI_API_KEY:
        return CheckResult(
            name="CHECK_LLM_STRATEGY",
            status="WARN",
            message="GEMINI_API_KEY not set",
            details={}
        )
    
    try:
        from strategy_engine import StrategyEngine
    except ImportError as e:
        return CheckResult(
            name="CHECK_LLM_STRATEGY",
            status="FAIL",
            message=f"Cannot import StrategyEngine: {e}",
            details={"error": str(e)}
        )
    
    try:
        strategy = StrategyEngine(
            gemini_api_key=SETTINGS.GEMINI_API_KEY,
            enable_llm=True,
            deterministic=False
        )
        
        # Build minimal market snapshot
        test_symbol = symbols[0].upper().replace("USDT", "") if symbols else "BTC"
        
        minimal_snapshot = {
            "symbol": test_symbol,
            "timestamp": datetime.now().isoformat(),
            "price": 95000.0 if test_symbol == "BTC" else 3500.0,
            "volume_24h": 500000000,
            "technical": {
                "rsi": 55.0,
                "adx": 28.0,
                "trend": "NEUTRAL_BULLISH",
                "ema50": 94000.0,
                "ema200": 90000.0,
                "atr": 1500.0,
                "volume_24h": 500000000,
                "momentum_positive": True,
                "trend_strength": "MODERATE"
            },
            "sentiment": {
                "overall_sentiment": "NEUTRAL",
                "fear_greed": {"value": 55, "classification": "Neutral"}
            },
            "onchain": {
                "signal": "NEUTRAL",
                "total_inflow_usd": 0
            }
        }
        
        balance = SETTINGS.BASLANGIC_BAKIYE if hasattr(SETTINGS, 'BASLANGIC_BAKIYE') else 1000.0
        
        result = await strategy.evaluate_buy_opportunity(
            market_snapshot=minimal_snapshot,
            balance=balance
        )
        
        required_keys = ["action", "confidence", "reason", "metadata"]
        missing_keys = [k for k in required_keys if k not in result]
        
        details = {
            "action": result.get("action"),
            "confidence": result.get("confidence"),
            "reason": result.get("reason", "")[:100],
            "missing_keys": missing_keys
        }
        
        if missing_keys:
            return CheckResult(
                name="CHECK_LLM_STRATEGY",
                status="WARN",
                message=f"Strategy output missing: {missing_keys}",
                details=details
            )
        elif result.get("action") == "HOLD":
            return CheckResult(
                name="CHECK_LLM_STRATEGY",
                status="PASS",
                message=f"Strategy OK (HOLD, conf={result.get('confidence', 0):.0f})",
                details=details
            )
        else:
            return CheckResult(
                name="CHECK_LLM_STRATEGY",
                status="PASS",
                message=f"Strategy OK ({result.get('action')}, conf={result.get('confidence', 0):.0f})",
                details=details
            )
    
    except Exception as e:
        return CheckResult(
            name="CHECK_LLM_STRATEGY",
            status="FAIL",
            message=f"Strategy error: {e}",
            details={"error": str(e)}
        )


# ═══════════════════════════════════════════════════════════════════════════════
# REPORT GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

def print_table(results: List[CheckResult]) -> None:
    """Print results as a table to terminal."""
    print("\n" + "=" * 80)
    print("🔍 DIAGNOSTICS REPORT")
    print("=" * 80)
    print(f"\n{'CHECK_NAME':<24} {'STATUS':<8} {'TIME (ms)':<12} MESSAGE")
    print("-" * 80)
    
    for r in results:
        status_emoji = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}.get(r.status, "❓")
        status_str = f"{status_emoji} {r.status}"
        print(f"{r.name:<24} {status_str:<10} {r.duration_ms:<12.1f} {r.message[:40]}")
    
    print("-" * 80)
    
    # Summary
    pass_count = sum(1 for r in results if r.status == "PASS")
    warn_count = sum(1 for r in results if r.status == "WARN")
    fail_count = sum(1 for r in results if r.status == "FAIL")
    total_time = sum(r.duration_ms for r in results)
    
    print(f"\nSUMMARY: {pass_count} PASS | {warn_count} WARN | {fail_count} FAIL | Total: {total_time:.0f}ms")
    print("=" * 80 + "\n")


def save_json_report(results: List[CheckResult], output_dir: str = "logs") -> str:
    """Save detailed JSON report to logs directory."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"diagnostics_{timestamp}.json"
    filepath = os.path.join(output_dir, filename)
    
    # Ensure directory exists
    os.makedirs(output_dir, exist_ok=True)
    
    report = {
        "timestamp": datetime.now().isoformat(),
        "summary": {
            "total_checks": len(results),
            "pass": sum(1 for r in results if r.status == "PASS"),
            "warn": sum(1 for r in results if r.status == "WARN"),
            "fail": sum(1 for r in results if r.status == "FAIL"),
            "total_duration_ms": sum(r.duration_ms for r in results)
        },
        "checks": [asdict(r) for r in results]
    }
    
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    
    return filepath


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Diagnostics script for trading bot stack validation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python debug_suite.py --mode deep --symbols BTCUSDT,ETHUSDT
    python debug_suite.py --mode quick --skip-llm 1
    python debug_suite.py --test-router 1 --skip-news 1
        """
    )
    
    parser.add_argument("--mode", choices=["quick", "deep"], default="deep",
                        help="quick|deep (default: deep)")
    parser.add_argument("--symbols", type=str, default="BTCUSDT,ETHUSDT",
                        help="Comma-separated symbols (default: BTCUSDT,ETHUSDT)")
    parser.add_argument("--test-router", type=int, choices=[0, 1], default=0,
                        help="Test ExchangeRouter start/stop (default: 0)")
    parser.add_argument("--skip-llm", type=int, choices=[0, 1], default=0,
                        help="Skip LLM checks (default: 0)")
    parser.add_argument("--skip-news", type=int, choices=[0, 1], default=0,
                        help="Skip RSS/news checks (default: 0)")
    parser.add_argument("--skip-etherscan", type=int, choices=[0, 1], default=0,
                        help="Skip etherscan checks (default: 0)")
    parser.add_argument("--timeout", type=int, default=8,
                        help="HTTP timeout seconds (default: 8)")
    
    args = parser.parse_args()
    
    # Parse symbols
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    
    # Get project directory
    project_dir = os.path.dirname(os.path.abspath(__file__))
    
    print("\n" + "=" * 80)
    print("🚀 STARTING DIAGNOSTICS SUITE")
    print("=" * 80)
    print(f"   Mode:         {args.mode}")
    print(f"   Symbols:      {', '.join(symbols)}")
    print(f"   Test Router:  {'Yes' if args.test_router else 'No'}")
    print(f"   Skip LLM:     {'Yes' if args.skip_llm else 'No'}")
    print(f"   Skip News:    {'Yes' if args.skip_news else 'No'}")
    print(f"   Skip Etherscan: {'Yes' if args.skip_etherscan else 'No'}")
    print(f"   Timeout:      {args.timeout}s")
    print("=" * 80 + "\n")
    
    results: List[CheckResult] = []
    
    # ─────────────────────────────────────────────────────────────────────
    # RUN CHECKS SEQUENTIALLY
    # ─────────────────────────────────────────────────────────────────────
    
    # 1. CHECK_IMPORTS
    print("🔄 Running CHECK_IMPORTS...")
    results.append(check_imports())
    
    # 2. CHECK_ENV_KEYS
    print("🔄 Running CHECK_ENV_KEYS...")
    results.append(check_env_keys(test_router=bool(args.test_router)))
    
    # 3. CHECK_LOGGER_MISUSE (CRITICAL)
    print("🔄 Running CHECK_LOGGER_MISUSE...")
    results.append(check_logger_misuse_scan(project_dir))
    
    # 4. CHECK_FILE_IO
    print("🔄 Running CHECK_FILE_IO...")
    results.append(check_file_io())
    
    # 5. CHECK_BINANCE_PUBLIC
    print("🔄 Running CHECK_BINANCE_PUBLIC...")
    results.append(check_binance_public(timeout=args.timeout))
    
    # 6. CHECK_BINANCE_MARKET_DATA
    print("🔄 Running CHECK_BINANCE_MARKET...")
    results.append(check_binance_market_data(symbols, timeout=args.timeout))
    
    # 7. CHECK_EXCHANGE_ROUTER (optional)
    if args.test_router:
        print("🔄 Running CHECK_EXCHANGE_ROUTER...")
        results.append(check_exchange_router_sync(symbols))
    
    # 8. CHECK_ETHERSCAN (skip if --skip-etherscan=1)
    if not args.skip_etherscan:
        print("🔄 Running CHECK_ETHERSCAN...")
        results.append(check_etherscan_onchain(timeout=args.timeout))
    
    # 9. CHECK_RSS_NEWS (skip if --skip-news=1)
    if not args.skip_news:
        print("🔄 Running CHECK_RSS_NEWS...")
        results.append(check_rss_news())
    
    # 10. CHECK_ARTICLE_CONTENT (deep mode only, skip if --skip-news=1)
    if args.mode == "deep" and not args.skip_news:
        print("🔄 Running CHECK_ARTICLE_CONTENT...")
        results.append(check_article_content())
    
    # 11. CHECK_LLM_NEWS (skip if --skip-llm=1)
    if not args.skip_llm:
        print("🔄 Running CHECK_LLM_NEWS...")
        results.append(check_llm_news())
    
    # 12. CHECK_LLM_STRATEGY (skip if --skip-llm=1)
    if not args.skip_llm:
        print("🔄 Running CHECK_LLM_STRATEGY...")
        results.append(check_llm_strategy_sync(symbols))
    
    # ─────────────────────────────────────────────────────────────────────
    # OUTPUT RESULTS
    # ─────────────────────────────────────────────────────────────────────
    
    print_table(results)
    
    json_path = save_json_report(results)
    print(f"📄 Full report saved: {json_path}\n")
    
    # ─────────────────────────────────────────────────────────────────────
    # EXIT CODE
    # ─────────────────────────────────────────────────────────────────────
    
    has_failures = any(r.status == "FAIL" for r in results)
    exit_code = 1 if has_failures else 0
    
    if has_failures:
        print("❌ DIAGNOSTICS FAILED - See report for details\n")
    else:
        print("✅ DIAGNOSTICS PASSED\n")
    
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
