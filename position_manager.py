
import time
import asyncio
from datetime import datetime
from trade_logger import logger

# Exit reason enum
try:
    from exit_reason import ExitReason
except ImportError:
    class ExitReason:
        STOP_LOSS = "STOP_LOSS"
        TRAIL_STOP = "TRAIL_STOP"
        PARTIAL_TP = "PARTIAL_TP"

# Config import (retry ayarları için)
try:
    from config import SETTINGS
except ImportError:
    class MockSettings:
        LIVE_ORDER_MAX_RETRIES = 3
        LIVE_ORDER_RETRY_DELAY = 2.0
    SETTINGS = MockSettings()

class PositionManager:
    """
    Manages portfolio open positions, monitors risk (SL/TP),
    and executes close decisions via OrderExecutor.
    """
    def __init__(self, portfolio, market_data_engine, strategy_engine, executor, execution_manager,
                 save_portfolio_fn=None, telegram_fn=None, telegram_config=None):
        """
        Args:
            portfolio: Reference to the portfolio dictionary
            market_data_engine: Engine to get current prices/market state
            strategy_engine: Engine for trading strategy decisions
            executor: OrderExecutor (can also be accessed via execution_manager)
            execution_manager: ExecutionManager instance for executing trades
            save_portfolio_fn: Function to save portfolio state
            telegram_fn: Async function to send telegram notifications
            telegram_config: Dict containing bot_token and chat_id
        """
        self.portfolio = portfolio
        self.market_data_engine = market_data_engine
        self.strategy_engine = strategy_engine
        self.executor = executor
        self.execution_manager = execution_manager
        self.save_portfolio_fn = save_portfolio_fn
        self.telegram_fn = telegram_fn
        self.telegram_config = telegram_config or {}
        self._consecutive_losses = self._calculate_initial_consecutive_losses()

    def _calculate_initial_consecutive_losses(self):
        """Count trailing consecutive losses from history on init."""
        history = self.portfolio.get("history", [])
        count = 0
        for trade in reversed(history):
            pnl = trade.get("profit_loss", 0)
            if pnl is not None and pnl < 0:
                count += 1
            else:
                break
        return count

    def get_open_positions(self):
        """Returns list of open positions."""
        return self.portfolio.get("positions", [])

    def get_open_position_count(self):
        """Return number of open positions."""
        return len(self.portfolio.get("positions", []))

    def get_consecutive_losses(self):
        """Return current consecutive loss count."""
        return self._consecutive_losses

    def reset_consecutive_losses(self):
        """Reset consecutive losses counter."""
        self._consecutive_losses = 0

    def register_trade_result(self, pnl: float):
        """Update consecutive loss counter based on trade result."""
        if pnl is None:
            return
        if pnl < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

    def get_portfolio_summary(self):
        """
        Returns aggregated portfolio metrics.
        Moved from scraper-v90.py (get_portfolio_summary).
        """
        positions = self.get_open_positions()
        history = self.portfolio.get("history", [])
        
        total_trades = len(history)
        winning_trades = len([h for h in history if h.get("profit_loss", 0) > 0])
        losing_trades = len([h for h in history if h.get("profit_loss", 0) < 0])
        total_pnl = sum(h.get("profit_loss", 0) for h in history)
        
        return {
            "balance": self.portfolio["balance"],
            "open_positions": len(positions),
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "win_rate": (winning_trades / total_trades * 100) if total_trades > 0 else 0,
            "total_pnl": total_pnl
        }

    async def check_positions_and_apply_risk(self, live_trading_enabled=False):
        """
        Iterates open positions, checks SL/TP, and closes them if triggered.
        Logic moved from portfoy_yonet in scraper-v90.py.
        
        Args:
            live_trading_enabled: Boolean from SETTINGS.LIVE_TRADING
        
        Returns: (closed_count, total_pnl)
        """
        positions = self.get_open_positions()
        
        if not positions:
            return 0, 0
            
        # Log section header
        logger.info("─" * 50)
        logger.info("💼 PORTFÖY YÖNETİMİ (SL/TP KONTROLÜ)")
        logger.info("─" * 50)
        
        logger.info(f"Açık pozisyon sayısı: {len(positions)}")
        
        closed_count = 0
        total_pnl = 0
        
        bot_token = self.telegram_config.get("bot_token")
        chat_id = self.telegram_config.get("chat_id")
        
        # Iterate over a copy
        for position in positions[:]:
            symbol = position.get("symbol")
            position_id = position.get("id")
            stop_loss = position.get("stop_loss")
            take_profit = position.get("take_profit")
            entry_price = position.get("entry_price")
            
            # Get current price from MarketDataEngine
            current_price = self.market_data_engine.get_current_price(symbol)
            
            if current_price is None:
                logger.warning(f"  {symbol}: Fiyat alınamadı (MarketDataEngine), atlanıyor")
                continue
            
            logger.info(f"  {symbol}: ${current_price:.4f} (SL: ${stop_loss:.4f} | TP: ${take_profit:.4f})")
            
            close_reason = None
            log_emoji = ""
            log_msg = ""
            is_error = False
            
            if current_price <= stop_loss:
                close_reason = "SL"
                log_emoji = "🛑"
                log_msg = "STOP LOSS"
                is_error = True
            elif current_price >= take_profit:
                close_reason = "TP"
                log_emoji = "💰"
                log_msg = "TAKE PROFIT"
                is_error = False
            
            if close_reason:
                # Delegate paper close to ExecutionManager
                success, pnl, closed_trade = self.execution_manager.close_position(position_id, current_price, close_reason)
                
                if success:
                    closed_count += 1
                    total_pnl += pnl
                    self.register_trade_result(pnl)
                    msg = f"{log_emoji} {log_msg}: {symbol} kapatıldı | PnL: ${pnl:.2f}"
                    
                    if is_error:
                         logger.error(msg)
                    else:
                         logger.info(msg)
                    
                    if self.telegram_fn and bot_token and chat_id:
                        mesaj = (
                            f"{log_emoji} <b>{log_msg} ({close_reason})</b>\n\n"
                            f"<b>Coin:</b> {symbol}/USDT\n"
                            f"<b>Giriş:</b> ${entry_price:.4f}\n"
                            f"<b>Çıkış:</b> ${current_price:.4f}\n"
                            f"<b>PnL:</b> ${pnl:.2f} ({closed_trade['profit_pct']:.1f}%)\n\n"
                            f"<b>💰 Güncel Bakiye:</b> ${self.portfolio['balance']:.2f}\n\n"
                            f"<i>{closed_trade.get('haber_baslik', '')}</i>"
                        )
                        await self.telegram_fn(bot_token, chat_id, mesaj)
                    
                    # LIVE TRADING: Executing sell via Executor with retry
                    if live_trading_enabled and self.executor:
                        quantity = position.get('quantity', 0)
                        max_retries = getattr(SETTINGS, 'LIVE_ORDER_MAX_RETRIES', 3)
                        retry_delay = getattr(SETTINGS, 'LIVE_ORDER_RETRY_DELAY', 2.0)
                        
                        for attempt in range(max_retries):
                            try:
                                live_order = await self.executor.create_order(
                                    symbol=f"{symbol}USDT", 
                                    side="SELL",
                                    quantity=quantity, 
                                    order_type="MARKET"
                                )
                                logger.info(f"🔴 CANLI {close_reason} SATIŞ: {symbol} OrderId={live_order.get('orderId')}")
                                
                                # Update history with live order info
                                if self.portfolio.get("history"):
                                    self.portfolio["history"][-1]["live_sell_order_id"] = live_order.get("orderId")
                                    self.portfolio["history"][-1]["live_sell_status"] = "FILLED"
                                    if self.save_portfolio_fn:
                                        self.save_portfolio_fn(self.portfolio)
                                break  # Başarılı, döngüden çık
                                        
                            except Exception as e:
                                if attempt < max_retries - 1:
                                    logger.warning(f"⚠️ CANLI {close_reason} DENEME {attempt + 1}/{max_retries} BAŞARISIZ: {e}")
                                    await asyncio.sleep(retry_delay)
                                else:
                                    logger.error(f"❌ CANLI {close_reason} SATIŞ TÜM DENEMELER BAŞARISIZ: {symbol} - {e}")
                                    if self.portfolio.get("history"):
                                        self.portfolio["history"][-1]["live_sell_failed"] = True
                                        self.portfolio["history"][-1]["live_sell_error"] = str(e)
                                        if self.save_portfolio_fn:
                                            self.save_portfolio_fn(self.portfolio)
                    
                    await asyncio.sleep(1)
            
            await asyncio.sleep(0.3)  # Rate limiting
        
        if closed_count > 0:
            logger.info(f"Toplam kapatılan: {closed_count} | Toplam PnL: ${total_pnl:.2f}")
        else:
            logger.debug("SL/TP tetiklenmedi, pozisyonlar devam ediyor")
        
        return closed_count, total_pnl

    # ═══════════════════════════════════════════════════════════════════════════
    # SL/TP WATCHDOG - Bağımsız Pozisyon İzleme Görevi
    # ═══════════════════════════════════════════════════════════════════════════
    
    async def start_watchdog(self, live_trading_enabled: bool = False):
        """
        SL/TP Watchdog - Ana döngüden bağımsız pozisyon izleme görevi.
        
        Config'den SLTP_WATCHDOG_INTERVAL_SEC (varsayılan 30sn) aralıklarla
        açık pozisyonları kontrol eder ve SL/TP tetiklenince kapatır.
        
        Bu görev asyncio.create_task() ile başlatılmalı.
        
        Args:
            live_trading_enabled: Canlı trading aktif mi?
        """
        # Config'den ayarları al
        enabled = getattr(SETTINGS, 'SLTP_WATCHDOG_ENABLED', True)
        interval = getattr(SETTINGS, 'SLTP_WATCHDOG_INTERVAL_SEC', 30)
        
        if not enabled:
            logger.info("🐕 SL/TP Watchdog devre dışı (config'de kapalı)")
            return
        
        logger.info(f"🐕 SL/TP Watchdog başlatıldı (her {interval}sn)")
        
        self._watchdog_running = True
        
        while self._watchdog_running:
            try:
                # Sadece açık pozisyon varsa kontrol yap
                positions = self.get_open_positions()
                
                if positions:
                    # Hafif bir kontrol - sadece SL/TP, log header yazmadan
                    await self._quick_sltp_check(positions, live_trading_enabled)
                
                await asyncio.sleep(interval)
                
            except asyncio.CancelledError:
                logger.info("🐕 SL/TP Watchdog durduruldu (cancelled)")
                break
            except Exception as e:
                logger.error(f"🐕 Watchdog hatası: {e}")
                await asyncio.sleep(interval)
        
        logger.info("🐕 SL/TP Watchdog sonlandı")
    
    def stop_watchdog(self):
        """Watchdog'u durdur."""
        self._watchdog_running = False
    
    async def _quick_sltp_check(self, positions: list, live_trading_enabled: bool):
        """
        Hızlı SL/TP kontrolü - sadece tetiklenenleri loglar.
        Ana check_positions_and_apply_risk'ten daha hafif.
        """
        bot_token = self.telegram_config.get("bot_token")
        chat_id = self.telegram_config.get("chat_id")
        
        for position in positions[:]:  # Copy to avoid modification issues
            symbol = position.get("symbol")
            position_id = position.get("id")
            stop_loss = position.get("stop_loss")
            take_profit = position.get("take_profit")
            entry_price = position.get("entry_price")
            
            # Güncel fiyat al
            current_price = self.market_data_engine.get_current_price(symbol)
            
            if current_price is None:
                continue
            
            close_reason = None
            log_emoji = ""
            log_msg = ""
            
            if current_price <= stop_loss:
                close_reason = "SL"
                log_emoji = "🛑"
                log_msg = "STOP LOSS"
            elif current_price >= take_profit:
                close_reason = "TP"
                log_emoji = "💰"
                log_msg = "TAKE PROFIT"
            
            if close_reason:
                logger.info(f"🐕 Watchdog: {symbol} {log_msg} tetiklendi! (${current_price:.4f})")
                
                # ExecutionManager ile kapat
                success, pnl, closed_trade = self.execution_manager.close_position(
                    position_id, current_price, close_reason
                )
                
                if success:
                    self.register_trade_result(pnl)
                    logger.info(f"{log_emoji} {log_msg}: {symbol} kapatıldı | PnL: ${pnl:.2f}")
                    
                    # Telegram bildirimi
                    if self.telegram_fn and bot_token and chat_id:
                        mesaj = (
                            f"{log_emoji} <b>{log_msg} ({close_reason})</b>\n\n"
                            f"<b>Coin:</b> {symbol}/USDT\n"
                            f"<b>Giriş:</b> ${entry_price:.4f}\n"
                            f"<b>Çıkış:</b> ${current_price:.4f}\n"
                            f"<b>PnL:</b> ${pnl:.2f} ({closed_trade['profit_pct']:.1f}%)\n\n"
                            f"<b>💰 Güncel Bakiye:</b> ${self.portfolio['balance']:.2f}"
                        )
                        try:
                            await self.telegram_fn(bot_token, chat_id, mesaj)
                        except Exception as e:
                            logger.error(f"Telegram hatası: {e}")
                    
                    # Live trading - aynı retry logic
                    if live_trading_enabled and self.executor:
                        quantity = position.get('quantity', 0)
                        max_retries = getattr(SETTINGS, 'LIVE_ORDER_MAX_RETRIES', 3)
                        retry_delay = getattr(SETTINGS, 'LIVE_ORDER_RETRY_DELAY', 2.0)
                        
                        for attempt in range(max_retries):
                            try:
                                live_order = await self.executor.create_order(
                                    symbol=f"{symbol}USDT",
                                    side="SELL",
                                    quantity=quantity,
                                    order_type="MARKET"
                                )
                                logger.info(f"🔴 CANLI {close_reason} SATIŞ: {symbol} OrderId={live_order.get('orderId')}")
                                
                                if self.portfolio.get("history"):
                                    self.portfolio["history"][-1]["live_sell_order_id"] = live_order.get("orderId")
                                    self.portfolio["history"][-1]["live_sell_status"] = "FILLED"
                                    if self.save_portfolio_fn:
                                        self.save_portfolio_fn(self.portfolio)
                                break
                            except Exception as e:
                                if attempt < max_retries - 1:
                                    logger.warning(f"⚠️ CANLI {close_reason} DENEME {attempt + 1}/{max_retries} BAŞARISIZ: {e}")
                                    await asyncio.sleep(retry_delay)
                                else:
                                    logger.error(f"❌ CANLI {close_reason} TÜM DENEMELER BAŞARISIZ: {symbol} - {e}")
                                    if self.portfolio.get("history"):
                                        self.portfolio["history"][-1]["live_sell_failed"] = True
                                        self.portfolio["history"][-1]["live_sell_error"] = str(e)
                                        if self.save_portfolio_fn:
                                            self.save_portfolio_fn(self.portfolio)

    # ═══════════════════════════════════════════════════════════════════════════
    # V1: Watchdog - Partial TP ve Trailing Stop 
    # ═══════════════════════════════════════════════════════════════════════════
    
    async def v1_watchdog_check(self, live_trading_enabled: bool = False):
        """
        V1 stratejisi için gelişmiş watchdog kontrolü.
        
        Kontroller:
        1. SL tetiklendi mi?
        2. Partial TP (1R) tetiklendi mi?
        3. Trailing stop güncellenmeli mi?
        4. Trailing stop tetiklendi mi?
        """
        positions = self.get_open_positions()
        if not positions:
            return
        
        partial_tp_enabled = getattr(SETTINGS, 'PARTIAL_TP_ENABLED', True)
        partial_tp_fraction = getattr(SETTINGS, 'PARTIAL_TP_FRACTION', 0.5)
        trailing_enabled = getattr(SETTINGS, 'TRAILING_ENABLED', True)
        trail_atr_mult = getattr(SETTINGS, 'TRAIL_ATR_MULT', 3.0)
        
        bot_token = self.telegram_config.get("bot_token")
        chat_id = self.telegram_config.get("chat_id")
        
        for position in positions[:]:
            symbol = position.get("symbol")
            position_id = position.get("id")
            entry_price = position.get("entry_price", 0)
            quantity = position.get("quantity", 0)
            
            # V1 alanları
            current_sl = position.get("current_sl", position.get("stop_loss", 0))
            initial_sl = position.get("initial_sl", current_sl)
            partial_taken = position.get("partial_taken", False)
            partial_tp_price = position.get("partial_tp_price", 0)
            
            # Güncel fiyat ve ATR
            current_price = self.market_data_engine.get_current_price(symbol)
            if not current_price:
                continue
            
            # Snapshot'tan ATR al (trailing için)
            try:
                snapshot = self.market_data_engine.build_snapshot(symbol)
                tf_1h = snapshot.get("tf", {}).get("1h", {})
                atr = tf_1h.get("atr", snapshot.get("technical", {}).get("atr", 0))
                highest_close = tf_1h.get("highest_close_trail", current_price)
            except:
                atr = 0
                highest_close = current_price
            
            # ─────────────────────────────────────────────────────────────────────
            # 1. SL Kontrolü
            # ─────────────────────────────────────────────────────────────────────
            if current_price <= current_sl:
                # Standardized watchdog event log
                exit_reason = ExitReason.TRAIL_STOP if partial_taken else ExitReason.STOP_LOSS
                logger.info(
                    f"[WATCHDOG] {symbol} | event=stop_triggered | "
                    f"exit_reason={exit_reason} | price=${current_price:.4f} | "
                    f"sl=${current_sl:.4f} | partial_taken={partial_taken}"
                )
                
                success, pnl, closed = self.execution_manager.close_position(
                    position_id, current_price, str(exit_reason)
                )
                
                if success:
                    self.register_trade_result(pnl)
                    logger.info(f"🛑 {exit_reason}: {symbol} kapatıldı | PnL: ${pnl:.2f}")
                    
                    if self.telegram_fn and bot_token and chat_id:
                        await self._send_close_notification(
                            bot_token, chat_id, symbol, entry_price, 
                            current_price, pnl, exit_reason
                        )
                continue
            
            # ─────────────────────────────────────────────────────────────────────
            # 2. Partial TP Kontrolü (1R)
            # ─────────────────────────────────────────────────────────────────────
            if partial_tp_enabled and not partial_taken and entry_price > 0 and initial_sl > 0:
                # 1R hesapla
                stop_distance = entry_price - initial_sl
                one_r_price = entry_price + stop_distance
                
                if partial_tp_price <= 0:
                    partial_tp_price = one_r_price
                
                if current_price >= one_r_price:
                    sell_qty = quantity * partial_tp_fraction
                    remaining_qty = quantity - sell_qty
                    
                    # Standardized watchdog event log
                    logger.info(
                        f"[WATCHDOG] {symbol} | event=partial_tp_triggered | "
                        f"exit_reason={ExitReason.PARTIAL_TP} | price=${current_price:.4f} | "
                        f"1R=${one_r_price:.4f} | sell_qty={sell_qty:.6f}"
                    )
                    
                    # Kısmi satış yap
                    success, pnl, _ = self.execution_manager.close_position(
                        position_id, current_price, str(ExitReason.PARTIAL_TP),
                        partial_qty=sell_qty
                    )
                    
                    if success:
                        # Pozisyon güncelle - partial_taken=True
                        for pos in self.portfolio.get("positions", []):
                            if pos.get("id") == position_id:
                                pos["partial_taken"] = True
                                pos["partial_tp_price"] = one_r_price
                                pos["quantity"] = remaining_qty
                                pos["highest_close_since_entry"] = current_price
                                break
                        
                        if self.save_portfolio_fn:
                            self.save_portfolio_fn(self.portfolio)
                        
                        logger.info(
                            f"✅ Partial TP: {symbol} {sell_qty:.6f} satıldı @ ${current_price:.4f} | "
                            f"Kalan: {remaining_qty:.6f}"
                        )
                        
                        if self.telegram_fn and bot_token and chat_id:
                            mesaj = (
                                f"📊 <b>PARTIAL TP (1R)</b>\n\n"
                                f"<b>Coin:</b> {symbol}\n"
                                f"<b>Satılan:</b> {sell_qty:.6f} @ ${current_price:.4f}\n"
                                f"<b>Kalan:</b> {remaining_qty:.6f}\n"
                                f"<b>PnL:</b> ${pnl:.2f}\n\n"
                                f"🔄 Trailing stop aktif edildi"
                            )
                            try:
                                await self.telegram_fn(bot_token, chat_id, mesaj)
                            except Exception as e:
                                logger.error(f"Telegram hatası: {e}")
                    continue
            
            # ─────────────────────────────────────────────────────────────────────
            # 3. Trailing Stop Güncelleme
            # ─────────────────────────────────────────────────────────────────────
            if trailing_enabled and partial_taken and atr > 0:
                # Highest close güncelle
                highest_close_saved = position.get("highest_close_since_entry", entry_price)
                if current_price > highest_close_saved:
                    highest_close_saved = current_price
                
                # Chandelier trailing: HighestClose - TRAIL_ATR_MULT * ATR
                new_trail_sl = highest_close_saved - (trail_atr_mult * atr)
                
                # Trailing sadece yukarı güncellenir
                if new_trail_sl > current_sl:
                    old_sl = current_sl
                    
                    # Standardized watchdog event log
                    logger.info(
                        f"[WATCHDOG] {symbol} | event=trailing_updated | "
                        f"old_sl=${old_sl:.4f} | new_sl=${new_trail_sl:.4f} | "
                        f"highest_close=${highest_close_saved:.4f} | atr=${atr:.4f}"
                    )
                    
                    # Pozisyon güncelle
                    for pos in self.portfolio.get("positions", []):
                        if pos.get("id") == position_id:
                            pos["current_sl"] = new_trail_sl
                            pos["highest_close_since_entry"] = highest_close_saved
                            pos["last_trailing_update_ts"] = int(time.time())
                            break
                    
                    if self.save_portfolio_fn:
                        self.save_portfolio_fn(self.portfolio)
    
    async def _send_close_notification(
        self, bot_token, chat_id, symbol, entry_price, 
        exit_price, pnl, reason
    ):
        """Telegram kapatma bildirimi gönder."""
        profit_pct = ((exit_price / entry_price) - 1) * 100 if entry_price else 0
        emoji = "💰" if pnl > 0 else "🛑"
        
        mesaj = (
            f"{emoji} <b>{reason}</b>\n\n"
            f"<b>Coin:</b> {symbol}\n"
            f"<b>Giriş:</b> ${entry_price:.4f}\n"
            f"<b>Çıkış:</b> ${exit_price:.4f}\n"
            f"<b>PnL:</b> ${pnl:.2f} ({profit_pct:.1f}%)\n\n"
            f"💰 Bakiye: ${self.portfolio['balance']:.2f}"
        )
        
        try:
            await self.telegram_fn(bot_token, chat_id, mesaj)
        except Exception as e:
            logger.error(f"Telegram hatası: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST / DEMO
# ═══════════════════════════════════════════════════════════════════════════════

async def demo():
    """PositionManager demo - mock nesnelerle test."""
    print("\n" + "=" * 60)
    print("🧪 POSITION MANAGER DEMO")
    print("=" * 60 + "\n")
    
    # Mock portfolio
    mock_portfolio = {
        "balance": 1000.0,
        "positions": [
            {
                "id": "BTCUSDT_123",
                "symbol": "BTCUSDT",
                "entry_price": 100.0,
                "quantity": 1.0,
                "stop_loss": 95.0,
                "take_profit": 110.0,
                "trade_cost": 100.0
            }
        ],
        "history": []
    }
    
    # Mock market data engine
    class MockMDE:
        def get_current_price(self, symbol):
            return 102.0  # Mevcut fiyat
    
    # Mock execution manager
    class MockEM:
        def __init__(self, portfolio):
            self.portfolio = portfolio
        def close_position(self, pos_id, price, reason):
            pos = self.portfolio["positions"][0]
            pnl = (price - pos["entry_price"]) * pos["quantity"]
            closed = {**pos, "exit_price": price, "profit_pct": (price/pos["entry_price"]-1)*100}
            self.portfolio["positions"] = []
            self.portfolio["balance"] += price * pos["quantity"]
            self.portfolio["history"].append(closed)
            return True, pnl, closed
    
    def mock_save(p):
        print(f"   💾 Portföy kaydedildi")
    
    # PositionManager oluştur
    pm = PositionManager(
        portfolio=mock_portfolio,
        market_data_engine=MockMDE(),
        strategy_engine=None,
        executor=None,
        execution_manager=MockEM(mock_portfolio),
        save_portfolio_fn=mock_save
    )
    
    print("📋 Başlangıç Durumu:")
    summary = pm.get_portfolio_summary()
    print(f"   Bakiye: ${summary['balance']:.2f}")
    print(f"   Açık pozisyon: {summary['open_positions']}")
    print(f"   Consecutive losses: {pm.get_consecutive_losses()}")
    
    print("\n" + "-" * 60)
    print("📊 TEST 1: Portfolio Summary")
    print("-" * 60)
    for k, v in summary.items():
        print(f"   {k}: {v}")
    
    print("\n" + "-" * 60)
    print("📊 TEST 2: SL/TP Kontrolü (fiyat: $102)")
    print("-" * 60)
    print("   (SL: $95, TP: $110 - tetiklenmemeli)")
    
    closed, pnl = await pm.check_positions_and_apply_risk()
    print(f"\n   Kapatılan: {closed} | PnL: ${pnl:.2f}")
    
    print("\n" + "-" * 60)
    print("📊 TEST 3: Consecutive Loss Tracking")
    print("-" * 60)
    pm.register_trade_result(-10)  # Zarar
    print(f"   -$10 işlem sonrası: {pm.get_consecutive_losses()} ardışık zarar")
    pm.register_trade_result(-5)   # Zarar
    print(f"   -$5 işlem sonrası: {pm.get_consecutive_losses()} ardışık zarar")
    pm.register_trade_result(20)   # Kar
    print(f"   +$20 işlem sonrası: {pm.get_consecutive_losses()} ardışık zarar (sıfırlandı)")
    
    print("\n" + "=" * 60)
    print("✅ Demo tamamlandı!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(demo())
