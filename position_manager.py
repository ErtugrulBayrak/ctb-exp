
import time
import asyncio
from datetime import datetime
from trade_logger import logger

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
            
        # Log section header (mimicking helper from scraper)
        print(f"\n{'─'*50}", flush=True)
        print("💼 PORTFÖY YÖNETİMİ (SL/TP KONTROLÜ)", flush=True)
        print(f"{'─'*50}", flush=True)
        
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
                    
                    # LIVE TRADING: Executing sell via Executor (stored in ExecutionManager or self)
                    if live_trading_enabled and self.executor:
                        try:
                            quantity = position.get('quantity', 0)
                            live_order = await self.executor.create_order(
                                symbol=f"{symbol}USDT", 
                                side="SELL",
                                quantity=quantity, 
                                order_type="MARKET"
                            )
                            logger.info(f"🔴 CANLI {close_reason} SATIŞ: {symbol} OrderId={live_order.get('orderId')}")
                            
                            # Update history with live order info if possible
                            # Note: ExecutionManager.close_position already archived it to history.
                            # We need to update the last item in history.
                            if self.portfolio.get("history"):
                                self.portfolio["history"][-1]["live_sell_order_id"] = live_order.get("orderId")
                                self.portfolio["history"][-1]["live_sell_status"] = "FILLED"
                                if self.save_portfolio_fn:
                                    self.save_portfolio_fn(self.portfolio)
                                    
                        except Exception as e:
                            logger.error(f"❌ CANLI {close_reason} SATIŞ BAŞARISIZ: {symbol} - {e}")
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
            logger.info("SL/TP tetiklenmedi, pozisyonlar devam ediyor")
        
        return closed_count, total_pnl
