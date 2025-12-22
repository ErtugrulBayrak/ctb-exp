import asyncio
import time
import logging
import traceback
from typing import List, Dict, Any, Optional

# Import modules (assuming they are in the python path)
try:
    from config import SETTINGS, RUN_PROFILE, PAPER_SANITY_MODE
except ImportError:
    # Fallback if config is missing (should not happen in production)
    RUN_PROFILE = "paper"
    PAPER_SANITY_MODE = False
    class SETTINGS:
        WATCHLIST = ['BTC', 'ETH']
        LOOP_SECONDS = 900
        USE_NEWS_LLM = False
        MAX_DAILY_LOSS_PCT = 3.0
        MAX_OPEN_POSITIONS = 3
        MAX_CONSECUTIVE_LOSSES = 4
        COOLDOWN_MINUTES = 60
        BASLANGIC_BAKIYE = 1000
        STRATEGY_MODE = "LEGACY"

# V1 Strategy Imports
try:
    from strategies.regime_filter import RegimeFilter
    from strategies.swing_trend_v1 import SwingTrendV1
    from strategies.news_veto import NewsVeto
    V1_AVAILABLE = True
except ImportError:
    V1_AVAILABLE = False
    RegimeFilter = None
    SwingTrendV1 = None
    NewsVeto = None

# Setup Logger
# NOTE: Handler ekleme işlemi main.py'deki merkezi TeeHandler tarafından yapılıyor
# Bu dosyada handler eklemek duplikasyona neden olur
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
    
    V1 Mode: Uses RegimeFilter, SwingTrendV1, and NewsVeto for deterministic signals.
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
        
        # V1 Strategy Mode
        self._strategy_mode = getattr(SETTINGS, "STRATEGY_MODE", "LEGACY")
        self._v1_strategy = None
        self._v1_regime_filter = None
        self._v1_news_veto = None
        
        if self._strategy_mode == "REGIME_SWING_TREND_V1" and V1_AVAILABLE:
            self._v1_regime_filter = RegimeFilter()
            self._v1_strategy = SwingTrendV1()
            self._v1_news_veto = NewsVeto()
            logger.info("✅ V1 Strategy components initialized")
            
            # PAPER_SANITY_MODE: ADX eşiğini düşür (hızlı test için)
            if RUN_PROFILE == "paper" and PAPER_SANITY_MODE:
                # RegimeFilter'daki ADX eşiğini override et
                if hasattr(self._v1_regime_filter, 'min_adx'):
                    self._v1_regime_filter.min_adx = 15.0
                logger.warning("⚠️ [SANITY MODE] MIN_ADX_ENTRY forced to 15 for paper test")
        
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
        logger.info(f"Strategy Mode: {self._strategy_mode}")
        logger.info(f"Daily Loss Limit: {SETTINGS.MAX_DAILY_LOSS_PCT}%")
        logger.info(f"Max Open Positions: {SETTINGS.MAX_OPEN_POSITIONS}")
        logger.info(f"Max Loss Streak: {SETTINGS.MAX_CONSECUTIVE_LOSSES}")
        logger.info(f"Cooldown Minutes: {SETTINGS.COOLDOWN_MINUTES}")
        if self._strategy_mode == "REGIME_SWING_TREND_V1":
            logger.info(f"V1 Risk Per Trade: {getattr(SETTINGS, 'RISK_PER_TRADE_V1', 1.0)}%")
            logger.info(f"V1 Partial TP: {getattr(SETTINGS, 'PARTIAL_TP_ENABLED', True)}")
            logger.info(f"V1 Trailing: {getattr(SETTINGS, 'TRAILING_ENABLED', True)}")
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
        logger.info(f"\n⚡ Cycle Start: {time.strftime('%H:%M:%S')}")
        
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

        # 4b. Fetch Global News Summary (TTL cached)
        global_news_summary = None
        try:
            global_news_summary = self.market_data_engine.get_global_news_summary()
        except Exception as e:
            logger.warning(f"⚠️ Failed to fetch global news summary: {e}")

        # 4c. Fetch Reddit LLM Summary (TTL cached)
        reddit_summary = None
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

        # 5. Parallel Snapshot Collection
        logger.info(f"📥 Collecting snapshots for {len(self.watchlist)} symbols...")
        tasks = []
        for symbol in self.watchlist:
            # build_snapshot involves I/O (router calls), now purely async.
            tasks.append(self.market_data_engine.build_snapshot(symbol))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 6. Process Opportunities
        for symbol, result in zip(self.watchlist, results):
            if isinstance(result, Exception):
                logger.error(f"❌ Snapshot failed for {symbol}: {result}")
                continue
            
            snapshot = result
            if not snapshot:
                continue

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

            # V1: Fetch multi-timeframe data if in V1 mode
            if self._strategy_mode == "REGIME_SWING_TREND_V1":
                try:
                    tf_data = await self.market_data_engine.get_v1_timeframe_data(symbol)
                    snapshot["tf"] = tf_data
                except Exception as e:
                    logger.warning(f"[V1] TF data fetch error for {symbol}: {e}")
                    snapshot["tf"] = {"1h": {}, "15m": {}}

            # Check for Sell logic (always allowed)
            await self.process_sell_logic(symbol, snapshot)
            
            # Check for Buy logic (if safety allows)
            if can_buy:
                await self.process_buy_logic(symbol, snapshot)
        
        # 7. Summary Log
        port_summary = self.position_manager.get_portfolio_summary()
        logger.info(f"💰 Balance: ${port_summary.get('balance'):.0f} | Open: {port_summary.get('open_positions')}")



    async def monitor_positions(self):
        """
        Periodically checks current price of open positions against SL/TP.
        """
        open_positions = self.position_manager.get_open_positions()
        if not open_positions:
            return

        logger.info(f"🛡️ Monitoring {len(open_positions)} open positions...")
        
        for pos in open_positions:
            symbol = pos.get('symbol')
            if not symbol: continue

            try:
                # Fetch current price directly from Router (Fast/Cached)
                current_price = self.exchange_router.get_price(symbol)
                
                # If cached price is None/stale, might need to wait or skip
                if not current_price:
                    # Attempt fetch
                    current_price = self.exchange_router.get_price_or_fetch(symbol)
                
                if not current_price:
                    logger.warning(f"  Unknown price for {symbol}, skipping monitor.")
                    continue

                # Check SL/TP
                sl = pos.get('stop_loss')
                tp = pos.get('take_profit')
                
                # Decision logic
                close_signal = None
                pnl_reason = None
                
                if sl and current_price <= sl:
                    close_signal = "STOP_LOSS"
                    pnl_reason = "SL Hit"
                elif tp and current_price >= tp:
                    close_signal = "TAKE_PROFIT"
                    pnl_reason = "TP Hit"
                
                if close_signal:
                    logger.info(f"🚨 {close_signal} TRIGGERED for {symbol} @ {current_price}")
                    # Execute Close
                    success, pnl, trade = await self.execution_manager.execute_sell_flow(
                        symbol=symbol,
                        current_price=current_price,
                        ai_reasoning=pnl_reason,
                        ai_confidence=100,
                        market_snapshot={} # Empty snapshot acceptable for SL/TP
                    )
                    
                    if success:
                        self.position_manager.register_trade_result(pnl)
                        logger.info(f"  ✅ Position Closed. PnL: ${pnl:.2f}")
                    else:
                        logger.error(f"  ❌ Close Failed for {symbol}")

            except Exception as e:
                logger.error(f"⚠️ Error monitoring {symbol}: {e}")

    async def process_buy_logic(self, symbol: str, snapshot: Dict[str, Any]):
        """Evaluate and execute BUY opportunities."""
        # V1 Strategy Mode - deterministik kurallar
        if self._strategy_mode == "REGIME_SWING_TREND_V1" and self._v1_strategy:
            await self._process_buy_v1(symbol, snapshot)
        else:
            await self._process_buy_legacy(symbol, snapshot)
    
    async def _process_buy_v1(self, symbol: str, snapshot: Dict[str, Any]):
        """V1 Buy Logic - Regime Filter + Swing Trend + News Veto."""
        try:
            portfolio = self.execution_manager.portfolio
            balance = portfolio.get("balance", 0)
            
            # 1. V1 Strategy Entry Evaluation
            signal = self._v1_strategy.evaluate_entry(snapshot, balance)
            
            if signal.action != "BUY":
                # Log blocking reason for debugging
                if signal.metadata.get("blocked_by_regime"):
                    logger.debug(f"[V1] {symbol}: Regime block - {signal.reason}")
                return
            
            logger.info(f"💡 V1 Signal ({symbol}): BUY (Conf: {signal.confidence}%) | {signal.reason}")
            
            # 2. News/Reddit Veto Check (LLM-based)
            veto_checked = False
            veto_result = None
            if self._v1_news_veto and getattr(SETTINGS, 'USE_NEWS_LLM_VETO', True):
                # Bundle haber + reddit
                news_summary = snapshot.get("coin_news_str", "")
                reddit_data = snapshot.get("reddit_summary", {})
                reddit_str = reddit_data.get("coin_specific_impacts", {}).get(
                    symbol.replace("USDT", ""), ""
                ) if isinstance(reddit_data, dict) else ""
                
                veto_result = self._v1_news_veto.check_veto(
                    symbol, 
                    news_summary=news_summary,
                    reddit_summary=reddit_str
                )
                veto_checked = True
                
                if veto_result.veto:
                    logger.warning(
                        f"🚫 V1 News Veto ({symbol}): {veto_result.reason} "
                        f"(Conf: {veto_result.confidence}%) | Tags: {veto_result.tags}"
                    )
                    # Log blocked_by_news_veto
                    logger.info(
                        f"[VETO_LOG] symbol={symbol} blocked_by_news_veto=True "
                        f"veto_confidence={veto_result.confidence} "
                        f"veto_reason={veto_result.reason} veto_tags={veto_result.tags}"
                    )
                    return
                else:
                    logger.debug(f"[VETO] {symbol}: No veto - {veto_result.reason}")
            
            # 3. Cooldown Check (Risk Manager)
            in_cooldown, cooldown_reason = self.risk_manager.is_in_cooldown()
            if in_cooldown:
                logger.info(f"❄️ V1 {symbol}: {cooldown_reason}")
                return
            
            # 4. Build decision result for ExecutionManager
            current_price = snapshot.get("price", 0)
            if not current_price:
                current_price = snapshot.get("technical", {}).get("price", 0)
            
            if not current_price:
                logger.warning(f"🚫 V1 BUY Failed: No valid price for {symbol}")
                return
            
            decision_result = {
                "action": "BUY",
                "confidence": signal.confidence,
                "reason": signal.reason,
                "stop_loss": signal.stop_loss,
                "take_profit": signal.take_profit,
                "quantity": signal.quantity,
                "allowed": True,
                "metadata": signal.metadata
            }
            
            # 5. Execute Buy
            success, result = await self.execution_manager.execute_buy_flow(
                symbol=symbol,
                current_price=current_price,
                decision_result=decision_result,
                trade_reason="V1-SWING",
                trigger_info=signal.reason,
                market_snapshot=snapshot
            )
            
            if success:
                # Add V1 position fields
                position = result
                v1_fields = self._v1_strategy.calculate_position_fields(
                    entry_price=current_price,
                    quantity=signal.quantity,
                    atr=snapshot.get("tf", {}).get("1h", {}).get("atr", current_price * 0.02)
                )
                
                # Update position with V1 fields
                for pos in portfolio.get("positions", []):
                    if pos.get("id") == position.get("id"):
                        pos.update(v1_fields)
                        break
                
                if self.execution_manager._save_portfolio:
                    self.execution_manager._save_portfolio(portfolio)
                
                logger.info(f"  ✅ V1 BUY Executed for {symbol} | SL=${signal.stop_loss:.2f} TP=${signal.take_profit:.2f}")
            else:
                logger.error(f"  ❌ V1 Buy Failed: {result}")
        
        except Exception as e:
            logger.error(f"⚠️ V1 Buy Logic Error ({symbol}): {e}")
            traceback.print_exc()
    
    async def _process_buy_legacy(self, symbol: str, snapshot: Dict[str, Any]):
        """Legacy Buy Logic - LLM-based strategy."""
        try:
            # 1. Strategy Evaluation
            decision = await self.strategy_engine.evaluate_opportunity(snapshot)
            
            action = decision.get("action")
            confidence = decision.get("confidence", 0)
            
            if action != "BUY":
                return

            src = decision.get("metadata", {}).get("source", "UNKNOWN")
            logger.info(f"💡 Strategy Signal ({symbol}): BUY (Conf: {confidence}%) [src={src}]")

            # 2. Risk Management
            portfolio = self.execution_manager.portfolio
            
            risk_decision = self.risk_manager.evaluate_entry_risk(
                snapshot=snapshot,
                base_decision=decision,
                portfolio=portfolio
            )
            
            if not risk_decision.get("allowed"):
                logger.info(f"  🛡️ Risk Manager blocked BUY: {risk_decision.get('reason')}")
                return

            # 3. Execution
            current_price = snapshot.get("price")
            if not current_price:
                current_price = snapshot.get("technical", {}).get("price")
            
            if not current_price:
                logger.warning(f"🚫 BUY Failed: No valid price for {symbol}")
                return
            
            success, result = await self.execution_manager.execute_buy_flow(
                symbol=symbol,
                current_price=current_price,
                decision_result=risk_decision,
                market_snapshot=snapshot
            )
            
            if success:
                logger.info(f"  ✅ BUY Executed for {symbol}")
            else:
                logger.error(f"  ❌ Buy Failed: {result}")

        except Exception as e:
            logger.error(f"⚠️ Buy Logic Error ({symbol}): {e}")
            traceback.print_exc()


    async def process_sell_logic(self, symbol: str, snapshot: Dict[str, Any]):
        """Evaluate AI Sell logic (Technical/Profit Protection)."""
        # Only relevant if we have a position
        positions = self.position_manager.get_open_positions()
        # Find position for this symbol
        # Assuming symbol match is direct (e.g. BTC vs BTCUSDT needs normalization if not consistent)
        # Snapshot symbol usually is normalized. Position symbol should be normalized.
        
        pos = next((p for p in positions if p['symbol'] == symbol), None)
        if not pos:
            return

        try:
            # 1. Strategy Evaluation
            decision = await self.strategy_engine.evaluate_sell_opportunity(
                position=pos,
                market_snapshot=snapshot
            )
            
            if decision.get("action") == "SELL":
                reason = decision.get("reason", "Strategy Sell")
                confidence = decision.get("confidence", 0)
                
                src = decision.get("metadata", {}).get("source", "UNKNOWN")
                logger.info(f"📉 Strategy Signal ({symbol}): SELL ({reason}) [src={src}]")
                
                # 2. Risk Validation (Evaluate Exit)
                risk_decision = self.risk_manager.evaluate_exit_risk(
                    snapshot=snapshot,
                    position=pos,
                    base_decision=decision
                )
                
                if not risk_decision.get("allowed"):
                     logger.info(f"  🛡️ Risk Manager blocked SELL: {risk_decision.get('reason')}")
                     return

                # 3. Execution
                current_price = snapshot.get("price")
                if not current_price:
                    # Fallback to technical price if available
                    current_price = snapshot.get("technical", {}).get("price")
                
                if not current_price:
                    logger.warning(f"🚫 SELL Failed: No valid price for {symbol}")
                    return
                
                success, pnl, trade = await self.execution_manager.execute_sell_flow(
                    symbol=symbol,
                    current_price=current_price,
                    ai_reasoning=reason,
                    ai_confidence=confidence,
                    market_snapshot=snapshot
                )
                
                if success:
                    self.position_manager.register_trade_result(pnl)
                    logger.info(f"  ✅ SELL Executed. PnL: ${pnl:.2f}")

        except Exception as e:
             logger.error(f"⚠️ Sell Logic Error ({symbol}): {e}")

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
