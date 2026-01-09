import asyncio
import time
import logging
import traceback
import uuid
from typing import List, Dict, Any, Optional

# Import modules (assuming they are in the python path)
try:
    from config import SETTINGS, RUN_PROFILE, PAPER_SANITY_MODE, CANARY_MODE, SAFE_MODE
    import config
except ImportError:
    # Fallback if config is missing (should not happen in production)
    RUN_PROFILE = "paper"
    PAPER_SANITY_MODE = False
    CANARY_MODE = False
    SAFE_MODE = False
    config = None
    class SETTINGS:
        WATCHLIST = ['BTC', 'ETH']
        LOOP_SECONDS = 900
        USE_NEWS_LLM = False
        MAX_DAILY_LOSS_PCT = 3.0
        MAX_OPEN_POSITIONS = 3
        MAX_CONSECUTIVE_LOSSES = 4
        COOLDOWN_MINUTES = 60
        BASLANGIC_BAKIYE = 1000

# Setup Logger
logger = logging.getLogger("LoopController")
logger.setLevel(logging.INFO)

class LoopController:
    """
    Orchestrates the trading lifecycle:
    1. Monitor/Manage existing positions.
    2. Collect market data (parallel).
    3. Analyze opportunities (Strategy + Risk).
    4. Execute trades.
    5. Sleep.
    
    Strategy: Hybrid V2 with regime detection and multi-timeframe analysis.
    """

    def __init__(
        self,
        watchlist: List[str],
        market_data_engine,
        strategy_engine,
        execution_manager,
        position_manager,
        exchange_router,
        risk_manager,
        telegram_fn=None,
        telegram_config=None
    ):
        self.watchlist = watchlist
        self.market_data_engine = market_data_engine
        self.strategy_engine = strategy_engine
        self.execution_manager = execution_manager
        self.position_manager = position_manager
        self.exchange_router = exchange_router
        self.risk_manager = risk_manager
        self.telegram_fn = telegram_fn
        self.telegram_config = telegram_config or {}

        self.loop_duration = getattr(SETTINGS, "LOOP_SECONDS", 900)
        self.cooldown_until = 0.0
        
        # ──────────────── RUN TRACKING ────────────────
        self._run_id = uuid.uuid4().hex[:8]  # Short unique run identifier
        self._cycle_id = 0
        
        # Freshness tracking for telemetry
        self._max_candle_age_s = 0
        self._max_ticker_age_s = 0
        
        # ─────────────────────────────────────────────────────────────────────
        # HYBRID V2 Strategy - Multi-Timeframe
        # ─────────────────────────────────────────────────────────────────────
        self.strategy_version = "HYBRID_V2"
        logger.info("✅ Using HYBRID_V2 strategy (multi-timeframe)")
        # Note: V2 components are initialized in strategy_engine
        
        
        # Alarm tracking - config'den oku
        self._alarm_thresholds = {
            "consecutive_parse_fail": getattr(SETTINGS, 'ALARM_PARSE_FAIL_THRESHOLD', 15),
            "consecutive_adx_block": getattr(SETTINGS, 'ALARM_ADX_BLOCK_THRESHOLD', 20),
            "consecutive_data_fail": getattr(SETTINGS, 'ALARM_DATA_FAIL_THRESHOLD', 5)
        }
        self._alarm_counters = {
            "parse_fail": 0,
            "adx_block": 0,
            "data_fail": 0
        }
        self._last_metrics = {}
        
        # Log startup settings
        self.log_startup_risk_settings()

    def log_startup_risk_settings(self):
        """Log the global risk limits at startup."""
        logger.info("════════════════════════════════════════")
        logger.info("🚀 LOOP CONTROLLER STARTUP")
        logger.info(f"Strategy: HYBRID_V2 (Multi-Timeframe)")
        logger.info(f"Daily Loss Limit: {SETTINGS.MAX_DAILY_LOSS_PCT}%")
        logger.info(f"Max Open Positions: {SETTINGS.MAX_OPEN_POSITIONS}")
        logger.info(f"Max Loss Streak: {SETTINGS.MAX_CONSECUTIVE_LOSSES}")
        logger.info(f"Cooldown Minutes: {SETTINGS.COOLDOWN_MINUTES}")
        
        if config:
            logger.info(f"├─ 4H Swing Allocation: {getattr(config, 'CAPITAL_ALLOCATION_4H', 0.4) * 100:.0f}%")
            logger.info(f"├─ 1H Momentum Allocation: {getattr(config, 'CAPITAL_ALLOCATION_1H', 0.4) * 100:.0f}%")
            logger.info(f"├─ 15M Scalp Allocation: {getattr(config, 'CAPITAL_ALLOCATION_15M', 0.2) * 100:.0f}%")
            logger.info(f"├─ Scalping Enabled: {getattr(config, 'SCALP_15M_ENABLED', True)}")
            logger.info(f"└─ Regime Confidence: {getattr(config, 'REGIME_CONFIDENCE_THRESHOLD', 0.6):.0%}")
        
        logger.info("════════════════════════════════════════")


    async def _check_alarms(self, strategy_metrics: Dict, news_metrics: Dict):
        """
        Check for alarm conditions and send Telegram alerts if thresholds exceeded.
        
        Monitors:
        - Consecutive parse failures (>5)
        - Consecutive ADX blocks (>10)
        - Consecutive data fetch failures (>3)
        """
        alerts = []
        
        # 1. Parse fail tracking
        current_parse_fail = strategy_metrics.get("parse_fail", 0)
        last_parse_fail = self._last_metrics.get("parse_fail", 0)
        
        if current_parse_fail > last_parse_fail:
            self._alarm_counters["parse_fail"] += (current_parse_fail - last_parse_fail)
        else:
            self._alarm_counters["parse_fail"] = 0  # Reset on success
        
        if self._alarm_counters["parse_fail"] >= self._alarm_thresholds["consecutive_parse_fail"]:
            alerts.append(f"⚠️ Parse Fail Alarm: {self._alarm_counters['parse_fail']} consecutive failures")
            self._alarm_counters["parse_fail"] = 0  # Reset after alert
        
        # 2. News/Data fail tracking
        current_news_fail = news_metrics.get("news_failures", 0)
        last_news_fail = self._last_metrics.get("news_failures", 0)
        
        if current_news_fail > last_news_fail:
            self._alarm_counters["data_fail"] += 1
        else:
            self._alarm_counters["data_fail"] = 0
        
        if self._alarm_counters["data_fail"] >= self._alarm_thresholds["consecutive_data_fail"]:
            alerts.append(f"⚠️ Data Fetch Alarm: {self._alarm_counters['data_fail']} consecutive failures")
            self._alarm_counters["data_fail"] = 0
        
        # Update last metrics for next cycle
        self._last_metrics = {
            "parse_fail": current_parse_fail,
            "news_failures": current_news_fail
        }
        
        # Send alerts via Telegram
        if alerts and self.telegram_fn:
            bot_token = self.telegram_config.get("bot_token", "")
            chat_id = self.telegram_config.get("chat_id", "")
            
            if bot_token and chat_id:
                alert_msg = (
                    "🚨 <b>SYSTEM ALERT</b> 🚨\n\n" +
                    "\n".join(alerts) +
                    f"\n\n<i>Time: {time.strftime('%H:%M:%S')}</i>"
                )
                try:
                    await self.telegram_fn(bot_token, chat_id, alert_msg)
                    logger.warning(f"[ALARM] Telegram alert sent: {alerts}")
                except Exception as e:
                    logger.error(f"[ALARM] Failed to send Telegram alert: {e}")
        
        # Log alerts locally regardless
        for alert in alerts:
            logger.warning(f"[ALARM] {alert}")

    async def run(self):
        """Main infinite loop."""
        logger.info("LoopController.run() started.")
        
        # SL/TP Watchdog'u başlat (paralel task)
        watchdog_task = None
        if getattr(SETTINGS, 'SLTP_WATCHDOG_ENABLED', True):
            live_trading = getattr(SETTINGS, 'LIVE_TRADING', False)
            watchdog_task = asyncio.create_task(
                self.position_manager.start_watchdog(live_trading_enabled=live_trading)
            )
        
        try:
            while True:
                try:
                    start_time = time.time()
                    await self.run_once()
                    
                    # Adaptive Sleep
                    elapsed = time.time() - start_time
                    sleep_time = max(10, self.loop_duration - elapsed)
                    
                    # LLM Metrics Log (only if errors)
                    try:
                        sm = self.strategy_engine.get_llm_metrics()
                        nm = self.market_data_engine.get_llm_metrics()
                        # Sadece hata varsa logla
                        total_errors = sm.get('api_fail', 0) + sm.get('parse_fail', 0) + nm.get('news_failures', 0)
                        if total_errors > 0:
                            logger.info(
                                f"[LLM METRICS] Strategy: calls={sm.get('strategy_calls',0)} "
                                f"api_f={sm.get('api_fail',0)} parse_f={sm.get('parse_fail',0)} "
                                f"schema_f={sm.get('schema_fail',0)} fb={sm.get('strategy_fallbacks',0)} "
                                f"retry={sm.get('retry_count',0)}/{sm.get('retry_success',0)} "
                                f"ema={sm.get('strategy_latency_ema_ms',0):.0f}ms | "
                                f"News: calls={nm.get('news_calls',0)} fail={nm.get('news_failures',0)} "
                                f"fb={nm.get('news_fallbacks',0)} ema={nm.get('news_latency_ema_ms',0):.0f}ms"
                            )
                    except Exception:
                        pass

                    # Check for alarm conditions
                    await self._check_alarms(sm, nm)

                    # Summary Reporter - günlük/saatlik özet raporlama
                    try:
                        from summary_reporter import get_reporter
                        reporter = get_reporter()
                        await reporter.maybe_report(
                            portfolio=self.execution_manager.portfolio,
                            telegram_fn=self.telegram_fn,
                            telegram_config=self.telegram_config
                        )
                    except ImportError:
                        pass
                    except Exception as e:
                        logger.warning(f"[SUMMARY] Report error: {e}")

                    # Alert Manager - kritik olay bildirimleri
                    try:
                        from alert_manager import get_alert_manager
                        am = get_alert_manager()
                        am.set_telegram_config(self.telegram_fn, self.telegram_config)
                        await am.poll(portfolio=self.execution_manager.portfolio)
                    except ImportError:
                        pass
                    except Exception as e:
                        logger.debug(f"[ALERT] Poll error: {e}")

                    logger.info(f"💤 Sleeping for {sleep_time:.1f}s...")
                    await asyncio.sleep(sleep_time)
                    
                except asyncio.CancelledError:
                    logger.info("LoopController cancelled.")
                    break
                except Exception as e:
                    logger.error(f"❌ Critical Loop Error: {e}")
                    traceback.print_exc()
                    await asyncio.sleep(60) # Wait before retry on crash
        finally:
            # Watchdog'u durdur
            if watchdog_task:
                self.position_manager.stop_watchdog()
                watchdog_task.cancel()
                try:
                    await watchdog_task
                except asyncio.CancelledError:
                    pass
                logger.info("🐕 Watchdog task stopped.")

    async def run_once(self):
        """Executes one 15-min cycle."""
        cycle_start = time.time()
        logger.info(f"\n⚡ Cycle Start: {time.strftime('%H:%M:%S')}")
        
        # ──────────────── CYCLE STATS ────────────────
        # Track blocking reasons and events throughout the cycle
        self._cycle_stats = {
            "regime_blocked": 0,
            "news_veto": 0,
            "cooldown": 0,
            "data_stale": 0,
            "snapshot_failed": 0,
            "trade_attempt": 0,
            "trade_success": 0
        }
        
        # 1. Update Portfolio/Positions
        # If needed, execution_manager might sync with exchange here
        if hasattr(self.execution_manager, "sync_with_exchange"):
            await self.execution_manager.sync_with_exchange()

        # 2. Monitor & Manage Exisiting Positions (SL/TP)
        await self.monitor_positions()

        # 3. Global Safety Checks (for new entries)
        can_buy, block_reason = self.check_global_safety()
        if not can_buy:
             logger.warning(f"🚫 Buying Blocked: {block_reason}")

        # 4. Fetch Globals (Sentiment, Whales, News)
        # We fetch these once to log them provided MDE caches them internally.
        # Note: MDE signatures found: get_sentiment_snapshot, get_onchain_snapshot
        try:
            # We assume these utilize internal cache
            sentiment_snap = self.market_data_engine.get_sentiment_snapshot()
            onchain_snap = self.market_data_engine.get_onchain_snapshot()
            
            fng = sentiment_snap.get("fear_greed") or {}
            logger.info(f"🌡️ Global Sentiment: F&G={fng.get('value', 'N/A')} ({fng.get('classification', 'Unavailable')})")
            
            whale_signal = onchain_snap.get("signal", "NEUTRAL") if onchain_snap else "NEUTRAL"
            inflow_usd = onchain_snap.get("total_inflow_usd", 0) if onchain_snap else 0
            logger.info(f"🐋 Global On-Chain: {whale_signal} (${inflow_usd:,.0f})")
            
        except Exception as e:
            logger.error(f"⚠️ Failed to fetch global data: {e}")
            self._cycle_stats["data_stale"] += 1

        # 4b. Fetch Global News Summary (TTL cached)
        global_news_summary = None
        try:
            global_news_summary = self.market_data_engine.get_global_news_summary()
        except Exception as e:
            logger.warning(f"⚠️ Failed to fetch global news summary: {e}")

        # 4c. Fetch Reddit LLM Summary (TTL cached) - if enabled
        reddit_summary = None
        if getattr(SETTINGS, 'REDDIT_ENABLED', False):
            try:
                reddit_summary = await self.market_data_engine.get_crypto_reddit_summary(self.watchlist)
                if reddit_summary:
                    logger.info(f"📢 Reddit Summary: {reddit_summary.get('general_impact', 'N/A')}")
            except Exception as e:
                logger.warning(f"⚠️ Failed to fetch Reddit summary: {e}")

        # 4d. Run News Analysis Pipeline (Per-Article LLM Analysis)
        new_articles_count = 0
        try:
            new_articles_count = self.market_data_engine.run_news_analysis_pipeline()
            if new_articles_count > 0:
                logger.info(f"📰 Analyzed {new_articles_count} new articles")
        except Exception as e:
            logger.warning(f"⚠️ News analysis pipeline error: {e}")

        # 5. Parallel Snapshot Collection using Hybrid V2 snapshot method
        # This fetches 1d, 4h, 1h, 15m candles with full indicators for Hybrid V2 strategy
        logger.info(f"📥 Collecting Hybrid V2 snapshots for {len(self.watchlist)} symbols...")
        tasks = []
        for symbol in self.watchlist:
            # Use Hybrid V2 snapshot for multi-timeframe data (1d, 4h, 1h, 15m)
            tasks.append(self.market_data_engine.get_hybrid_v2_snapshot(symbol))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 6. Process Opportunities
        for symbol, result in zip(self.watchlist, results):
            if isinstance(result, Exception):
                logger.error(f"❌ Snapshot failed for {symbol}: {result}")
                self._cycle_stats["snapshot_failed"] += 1
                continue
            
            snapshot = result
            if not snapshot or snapshot.get("error"):
                self._cycle_stats["data_stale"] += 1
                if snapshot:
                    logger.warning(f"[V2] {symbol}: Snapshot error - {snapshot.get('error')}")
                continue
            
            # Log V2 snapshot status
            tf_data = snapshot.get("tf", {})
            adx_4h = tf_data.get("4h", {}).get("adx", 0)
            adx_1h = tf_data.get("1h", {}).get("adx", 0)
            atr_pct = tf_data.get("1h", {}).get("atr_pct", 0)
            logger.info(
                f"[V2] {symbol}: ADX(4h)={adx_4h:.1f} ADX(1h)={adx_1h:.1f} ATR%={atr_pct:.2f}% "
                f"trend(1h)={tf_data.get('1h', {}).get('trend', 'N/A')}"
            )

            # Inject global news summary into snapshot (deprecated, kept for compatibility)
            snapshot["news_summary"] = global_news_summary or {}
            
            # Inject Reddit summary for coin-specific insights
            snapshot["reddit_summary"] = reddit_summary or {}
            
            # Inject coin-specific news for this symbol
            coin_news = self.market_data_engine.get_coin_specific_news(symbol)
            snapshot["coin_news"] = coin_news
            
            # Format coin news for LLM prompt consumption
            if coin_news:
                news_lines = [f"[Impact:{n.get('impact_score', 0)}] {n.get('summary', 'No summary')}" for n in coin_news[:3]]
                snapshot["coin_news_str"] = "Relevant News:\n" + "\n".join(news_lines)
            else:
                snapshot["coin_news_str"] = ""
            
            # tf data is already in V2 snapshot, no need to fetch separately

            # ──────────────── DATA FRESHNESS GATE ────────────────
            # Block trading if data is too old to prevent stale decisions
            MAX_CANDLE_AGE_SECONDS = 180  # 3 minutes
            MAX_TICKER_AGE_SECONDS = 60   # 1 minute
            
            now = time.time()
            is_data_stale = False
            stale_reason = None
            
            # Check candle freshness (from tf data timestamp if available)
            candle_ts = snapshot.get("tf", {}).get("1h", {}).get("timestamp", 0)
            candle_age = int(now - candle_ts) if candle_ts else 0
            
            # Track max age for telemetry
            if candle_age > self._max_candle_age_s:
                self._max_candle_age_s = candle_age
            
            if candle_ts and candle_age > MAX_CANDLE_AGE_SECONDS:
                is_data_stale = True
                stale_reason = f"candle_age={candle_age}s"
                logger.warning(f"[FRESHNESS] {symbol}: {stale_reason} > {MAX_CANDLE_AGE_SECONDS}s")
            
            # Check ticker/price freshness
            ticker_ts = snapshot.get("ticker_timestamp", 0)
            ticker_age = int(now - ticker_ts) if ticker_ts else 0
            
            # Track max age for telemetry
            if ticker_age > self._max_ticker_age_s:
                self._max_ticker_age_s = ticker_age
            
            if ticker_ts and ticker_age > MAX_TICKER_AGE_SECONDS:
                is_data_stale = True
                stale_reason = f"ticker_age={ticker_age}s"
                logger.warning(f"[FRESHNESS] {symbol}: {stale_reason} > {MAX_TICKER_AGE_SECONDS}s")
            
            if is_data_stale:
                self._cycle_stats["data_stale"] += 1
                # Still allow sell logic for exit management
                await self.process_sell_logic(symbol, snapshot)
                continue  # Skip buy logic for stale data

            # Check for Sell logic (always allowed)
            await self.process_sell_logic(symbol, snapshot)
            
            # Check for Buy logic (if safety allows)
            if can_buy:
                await self.process_buy_logic(symbol, snapshot)
        
        # 7. Summary Log
        port_summary = self.position_manager.get_portfolio_summary()
        logger.info(f"💰 Balance: ${port_summary.get('balance'):.0f} | Open: {port_summary.get('open_positions')}")
        
        # 8. Cycle Summary - Enriched with blocking reasons and latency
        cycle_latency_ms = int((time.time() - cycle_start) * 1000)
        
        # Build blocked_by list
        blocked_by = []
        if not can_buy:
            blocked_by.append(f"SAFETY:{block_reason[:20]}" if block_reason else "SAFETY")
        if self._cycle_stats["regime_blocked"] > 0:
            blocked_by.append(f"REGIME:{self._cycle_stats['regime_blocked']}")
        if self._cycle_stats["news_veto"] > 0:
            blocked_by.append(f"NEWS_VETO:{self._cycle_stats['news_veto']}")
        if self._cycle_stats["cooldown"] > 0:
            blocked_by.append(f"COOLDOWN:{self._cycle_stats['cooldown']}")
        if self._cycle_stats["data_stale"] > 0:
            blocked_by.append(f"DATA_STALE:{self._cycle_stats['data_stale']}")
        
        # Get circuit breaker stats for telemetry
        circuit_stats = {"state": "UNKNOWN", "errors_60s": 0}
        if hasattr(self, 'exchange_router') and hasattr(self.exchange_router, 'get_circuit_stats'):
            circuit_stats = self.exchange_router.get_circuit_stats()
        
        # Get daily PnL
        daily_pnl = 0.0
        if hasattr(self, 'execution_manager') and hasattr(self.execution_manager, 'get_today_pnl'):
            daily_pnl = self.execution_manager.get_today_pnl()
        
        # Calculate PnL percentage
        balance = port_summary.get('balance', 0)
        initial_balance = getattr(SETTINGS, 'BASLANGIC_BAKIYE', 1000)
        daily_pnl_pct = (daily_pnl / initial_balance * 100) if initial_balance > 0 else 0
        
        # Determine run mode
        if SAFE_MODE:
            run_mode = "SAFE"
        elif CANARY_MODE:
            run_mode = "CANARY"
        else:
            run_mode = "NORMAL"
        
        # Build structured blocked_by dict
        blocked_by_dict = {}
        if not can_buy and block_reason:
            blocked_by_dict["SAFETY"] = 1
        if self._cycle_stats["regime_blocked"] > 0:
            blocked_by_dict["REGIME"] = self._cycle_stats["regime_blocked"]
        if self._cycle_stats["news_veto"] > 0:
            blocked_by_dict["NEWS_VETO"] = self._cycle_stats["news_veto"]
        if self._cycle_stats["cooldown"] > 0:
            blocked_by_dict["COOLDOWN"] = self._cycle_stats["cooldown"]
        if self._cycle_stats["data_stale"] > 0:
            blocked_by_dict["DATA_STALE"] = self._cycle_stats["data_stale"]
        
        # Increment cycle counter
        self._cycle_id += 1
        
        # V2 Cycle Summary - structured for machine parsing
        cycle_summary = {
            "run_id": self._run_id,
            "cycle_id": self._cycle_id,
            "ts": time.strftime('%H:%M:%S'),
            "mode": run_mode,
            "symbols": len(self.watchlist),
            "blocked_by": blocked_by_dict if blocked_by_dict else {},
            "trades_open": self._cycle_stats['trade_success'],
            "trades_attempt": self._cycle_stats['trade_attempt'],
            "open_pos": port_summary.get('open_positions', 0),
            "balance_usd": round(balance, 2),
            "daily_pnl_usd": round(daily_pnl, 2),
            "daily_pnl_pct": round(daily_pnl_pct, 2),
            "max_candle_age_s": self._max_candle_age_s,
            "max_ticker_age_s": self._max_ticker_age_s,
            "api_state": circuit_stats.get("state", "UNKNOWN"),
            "api_errors_60s": circuit_stats.get("errors_60s", 0),
            "latency_ms": cycle_latency_ms
        }
        logger.info(f"[CYCLE_SUMMARY] {cycle_summary}")
        
        # Save summary state to file
        try:
            import json
            summary_state = {
                "cycle_count": self._cycle_id,
                "run_id": self._run_id
            }
            with open("summary_state.json", "w") as f:
                json.dump(summary_state, f, indent=2)
        except Exception as e:
            logger.warning(f"[SUMMARY_STATE] Save error: {e}")
        
        # Reset freshness tracking for next cycle
        self._max_candle_age_s = 0
        self._max_ticker_age_s = 0



    async def monitor_positions(self):
        """
        Log status of open positions.
        
        Note: V2 exit logic (SL/TP/Partial TP/Trailing Stop) is handled by
        the Watchdog task (_quick_sltp_check) which runs every 30 seconds.
        This method now only logs position status for monitoring.
        """
        open_positions = self.position_manager.get_open_positions()
        if not open_positions:
            return

        logger.info(f"🛡️ Monitoring {len(open_positions)} open positions...")
        
        for pos in open_positions:
            symbol = pos.get('symbol')
            if not symbol: continue

            try:
                # Fetch current price
                current_price = self.exchange_router.get_price(symbol)
                if not current_price:
                    current_price = self.exchange_router.get_price_or_fetch(symbol)
                
                if not current_price:
                    logger.warning(f"  Unknown price for {symbol}, skipping.")
                    continue

                # Calculate P&L
                entry_price = pos.get('entry_price', 0)
                pnl_pct = ((current_price - entry_price) / entry_price * 100) if entry_price else 0
                
                # Get V2 fields
                entry_type = pos.get('entry_type', 'UNKNOWN')
                partial_hit = pos.get('partial_tp_hit', False)
                current_sl = pos.get('current_sl', pos.get('stop_loss', 0))
                
                # Log position status
                status = "🟢" if pnl_pct > 0 else "🔴"
                partial_str = "✅PartialTP" if partial_hit else ""
                logger.info(
                    f"  {status} {symbol} ({entry_type}): ${current_price:.2f} | "
                    f"PnL: {pnl_pct:+.1f}% | SL: ${current_sl:.2f} {partial_str}"
                )

            except Exception as e:
                logger.error(f"⚠️ Error monitoring {symbol}: {e}")

    async def process_buy_logic(self, symbol: str, snapshot: Dict[str, Any]):
        """
        Entry logic using HYBRID V2 strategy.
        
        Includes position limit check before evaluating signals.
        """
        try:
            # ─────────── POSITION LIMIT CHECK ───────────
            # Get current open positions count
            open_positions = self.position_manager.get_open_positions()
            current_count = len(open_positions)
            max_positions = getattr(SETTINGS, 'MAX_OPEN_POSITIONS', 3)
            
            if current_count >= max_positions:
                logger.debug(
                    f"[{symbol}] Position limit reached: {current_count}/{max_positions} - skipping buy"
                )
                return
            
            # Check if we already have a position in this symbol
            for pos in open_positions:
                if pos.get('symbol') == symbol:
                    logger.debug(f"[{symbol}] Already have open position - skipping buy")
                    return
            
            await self._process_buy_hybrid_v2(symbol, snapshot)
        except Exception as e:
            logger.error(f"[{symbol}] Buy logic error: {e}")
    
    async def _process_buy_hybrid_v2(self, symbol: str, snapshot: Dict[str, Any]):
        """
        Hybrid V2 entry logic using multi-timeframe strategy.
        
        Uses strategy_engine.decide() which internally uses RegimeDetector,
        TimeframeAnalyzer, and HybridMultiTFV2 for signal generation.
        """
        try:
            # 1. Verify multi-timeframe data exists in snapshot (already fetched via get_hybrid_v2_snapshot)
            if "tf" not in snapshot or not snapshot.get("tf", {}).get("1h"):
                logger.warning(f"[HYBRID V2] {symbol}: Missing tf data in snapshot, fetching now...")
                try:
                    v2_snap = await self.market_data_engine.get_hybrid_v2_snapshot(symbol)
                    snapshot["tf"] = v2_snap.get("tf", {})
                except Exception as e:
                    logger.warning(f"[V2] {symbol}: TF data fetch error: {e}")
                    snapshot["tf"] = {"4h": {}, "1h": {}, "15m": {}}
            
            # 2. Call strategy engine decide method (routes to V2 internally)
            decision = self.strategy_engine.decide(
                symbol=symbol,
                snapshot=snapshot,
                action_type="BUY"
            )
            
            if not decision or decision.get("action") != "BUY":
                # Log why we're not entering
                reason = decision.get("reason", "No signal") if decision else "No decision"
                logger.debug(f"[V2] {symbol}: {reason}")
                
                # Track blocking reasons
                if hasattr(self, '_cycle_stats'):
                    if "regime" in reason.lower():
                        self._cycle_stats["regime_blocked"] += 1
                    elif "veto" in reason.lower():
                        self._cycle_stats["news_veto"] += 1
                return
            
            # 3. Log V2 signal
            entry_type = decision.get('entry_type', 'UNKNOWN')
            confidence = decision.get('confidence', 0)
            rr_ratio = decision.get('risk_reward_ratio', 0)
            
            logger.info(
                f"🎯 HYBRID V2 SIGNAL ({symbol}) | "
                f"Type: {entry_type} | "
                f"Conf: {confidence:.0f}% | "
                f"R:R: {rr_ratio:.2f}"
            )
            
            # 4. Cooldown Check
            in_cooldown, cooldown_reason = self.risk_manager.is_in_cooldown()
            if in_cooldown:
                logger.info(f"❄️ V2 {symbol}: {cooldown_reason}")
                if hasattr(self, '_cycle_stats'):
                    self._cycle_stats["cooldown"] += 1
                return
            
            # 5. Build decision result for ExecutionManager
            current_price = snapshot.get("price", 0)
            if not current_price:
                current_price = snapshot.get("technical", {}).get("price", 0)
            
            if not current_price:
                logger.warning(f"🚫 V2 BUY Failed: No valid price for {symbol}")
                return
            
            decision_result = {
                "action": "BUY",
                "confidence": confidence,
                "reason": decision.get("reasoning", f"V2 {entry_type}"),
                "stop_loss": decision.get("stop_loss", current_price * 0.95),
                "take_profit": decision.get("take_profit_2", current_price * 1.10),
                "quantity": decision.get("quantity", 0),
                "allowed": True,
                # V2 CRITICAL: These fields MUST be at top level for execute_buy_flow
                "entry_type": entry_type,
                "partial_tp_target": decision.get("partial_tp_target"),
                "take_profit_1": decision.get("take_profit_1"),
                "partial_tp_percentage": decision.get("partial_tp_percentage", 0.5),
                "metadata": {
                    "risk_reward_ratio": rr_ratio,
                    "regime": decision.get("regime", "UNKNOWN")
                }
            }
            
            # 6. Execute Buy
            if hasattr(self, '_cycle_stats'):
                self._cycle_stats["trade_attempt"] += 1
            
            portfolio = self.execution_manager.portfolio
            success, result = await self.execution_manager.execute_buy_flow(
                symbol=symbol,
                current_price=current_price,
                decision_result=decision_result,
                trade_reason=f"V2-{entry_type}",
                trigger_info=decision.get("reasoning", ""),
                market_snapshot=snapshot
            )
            
            if success:
                if hasattr(self, '_cycle_stats'):
                    self._cycle_stats["trade_success"] += 1
                
                # Add V2-specific position fields
                position = result
                for pos in portfolio.get("positions", []):
                    if pos.get("id") == position.get("id"):
                        pos["entry_type"] = entry_type
                        pos["partial_tp_hit"] = False
                        pos["highest_close_since_entry"] = current_price
                        pos["entry_time"] = time.time()
                        break
                
                if self.execution_manager._save_portfolio:
                    self.execution_manager._save_portfolio(portfolio)
                
                logger.info(
                    f"  ✅ V2 BUY Executed for {symbol} | "
                    f"Type: {entry_type} | "
                    f"SL: ${decision.get('stop_loss', 0):.2f} | "
                    f"TP: ${decision.get('take_profit_2', 0):.2f}"
                )
            else:
                logger.error(f"  ❌ V2 Buy Failed: {result}")
        
        except Exception as e:
            logger.error(f"⚠️ V2 Buy Logic Error ({symbol}): {e}")
            traceback.print_exc()

    async def process_sell_logic(self, symbol: str, snapshot: Dict[str, Any]):
        """
        V2 Exit Logic - Uses position_manager.check_exit_conditions().
        
        This is a backup to the Watchdog for exit logic.
        Watchdog runs every 30s, this runs every cycle (15min).
        
        V2 exit types:
        - 4H_SWING: Partial TP at 5%, trailing stop, final at 10%
        - 1H_MOMENTUM: Partial TP at 2%, trailing stop, final at 4%
        - 15M_SCALP: Target at 1.5%, time exit at 4h
        """
        # Only relevant if we have a position
        positions = self.position_manager.get_open_positions()
        pos = next((p for p in positions if p['symbol'] == symbol), None)
        if not pos:
            return

        try:
            # Get current price
            current_price = snapshot.get("price")
            if not current_price:
                current_price = snapshot.get("technical", {}).get("price")
            
            if not current_price:
                logger.warning(f"[V2 EXIT] {symbol}: Fiyat bulunamadı, exit check atlanıyor")
                return
            
            # Log position status for debugging
            entry_price = pos.get("entry_price", 0)
            pnl_pct = ((current_price - entry_price) / entry_price * 100) if entry_price else 0
            stop_loss = pos.get("current_sl", pos.get("stop_loss", 0))
            take_profit = pos.get("take_profit", 0)
            entry_type = pos.get("entry_type", "UNKNOWN")
            
            logger.info(
                f"[V2 EXIT] {symbol}: price=${current_price:.2f}, entry=${entry_price:.2f}, "
                f"PnL={pnl_pct:+.2f}%, SL=${stop_loss:.2f}, TP=${take_profit:.2f}, type={entry_type}"
            )
            
            # Use V2 exit logic
            exit_result = self.position_manager.check_exit_conditions(pos, current_price, snapshot)
            action = exit_result.get("action", "HOLD")
            reason = exit_result.get("reason", "No reason")
            
            logger.debug(f"[V2 EXIT] {symbol}: action={action}, reason={reason}")
            
            if action == "HOLD":
                return
            
            if action == "SELL_PARTIAL":
                # Partial TP - handled by Watchdog primarily
                # Log but don't execute here (Watchdog will catch it)
                logger.info(f"[V2 SELL] {symbol}: Partial TP detected, Watchdog will handle")
                return
            
            elif action == "SELL":
                logger.info(f"📉 V2 Exit Signal ({symbol}): {reason} [type={entry_type}]")
                
                success, pnl, trade = await self.execution_manager.execute_sell_flow(
                    symbol=symbol,
                    current_price=current_price,
                    ai_reasoning=reason,
                    ai_confidence=100,
                    market_snapshot=snapshot
                )
                
                if success:
                    self.position_manager.register_trade_result(pnl)
                    logger.info(f"  ✅ V2 SELL Executed. PnL: ${pnl:.2f}")

        except Exception as e:
             logger.error(f"⚠️ V2 Sell Logic Error ({symbol}): {e}")

    def check_global_safety(self):
        """
        Check if new BUY orders are allowed allowed.
        Returns: (bool, reason_str)
        """
        # 0. Cooldown Check
        if time.time() < self.cooldown_until:
             return False, f"Cooldown active until {time.ctime(self.cooldown_until)}"

        # 1. Daily Loss Limit
        # Need today's PnL. ExecutionManager/PositionManager should track this.
        # Assuming ExecutionManager has get_today_pnl based on history
        if hasattr(self.execution_manager, "get_today_pnl"):
            today_pnl = self.execution_manager.get_today_pnl()
            balance = getattr(SETTINGS, "BASLANGIC_BAKIYE", 1000)
            # If current portfolio balance is available, use that?
            if self.execution_manager.portfolio:
                 balance = self.execution_manager.portfolio.get("balance", balance)
            
            loss_limit = -(SETTINGS.MAX_DAILY_LOSS_PCT / 100.0) * balance
            if today_pnl <= loss_limit:
                 # Emit CRITICAL alert
                 try:
                     from alert_manager import get_alert_manager, AlertLevel, AlertCode
                     get_alert_manager().emit(
                         AlertCode.DAILY_LOSS_LIMIT_HIT, AlertLevel.CRITICAL,
                         "Daily loss limit reached", pnl=f"${today_pnl:.2f}", limit=f"${loss_limit:.2f}"
                     )
                 except: pass
                 return False, f"Daily Loss Limit Hit (${today_pnl:.2f} <= ${loss_limit:.2f})"
        
        # 2. Max Open Positions
        open_count = self.position_manager.get_open_position_count()
        if open_count >= SETTINGS.MAX_OPEN_POSITIONS:
            # Emit WARN alert (throttled)
            try:
                from alert_manager import get_alert_manager, AlertLevel, AlertCode
                get_alert_manager().emit(
                    AlertCode.MAX_OPEN_POSITIONS_REACHED, AlertLevel.WARN,
                    "Max open positions reached", count=open_count, max=SETTINGS.MAX_OPEN_POSITIONS
                )
            except: pass
            return False, f"Max Positions Reached ({open_count}/{SETTINGS.MAX_OPEN_POSITIONS})"

        # 3. Consecutive Losses
        streak = self.position_manager.get_consecutive_losses()
        if streak >= SETTINGS.MAX_CONSECUTIVE_LOSSES:
            # Trigger Cooldown
            wait_mins = getattr(SETTINGS, "COOLDOWN_MINUTES", 60)
            self.cooldown_until = time.time() + (wait_mins * 60)
            # Reset streak maybe? Or keep it until a win? usually triggered once.
            # Here we just block.
            logger.warning(f"❄️ Consecutive Loss Limit ({streak}) Hit! Cooldown for {wait_mins}m.")
            return False, "Consecutive Loss Cooldown"
            
        return True, None
