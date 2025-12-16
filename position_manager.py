
import time
import asyncio
from datetime import datetime
from trade_logger import logger

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
