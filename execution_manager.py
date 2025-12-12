"""
execution_manager.py - Merkezi İşlem Yürütme Modülü
====================================================

Bu modül tüm BUY/SELL işlem akışlarını merkezileştirir.
StrategyEngine kararlarını aşağıdakilere dönüştürür:
- Portföy açma/kapama işlemleri
- Paper/Live order yürütmeleri
- Trade logging ve bildirimler

ExecutionManager, StrategyEngine kararlarını yorumlar ve uygular.
"""

import time
import json
import os
import asyncio
from typing import Dict, Any, Optional, Tuple
from datetime import datetime

# Config import
try:
    from config import SETTINGS
except ImportError:
    class MockSettings:
        LIVE_TRADING = False
    SETTINGS = MockSettings()

# Trade logger import
try:
    from trade_logger import logger as trade_log
except ImportError:
    import logging
    trade_log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# EXECUTION MANAGER
# ═══════════════════════════════════════════════════════════════════════════════

class ExecutionManager:
    """
    Merkezi işlem yürütme sınıfı.
    
    StrategyEngine kararlarını portföy işlemlerine ve 
    gerçek/simüle emirlere dönüştürür.
    
    Responsibilities:
    - BUY flow: validate, open position, live order, notify, log
    - SELL flow: find position, profit protection, close, live order, notify, log
    """
    
    # Profit protection defaults (can be overridden)
    PROTECT_PROFITABLE_POSITIONS = True
    MIN_PROFIT_TO_PROTECT = 0.5
    AI_SELL_OVERRIDE_CONFIDENCE = 90
    
    def __init__(
        self,
        portfolio: Dict[str, Any],
        strategy_engine=None,
        market_data_engine=None,
        executor=None,
        telegram_config: Optional[Dict[str, str]] = None,
        save_portfolio_fn=None,
        log_fn=None,
        telegram_fn=None
        
    ):
        """
        ExecutionManager başlat.
        
        Args:
            portfolio: Portföy referansı
            strategy_engine: StrategyEngine instance
            market_data_engine: MarketDataEngine instance
            executor: OrderExecutor instance
            telegram_config: {"bot_token": str, "chat_id": str, "notify_trades": bool}
            save_portfolio_fn: Portföy kaydetme fonksiyonu
            log_fn: Log fonksiyonu
            telegram_fn: Telegram bildirim fonksiyonu (async)
        """
        self.portfolio = portfolio
        self.strategy_engine = strategy_engine
        self.market_data_engine = market_data_engine
        self.executor = executor
        
        # Telegram config
        self.telegram_config = telegram_config or {}
        self.bot_token = self.telegram_config.get("bot_token", "")
        self.chat_id = self.telegram_config.get("chat_id", "")
        self.notify_trades = self.telegram_config.get("notify_trades", True)
        
        # Injected functions
        self._save_portfolio = save_portfolio_fn
        self._log = log_fn or self._default_log
        self._telegram_fn = telegram_fn
        
        # Trade Log config
        self.trade_log_file = "trade_log.json"

        # Stats
        self._stats = {
            "buys_executed": 0,
            "sells_executed": 0,
            "live_orders_placed": 0,
            "live_orders_failed": 0
        }
    
    def _default_log(self, msg: str, level: str = "INFO", indent: int = 0):
        """Fallback log function."""
        prefix = "  " * indent
        print(f"[{level}] {prefix}{msg}")
    
    def update_portfolio(self, portfolio: Dict[str, Any]):
        """Update portfolio reference (for loop refresh)."""
        self.portfolio = portfolio
    
    # ═══════════════════════════════════════════════════════════════════════════
    # POSITION MANAGEMENT
    # ═══════════════════════════════════════════════════════════════════════════
    
    def get_open_positions(self) -> list:
        """Açık pozisyonları döndürür."""
        return self.portfolio.get("positions", [])
    
    def open_position(
        self,
        symbol: str,
        entry_price: float,
        quantity: float,
        stop_loss: float,
        take_profit: float,
        haber_baslik: str = "",
        ai_confidence: int = 0,
        ai_reasoning: str = ""
    ) -> Tuple[bool, Any]:
        """
        Yeni pozisyon açar ve portföye ekler.
        
        Returns: (success, position_or_message)
        """
        trade_cost = entry_price * quantity
        
        if trade_cost > self.portfolio["balance"]:
            return False, f"Yetersiz bakiye: ${self.portfolio['balance']:.2f} < ${trade_cost:.2f}"
        
        position = {
            "id": f"{symbol}_{int(time.time())}",
            "symbol": symbol,
            "entry_price": entry_price,
            "quantity": quantity,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "trade_cost": trade_cost,
            "entry_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "haber_baslik": haber_baslik[:150] if haber_baslik else "",
            "ai_confidence": ai_confidence,
            "ai_reasoning": ai_reasoning[:200] if ai_reasoning else ""
        }
        
        self.portfolio["balance"] -= trade_cost
        self.portfolio["positions"].append(position)
        
        if self._save_portfolio:
            self._save_portfolio(self.portfolio)
        
        return True, position
    
    def close_position(
        self,
        position_id: str,
        exit_price: float,
        reason: str = "Manuel"
    ) -> Tuple[bool, float, Optional[Dict]]:
        """
        Pozisyonu kapatır, bakiyeyi günceller ve geçmişe ekler.
        
        Args:
            position_id: Pozisyon ID
            exit_price: Çıkış fiyatı
            reason: "SL", "TP", "AI-SELL", "Manuel"
        
        Returns: (success, profit_loss, closed_position)
        """
        positions = self.portfolio.get("positions", [])
        position_to_close = None
        position_index = -1
        
        for i, pos in enumerate(positions):
            if pos.get("id") == position_id:
                position_to_close = pos
                position_index = i
                break
        
        if position_to_close is None:
            return False, 0, None
        
        # Kar/zarar hesapla
        entry_price = position_to_close["entry_price"]
        quantity = position_to_close["quantity"]
        exit_value = exit_price * quantity
        entry_value = position_to_close["trade_cost"]
        profit_loss = exit_value - entry_value
        profit_pct = ((exit_price - entry_price) / entry_price) * 100
        
        # Geçmiş kaydı oluştur
        closed_trade = {
            **position_to_close,
            "exit_price": exit_price,
            "exit_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "profit_loss": profit_loss,
            "profit_pct": profit_pct,
            "exit_reason": reason
        }
        
        # Bakiyeyi güncelle
        self.portfolio["balance"] += exit_value
        
        # Pozisyonu kaldır ve geçmişe ekle
        del self.portfolio["positions"][position_index]
        self.portfolio["history"].append(closed_trade)
        
        if self._save_portfolio:
            self._save_portfolio(self.portfolio)
        
        return True, profit_loss, closed_trade
    
    # ═══════════════════════════════════════════════════════════════════════════
    # TRADE LOGGING
    # ═══════════════════════════════════════════════════════════════════════════
    
    def load_trade_log(self):
        try:
            with open(self.trade_log_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            data = {}

        # --- SELF-HEALING SCHEMA ---
        if not isinstance(data, dict):
            # If list (legacy), preserve it in decisions if needed, or just reset structure
            # But safer to just wrap it if it looks like a list of decisions
            if isinstance(data, list):
                data = {"decisions": data}
            else:
                data = {}

        # Ensure required structure
        if "stats" not in data or not isinstance(data["stats"], dict):
            data["stats"] = {
                "total_buys": 0,
                "total_sells": 0,
                "wins": 0,
                "losses": 0
            }

        if "decisions" not in data or not isinstance(data["decisions"], list):
            data["decisions"] = []

        if "trades" not in data or not isinstance(data["trades"], list):
            data["trades"] = []

        return data

    def _log_trade_decision(
        self,
        action: str,
        symbol: str,
        price: float,
        ai_decision: Dict,
        market_snapshot: Dict,
        position_id: str = None,
        trade_details: Dict = None
    ):
        """Trade kararını dosyaya loglar."""
        trade_log = self.load_trade_log()
        
        decision_record = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "action": action,
            "symbol": symbol,
            "price": price,
            "position_id": position_id,
            "ai_decision": ai_decision or {},
            "market_snapshot": market_snapshot or {},
            "trade_details": trade_details or {}
        }
        
        trade_log["decisions"].append(decision_record)
        
        if action == "BUY":
            trade_log["stats"]["total_buys"] = trade_log["stats"].get("total_buys", 0) + 1
        elif action == "SELL":
            trade_log["stats"]["total_sells"] = trade_log["stats"].get("total_sells", 0) + 1
        
        trade_log["stats"]["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
                
        try:
            with open(self.trade_log_file, 'w', encoding='utf-8') as f:
                json.dump(trade_log, f, indent=2, ensure_ascii=False)
            self._log(f"📝 Trade log kaydedildi: {action} {symbol}", "DATA", 1)
        except Exception as e:
            self._log(f"Trade log kaydetme hatası: {e}", "ERR")
    
    # ═══════════════════════════════════════════════════════════════════════════
    # EXECUTE BUY FLOW
    # ═══════════════════════════════════════════════════════════════════════════
    
    async def execute_buy_flow(
        self,
        symbol: str,
        current_price: float,
        decision_result: Dict[str, Any],
        trade_reason: str = "AI-TECH",
        trigger_info: str = "",
        market_snapshot: Dict = None
    ) -> Tuple[bool, Any]:
        """
        BUY kararını uygular - yeni pozisyon açar.
        
        Args:
            symbol: Coin sembolü
            current_price: Güncel fiyat
            decision_result: StrategyEngine.evaluate_buy_opportunity() sonucu
            trade_reason: "AI-NEWS" veya "AI-TECH"
            trigger_info: Tetikleyen bilgi
            market_snapshot: Piyasa durumu dict
        
        Returns: (success, position_or_message)
        """
        # Aynı coin'de açık pozisyon kontrolü
        for pos in self.portfolio.get("positions", []):
            if pos.get("symbol") == symbol:
                return False, f"{symbol} için zaten açık pozisyon var"
        
        # StrategyEngine'den gelen değerleri kullan
        stop_loss = decision_result.get("stop_loss")
        take_profit = decision_result.get("take_profit")
        quantity = decision_result.get("quantity", 0)
        ai_confidence = decision_result.get("confidence", 0)
        ai_reasoning = decision_result.get("reason", "") or decision_result.get("reasoning", "")
        
        if not stop_loss or not take_profit or quantity <= 0:
            return False, "StrategyEngine değerleri geçersiz"
        
        trade_cost = current_price * quantity
        if trade_cost < 10:
            return False, f"İşlem değeri çok düşük: ${trade_cost:.2f}"
        
        if trade_cost > self.portfolio["balance"]:
            quantity = (self.portfolio["balance"] * 0.95) / current_price
            trade_cost = current_price * quantity
        
        # Pozisyon aç
        success, result = self.open_position(
            symbol=symbol,
            entry_price=current_price,
            quantity=quantity,
            stop_loss=stop_loss,
            take_profit=take_profit,
            haber_baslik=f"[{trade_reason}] {trigger_info[:120]}",
            ai_confidence=ai_confidence,
            ai_reasoning=ai_reasoning
        )
        
        if success:
            position = result
            reason_emoji = "🤖📰" if "NEWS" in trade_reason else "🤖📊"
            reason_text = "AI HABER TETİKLİ" if "NEWS" in trade_reason else "AI TEKNİK TETİKLİ"
            
            self._log(f"🆕 SANAL ALIM ({reason_text}): {symbol} @ ${current_price:.4f}", "OK")
            self._log(f"   Miktar: {quantity:.6f} | Değer: ${trade_cost:.2f}", "DATA", 1)
            self._log(f"   SL: ${stop_loss:.4f} | TP: ${take_profit:.4f}", "DATA", 1)
            
            # Trade log kaydı
            self._log_trade_decision(
                action="BUY",
                symbol=symbol,
                price=current_price,
                ai_decision={"decision": "BUY", "confidence": ai_confidence, "reasoning": ai_reasoning},
                market_snapshot=market_snapshot or {},
                position_id=position.get("id"),
                trade_details={"stop_loss": stop_loss, "take_profit": take_profit, "quantity": quantity, "trade_cost": trade_cost}
            )
            
            # Telegram bildirimi
            if self.notify_trades and self._telegram_fn:
                mesaj = (
                    f"🆕 <b>SANAL ALIM - {reason_text}</b> {reason_emoji}\n\n"
                    f"<b>Coin:</b> {symbol}/USDT\n"
                    f"<b>Fiyat:</b> ${current_price:.4f}\n"
                    f"<b>Miktar:</b> {quantity:.6f} (${trade_cost:.2f})\n"
                    f"<b>SL:</b> ${stop_loss:.4f} | <b>TP:</b> ${take_profit:.4f}\n\n"
                    f"<b>🧠 AI:</b> {ai_reasoning[:100]}\n"
                    f"<b>💰 Bakiye:</b> ${self.portfolio['balance']:.2f}"
                )
                await self._telegram_fn(self.bot_token, self.chat_id, mesaj)
            
            # LIVE TRADING
            if SETTINGS.LIVE_TRADING and self.executor:
                try:
                    live_order = await self.executor.create_order(
                        symbol=f"{symbol}USDT",
                        side="BUY",
                        quantity=quantity,
                        order_type="MARKET"
                    )
                    position["live_order_id"] = live_order.get("orderId")
                    if self._save_portfolio:
                        self._save_portfolio(self.portfolio)
                    self._log(f"🔴 CANLI EMİR: {symbol} OrderId={live_order.get('orderId')}", "OK")
                    self._stats["live_orders_placed"] += 1
                except Exception as e:
                    self._log(f"❌ CANLI EMİR BAŞARISIZ: {e}", "ERR")
                    self._stats["live_orders_failed"] += 1
            
            self._stats["buys_executed"] += 1
            return True, position
        
        return False, result
    
    # ═══════════════════════════════════════════════════════════════════════════
    # EXECUTE SELL FLOW
    # ═══════════════════════════════════════════════════════════════════════════
    
    async def execute_sell_flow(
        self,
        symbol: str,
        current_price: float,
        ai_reasoning: str,
        ai_confidence: int = 0,
        market_snapshot: Dict = None
    ) -> Tuple[bool, float, Any]:
        """
        AI SELL kararını uygular - açık pozisyonu kapatır.
        
        Args:
            symbol: Coin sembolü
            current_price: Güncel fiyat
            ai_reasoning: AI'ın satış gerekçesi
            ai_confidence: AI güven skoru (0-100)
            market_snapshot: Piyasa durumu dict
        
        Returns: (success, profit_loss, message_or_closed)
        """
        positions = self.get_open_positions()
        
        # Bu coin için açık pozisyon bul
        target_position = None
        for pos in positions:
            if pos.get('symbol') == symbol:
                target_position = pos
                break
        
        if not target_position:
            return False, 0, f"{symbol} için açık pozisyon bulunamadı"
        
        position_id = target_position.get('id')
        entry_price = target_position.get('entry_price')
        
        # ═══════════════════════════════════════════════════════════════════════
        # KÂR KORUMA MEKANİZMASI
        # ═══════════════════════════════════════════════════════════════════════
        if self.PROTECT_PROFITABLE_POSITIONS and entry_price and current_price:
            current_profit_pct = ((current_price - entry_price) / entry_price) * 100
            
            if current_profit_pct >= self.MIN_PROFIT_TO_PROTECT:
                if ai_confidence < self.AI_SELL_OVERRIDE_CONFIDENCE:
                    self._log(
                        f"🛡️ {symbol}: Kâr koruma aktif! +{current_profit_pct:.2f}% kârda, "
                        f"TP bekliyor (AI güven: {ai_confidence}% < {self.AI_SELL_OVERRIDE_CONFIDENCE}%)",
                        "WARN"
                    )
                    return False, 0, f"{symbol}: Kârdaki pozisyon korunuyor (TP'ye ulaşmasını bekle)"
                else:
                    self._log(f"⚠️ {symbol}: Yüksek güvenli AI SELL ({ai_confidence}%) kâr korumasını geçiyor", "WARN")
        
        # Pozisyonu kapat
        success, pnl, closed = self.close_position(position_id, current_price, "AI-SELL")
        
        if success:
            profit_pct = closed.get('profit_pct', 0)
            pnl_emoji = "💰" if pnl > 0 else "🔻"
            
            self._log(f"{pnl_emoji} AI SELL: {symbol} kapatıldı | PnL: ${pnl:.2f} ({profit_pct:.1f}%)", "OK")
            
            # Trade log kaydı
            ai_decision_data = {
                "decision": "SELL",
                "confidence": ai_confidence,
                "reasoning": ai_reasoning
            }
            
            trade_details = {
                "entry_price": entry_price,
                "exit_price": current_price,
                "profit_loss": pnl,
                "profit_pct": profit_pct,
                "quantity": target_position.get('quantity'),
                "trade_cost": target_position.get('trade_cost'),
                "hold_time": closed.get('exit_time', '') + " - " + target_position.get('entry_time', ''),
                "original_stop_loss": target_position.get('stop_loss'),
                "original_take_profit": target_position.get('take_profit'),
                "balance_after": self.portfolio["balance"]
            }
            
            self._log_trade_decision(
                action="SELL",
                symbol=symbol,
                price=current_price,
                ai_decision=ai_decision_data,
                market_snapshot=market_snapshot or {},
                position_id=position_id,
                trade_details=trade_details
            )
            
            # Telegram bildirimi
            if self.notify_trades and self._telegram_fn:
                mesaj = (
                    f"🤖 <b>AI SATIŞ KARARI</b> {pnl_emoji}\n\n"
                    f"<b>Coin:</b> {symbol}/USDT\n"
                    f"<b>Giriş:</b> ${entry_price:.4f}\n"
                    f"<b>Çıkış:</b> ${current_price:.4f}\n"
                    f"<b>{'Kâr' if pnl > 0 else 'Zarar'}:</b> ${abs(pnl):.2f} ({profit_pct:+.1f}%)\n\n"
                    f"<b>🧠 AI Gerekçe:</b>\n<i>{ai_reasoning}</i>\n\n"
                    f"<b>💰 Güncel Bakiye:</b> ${self.portfolio['balance']:.2f}"
                )
                await self._telegram_fn(self.bot_token, self.chat_id, mesaj)
            
            # LIVE TRADING: Gerçek SELL emri
            if SETTINGS.LIVE_TRADING and self.executor:
                quantity = target_position.get('quantity', 0)
                try:
                    live_order = await self.executor.create_order(
                        symbol=f"{symbol}USDT",
                        side="SELL",
                        quantity=quantity,
                        order_type="MARKET"
                    )
                    
                    closed["live_sell_order_id"] = live_order.get("orderId")
                    closed["live_sell_status"] = "FILLED"
                    if self._save_portfolio:
                        self._save_portfolio(self.portfolio)
                    
                    self._log(f"🔴 CANLI SATIŞ BAŞARILI: {symbol} OrderId={live_order.get('orderId')}", "OK")
                    self._stats["live_orders_placed"] += 1
                    
                except Exception as e:
                    self._log(f"❌ CANLI SATIŞ BAŞARISIZ: {symbol} - {e}", "ERR")
                    self._log(f"⚠️ RECOVERY GEREKLİ: Pozisyon paper'da kapatıldı ama canlı satış yapılamadı!", "ERR")
                    
                    if self.portfolio.get("history"):
                        self.portfolio["history"][-1]["live_sell_failed"] = True
                        self.portfolio["history"][-1]["live_sell_error"] = str(e)
                        self.portfolio["history"][-1]["recovery_needed"] = True
                        if self._save_portfolio:
                            self._save_portfolio(self.portfolio)
                    
                    self._stats["live_orders_failed"] += 1
            
            self._stats["sells_executed"] += 1
            return True, pnl, closed
        else:
            return False, 0, "Pozisyon kapatılamadı"
    
    # ═══════════════════════════════════════════════════════════════════════════
    # STATS
    # ═══════════════════════════════════════════════════════════════════════════
    
    def get_stats(self) -> Dict[str, int]:
        """Execution istatistiklerini döndürür."""
        return self._stats.copy()

    def get_today_pnl(self) -> float:
        """Bugünkü gerçekleşmiş PnL toplamı."""
        history = self.portfolio.get("history", [])
        today = datetime.now().strftime("%Y-%m-%d")
        total = 0.0
        for trade in history:
            exit_time = trade.get("exit_time") or ""
            if isinstance(exit_time, str) and exit_time.startswith(today):
                try:
                    total += float(trade.get("profit_loss", 0) or 0)
                except (TypeError, ValueError):
                    continue
        return total
    
    def __repr__(self) -> str:
        return (
            f"ExecutionManager(buys={self._stats['buys_executed']}, "
            f"sells={self._stats['sells_executed']}, "
            f"live_orders={self._stats['live_orders_placed']})"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# FACTORY FUNCTION
# ═══════════════════════════════════════════════════════════════════════════════

def create_execution_manager(
    portfolio: Dict[str, Any],
    strategy_engine=None,
    market_data_engine=None,
    executor=None,
    **kwargs
) -> ExecutionManager:
    """Factory fonksiyonu."""
    return ExecutionManager(
        portfolio=portfolio,
        strategy_engine=strategy_engine,
        market_data_engine=market_data_engine,
        executor=executor,
        **kwargs
    )
