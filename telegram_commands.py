"""
telegram_commands.py - Telegram Bot Command Handler
=====================================================

Handles incoming Telegram commands and responds with relevant information.

Supported Commands:
- /portfo - Shows current portfolio summary (balance, positions, PnL)
- /status - Shows bot status (coming soon)
- /help   - Shows available commands

Usage:
    In main.py, after initializing components:
    
    from telegram_commands import TelegramCommandHandler
    
    cmd_handler = TelegramCommandHandler(
        bot_token=TELEGRAM_BOT_TOKEN,
        chat_id=TELEGRAM_CHAT_ID,
        load_portfolio_fn=load_portfolio
    )
    asyncio.create_task(cmd_handler.start_polling())
"""

import json
import asyncio
import aiohttp
from datetime import datetime
from typing import Callable, Optional, Dict, Any

# Logger import
try:
    from trade_logger import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


class TelegramCommandHandler:
    """
    Handles Telegram bot commands via polling.
    
    Polls Telegram API for updates and responds to recognized commands.
    Only processes messages from the authorized chat_id.
    """
    
    # Polling interval in seconds
    POLL_INTERVAL = 5
    
    # API base URL
    API_BASE = "https://api.telegram.org/bot"
    
    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        load_portfolio_fn: Callable,
        market_data_engine=None
    ):
        """
        Initialize the command handler.
        
        Args:
            bot_token: Telegram bot token
            chat_id: Authorized chat ID to respond to
            load_portfolio_fn: Function to load current portfolio
            market_data_engine: Optional, for fetching current prices
        """
        self.bot_token = bot_token
        self.chat_id = str(chat_id)
        self.load_portfolio = load_portfolio_fn
        self.market_data_engine = market_data_engine
        
        self._running = False
        self._last_update_id = 0
        
        # Command registry
        self._commands = {
            "/portfo": self._cmd_portfo,
            "/portfolio": self._cmd_portfo,
            "/status": self._cmd_status,
            "/help": self._cmd_help,
            "/start": self._cmd_help,
        }
        
        logger.info("[TG_CMD] TelegramCommandHandler initialized")
    
    async def start_polling(self):
        """
        Start the polling loop. 
        Run as: asyncio.create_task(handler.start_polling())
        """
        self._running = True
        logger.info(f"[TG_CMD] Starting command polling (interval={self.POLL_INTERVAL}s)")
        
        while self._running:
            try:
                await self._poll_updates()
            except asyncio.CancelledError:
                logger.info("[TG_CMD] Polling cancelled")
                break
            except Exception as e:
                logger.error(f"[TG_CMD] Polling error: {e}")
            
            await asyncio.sleep(self.POLL_INTERVAL)
        
        logger.info("[TG_CMD] Polling stopped")
    
    def stop_polling(self):
        """Stop the polling loop."""
        self._running = False
    
    async def _poll_updates(self):
        """Poll Telegram API for new updates."""
        url = f"{self.API_BASE}{self.bot_token}/getUpdates"
        params = {
            "offset": self._last_update_id + 1,
            "timeout": 5,
            "allowed_updates": ["message"]
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return
                
                data = await resp.json()
                
                if not data.get("ok"):
                    return
                
                for update in data.get("result", []):
                    await self._handle_update(update)
                    self._last_update_id = max(self._last_update_id, update.get("update_id", 0))
    
    async def _handle_update(self, update: Dict[str, Any]):
        """Handle a single update from Telegram."""
        message = update.get("message", {})
        chat = message.get("chat", {})
        text = message.get("text", "")
        
        # Security: Only respond to authorized chat
        if str(chat.get("id")) != self.chat_id:
            logger.warning(f"[TG_CMD] Unauthorized chat: {chat.get('id')}")
            return
        
        # Check if it's a command
        if not text.startswith("/"):
            return
        
        # Extract command (first word)
        command = text.split()[0].lower()
        
        # Remove @botname if present
        if "@" in command:
            command = command.split("@")[0]
        
        # Find and execute handler
        handler = self._commands.get(command)
        if handler:
            logger.info(f"[TG_CMD] Executing command: {command}")
            try:
                await handler()
            except Exception as e:
                logger.error(f"[TG_CMD] Command error: {e}")
                await self._send_message(f"❌ Komut hatası: {str(e)[:100]}")
        else:
            await self._send_message(f"❓ Bilinmeyen komut: {command}\n\n/help yazarak komut listesini görebilirsiniz.")
    
    async def _send_message(self, text: str, parse_mode: str = "HTML"):
        """Send a message to the authorized chat."""
        url = f"{self.API_BASE}{self.bot_token}/sendMessage"
        
        # Truncate if too long
        if len(text) > 4000:
            text = text[:4000] + "\n\n...(kısaltıldı)..."
        
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    error = await resp.text()
                    logger.error(f"[TG_CMD] Send failed: {error}")
    
    # ═══════════════════════════════════════════════════════════════════════════
    # COMMAND HANDLERS
    # ═══════════════════════════════════════════════════════════════════════════
    
    async def _cmd_portfo(self):
        """Handle /portfo command - send raw portfolio.json content."""
        try:
            portfolio = self.load_portfolio()
        except Exception as e:
            await self._send_message(f"❌ Portföy yüklenemedi: {e}")
            return
        
        # Send raw JSON content
        raw_json = json.dumps(portfolio, indent=2, ensure_ascii=False)
        msg = f"<pre>{raw_json}</pre>"
        await self._send_message(msg)
    
    async def _cmd_status(self):
        """Handle /status command - show bot status."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        msg = (
            "🤖 <b>Bot Durumu</b>\n\n"
            f"⏰ <b>Zaman:</b> {now}\n"
            f"✅ <b>Durum:</b> Aktif\n\n"
            "<i>Detaylı status yakında eklenecek...</i>"
        )
        await self._send_message(msg)
    
    async def _cmd_help(self):
        """Handle /help command - show available commands."""
        msg = (
            "📋 <b>Kullanılabilir Komutlar</b>\n\n"
            "/portfo - Portföy özeti (bakiye, pozisyonlar, PnL)\n"
            "/status - Bot durumu\n"
            "/help - Bu mesajı göster\n\n"
            "<i>Komutlar 5-10 saniye içinde yanıt verir.</i>"
        )
        await self._send_message(msg)
    
    # ═══════════════════════════════════════════════════════════════════════════
    # FORMATTING HELPERS
    # ═══════════════════════════════════════════════════════════════════════════
    
    def _format_portfolio(self, portfolio: Dict[str, Any]) -> str:
        """Format portfolio data as a nice Telegram message."""
        balance = portfolio.get("balance", 0)
        positions = portfolio.get("positions", [])
        history = portfolio.get("history", [])
        
        # Calculate stats
        total_trades = len(history)
        winning = len([h for h in history if h.get("profit_loss", 0) > 0])
        losing = len([h for h in history if h.get("profit_loss", 0) < 0])
        total_pnl = sum(h.get("profit_loss", 0) for h in history)
        win_rate = (winning / total_trades * 100) if total_trades > 0 else 0
        
        # Today's PnL
        today = datetime.now().strftime("%Y-%m-%d")
        today_pnl = sum(
            h.get("profit_loss", 0) for h in history 
            if str(h.get("exit_time", "")).startswith(today)
        )
        
        # Build message
        lines = [
            "💼 <b>PORTFÖY ÖZETİ</b>",
            f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            f"💰 <b>Bakiye:</b> ${balance:,.2f}",
            f"📊 <b>Bugünkü PnL:</b> ${today_pnl:+,.2f}",
            f"📈 <b>Toplam PnL:</b> ${total_pnl:+,.2f}",
            "",
        ]
        
        # Open positions
        if positions:
            lines.append(f"📍 <b>Açık Pozisyonlar ({len(positions)}):</b>")
            
            for pos in positions:
                symbol = pos.get("symbol", "???")
                entry = pos.get("entry_price", 0)
                qty = pos.get("quantity", 0)
                entry_type = pos.get("entry_type", "?")
                
                # V2 fields
                stop_loss = pos.get("current_sl") or pos.get("stop_loss", 0)
                take_profit = pos.get("take_profit", 0)
                partial_tp_target = pos.get("partial_tp_target", 0)
                partial_tp_hit = pos.get("partial_tp_hit", False)
                
                # Try to get current price
                current_price = entry
                if self.market_data_engine:
                    try:
                        current_price = self.market_data_engine.get_current_price(symbol) or entry
                    except:
                        pass
                
                # Calculate P&L
                if entry > 0:
                    pnl_pct = ((current_price - entry) / entry) * 100
                    pnl_usd = (current_price - entry) * qty
                    pnl_emoji = "🟢" if pnl_pct >= 0 else "🔴"
                else:
                    pnl_pct = 0
                    pnl_usd = 0
                    pnl_emoji = "⚪"
                
                # Partial TP status
                if partial_tp_hit:
                    partial_str = "✓ Partial alındı"
                elif partial_tp_target:
                    partial_str = f"${partial_tp_target:,.2f}"
                else:
                    partial_str = "N/A"
                
                lines.append(
                    f"  {pnl_emoji} <b>{symbol}</b> ({entry_type})\n"
                    f"      Giriş: ${entry:,.2f} | Şimdi: ${current_price:,.2f}\n"
                    f"      PnL: {pnl_pct:+.2f}% (${pnl_usd:+.2f})\n"
                    f"      SL: ${stop_loss:,.2f} | TP: ${take_profit:,.2f}\n"
                    f"      Partial TP: {partial_str}"
                )
        else:
            lines.append("📍 <b>Açık Pozisyon:</b> Yok")
        
        lines.append("")
        
        # Trade stats
        lines.extend([
            "📊 <b>İstatistikler:</b>",
            f"   Toplam İşlem: {total_trades}",
            f"   Kazanan: {winning} | Kaybeden: {losing}",
            f"   Win Rate: {win_rate:.1f}%",
        ])
        
        # Last 3 trades
        if history:
            lines.append("")
            lines.append("🕐 <b>Son İşlemler:</b>")
            for trade in history[-3:][::-1]:  # Last 3, newest first
                sym = trade.get("symbol", "?")
                pnl = trade.get("profit_loss", 0)
                reason = trade.get("exit_reason", "?")
                emoji = "💚" if pnl > 0 else "💔"
                lines.append(f"   {emoji} {sym}: ${pnl:+.2f} ({reason})")
        
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# STANDALONE TEST
# ═══════════════════════════════════════════════════════════════════════════════

async def _test():
    """Quick test of the command handler."""
    print("Testing TelegramCommandHandler...")
    
    # Mock portfolio loader
    def mock_load():
        return {
            "balance": 1250.00,
            "positions": [
                {"symbol": "BTCUSDT", "entry_price": 90000, "quantity": 0.01, "entry_type": "1H_MOMENTUM"}
            ],
            "history": [
                {"symbol": "ETHUSDT", "profit_loss": 15.50, "exit_reason": "TP", "exit_time": "2024-01-04 12:00:00"},
                {"symbol": "SOLUSDT", "profit_loss": -8.20, "exit_reason": "SL", "exit_time": "2024-01-04 10:00:00"}
            ]
        }
    
    handler = TelegramCommandHandler(
        bot_token="test",
        chat_id="12345",
        load_portfolio_fn=mock_load
    )
    
    # Test formatting
    msg = handler._format_portfolio(mock_load())
    print(msg)
    print("\n✅ Format test passed!")


if __name__ == "__main__":
    asyncio.run(_test())
