from __future__ import annotations
import logging,threading,time,uuid,calendar
from collections import defaultdict,deque
import pandas as pd
from .models import Position,PendingOrder,EntryLot,Trade
from .risk import RiskManager
from .bybit import BybitAdapter
from .telegram import Telegram
from .websocket import MarketStream,PrivateStream
from strategies.ema_strategies import EMABandStrategy,EMARSIOversoldStrategy
log=logging.getLogger('TRADING_ENGINE'); ACTIVE={'NEW','PARTIAL','SUBMITTING'}
EPSILON=1e-9  # unified epsilon for float comparisons
HB_ENV=__import__('os').environ
HB_TABLE_EVERY_S=float(HB_ENV.get('HEARTBEAT_TABLE_SECONDS','10') or 10)  # 0 = old monitor
HB_COLOR=(HB_ENV.get('HEARTBEAT_COLOR','auto') or 'auto').strip().lower()  # auto|1|0
HB_CLEAR=(HB_ENV.get('HEARTBEAT_CLEAR','0') or '0').strip().lower() in ('1','true','yes')
HB_ROWS=(HB_ENV.get('HEARTBEAT_ROWS','auto') or 'auto').strip().lower()  # auto|all|N
LEVERAGE_SET_INTERVAL_S=float(HB_ENV.get('LEVERAGE_SET_INTERVAL_SECONDS','0.20') or 0.20)  # <=5 leverage requests/sec
try:
    import os,ctypes
    if os.name=="nt":
        k=ctypes.windll.kernel32
        h=k.GetStdHandle(-11)
        mode=ctypes.c_ulong()
        if k.GetConsoleMode(h,ctypes.byref(mode)):
            k.SetConsoleMode(h,mode.value|0x0004)
except Exception:
    pass

def _build_strategy(settings):
 if settings.strategy_mode=='ema_band':
  return EMABandStrategy(settings.ema_band_fast,settings.ema_band_slow,settings.ema_band_rsi_period,settings.ema_band_cooldown_candles,settings.ema_band_exit_candles,settings.ema_band_exit_rsi)
 if settings.strategy_mode=='ema_rsi':
  return EMARSIOversoldStrategy(settings.ema_rsi_ema_period,settings.ema_rsi_period,settings.ema_rsi_entry_rsi,settings.ema_rsi_exit_rsi)
 raise RuntimeError(f"unknown STRATEGY_MODE: {settings.strategy_mode!r} (valid: 'ema_band', 'ema_rsi')")

def _cooldown_candles_for(settings):
 if settings.strategy_mode=='ema_band': return settings.ema_band_cooldown_candles
 if settings.strategy_mode=='ema_rsi': return settings.ema_rsi_cooldown_candles
 return 0

class Engine:
 def __init__(self,settings,store,telegram,adapter=None):
  self.settings=settings; self.store=store; self.telegram=telegram; self.bybit=adapter or BybitAdapter(settings); self.strategy=_build_strategy(settings); self.risk=RiskManager(settings,store)
  self.candles=defaultdict(lambda:deque(maxlen=1000)); self.last_confirmed={}; self.positions={}; self.pending={}; self.lots={}; self.long_entry_counts=defaultdict(int); self.last_scanner_entry_candle={}; self.next_entry_allowed_candle={}; self.leverage_confirmed=set(); self.last_reconcile=0; self.last_pnl_review=0; self.last_hourly_report=0.0; self._kill_switch_alerted=False; self._daily_loss_block_date=None; self.last_error=''; self.running=False; self.lock=threading.RLock(); self.ws_market=MarketStream(settings,self.on_candle); self.ws_private=PrivateStream(settings,self.on_order,self.on_execution,self.on_position)
 def start(self):
  errors=self.settings.validate()
  if errors: raise RuntimeError('Configuration errors: '+'; '.join(errors))
  self.bybit.load_instruments()
  if self.settings.enable_live_trading:
   for i,s in enumerate(self.settings.symbols):
    if i > 0 and LEVERAGE_SET_INTERVAL_S > 0:
     time.sleep(LEVERAGE_SET_INTERVAL_S)
    try:
     self.bybit.set_leverage(s,self.settings.leverage)
     self.leverage_confirmed.add(s)
    except Exception as exc:
     log.error('Could not set leverage for %s to %sx: %s',s,self.settings.leverage,exc)
     if self.settings.require_leverage_confirmation:
      log.error('%s will be BLOCKED from trading (REQUIRE_LEVERAGE_CONFIRMATION=true)',s)
      self.telegram.send(f'⚙️ LEVERAGE NOT SET\n{s}\nTrading BLOCKED for this symbol.\nSet {self.settings.leverage}x manually on Bybit, then restart.')
  else:
   self.leverage_confirmed.update(self.settings.symbols)
  for s in self.settings.symbols:
   rows=self.bybit.candles(s,500); self.candles[s].extend(rows)
   if rows: self.last_confirmed[s]=rows[-1]['start']
  with self.lock:
   self.positions={(p.symbol,p.side):p for p in self.store.load_positions() if p.managed}; self.lots={l.lot_id:l for l in self.store.load_lots('OPEN')}; self.pending={p.order_id:p for p in self.store.load_pending() if p.status in ACTIVE}
   for s in self.settings.symbols: self.long_entry_counts[s]=self.store.lot_count(s,'LONG')
  try: self.reconcile()
  except Exception as exc:
   if self.settings.enable_live_trading: raise RuntimeError(f'live reconciliation failed: {exc}') from exc
   self.last_error=f'Reconciliation warning: {exc}'
  if not self.ws_market.start(): raise RuntimeError('market WebSocket failed to start')
  if self.settings.enable_live_trading and not self.ws_private.start(): raise RuntimeError('live trading requires private execution WebSocket')
  self.running=True
  if self.settings.telegram_notify_start_stop: self.telegram.send(self.status_message('BOT STARTED'))
 def status_message(self,title):
  icon='🟢' if 'STARTED' in title else ('🔻' if 'STOPPED' in title else 'ℹ️')
  mode='LIVE TESTNET' if self.settings.enable_live_trading and self.settings.bybit_testnet else ('LIVE MAINNET' if self.settings.enable_live_trading else 'PAPER MODE (NO LIVE ORDERS)')
  sm=self.settings.strategy_mode
  if sm=='ema_band':
   strat_txt=(f'EMA Band (long only)\n'
    f'Entry: close between EMA{self.settings.ema_band_fast}/EMA{self.settings.ema_band_slow}\n'
    f'Cooldown: {self.settings.ema_band_cooldown_candles} candles after each entry\n'
    f'Exit: after {self.settings.ema_band_exit_candles} candles AND RSI{self.settings.ema_band_rsi_period}>={self.settings.ema_band_exit_rsi:.0f}, only if profitable')
  elif sm=='ema_rsi':
   strat_txt=(f'EMA+RSI Oversold (long only)\n'
    f'Entry: close>EMA{self.settings.ema_rsi_ema_period} AND RSI{self.settings.ema_rsi_period}<{self.settings.ema_rsi_entry_rsi:.0f}\n'
    f'Exit: RSI{self.settings.ema_rsi_period}>={self.settings.ema_rsi_exit_rsi:.0f}, only if profitable')
  else:
   strat_txt=f'UNKNOWN MODE {sm}'
  return (f'{icon} {title}\nMode: {mode}\nSymbols: {", ".join(self.settings.symbols)}\nTF: 15m\n'
   f'Strategy: {strat_txt}\n'
   f'Leverage: {self.settings.leverage}x | Hard Stop-Loss: {self.settings.stop_loss_pct:.0f}%\n'
   f'Max trades/asset: {self.settings.max_long_entries} | Min trade: {self.settings.min_trade_usdt:.2f} USDT\n'
   f'Scanner: {self.settings.scanner_interval_seconds:.1f}s | Position Mode: ONE-WAY')
 def _frame(self,s): return self.strategy.enrich(pd.DataFrame(list(self.candles[s])))
 def on_candle(self,c):
  if not c.get('confirm'):
   try: self.__dict__.setdefault('_live_bar',{})[c['symbol']]=c  # forming candle (display only)
   except Exception: pass
   return
  s=c['symbol']
  if s not in self.settings.symbols: return
  with self.lock:
   if self.last_confirmed.get(s)==c['start']: return
   self.last_confirmed[s]=c['start']; self.candles[s].append(c); self._process_closed_candle(s)
 def _estimated_net(self,lot,price,qty=None):
  q=lot.qty if qty is None else qty; gross=(price-lot.entry_price)*q if lot.side=='LONG' else (lot.entry_price-price)*q; return gross-lot.entry_fee-abs(price*q)*self.settings.taker_fee_rate
 def _check_exits(self,s,price,row=None):
  # FIXED: this method mutates self.lots/self.pending (exit_armed, request_close_lot)
  # and used to be called both from inside the lock (on_candle path) AND from outside
  # it (scan_live_entries/heartbeat path), racing against the WS callback threads
  # (on_order/on_execution/on_position) which always mutate the same dicts under
  # self.lock. threading.RLock is reentrant, so acquiring it here is always safe,
  # including when already held by the caller.
  with self.lock:
   self._check_exits_locked(s,price,row)
 def _check_exits_locked(self,s,price,row=None):
  candle_ms=int(self.settings.timeframe)*60*1000
  current_candle_start=int(row['start']) if row is not None and 'start' in row else None
  for lot in sorted([x for x in self.lots.values() if x.symbol==s and x.status=='OPEN'],key=lambda x:x.entry_time_ms):
   if any(p.action=='EXIT' and p.symbol==s and p.side==lot.side and p.status in ACTIVE for p in self.pending.values()): continue
   # HARD STOP LOSS: unconditional, bypasses exit_armed/profit gate, applies to every strategy.
   if lot.side=='LONG' and lot.entry_price>EPSILON:
    loss_pct=(lot.entry_price-price)/lot.entry_price*100.0
    if loss_pct>=self.settings.stop_loss_pct:
     log.warning('HARD STOP LOSS %s: loss=%.2f%% >= limit %.2f%% (lot=%s)',s,loss_pct,self.settings.stop_loss_pct,lot.lot_id)
     self.store.event('HARD_STOP_LOSS',{'lot_id':lot.lot_id,'price':price,'loss_pct':loss_pct,'limit_pct':self.settings.stop_loss_pct},s,lot.side)
     self.telegram.send(f'🛑 HARD STOP LOSS TRIGGERED\n{s} {lot.side}\nLoss: {loss_pct:.2f}% (limit {self.settings.stop_loss_pct:.0f}%)\nClosing at market.')
     self.request_close_lot(lot,price,'hard_stop_loss',force=True)
     continue
   candles_elapsed=10**9
   if current_candle_start is not None and lot.entry_candle_start:
    candles_elapsed=max(0,(current_candle_start-lot.entry_candle_start)//candle_ms)
   if not lot.exit_armed and row is not None and self.strategy.exit_ready(row,candles_elapsed):
    lot.exit_armed=True; self.store.save_lot(lot); self.store.event('EXIT_ARMED',{'lot_id':lot.lot_id,'price':price,'candles_elapsed':int(candles_elapsed)},s,lot.side)
   if lot.exit_armed and self._estimated_net(lot,price)>0:
    self.request_close_lot(lot,price,'strategy_target_then_profitable'); continue
   if lot.exit_armed and self._estimated_net(lot,price)<=0: self.store.event('EXIT_HELD_UNPROFITABLE',{'lot_id':lot.lot_id,'price':price,'net_pnl':self._estimated_net(lot,price)},s,lot.side)
 def _process_closed_candle(self,s):
  df=self._frame(s)
  if df.empty: return
  row=df.iloc[-1]; close=float(row['close'])
  if not(close>0): return
  self._check_exits(s,close,row)
  sig=self.strategy.signal(s,df)
  if not sig: return
  if self.last_scanner_entry_candle.get(s)==int(sig.candle_start): return
  if int(sig.candle_start)<self.next_entry_allowed_candle.get(s,0):
   return  # still inside post-entry cooldown window for this symbol
  active=self.store.lot_count(s,'LONG'); pending=sum(1 for p in self.pending.values() if p.action=='ENTRY' and p.symbol==s and p.side=='LONG' and p.status in ACTIVE)
  self.long_entry_counts[s]=active
  if active+pending>=self.settings.max_long_entries: return
  submitted=self.open_position(sig)
  if submitted:
   self.last_scanner_entry_candle[s]=int(sig.candle_start)
   cd=_cooldown_candles_for(self.settings)
   if cd>0:
    candle_ms=int(self.settings.timeframe)*60*1000
    self.next_entry_allowed_candle[s]=int(sig.candle_start)+cd*candle_ms

 def open_position(self,signal):
  with self.lock:
   kill_path=self.settings.log_dir.parent/self.settings.kill_switch_file
   if kill_path.exists():
    if not self._kill_switch_alerted:
     self._kill_switch_alerted=True
     log.error('KILL SWITCH ACTIVE (%s exists): refusing all new entries',kill_path)
     self.telegram.send(f'🧯 KILL SWITCH ACTIVE\nFile {self.settings.kill_switch_file} is present.\nAll new entries are blocked. Delete the file to resume.')
    return False
   elif self._kill_switch_alerted:
    self._kill_switch_alerted=False
    log.info('Kill switch file removed, new entries re-enabled')
    self.telegram.send('✅ Kill switch cleared. New entries re-enabled.')
   if self.settings.max_daily_loss_usdt>0:
    today=time.strftime('%Y-%m-%d',time.gmtime())
    # FIXED: time.mktime() interprets a UTC date string as LOCAL time, which shifted
    # the daily-loss window by the server's UTC offset. calendar.timegm() is correct.
    day_start_ms=calendar.timegm(time.strptime(today,'%Y-%m-%d'))*1000
    daily_pnl=sum(float(t['pnl']) for t in self.store.recent_trades(500) if t['closed_at_ms']>=day_start_ms)
    if daily_pnl<=-abs(self.settings.max_daily_loss_usdt):
     if self._daily_loss_block_date!=today:
      self._daily_loss_block_date=today
      log.error('MAX DAILY LOSS reached (%.2f USDT <= -%.2f): blocking new entries for the rest of the day',daily_pnl,self.settings.max_daily_loss_usdt)
      self.telegram.send(f'📉 MAX DAILY LOSS REACHED\nToday PnL: {daily_pnl:.2f} USDT (limit -{self.settings.max_daily_loss_usdt:.2f})\nNo new entries until tomorrow (UTC).')
     return False
   if self.settings.enable_live_trading and self.settings.require_leverage_confirmation and signal.symbol not in self.leverage_confirmed:
    log.error('ENTRY_GATE %s: leverage was never confirmed at startup, refusing to trade this symbol',signal.symbol)
    return False
   if self.settings.max_total_open_lots>0:
    total_open=sum(1 for x in self.lots.values() if x.status=='OPEN')
    total_pending=sum(1 for x in self.pending.values() if x.action=='ENTRY' and x.status in ACTIVE)
    if total_open+total_pending>=self.settings.max_total_open_lots:
     log.warning('ENTRY_GATE %s: global open-lot cap reached (%d/%d), refusing new entry',signal.symbol,total_open+total_pending,self.settings.max_total_open_lots)
     return False
   if hasattr(self.ws_market,'is_connected') and not self.ws_market.is_connected():
    log.error('ENTRY_GATE %s: MarketStream disconnected, refusing to place order',signal.symbol)
    return False
   if self.settings.enable_live_trading and hasattr(self.ws_private,'is_connected') and not self.ws_private.is_connected():
    log.error('ENTRY_GATE %s: PrivateStream disconnected, cannot track execution, refusing to place order',signal.symbol)
    return False
   if any(p.action=='ENTRY' and p.symbol==signal.symbol and p.side=='LONG' and p.status in ACTIVE for p in self.pending.values()):
    log.warning('ENTRY_GATE %s: same-side entry already pending',signal.symbol)
    return False

   try:
    live=self.bybit.tickers().get(signal.symbol,{})
    live_px=float(live.get('last') or 0)
    if live_px>EPSILON and signal.close>EPSILON:
     dev=abs(live_px-signal.close)/signal.close*100.0
     if dev>self.settings.max_entry_price_deviation_pct:
      reason=f'signal price {signal.close:.8f} deviates {dev:.3f}% from live {live_px:.8f} (cap {self.settings.max_entry_price_deviation_pct:.3f}%) - stale/fast-moving market'
      self.store.event('RISK_BLOCK',{'reason':reason,'signal':signal.__dict__},signal.symbol,'LONG')
      log.warning('ENTRY_GATE STALE PRICE BLOCKED %s: %s',signal.symbol,reason)
      return False
   except Exception as exc:
    log.error('ENTRY_GATE %s: could not verify live price (%s), refusing entry to stay safe',signal.symbol,exc)
    return False
   try:
    _,available=self.bybit.get_balance()
    log.warning(
     'ENTRY_GATE %s balance=%.8f size_pct=%.4f min_trade=%.4f',
     signal.symbol,
     available,
     self.settings.position_size_pct,
     self.settings.min_trade_usdt
    )
   except Exception as exc:
    log.exception('ENTRY_GATE BALANCE FAILED %s: %s',signal.symbol,exc)
    return False

   try:
    qty=self.bybit.size_from_balance(
     signal.symbol,
     available,
     self.settings.position_size_pct,
     signal.close
    )
    log.warning(
     'ENTRY_GATE %s calculated_qty=%s notional=%.8f',
     signal.symbol,
     qty,
     qty*signal.close
    )
    if self.settings.max_order_notional_usdt>0 and qty*signal.close>self.settings.max_order_notional_usdt:
     reason=f'order notional {qty*signal.close:.4f} USDT exceeds MAX_ORDER_NOTIONAL_USDT cap {self.settings.max_order_notional_usdt:.4f}'
     self.store.event('RISK_BLOCK',{'reason':reason,'signal':signal.__dict__},signal.symbol,'LONG')
     log.warning('ENTRY_GATE NOTIONAL CAP BLOCKED %s: %s',signal.symbol,reason)
     return False
   except ValueError as exc:
    reason=str(exc)
    self.store.event(
     'RISK_BLOCK',
     {'reason':reason,'signal':signal.__dict__},
     signal.symbol,
     'LONG'
    )
    log.warning('%s LONG entry blocked: %s',signal.symbol,reason)
    return False

   ok,reason=self.risk.validate_order(
    signal.symbol,
    'LONG',
    qty,
    self.positions,
    self.pending
   )

   if not ok:
    self.store.event(
     'RISK_BLOCK',
     {'reason':reason,'signal':signal.__dict__},
     signal.symbol,
     'LONG'
    )
    log.warning(
     'ENTRY_GATE RISK BLOCKED %s: %s',
     signal.symbol,
     reason
    )
    return False

   cycle=uuid.uuid4().hex[:12]
   lot_id=uuid.uuid4().hex[:16]

   if not self.settings.enable_live_trading:
    lot=EntryLot(
     lot_id,
     signal.symbol,
     'LONG',
     qty,
     signal.close,
     int(time.time()*1000),
     'PAPER',
     cycle,
     entry_candle_start=int(signal.candle_start)
    )
    self.lots[lot_id]=lot
    self.store.save_lot(lot)
    self._rebuild_position(signal.symbol,'LONG')
    self.telegram.send(
     f'✅ PAPER ENTRY\n'
     f'{signal.symbol} LONG\n'
     f'Qty: {qty}\n'
     f'Price: {signal.close}\n'
     f'Reason: {signal.reason}\n'
     f'Leverage: {self.settings.leverage}x'
    )
    return True

   link=f'EMA-E-{signal.symbol}-{signal.candle_start}-{uuid.uuid4().hex[:6]}'[:36]

   p=PendingOrder(
    'SUBMITTING:'+uuid.uuid4().hex,
    link,
    signal.symbol,
    'LONG',
    'ENTRY',
    0,
    qty,
    reason='entry_signal',
    candle_start=signal.candle_start,
    created_at_ms=int(time.time()*1000),
    cycle_id=cycle,
    lot_id=lot_id,
    status='SUBMITTING'
   )

   self.pending[p.order_id]=p
   self.store.save_pending(p)

   try:
    log.warning(
     'EXECUTION: calling place_market symbol=%s side=LONG qty=%s link=%s',
     signal.symbol,
     qty,
     link
    )

    r=self.bybit.place_market(
     signal.symbol,
     'LONG',
     qty,
     link,
     0
    )

    log.warning('EXECUTION: place_market returned %r',r)

    if not isinstance(r,dict):
     raise RuntimeError(
      f'Invalid Bybit response type: {type(r).__name__}'
     )

    result=r.get('result') or {}
    oid=result.get('orderId') if isinstance(result,dict) else None

    if not oid:
     raise RuntimeError(
      f'Bybit accepted call but returned no orderId: {r!r}'
     )

    log.warning('EXECUTION: ORDER_ID_RECEIVED=%s',oid)

   except Exception as exc:
    self.store.event(
     'ENTRY_SUBMISSION_UNCERTAIN',
     {
      'error':str(exc),
      'order_link_id':link
     },
     signal.symbol,
     'LONG'
    )
    self.telegram.send(
     f'❗ ENTRY SUBMISSION UNCERTAIN\n'
     f'{signal.symbol} LONG\n'
     f'Link: {link}\n'
     f'{exc}'
    )
    return False

   self._replace_pending(p,oid)

   self.store.event(
    'ENTRY_ACCEPTED',
    {
     'order_id':oid,
     'qty':qty
    },
    signal.symbol,
    'LONG'
   )

   log.info('SUCCESS %s LONG order submitted: orderId=%s qty=%.8f',signal.symbol,oid,qty)
   return True

 def _replace_pending(self,p,oid):
  old=p.order_id; self.pending.pop(old,None); self.store.delete_pending(old); p.order_id=oid; p.status='NEW'; self.pending[oid]=p; self.store.save_pending(p)

 def request_close_lot(self,lot,price,reason,force=False):
  with self.lock:
   if not force and (not lot.exit_armed or self._estimated_net(lot,price)<=0): return
   if any(p.action=='EXIT' and p.symbol==lot.symbol and p.side==lot.side and p.status in ACTIVE for p in self.pending.values()): return
   if not self.settings.enable_live_trading: self.close_lot_paper(lot,price,reason); return
   link=f'EMA-X-{lot.symbol}-{int(time.time()*1000)}-{uuid.uuid4().hex[:5]}'[:36]; p=PendingOrder('SUBMITTING:'+uuid.uuid4().hex,link,lot.symbol,lot.side,'EXIT',0,lot.qty,reason=reason,created_at_ms=int(time.time()*1000),lot_id=lot.lot_id,status='SUBMITTING'); self.pending[p.order_id]=p; self.store.save_pending(p)
   try: r=self.bybit.close_market(lot.symbol,lot.side,lot.qty,link,0); oid=r['result']['orderId']
   except Exception as exc:
    self.store.event('EXIT_SUBMISSION_UNCERTAIN',{'error':str(exc),'order_link_id':link,'lot_id':lot.lot_id},lot.symbol,lot.side)
    log.error('EXIT submission failed for %s %s (%s): %s -- order kept pending, will be reconciled/retried',lot.symbol,lot.side,reason,exc)
    self.telegram.send(f'⚠️ EXIT SUBMISSION FAILED\n{lot.symbol} {lot.side}\nReason: {reason}\nError: {str(exc)[:150]}\nBot will re-check with Bybit and retry automatically.')
    return
   lot.exit_order_id=oid; self.store.save_lot(lot); self._replace_pending(p,oid)
 def close_lot_paper(self,lot,price,reason):
  pnl=self._estimated_net(lot,price); fee=lot.entry_fee+abs(price*lot.qty)*self.settings.taker_fee_rate; self.store.trade(Trade(lot.symbol,lot.side,lot.qty,lot.entry_price,price,pnl,fee,lot.entry_time_ms,int(time.time()*1000),lot.cycle_id,reason,lot.entry_order_id,'PAPER-EXIT')); self.store.delete_lot(lot.lot_id); self.lots.pop(lot.lot_id,None); self._rebuild_position(lot.symbol,lot.side); self.telegram.send(f'{"🟢" if pnl>=0 else "🔴"} PAPER EXIT\n{lot.symbol} {lot.side}\nQty: {lot.qty}\nExit: {price}\nPnL: {pnl:.6f}\nReason: {reason}')
 def _rebuild_position(self,s,side):
  lots=[x for x in self.lots.values() if x.symbol==s and x.side==side and x.status=='OPEN']; key=(s,side)
  if not lots: self.positions.pop(key,None); self.store.delete_position(s,side); self.long_entry_counts[s]=0; return
  qty=sum(x.qty for x in lots); avg=(sum(x.qty*x.entry_price for x in lots)/qty) if qty>EPSILON else 0.0; first=min(lots,key=lambda x:x.entry_time_ms); p=Position(s,side,qty,avg,first.entry_time_ms,first.entry_order_id,first.cycle_id,0,True,entry_fee=sum(x.entry_fee for x in lots),entry_count=len(lots)); self.positions[key]=p; self.store.save_position(p); self.long_entry_counts[s]=len(lots)
 def on_order(self,message):
  with self.lock:
   for item in message.get('data',[]):
    oid=item.get('orderId'); p=self.pending.get(oid)
    if not p:
     link=item.get('orderLinkId'); p=next((x for x in self.pending.values() if link and x.order_link_id==link),None)
     if p and p.order_id!=oid: self._replace_pending(p,oid)
    if not p: continue
    p.status={'New':'NEW','PartiallyFilled':'PARTIAL','Filled':'FILLED','Cancelled':'CANCELLED','Rejected':'REJECTED'}.get(item.get('orderStatus',''),p.status); p.filled_qty=float(item.get('cumExecQty') or p.filled_qty); p.avg_fill_price=float(item.get('avgPrice') or p.avg_fill_price); self.store.save_pending(p)
    if p.status in {'CANCELLED','REJECTED'} and p.filled_qty<=0: self._drop_pending(p)
 def on_execution(self,message):
  with self.lock:
   for item in message.get('data',[]):
    oid=item.get('orderId',''); eid=item.get('execId','');
    if not eid or not self.store.save_execution(eid,oid,item,int(item.get('execTime') or time.time()*1000)): continue
    p=self.pending.get(oid)
    if not p: continue
    p.filled_qty,p.fee,p.avg_fill_price=self.store.execution_totals(oid); p.status='FILLED' if p.filled_qty+1e-12>=p.requested_qty else 'PARTIAL'; self.store.save_pending(p)
    if p.action=='ENTRY': self._apply_entry(p)
    else: self._apply_exit(p)
 def _apply_entry(self,p):
  lot=self.lots.get(p.lot_id)
  if not lot: lot=EntryLot(p.lot_id,p.symbol,p.side,p.filled_qty,p.avg_fill_price,int(time.time()*1000),p.order_id,p.cycle_id,p.fee,entry_candle_start=p.candle_start); self.lots[lot.lot_id]=lot
  else: lot.qty=p.filled_qty; lot.entry_price=p.avg_fill_price; lot.entry_fee=p.fee
  self.store.save_lot(lot); self._rebuild_position(p.symbol,p.side)
  if p.status=='FILLED': self._drop_pending(p); self.telegram.send(f'✅ ENTRY FILLED\n{p.symbol} {p.side}\nQty: {lot.qty}\nPrice: {lot.entry_price}')
 def _apply_exit(self,p):
  lot=self.lots.get(p.lot_id)
  if not lot: self._drop_pending(p); return
  inc=max(0,p.filled_qty-p.processed_fill_qty); fee=max(0,p.fee-p.processed_fill_fee)
  if inc<=1e-12:
   if p.status in {'CANCELLED','REJECTED'}: self._drop_pending(p)
   return
  before=lot.qty; inc=min(inc,before); alloc=lot.entry_fee*(inc/before) if before else 0; px=p.avg_fill_price or lot.entry_price; gross=(px-lot.entry_price)*inc if lot.side=='LONG' else (lot.entry_price-px)*inc; pnl=gross-alloc-fee
  self.store.trade(Trade(lot.symbol,lot.side,inc,lot.entry_price,px,pnl,alloc+fee,lot.entry_time_ms,int(time.time()*1000),lot.cycle_id,p.reason,lot.entry_order_id,p.order_id)); lot.qty-=inc; lot.entry_fee=max(0,lot.entry_fee-alloc); lot.exit_fee+=fee; p.processed_fill_qty+=inc; p.processed_fill_fee+=fee; self.store.save_pending(p)
  if lot.qty<=1e-12: self.store.delete_lot(lot.lot_id); self.lots.pop(lot.lot_id,None); self._rebuild_position(lot.symbol,lot.side); self._drop_pending(p); self.telegram.send(f'{"🟢" if pnl>=0 else "🔴"} TRADE CLOSED\n{lot.symbol} {lot.side}\nQty: {inc}\nExit: {px}\nPnL: {pnl:.6f}\nReason: {p.reason}')
  else: self.store.save_lot(lot); self._rebuild_position(lot.symbol,lot.side)
 def _drop_pending(self,p): self.pending.pop(p.order_id,None); self.store.delete_pending(p.order_id)
 def on_position(self,message):
  with self.lock:
   for item in message.get('data',[]):
    s=item.get('symbol'); raw=item.get('side'); q=float(item.get('size') or 0); side='LONG' if raw=='Buy' else 'SHORT' if raw=='Sell' else None
    if s not in self.settings.symbols or not side: continue
    key=(s,side)
    if q<=0: continue
    old=self.positions.get(key); entry=float(item.get('avgPrice') or item.get('entryPrice') or (old.entry_price if old else 0)); managed=old.managed if old else any(l.symbol==s and l.side==side for l in self.lots.values()); p=old or Position(s,side,q,entry,int(time.time()*1000),position_idx=0,managed=managed,entry_count=self.store.lot_count(s,side)); p.qty=q; p.entry_price=entry; p.position_idx=0; p.managed=managed; self.positions[key]=p; self.store.save_position(p)
 def reconcile(self):
  with self.lock:
   exchange={}
   raw_positions=self.bybit.get_positions()
   for item in raw_positions:
    s=item.get('symbol'); raw=item.get('side'); q=float(item.get('size') or 0); side='LONG' if raw=='Buy' else 'SHORT' if raw=='Sell' else None
    if s not in self.settings.symbols or not side or q<=0: continue
    key=(s,side); old=self.positions.get(key); managed=old.managed if old else any(l.symbol==s and l.side==side for l in self.lots.values()); entry=float(item.get('avgPrice') or item.get('entryPrice') or (old.entry_price if old else 0)); exchange[key]=old or Position(s,side,q,entry,int(time.time()*1000),position_idx=0,managed=managed,entry_count=self.store.lot_count(s,side)); exchange[key].qty=q; exchange[key].entry_price=entry; exchange[key].managed=managed; self.store.save_position(exchange[key])
   # Never silently forget a managed local position when an exit/order is still unresolved.
   for key,old in self.positions.items():
    if key not in exchange and (any(l.symbol==key[0] and l.side==key[1] for l in self.lots.values()) or any(p.action=='EXIT' and p.symbol==key[0] and p.side==key[1] and p.status in ACTIVE for p in self.pending.values())): exchange[key]=old
    elif key not in exchange and old.managed: self.store.delete_position(*key)
   self.positions=exchange
   for oid,p in list(self.pending.items()):
    rows=self.bybit.get_order_history(order_id=oid,limit=1) if not oid.startswith('SUBMITTING:') else self.bybit.get_order_history(order_link_id=p.order_link_id,limit=1)
    if not rows:
     # FIXED: previously just 'continue' forever. If place_market/close_market threw
     # an exception (e.g. a network blip), the PendingOrder was already saved with
     # status='NEW' (ACTIVE) BEFORE the API call, and Bybit has no record of it under
     # this order_link_id. That permanently blocked ALL future entries for the symbol,
     # or worse, ALL future exits for that lot -- including every future hard
     # stop-loss attempt, even after the network recovered. Verified reproducible via
     # tests/stuck_order_test.py before this fix.
     if oid.startswith('SUBMITTING:'):
      age_s=(time.time()*1000-p.created_at_ms)/1000.0
      if age_s>=self.settings.stuck_order_timeout_seconds:
       if p.action=='EXIT':
        # A retried close is reduceOnly -- it can only shrink/no-op, never open or
        # flip a position, so it is safe to simply drop this and let it be retried.
        log.error('EXIT order %s (%s %s) stuck SUBMITTING for %.0fs with no trace on Bybit -- dropping so it can be retried',oid,p.symbol,p.side,age_s)
        self.store.event('EXIT_SUBMISSION_TIMEOUT',{'order_link_id':p.order_link_id,'age_s':age_s,'lot_id':p.lot_id},p.symbol,p.side)
        self.telegram.send(f'⏱️ EXIT ORDER TIMED OUT\n{p.symbol} {p.side}\nNo confirmation from Bybit after {age_s:.0f}s.\nWill retry closing automatically.')
        self._drop_pending(p)
       else:
        # A retried entry is NOT reduceOnly -- if the original order actually landed
        # on Bybit despite our client-side exception, retrying could double the
        # position. Only auto-clear if the exchange shows no matching open position;
        # otherwise escalate loudly and leave it frozen for manual review.
        has_position=any(x.get('symbol')==p.symbol and float(x.get('size') or 0)>0 for x in raw_positions)
        if not has_position:
         log.error('ENTRY order %s (%s) stuck SUBMITTING for %.0fs, no trace on Bybit and no matching position -- dropping so entry can be retried',oid,p.symbol,age_s)
         self.store.event('ENTRY_SUBMISSION_TIMEOUT',{'order_link_id':p.order_link_id,'age_s':age_s},p.symbol,p.side)
         self.telegram.send(f'⏱️ ENTRY ORDER TIMED OUT\n{p.symbol} LONG\nNo confirmation and no matching position after {age_s:.0f}s.\nWill retry on next signal.')
         self._drop_pending(p)
        else:
         log.error('ENTRY order %s (%s) stuck SUBMITTING for %.0fs BUT a matching position exists on Bybit -- NOT auto-clearing, needs manual review',oid,p.symbol,age_s)
         self.telegram.send(f'🚨 ENTRY ORDER AMBIGUOUS - MANUAL REVIEW NEEDED\n{p.symbol} LONG\nNo order confirmation after {age_s:.0f}s BUT a position exists on Bybit.\nBot will NOT auto-retry this symbol until you resolve it.')
     continue
    item=rows[0]; actual=item.get('orderId') or oid; p.status={'New':'NEW','PartiallyFilled':'PARTIAL','Filled':'FILLED','Cancelled':'CANCELLED','Rejected':'REJECTED'}.get(item.get('orderStatus',''),p.status); p.filled_qty=float(item.get('cumExecQty') or p.filled_qty); p.avg_fill_price=float(item.get('avgPrice') or p.avg_fill_price)
    if actual!=oid: self._replace_pending(p,actual); oid=actual
    self.store.save_pending(p)
    if p.filled_qty>0 and not oid.startswith('SUBMITTING:'):
     executions=self.bybit.get_executions(order_id=oid,limit=100)
     if executions: self.on_execution({'data':executions})
    if p.status in {'CANCELLED','REJECTED'} and p.filled_qty<=0: self._drop_pending(p)
    elif p.status in {'CANCELLED','REJECTED'} and p.filled_qty>0 and p.order_id in self.pending:
     # Keep the pending record until all cumulative fills have been applied.
     if p.processed_fill_qty+1e-12>=p.filled_qty: self._drop_pending(p)
   for s in self.settings.symbols: self.long_entry_counts[s]=self.store.lot_count(s,'LONG')
   self.last_reconcile=time.time()
 def pnl_review(self):
  eq,av=self.bybit.get_balance(); unreal=sum(float(x.get('unrealisedPnl') or 0) for x in self.bybit.get_positions()); realized=sum(float(x['pnl']) for x in self.store.recent_trades(100)); self.store.snapshot(eq,av,realized,unreal,len(self.positions))
 def _live_ema_rsi_frame(self,s,price,df=None):
  if df is None:
   df=self._frame(s)
  if df.empty or price<=0:
   return df

  candle_ms=int(self.settings.timeframe)*60*1000
  live_start=(int(time.time()*1000)//candle_ms)*candle_ms
  eval_df=df.copy()
  last=eval_df.iloc[-1]

  try:
   last_start=int(last["start"])
  except Exception:
   last_start=0

  if last_start==live_start:
   idx=eval_df.index[-1]
   eval_df.loc[idx,"close"]=float(price)
   if "high" in eval_df.columns:
    eval_df.loc[idx,"high"]=max(float(eval_df.loc[idx,"high"]),float(price))
   if "low" in eval_df.columns:
    eval_df.loc[idx,"low"]=min(float(eval_df.loc[idx,"low"]),float(price))
  elif last_start<live_start:
   base_close=float(last["close"])
   live_bar={
    "start":live_start,
    "open":base_close,
    "high":max(base_close,float(price)),
    "low":min(base_close,float(price)),
    "close":float(price),
    "volume":0.0,
    "confirm":False
   }
   eval_df=pd.concat([eval_df,pd.DataFrame([live_bar])],ignore_index=True)

  return self.strategy.enrich(eval_df)

 def _ema_status_line(self):
  sm=self.settings.strategy_mode
  parts=[]

  try:
   market=self.bybit.tickers()
  except Exception:
   market={}

  for s in self.settings.symbols:
   try:
    df=self._frame(s)
    if df.empty:
     parts.append(f'{s}=WARMUP')
     continue

    q=market.get(s,{}) or {}
    price=float(q.get("last") or q.get("price") or df.iloc[-1]["close"])

    eval_df=self._live_ema_rsi_frame(s,price,df)
    row=eval_df.iloc[-1]

    rsi=float(row["rsi"]) if "rsi" in row and pd.notna(row["rsi"]) else None

    if sm=="ema_band":
     ema_f=float(row["ema_fast"]) if "ema_fast" in row and pd.notna(row["ema_fast"]) else None
     ema_s=float(row["ema_slow"]) if "ema_slow" in row and pd.notna(row["ema_slow"]) else None
     ind=f'EMAf={ema_f:.6g} EMAs={ema_s:.6g}' if ema_f is not None and ema_s is not None else 'WARMUP'
    else:
     ema=float(row["ema"]) if "ema" in row and pd.notna(row["ema"]) else None
     ind=f'EMA={ema:.6g}' if ema is not None else 'WARMUP'

    sig=self.strategy.signal(s,eval_df)
    lots=self.store.lot_count(s,"LONG")
    rsitxt=f'{rsi:.1f}' if rsi is not None else '--'
    sigtxt='SIGNAL' if sig is not None else 'wait'

    parts.append(f'{s}[px={price:.6g} {ind} RSI={rsitxt} lots={lots} {sigtxt}]')

   except Exception as e:
    parts.append(f'{s}=ERR({e})')

  return ' '.join(parts)


 def heartbeat(self):
     consecutive_errors=0
     while self.running:
         try:
             now=time.time()

             market_ok=self.ws_market.is_connected() if hasattr(self.ws_market,'is_connected') else True
             private_ok=self.ws_private.is_connected() if hasattr(self.ws_private,'is_connected') else True

             if not market_ok:
                 log.warning('MarketStream disconnected, attempting reconnect...')
                 if hasattr(self.ws_market,'reconnect'):
                     self.ws_market.reconnect()

             if not private_ok and self.settings.enable_live_trading:
                 log.warning('PrivateStream disconnected, attempting reconnect...')
                 if hasattr(self.ws_private,'reconnect'):
                     self.ws_private.reconnect()

                     private_ok = self.ws_private.is_connected()  # after reconnect
                 private_ok=self.ws_private.is_connected()

             position_state = "OPEN" if any(self.store.lot_count(sym,"LONG") > 0 for sym in self.candles.keys()) else "NONE"
             # EMA/RSI status per symbol, refreshed every heartbeat tick (SCANNER_INTERVAL_SECONDS).
             ema_status=self._ema_status_line()
             self.scan_live_entries()
             if HB_TABLE_EVERY_S>0 and now-self.__dict__.get('_last_hb_table',0.0)>=HB_TABLE_EVERY_S:
                 self._last_hb_table=now
                 try: self._hb_show()
                 except Exception as _hb_exc: log.warning('heartbeat table failed: %s',_hb_exc)

             # FIXED: previously read settings.reconcile_interval/scanner_interval, which
             # do not exist on Settings (real names are reconciliation_seconds and
             # scanner_interval_seconds) -- the getattr() defaults silently overrode any
             # value configured via .env. Reading the real fields directly below.
             reconcile_interval=float(self.settings.reconciliation_seconds)

             if now-self.last_reconcile>=reconcile_interval:
                 self.reconcile()
                 self.last_reconcile=now

             if self.settings.telegram_hourly_pnl and now-self.last_hourly_report>=self.settings.telegram_pnl_interval_seconds:
                 try:
                     eq,av=self.bybit.get_balance()
                     unreal=sum(float(x.get('unrealisedPnl') or 0) for x in self.bybit.get_positions())
                     realized_today=sum(float(t['pnl']) for t in self.store.recent_trades(500) if t['closed_at_ms']>=calendar.timegm(time.strptime(time.strftime('%Y-%m-%d',time.gmtime()),'%Y-%m-%d'))*1000)
                     open_lots=sum(1 for x in self.lots.values() if x.status=='OPEN')
                     mode='LIVE TESTNET' if self.settings.enable_live_trading and self.settings.bybit_testnet else ('LIVE MAINNET' if self.settings.enable_live_trading else 'PAPER')
                     self.telegram.send(
                         f'📊 HOURLY PNL REPORT ({mode})\n'
                         f'Equity: {eq:.4f} USDT\n'
                         f'Available: {av:.4f} USDT\n'
                         f"Today's realized PnL: {'🟢' if realized_today>=0 else '🔴'} {realized_today:.4f} USDT\n"
                         f'Unrealized PnL: {unreal:.4f} USDT\n'
                         f'Open lots: {open_lots}'
                     )
                 except Exception as e:
                     log.error('Hourly PnL report failed: %s',e)
                 finally:
                     self.last_hourly_report=now

             # FIXED: this previously called self._update_pnl() behind a hasattr()
             # guard for a method that does not exist, so pnl_review() never ran and
             # no equity snapshots were ever written (dashboard chart stayed empty).
             if now-self.last_pnl_review>=self.settings.pnl_review_minutes*60:
                 self.pnl_review()
                 self.last_pnl_review=now

             consecutive_errors=0
         except Exception:
             import logging
             logging.getLogger("TRADING_ENGINE").exception("heartbeat error")
             consecutive_errors+=1
             if consecutive_errors>=3:
                 self.running=False
                 raise RuntimeError('heartbeat failed three consecutive times')

         time.sleep(float(self.settings.scanner_interval_seconds))
 def _hb_usd(self,x):
  try: x=float(x)
  except Exception: return '--'
  if x!=x: return '--'
  a=abs(x)
  if a>=1e9: return f'{x/1e9:.2f}B'
  if a>=1e6: return f'{x/1e6:.2f}M'
  if a>=1e3: return f'{x/1e3:.1f}K'
  return f'{x:.0f}'
 def _hb_px(self,x):
  try: x=float(x)
  except Exception: return '--'
  if x!=x: return '--'
  if x>=1000: return f'{x:.1f}'
  if x>=1: return f'{x:.4f}'
  return f'{x:.6f}'
 def _hb_pxa(self,x,ipw=6,fpw=6):
  s=self._hb_px(x)
  if s=='--': return s.rjust(ipw+1+fpw)
  ip,_,fp=s.partition('.')
  return ip.rjust(ipw)+'.'+fp.ljust(fpw)
 def _hb_term(self):
  import shutil,sys
  try: tty=bool(sys.stdout.isatty())
  except Exception: tty=False
  try:
   sz=shutil.get_terminal_size(fallback=(120,40)); return tty,int(sz.columns),int(sz.lines)
  except Exception: return tty,120,40
 def _hb_show(self):
  import os
  if not self.__dict__.get('_hb_vt_done'):
   self._hb_vt_done=True
   if os.name=='nt':   # switch on ANSI colour support in the Windows console
    try:
     import ctypes; k=ctypes.windll.kernel32; h=k.GetStdHandle(-11); m=ctypes.c_uint32()
     if k.GetConsoleMode(h,ctypes.byref(m)): k.SetConsoleMode(h,m.value|0x0004)
    except Exception: pass
    try: os.system('')
    except Exception: pass
  txt=self._heartbeat_table()
  if HB_CLEAR and self._hb_term()[0]: txt='\033[H\033[2J'+txt
  print(txt,flush=True)
 def _heartbeat_table(self):
  import math,sys
  st=self.settings; sm=st.strategy_mode; candle_ms=int(st.timeframe)*60*1000
  now_ms=int(time.time()*1000); live_start=(now_ms//candle_ms)*candle_ms
  tty,tcols,tlines=self._hb_term()
  use_color=(HB_COLOR in ('1','true','yes')) or (HB_COLOR=='auto' and tty)
  uni='utf' in ((getattr(sys.stdout,'encoding',None) or '').lower())
  UP,DN=('\u25b2','\u25bc') if uni else ('^','v')
  K={'r':'\033[91m','g':'\033[92m','y':'\033[93m','b':'\033[94m','c':'\033[96m','w':'\033[97m','d':'\033[90m',
     'ttl':'\033[1;96m','hdr':'\033[1;97;44m','buy':'\033[1;30;42m','bg':'\033[1;92m','bw':'\033[1;97m'}
  def col(t,k): return (K[k]+t+'\033[0m') if (use_color and k) else t
  def cell(t,w,a='<',k=None): return col(format(str(t),a+str(w)),k)
  with self.lock:
   lots_all=[x for x in self.lots.values() if x.status=='OPEN']
   pend_all=[p for p in self.pending.values() if p.status in ACTIVE]
   cooldowns=dict(self.next_entry_allowed_candle); attempted=dict(self.last_scanner_entry_candle)
  try: market=self.bybit.tickers()
  except Exception: market={}
  if sm=='ema_band':
   ema_name=f'EMA{st.ema_band_fast}/{st.ema_band_slow}'; exit_rsi=float(st.ema_band_exit_rsi); entry_rsi=None; ew=15
   rule=f'entry: close inside {ema_name} band | exit: RSI{st.ema_band_rsi_period}>={exit_rsi:.0f} and profitable'
  else:
   ema_name=f'EMA{st.ema_rsi_ema_period}'; exit_rsi=float(st.ema_rsi_exit_rsi); entry_rsi=float(st.ema_rsi_entry_rsi); ew=13
   rule=f'entry: close>{ema_name} and RSI{st.ema_rsi_period}<{entry_rsi:.0f} | exit: RSI>={exit_rsi:.0f} and profitable'
  # ---- column model: (key, header, unit-line, width, align); low-priority columns dropped on narrow windows
  cols=[('sym','SYMBOL','',8,'<'),('px','PRICE','',13,'>'),('ema',ema_name,'',ew,'>'),('vs','PRICE vs EMA','',16,'<'),
        ('rsi','RSI','',10,'<'),('p24','24H %','',8,'>'),('vn','VOL15m now','(USDT)',10,'>'),('vl','VOL15m last','(USDT)',11,'>'),
        ('xa','vs avg','(20c)',7,'>'),('sig','SIGNAL','',7,'>')]
  def tw(cs): return sum(c[3] for c in cs)+len(cs)-1
  limit=max(60,tcols-1) if tty else 999
  for drop in ('p24','vn','ema','xa'):
   if tw(cols)<=limit: break
   cols=[c for c in cols if c[0]!=drop]
  rows=[]; px_by={}; n_above=0; n_low=0; n_sig=0; n_warm=0; n_ok=0
  for s in st.symbols:
   short=s[:-4] if s.endswith('USDT') else s
   try:
    df=self._frame(s); n=len(df); need=int(getattr(self.strategy,'min_bars',0))
    if df.empty or n<need:
     n_warm+=1; rows.append((9,0.0,col(f'{short:<8} WARMUP {n}/{need} candles','d'))); continue
    q=market.get(s,{}) or {}
    price=float(q.get('last') or q.get('price') or df.iloc[-1]['close'])
    try: pct24=float(q.get('pct24') or 0)
    except Exception: pct24=0.0
    eval_df=self._live_ema_rsi_frame(s,price,df); row=eval_df.iloc[-1]
    rsi=float(row['rsi']) if 'rsi' in row and pd.notna(row['rsi']) else None
    if sm=='ema_band':
     f_=float(row['ema_fast']); s_=float(row['ema_slow']); lo=min(f_,s_); hi=max(f_,s_)
     if not (math.isfinite(lo) and math.isfinite(hi) and lo>0): n_warm+=1; rows.append((9,0.0,col(f'{short:<8} WARMUP {n}/{need} candles','d'))); continue
     ema_txt=self._hb_px(lo)+'-'+self._hb_px(hi)
     if price>hi: rel='ABOVE'; pct=(price-hi)/hi*100
     elif price<lo: rel='BELOW'; pct=(price-lo)/lo*100
     else: rel='IN BAND'; pct=0.0
     above=price>hi
    else:
     e_=float(row['ema'])
     if not (math.isfinite(e_) and e_>0): n_warm+=1; rows.append((9,0.0,col(f'{short:<8} WARMUP {n}/{need} candles','d'))); continue
     ema_txt=self._hb_pxa(e_); pct=(price-e_)/e_*100; above=price>e_; rel='ABOVE' if above else 'BELOW'
    vs_txt='= IN BAND' if rel=='IN BAND' else f'{UP if above else DN} {rel} {pct:+.2f}%'
    vs_k='y' if rel=='IN BAND' else ('g' if above else 'r')
    rsi_txt='--'; rsi_k=None
    if rsi is not None and math.isfinite(rsi):
     rsi_txt=f'{rsi:.1f}'
     # NEW: RSI momentum arrow -- shows whether RSI moved up/down/flat since the
     # last heartbeat tick, so a rising RSI approaching entry/exit is visible at
     # a glance without waiting for the next full candle close.
     prev_rsi=self.__dict__.setdefault('_prev_rsi_hb',{}).get(s)
     if prev_rsi is not None:
      delta=rsi-prev_rsi
      rsi_txt+=(UP if delta>0.05 else (DN if delta<-0.05 else '='))
     self._prev_rsi_hb[s]=rsi
     if rsi>=exit_rsi: rsi_txt+=' HIGH'; rsi_k='y'
     elif entry_rsi is not None and rsi<entry_rsi: rsi_txt+=' LOW'; rsi_k='c'; n_low+=1
    vnow=vlast=xavg=None
    if 'volume' in df.columns:
     dv=df['volume'].astype(float)*df['close'].astype(float)
     vlast=float(dv.iloc[-1]); prev=dv.iloc[-21:-1]
     if len(prev)>0 and float(prev.mean())>0: xavg=vlast/float(prev.mean())
    lb=self.__dict__.get('_live_bar',{}).get(s)
    if lb and int(lb.get('start',0))==live_start: vnow=float(lb.get('volume') or 0)*price
    xtxt=f'{xavg:.1f}x' if xavg is not None else '--'
    x_k=None if xavg is None else ('y' if xavg>=2.0 else ('d' if xavg<0.5 else None))
    sig=self.strategy.signal(s,eval_df); na=cooldowns.get(s,0)
    if sig is not None and int(sig.candle_start)<na: sig_txt='cd:'+str(int(-(-(na-live_start)//candle_ms))); sig_k='y'
    elif sig is not None and attempted.get(s)==int(sig.candle_start): sig_txt='done'; sig_k='c'
    elif sig is not None: sig_txt='BUY!'; sig_k='bg'; n_sig+=1
    else: sig_txt='wait'; sig_k='d'
    px_by[s]=price; n_ok+=1
    if above: n_above+=1
    grp=1 if sig_txt in ('BUY!','done') else (2 if above else 3)
    key=(rsi if rsi is not None else 999.0) if (grp==2 and sm=='ema_rsi') else abs(pct)
    d={'sym':(short,'buy' if sig_txt=='BUY!' else 'bw'),'px':(self._hb_pxa(price),None),'ema':(ema_txt,None),'vs':(vs_txt,vs_k),
       'rsi':(rsi_txt,rsi_k),'p24':(f'{pct24:+.2f}%','g' if pct24>=0 else 'r'),'vn':(self._hb_usd(vnow),None),'vl':(self._hb_usd(vlast),None),
       'xa':(xtxt,x_k),'sig':(sig_txt,sig_k)}
    rows.append((grp,key,d))
   except Exception as exc:
    rows.append((9,0.0,col(f'{short:<8} ERR({str(exc)[:60]})','r')))
  rows.sort(key=lambda r:(r[0],r[1]))
  # ---- open positions
  by={}
  for x in lots_all:
   if x.side=='LONG': by.setdefault(x.symbol,[]).append(x)
  pcols=[('sym','SYMBOL',8,'<'),('lots','LOTS',4,'>'),('avg','AVG ENTRY',13,'>'),('px','PRICE',13,'>'),('pnl','PNL %',8,'>'),('net','NET USDT',10,'>'),
         ('sl','SL loss/lim',11,'>'),('ex','EXIT',13,'<'),('od','ORDERS',8,'<')]
  for drop in ('avg','px'):
   if tw([(c[0],c[1],'',c[2],c[3]) for c in pcols])<=limit: break
   pcols=[c for c in pcols if c[0]!=drop]
  pos=[]
  for s in sorted(by):
   ls=by[s]; short=s[:-4] if s.endswith('USDT') else s; price=px_by.get(s)
   q=sum(x.qty for x in ls); avg=(sum(x.qty*x.entry_price for x in ls)/q) if q>0 else 0.0
   if not price:
    pos.append({'sym':(short,'bw'),'lots':(len(ls),None),'avg':(self._hb_pxa(avg),None),'px':('(no live price)','d'),'pnl':('',None),'net':('',None),'sl':('',None),'ex':('',None),'od':('',None)}); continue
   pnl=((price-avg)/avg*100) if avg>0 else 0.0
   net=sum(self._estimated_net(x,price) for x in ls)
   worst=max([(x.entry_price-price)/x.entry_price*100 for x in ls if x.entry_price>0] or [0.0])
   ready=sum(1 for x in ls if x.exit_armed and self._estimated_net(x,price)>0)
   held=sum(1 for x in ls if x.exit_armed and self._estimated_net(x,price)<=0)
   ex='CLOSING+HELD' if (ready and held) else ('CLOSING' if ready else ('HELD(<0)' if held else '-'))
   ex_k='g' if ready else ('y' if held else 'd')
   od=sorted({p.action for p in pend_all if p.symbol==s}); od_txt='/'.join(od) if od else '-'
   sl=f'{max(worst,0.0):.1f}/{st.stop_loss_pct:.0f}'; sl_k='r' if worst>=st.stop_loss_pct*0.6 else ('y' if worst>=st.stop_loss_pct*0.3 else None)
   pos.append({'sym':(short,'bw'),'lots':(len(ls),None),'avg':(self._hb_pxa(avg),None),'px':(self._hb_pxa(price),None),
    'pnl':(f'{pnl:+.2f}','g' if pnl>=0 else 'r'),'net':(f'{net:+.3f}','g' if net>=0 else 'r'),'sl':(sl,sl_k),'ex':(ex,ex_k),'od':(od_txt,'c' if od else 'd')})
  # ---- header block
  mode='LIVE TESTNET' if st.enable_live_trading and st.bybit_testnet else ('LIVE MAINNET' if st.enable_live_trading else 'PAPER')
  def _ws(w):
   try: return 'OK' if w.is_connected() else 'DOWN'
   except Exception: return '?'
  def _wsc(t): return col(t,'g' if t in ('OK','n/a') else 'r')
  ws_m=_ws(self.ws_market); ws_p=_ws(self.ws_private) if st.enable_live_trading else 'n/a'
  today=time.strftime('%Y-%m-%d',time.gmtime()); realized=None
  try:
   day_ms=calendar.timegm(time.strptime(today,'%Y-%m-%d'))*1000
   realized=sum(float(t['pnl']) for t in self.store.recent_trades(500) if t['closed_at_ms']>=day_ms)
  except Exception: pass
  real_txt='--' if realized is None else col(f'{realized:+.3f} USDT','g' if realized>=0 else 'r')
  try: kill_on=(st.log_dir.parent/st.kill_switch_file).exists()
  except Exception: kill_on=False
  loss_block=getattr(self,'_daily_loss_block_date',None)==today
  cap=f'/{st.max_total_open_lots}' if st.max_total_open_lots>0 else ''
  W=tw(cols); bar=col('='*W,'b'); thin=col('-'*W,'b')
  narrow=tty and tcols<100
  if narrow: rule=rule.replace('entry: ','buy: ').replace(' and profitable',', profit only').replace('exit: ','sell: ')
  out=[bar,
   col(f'HEARTBEAT {time.strftime("%H:%M:%S") if narrow else time.strftime("%Y-%m-%d %H:%M:%S")}','ttl')+f' | {mode.replace("LIVE ","")} | {sm} {int(st.timeframe)}m | SL {st.stop_loss_pct:.0f}%'+('' if narrow else f' | refresh {st.scanner_interval_seconds:g}s'),
   rule,
   ('WS m:' if narrow else 'WS market:')+_wsc(ws_m)+(' p:' if narrow else ' private:')+_wsc(ws_p)+(' | kill:' if narrow else ' | kill switch:')+col('ON' if kill_on else 'off','r' if kill_on else None)+' | loss-block:'+col('YES' if loss_block else 'no','r' if loss_block else None)+f' | lots:{len(lots_all)}{cap} | today:{real_txt}',
   col(f'{ema_name} ABOVE {n_above}/{n_ok}','g' if n_above else None) + (f' | RSI LOW(<{entry_rsi:.0f}): {n_low}' if entry_rsi is not None else '') + ' | BUY signals: '+col(str(n_sig),'bg' if n_sig else None) + (f' | warming up: {n_warm}' if n_warm else '') + (col(f' | last error: {str(self.last_error)[:50]}','r') if getattr(self,'last_error','') else '')]
  cap_pos=10 if (tty and HB_ROWS=='auto') else 999
  out.append(thin)
  if pos:
   out.append(col(f'OPEN POSITIONS ({len(by)} symbols, {len(lots_all)} lots)','ttl'))
   out.append(col(' '.join(cell(c[1],c[2],c[3]) for c in pcols),'hdr'))
   out+=[' '.join(cell(d[c[0]][0],c[2],c[3],d[c[0]][1]) for c in pcols) for d in pos[:cap_pos]]
   if len(pos)>cap_pos: out.append(col(f'  +{len(pos)-cap_pos} more positions hidden - HEARTBEAT_ROWS=all','d'))
  else: out.append(col('OPEN POSITIONS: none','d'))
  out.append(thin)
  out.append(col('MARKET  (buy signals first, then closest to entry)','ttl'))
  out.append(col(' '.join(cell(c[1],c[3],c[4]) for c in cols),'hdr'))
  out.append(col(' '.join(cell(c[2],c[3],c[4]) for c in cols),'d'))
  if HB_ROWS=='all' or (HB_ROWS=='auto' and not tty): show=len(rows)
  elif HB_ROWS.isdigit(): show=max(1,int(HB_ROWS))
  else:
   import re as _re
   wrapped=sum((len(_re.sub(r'\x1b\[[0-9;]*m','',l))-1)//max(20,tcols) for l in out)
   show=max(8,tlines-len(out)-wrapped-3)
  for r in rows[:show]:
   out.append(r[2] if isinstance(r[2],str) else ' '.join(cell(r[2][c[0]][0],c[3],c[4],r[2][c[0]][1]) for c in cols))
  hid=rows[show:]
  if hid:
   nw=sum(1 for r in hid if r[0]==9); nb=sum(1 for r in hid if r[0]==3)
   out.append(col(f'  +{len(hid)} hidden ({nb} below EMA, {nw} warming) - HEARTBEAT_ROWS=all','d'))
  out.append(bar)
  return '\n'.join(out)
 def scan_live_entries(self):
  # FIXED: this whole loop reads/mutates self.lots, self.pending, self.next_entry_allowed_candle
  # and self.last_scanner_entry_candle, and calls _check_exits/open_position, with NO locking --
  # racing against the WS callback threads which always hold self.lock. Wrapping it here closes
  # that gap; RLock is reentrant so the internal self.lock acquisitions inside _check_exits/
  # open_position remain safe nested inside this one. This does hold the lock across the
  # bybit.tickers() HTTP call too (a few hundred ms, once per SCANNER_INTERVAL_SECONDS), briefly
  # delaying WS-driven fill processing during that window -- an acceptable, bounded tradeoff for
  # closing a real dict-mutation race, not a busy/hot-path lock.
  with self.lock:
   self._scan_live_entries_locked()
 def _scan_live_entries_locked(self):
  import builtins
  print=(lambda *a,**k: builtins.print(*a,**k) if any('ERROR:' in str(x) for x in a) else None) if HB_TABLE_EVERY_S>0 else builtins.print  # new heartbeat table replaces the old monitor output
  if not hasattr(self,"last_scanner_entry_candle"):
   self.last_scanner_entry_candle={}

  try:
   market=self.bybit.tickers()
  except Exception as e:
   log.error('Failed to fetch tickers: %s (skipping this scan cycle)',e)
   return

  RED="\033[91m"
  GREEN="\033[92m"
  YELLOW="\033[93m"
  BLUE="\033[94m"
  CYAN="\033[96m"
  WHITE="\033[97m"
  RESET="\033[0m"
  DIM="\033[2m"

  sm=self.settings.strategy_mode
  ind1_label,ind2_label=("EMA_F","EMA_S") if sm=="ema_band" else ("EMA","--")

  print("\n"+BLUE+"="*128+RESET)
  print(
   CYAN+f"{sm.upper()} STRATEGY MONITOR"+RESET+
   " | "+time.strftime("%Y-%m-%d %H:%M:%S")+
   " | "+YELLOW+f"REFRESH {self.settings.scanner_interval_seconds:g}s"+RESET
  )
  print(BLUE+"="*128+RESET)

  print(
   CYAN+
   "{:<10}{:>16}{:>16}{:>16}{:>9}{:>10}{:>7}  {}".format(
    "ASSET","PRICE",ind1_label,ind2_label,"RSI","24H%","LOTS","SIGNAL"
   )+
   RESET
  )

  for s in self.settings.symbols:
   try:
    q=market.get(s,{}) or {}
    price=float(q.get("last") or q.get("price") or 0)
    pct24=float(q.get("pct24") or 0)

    df=self._frame(s)

    if df.empty:
     print(YELLOW+"{:<10}{:<22}".format(s,"--- WAIT (history) ---")+RESET)
     continue

    # ONE shared live dataframe.
    eval_df=self._live_ema_rsi_frame(s,price,df)
    row=eval_df.iloc[-1]

    if price>0:
     self._check_exits(s,price,row)

    # Signal is evaluated on the SAME dataframe displayed below.
    sig=self.strategy.signal(s,eval_df)

    if sm=="ema_band":
     ind1_val=float(row["ema_fast"]) if "ema_fast" in row and pd.notna(row["ema_fast"]) else float("nan")
     ind2_val=float(row["ema_slow"]) if "ema_slow" in row and pd.notna(row["ema_slow"]) else float("nan")
    else:
     ind1_val=float(row["ema"]) if "ema" in row and pd.notna(row["ema"]) else float("nan")
     ind2_val=float("nan")

    rsi_val=float(row["rsi"]) if "rsi" in row and pd.notna(row["rsi"]) else float("nan")

    hit=(sig is not None)
    in_cooldown=bool(hit and int(sig.candle_start)<self.next_entry_allowed_candle.get(s,0))
    scanner_attempted=bool(hit and self.last_scanner_entry_candle.get(s)==int(sig.candle_start))
    has_long_position=self.store.lot_count(s,"LONG")>0
    has_long_pending=any(
     p.action=="ENTRY" and
     p.symbol==s and
     p.side=="LONG" and
     p.status in ACTIVE
     for p in self.pending.values()
    )

    blocked_display=bool(
     hit and (scanner_attempted or in_cooldown)
     and not has_long_position
     and not has_long_pending
    )

     # Scanner is display/monitor only.
     # Entry execution is handled by _process_closed_candle()
     # on confirmed 15-minute candles.
    lots=self.store.lot_count(s,"LONG")

    price_txt=f"{price:>16.8f}"
    ind1_txt=f"{ind1_val:>16.6f}" if ind1_val==ind1_val else f"{'--':>16}"
    ind2_txt=f"{ind2_val:>16.6f}" if ind2_val==ind2_val else f"{'--':>16}"
    rsi_txt=f"{rsi_val:>9.2f}" if rsi_val==rsi_val else f"{'--':>9}"

    pct_color=GREEN if pct24>=0 else RED
    pct_txt=pct_color+f"{pct24:>9.2f}%"+RESET
    lots_txt=WHITE+f"{lots:>7}"+RESET

    if hit:
     signal=(
      "--- LONG BLOCKED (cooldown) ---" if in_cooldown else
      ("--- LONG BLOCKED ---" if blocked_display else "+++ LONG SIGNAL +++")
     )
    else:
     signal=DIM+"--- WAIT ---"+RESET

    print(
     WHITE+f"{s:<10}"+RESET+
     price_txt+
     ind1_txt+
     ind2_txt+
     rsi_txt+
     "  "+pct_txt+
     lots_txt+
     "  "+signal
    )

   except Exception as e:
    print(RED+f"{s:<10}ERROR: {e}"+RESET)

  print(BLUE+"="*128+RESET,flush=True)


 def stop(self):
     log.info('Stopping trading engine...')
     if self.settings.telegram_notify_start_stop:
         try: self.telegram.send(self.status_message('BOT STOPPED'))
         except Exception as e: log.error('Stop notification failed: %s', e)
     self.running=False
     try:
         if getattr(self,'ws_market',None) and getattr(self.ws_market,'ws',None):
             self.ws_market.ws.exit()
     except Exception as e:
         log.error('Error closing MarketStream: %s', e)
     try:
         if getattr(self,'ws_private',None) and getattr(self.ws_private,'ws',None):
             self.ws_private.ws.exit()
     except Exception as e:
         log.error('Error closing PrivateStream: %s', e)
     log.info('Trading engine stopped')

 def run(self):
     self.start()
     self.heartbeat()









