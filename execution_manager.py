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
import hashlib
from typing import Dict, Any, Optional, Tuple
from datetime import datetime

# Production-grade imports
try:
    from order_ledger import get_ledger
    from metrics import increment as metrics_increment
except ImportError:
    get_ledger = None
    metrics_increment = lambda *a, **k: None

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
    
    # Profit protection - artık config'den okunuyor
    # Bu değerler SETTINGS'den alınır, fallback olarak burada
    @property
    def PROTECT_PROFITABLE_POSITIONS(self):
        return getattr(SETTINGS, 'PROTECT_PROFITABLE_POSITIONS', True)
    
    @property
    def MIN_PROFIT_TO_PROTECT(self):
        return getattr(SETTINGS, 'MIN_PROFIT_TO_PROTECT', 0.5)
    
    @property
    def AI_SELL_OVERRIDE_CONFIDENCE(self):
        return getattr(SETTINGS, 'AI_SELL_OVERRIDE_CONFIDENCE', 90)
    
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
        
        # ──────────────── IN-MEMORY INTENT TRACKING ────────────────
        # Fallback idempotency when order_ledger is not available
        self._recent_intents: Dict[str, float] = {}  # intent_id -> timestamp
        self._intent_ttl = 900  # 15 minutes
    
    def _default_log(self, msg: str, level: str = "INFO", indent: int = 0):
        """Fallback log function."""
        prefix = "  " * indent
        print(f"[{level}] {prefix}{msg}")
    
    def update_portfolio(self, portfolio: Dict[str, Any]):
        """Update portfolio reference (for loop refresh)."""
        self.portfolio = portfolio
    
    def _generate_intent_id(self, symbol: str, signal_ts: str = None) -> str:
        """
        Generate a unique intent ID for deduplication.
        
        Args:
            symbol: Trading symbol
            signal_ts: Signal timestamp (optional, uses current time if not provided)
        
        Returns:
            12-character hash string
        """
        ts = signal_ts or time.strftime('%Y-%m-%d %H:%M')
        raw = f"{symbol}:{ts}"
        return hashlib.sha256(raw.encode()).hexdigest()[:12]
    
    def _is_duplicate_intent(self, intent_id: str) -> bool:
        """
        Check if this intent was recently processed.
        
        Returns:
            True if duplicate (should be blocked), False if new
        """
        now = time.time()
        
        # Clean expired intents
        self._recent_intents = {
            k: v for k, v in self._recent_intents.items() 
            if now - v < self._intent_ttl
        }
        
        if intent_id in self._recent_intents:
            return True
        
        # Record this intent
        self._recent_intents[intent_id] = now
        return False
    
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
        ai_reasoning: str = "",
        entry_type: str = "UNKNOWN",
        partial_tp_target: float = None
    ) -> Tuple[bool, Any]:
        """
        Yeni pozisyon açar ve portföye ekler.
        
        Args:
            entry_type: Entry signal type (4H_SWING, 1H_MOMENTUM, 15M_SCALP, V1, UNKNOWN)
            partial_tp_target: Price level for partial take profit (None = no partial)
        
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
            "timestamp": time.time(),  # Unix timestamp for hold time calculations
            "haber_baslik": haber_baslik[:150] if haber_baslik else "",
            "ai_confidence": ai_confidence,
            "ai_reasoning": ai_reasoning[:200] if ai_reasoning else "",
            # ───────── V2 Fields for Partial TP & Trailing Stop ─────────
            "entry_type": entry_type,              # 4H_SWING, 1H_MOMENTUM, 15M_SCALP, V1
            "partial_tp_hit": False,               # Flag: has partial TP triggered?
            "partial_tp_target": partial_tp_target, # Price level for partial TP
            "initial_sl": stop_loss,               # Original stop loss (never changes)
            "current_sl": stop_loss,               # Current SL (updated by trailing)
            "highest_close_since_entry": entry_price  # For trailing stop calculation
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
        reason: str = "Manuel",
        partial_qty: float = None
    ) -> Tuple[bool, float, Optional[Dict]]:
        """
        Pozisyonu kapatır, bakiyeyi günceller ve geçmişe ekler.
        
        Args:
            position_id: Pozisyon ID
            exit_price: Çıkış fiyatı
            reason: "SL", "TP", "AI-SELL", "PARTIAL_TP", "TRAIL_SL", "Manuel"
            partial_qty: Kısmi satış miktarı (None = tamamını kapat)
        
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
        
        entry_price = position_to_close["entry_price"]
        full_quantity = position_to_close["quantity"]
        
        # Kısmi satış kontrolü
        if partial_qty and partial_qty < full_quantity:
            # Kısmi satış - pozisyonu güncelle, geçmişe ekle
            sell_quantity = partial_qty
            remaining_quantity = full_quantity - partial_qty
            
            exit_value = exit_price * sell_quantity
            entry_value = entry_price * sell_quantity
            profit_loss = exit_value - entry_value
            profit_pct = ((exit_price - entry_price) / entry_price) * 100
            
            # Partial trade kaydı
            partial_trade = {
                **position_to_close,
                "quantity": sell_quantity,
                "trade_cost": entry_price * sell_quantity,
                "exit_price": exit_price,
                "exit_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "profit_loss": profit_loss,
                "profit_pct": profit_pct,
                "exit_reason": reason,
                "is_partial": True,
                "remaining_qty": remaining_quantity
            }
            
            # Bakiyeyi güncelle
            self.portfolio["balance"] += exit_value
            
            # Pozisyonu güncelle (kalan miktar)
            # NOT: Pozisyon listede kalır, sadece miktarı azalır
            self.portfolio["positions"][position_index]["quantity"] = remaining_quantity
            self.portfolio["positions"][position_index]["trade_cost"] = entry_price * remaining_quantity
            
            # Geçmişe partial trade ekle
            self.portfolio["history"].append(partial_trade)
            
            if self._save_portfolio:
                self._save_portfolio(self.portfolio)
            
            return True, profit_loss, partial_trade
        
        else:
            # Tam kapanış - mevcut davranış
            quantity = full_quantity
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
            # Update wins/losses based on PnL
            if trade_details and trade_details.get("profit_loss", 0) > 0:
                trade_log["stats"]["wins"] = trade_log["stats"].get("wins", 0) + 1
            elif trade_details and trade_details.get("profit_loss", 0) < 0:
                trade_log["stats"]["losses"] = trade_log["stats"].get("losses", 0) + 1
        
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
        # ═══════════════════════════════════════════════════════════════════════
        # POSITION LIMIT CHECK (safety net)
        # ═══════════════════════════════════════════════════════════════════════
        current_positions = len(self.portfolio.get("positions", []))
        max_positions = getattr(SETTINGS, 'MAX_OPEN_POSITIONS', 3)
        
        if current_positions >= max_positions:
            trade_log.warning(
                f"[POSITION_LIMIT] {symbol}: Position limit reached: "
                f"{current_positions}/{max_positions} - BUY blocked"
            )
            return False, f"Position limit reached: {current_positions}/{max_positions}"
        
        # Aynı coin'de açık pozisyon kontrolü
        for pos in self.portfolio.get("positions", []):
            if pos.get("symbol") == symbol:
                return False, f"{symbol} için zaten açık pozisyon var"
        
        # ═══════════════════════════════════════════════════════════════════════
        # ORDER LEDGER IDEMPOTENCY CHECK
        # ═══════════════════════════════════════════════════════════════════════
        signal_id = decision_result.get("signal_id", "")
        if signal_id and get_ledger:
            ledger = get_ledger()
            blocked, reason = ledger.is_blocked(signal_id)
            if blocked:
                metrics_increment("order_ledger_block_count")
                self._log(f"[ORDER_LEDGER] {symbol}: Entry blocked | signal_id={signal_id} | blocked_by_order_ledger=True | reason={reason}", "WARN")
                return False, f"Order ledger block: {reason}"
        
        # ═══════════════════════════════════════════════════════════════════════
        # IN-MEMORY INTENT DEDUPLICATION (fallback when order_ledger unavailable)
        # ═══════════════════════════════════════════════════════════════════════
        if not signal_id:
            # Generate intent_id from symbol + current minute
            intent_id = self._generate_intent_id(symbol)
            if self._is_duplicate_intent(intent_id):
                self._log(
                    f"[INTENT_BLOCK] {symbol}: Duplicate intent blocked | intent_id={intent_id}",
                    "WARN"
                )
                return False, f"Duplicate intent blocked: {intent_id}"
        
        # StrategyEngine'den gelen değerleri kullan
        stop_loss = decision_result.get("stop_loss")
        take_profit = decision_result.get("take_profit") or decision_result.get("take_profit_2")
        quantity = decision_result.get("quantity", 0)
        ai_confidence = decision_result.get("confidence", 0)
        ai_reasoning = decision_result.get("reason", "") or decision_result.get("reasoning", "")
        
        # V2 fields for partial TP
        entry_type = decision_result.get("entry_type", "UNKNOWN")
        # Try direct partial_tp_target first, then fall back to take_profit_1
        partial_tp_target = decision_result.get("partial_tp_target") or decision_result.get("take_profit_1")
        
        # CRITICAL DEBUG: Log what we received from decision_result
        trade_log.info(
            f"[PARTIAL TP DEBUG] {symbol}: "
            f"decision_result.partial_tp_target={decision_result.get('partial_tp_target')} | "
            f"decision_result.take_profit_1={decision_result.get('take_profit_1')} | "
            f"after_or={partial_tp_target} | entry_type={entry_type}"
        )
        
        # FALLBACK: If partial_tp_target is still None, calculate from entry_type and config
        if partial_tp_target is None and entry_type not in ["UNKNOWN", "V1", "15M_SCALP"]:
            try:
                import config as cfg
                if entry_type == "1H_MOMENTUM":
                    partial_tp_pct = getattr(cfg, 'MOMENTUM_1H_PARTIAL_TP_PCT', 2.0) / 100.0
                    partial_tp_target = current_price * (1 + partial_tp_pct)
                    trade_log.info(f"[PARTIAL_TP_FALLBACK] {symbol}: Calculated partial_tp_target=${partial_tp_target:.2f} (+{partial_tp_pct*100:.1f}%)")
                elif entry_type == "4H_SWING":
                    partial_tp_pct = getattr(cfg, 'SWING_4H_PARTIAL_TP_PCT', 5.0) / 100.0
                    partial_tp_target = current_price * (1 + partial_tp_pct)
                    trade_log.info(f"[PARTIAL_TP_FALLBACK] {symbol}: Calculated partial_tp_target=${partial_tp_target:.2f} (+{partial_tp_pct*100:.1f}%)")
            except Exception as e:
                trade_log.warning(f"[PARTIAL_TP_FALLBACK] {symbol}: Failed to calculate fallback: {e}")
        
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
            ai_reasoning=ai_reasoning,
            entry_type=entry_type,
            partial_tp_target=partial_tp_target
        )
        
        # Record in order ledger after successful open
        if success and signal_id and get_ledger:
            ledger = get_ledger()
            ledger.record(
                signal_id=signal_id,
                symbol=symbol,
                side="BUY",
                status="filled",
                filled_qty=quantity,
                avg_price=current_price
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
            
            # LIVE TRADING with retry logic
            if SETTINGS.LIVE_TRADING and self.executor:
                max_retries = getattr(SETTINGS, 'LIVE_ORDER_MAX_RETRIES', 3)
                retry_delay = getattr(SETTINGS, 'LIVE_ORDER_RETRY_DELAY', 2.0)
                
                for attempt in range(max_retries):
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
                        break  # Başarılı, döngüden çık
                    except Exception as e:
                        if attempt < max_retries - 1:
                            self._log(f"⚠️ CANLI EMİR DENEME {attempt + 1}/{max_retries} BAŞARISIZ: {e}", "WARN")
                            await asyncio.sleep(retry_delay)
                        else:
                            self._log(f"❌ CANLI EMİR TÜM DENEMELER BAŞARISIZ: {e}", "ERR")
                            self._stats["live_orders_failed"] += 1
                            # Emit ORDER_REJECTED alert
                            try:
                                from alert_manager import get_alert_manager, AlertLevel, AlertCode
                                get_alert_manager().emit(
                                    AlertCode.ORDER_REJECTED, AlertLevel.CRITICAL,
                                    "Live order failed after retries", symbol=symbol, error=str(e)[:50]
                                )
                            except: pass
            
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
            
            # LIVE TRADING: Gerçek SELL emri with retry
            if SETTINGS.LIVE_TRADING and self.executor:
                quantity = target_position.get('quantity', 0)
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
                        
                        closed["live_sell_order_id"] = live_order.get("orderId")
                        closed["live_sell_status"] = "FILLED"
                        if self._save_portfolio:
                            self._save_portfolio(self.portfolio)
                        
                        self._log(f"🔴 CANLI SATIŞ BAŞARILI: {symbol} OrderId={live_order.get('orderId')}", "OK")
                        self._stats["live_orders_placed"] += 1
                        break  # Başarılı, döngüden çık
                        
                    except Exception as e:
                        if attempt < max_retries - 1:
                            self._log(f"⚠️ CANLI SATIŞ DENEME {attempt + 1}/{max_retries} BAŞARISIZ: {e}", "WARN")
                            await asyncio.sleep(retry_delay)
                        else:
                            self._log(f"❌ CANLI SATIŞ TÜM DENEMELER BAŞARISIZ: {symbol} - {e}", "ERR")
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


# ═══════════════════════════════════════════════════════════════════════════════
# TEST / DEMO
# ═══════════════════════════════════════════════════════════════════════════════

async def demo():
    """ExecutionManager demo - gerçek işlem yapmaz."""
    print("\n" + "=" * 60)
    print("🧪 EXECUTION MANAGER DEMO")
    print("=" * 60 + "\n")
    
    # Mock portföy oluştur
    mock_portfolio = {
        "balance": 1000.0,
        "positions": [],
        "history": []
    }
    
    def mock_save(p):
        print(f"   💾 Portföy kaydedildi (balance: ${p['balance']:.2f})")
    
    # ExecutionManager oluştur
    em = ExecutionManager(
        portfolio=mock_portfolio,
        save_portfolio_fn=mock_save
    )
    
    print("📋 Config Değerleri (config.py'den):")
    print(f"   PROTECT_PROFITABLE_POSITIONS: {em.PROTECT_PROFITABLE_POSITIONS}")
    print(f"   MIN_PROFIT_TO_PROTECT: {em.MIN_PROFIT_TO_PROTECT}%")
    print(f"   AI_SELL_OVERRIDE_CONFIDENCE: {em.AI_SELL_OVERRIDE_CONFIDENCE}%")
    print(f"   LIVE_ORDER_MAX_RETRIES: {getattr(SETTINGS, 'LIVE_ORDER_MAX_RETRIES', 3)}")
    print(f"   LIVE_ORDER_RETRY_DELAY: {getattr(SETTINGS, 'LIVE_ORDER_RETRY_DELAY', 2.0)}s")
    
    print("\n" + "-" * 60)
    print("📊 TEST 1: Pozisyon Açma")
    print("-" * 60)
    
    # Mock karar
    mock_decision = {
        "stop_loss": 95.0,
        "take_profit": 110.0,
        "quantity": 2.0,
        "confidence": 85,
        "reason": "Teknik göstergeler güçlü alım sinyali veriyor"
    }
    
    success, result = await em.execute_buy_flow(
        symbol="BTCUSDT",
        current_price=100.0,
        decision_result=mock_decision,
        trade_reason="AI-TECH",
        trigger_info="Demo Test"
    )
    
    if success:
        print(f"\n✅ Pozisyon açıldı!")
        print(f"   ID: {result['id']}")
        print(f"   Fiyat: ${result['entry_price']:.2f}")
        print(f"   Miktar: {result['quantity']}")
        print(f"   Kalan bakiye: ${mock_portfolio['balance']:.2f}")
    else:
        print(f"❌ Pozisyon açılamadı: {result}")
    
    print("\n" + "-" * 60)
    print("🛡️ TEST 2: Kâr Koruma Mekanizması")
    print("-" * 60)
    
    # Fiyat yükseldi - pozisyon kâra geçti
    current_price_profit = 102.0  # %2 kâr
    profit_pct = ((current_price_profit - 100.0) / 100.0) * 100
    print(f"   Güncel fiyat: ${current_price_profit:.2f} (+{profit_pct:.1f}%)")
    print(f"   AI güven: 70% (< {em.AI_SELL_OVERRIDE_CONFIDENCE}% eşiği)")
    
    success, pnl, msg = await em.execute_sell_flow(
        symbol="BTCUSDT",
        current_price=current_price_profit,
        ai_reasoning="Momentum zayıflıyor",
        ai_confidence=70  # Düşük güven
    )
    
    if not success:
        print(f"\n🛡️ Kâr koruma çalıştı! {msg}")
    else:
        print(f"⚠️ Beklenmedik: Pozisyon kapatıldı")
    
    print("\n" + "-" * 60)
    print("💰 TEST 3: Yüksek Güvenli Satış")
    print("-" * 60)
    
    print(f"   AI güven: 95% (>= {em.AI_SELL_OVERRIDE_CONFIDENCE}% eşiği)")
    
    success, pnl, closed = await em.execute_sell_flow(
        symbol="BTCUSDT",
        current_price=current_price_profit,
        ai_reasoning="Kritik direnç seviyesi, düşüş bekleniyor",
        ai_confidence=95  # Yüksek güven - kâr korumasını geçer
    )
    
    if success:
        print(f"\n✅ Pozisyon kapatıldı!")
        print(f"   PnL: ${pnl:.2f}")
        print(f"   Kâr %: {closed['profit_pct']:.1f}%")
        print(f"   Güncel bakiye: ${mock_portfolio['balance']:.2f}")
    else:
        print(f"❌ Satış yapılamadı: {closed}")
    
    print("\n" + "-" * 60)
    print("📈 İSTATİSTİKLER")
    print("-" * 60)
    
    stats = em.get_stats()
    for k, v in stats.items():
        print(f"   {k}: {v}")
    
    print("\n" + "=" * 60)
    print("✅ Demo tamamlandı!")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    import asyncio
    asyncio.run(demo())
