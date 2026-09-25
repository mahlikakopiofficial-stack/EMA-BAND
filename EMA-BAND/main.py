from __future__ import annotations
import logging,os,threading
from datetime import datetime,timezone
from config import SETTINGS,check_env_permissions
from core.engine import Engine
from core.persistence import Store
from core.telegram import Telegram
from core.digitalocean import DigitalOceanClient
from web.dashboard import create_app
logging.basicConfig(level=logging.INFO,format='%(asctime)s | %(levelname)s | %(name)s | %(message)s')
def main():
 if SETTINGS.require_env_file_permissions_check: check_env_permissions(logging.getLogger('SECURITY'))
 config_errors=SETTINGS.validate()
 if config_errors: raise RuntimeError('Configuration errors: '+'; '.join(config_errors))
 store=Store(SETTINGS.database_path); telegram=Telegram(SETTINGS.telegram_bot_token,SETTINGS.telegram_chat_id); digitalocean=DigitalOceanClient(SETTINGS.digitalocean_api_token,SETTINGS.digitalocean_droplet_id); engine=Engine(SETTINGS,store,telegram); startup_error=[]; ready=threading.Event()
 def run():
  try: engine.start()
  except Exception as exc: engine.running=False; engine.last_error=str(exc); startup_error.append(exc); logging.critical('TRADING DISABLED: %s',exc,exc_info=True)
  finally: ready.set()
  if not startup_error:
   try: engine.heartbeat()
   except Exception as exc: engine.running=False; engine.last_error=str(exc); logging.critical('Trading engine stopped: %s',exc,exc_info=True)
 t=threading.Thread(target=run,name='trading-engine',daemon=True); t.start(); ready.wait()
 if startup_error:
  engine.stop(); store.close()
  raise RuntimeError('Trading engine failed to start') from startup_error[0]
 app=create_app(engine)
 command_stop=threading.Event()
 def telegram_command(command):
  kill_path=SETTINGS.log_dir.parent/SETTINGS.kill_switch_file
  def fmt_time(value):
   try: return datetime.fromtimestamp(float(value)/1000,timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
   except Exception: return '--'
  def health():
   try: market='UP' if engine.ws_market.is_connected() else 'DOWN'
   except Exception: market='UNKNOWN'
   if SETTINGS.enable_live_trading:
    try: private='UP' if engine.ws_private.is_connected() else 'DOWN'
    except Exception: private='UNKNOWN'
   else: private='DISABLED'
   return (f'Health: {"RUNNING" if engine.running else "STOPPED"}\n'
           f'Market stream: {market}\nPrivate stream: {private}\n'
           f'Last error: {engine.last_error or "none"}\n'
           f'Pending orders: {len(engine.pending)}\nOpen lots: {len(engine.lots)}')
  def positions():
   if not engine.positions: return 'Positions: none'
   rows=['Positions:']
   for position in engine.positions.values():
    rows.append(f'{position.symbol} {position.side} qty={position.qty:g} entry={position.entry_price:g} managed={position.managed}')
   return '\n'.join(rows)
  def pnl():
   try:
    equity,available=engine.bybit.get_balance()
    exchange_positions=engine.bybit.get_positions()
    unrealized=sum(float(row.get('unrealisedPnl') or 0) for row in exchange_positions)
   except Exception as exc:
    return f'PnL unavailable: {exc}'
   today=datetime.now(timezone.utc).date()
   realized=0.0
   for trade in engine.store.recent_trades(500):
    try:
     if datetime.fromtimestamp(float(trade.get('closed_at_ms',0))/1000,timezone.utc).date()==today:
      realized+=float(trade.get('pnl') or 0)
    except Exception: pass
   return f'PnL UTC today\nEquity: {equity:.4f} USDT\nAvailable: {available:.4f} USDT\nRealized: {realized:.4f} USDT\nUnrealized: {unrealized:.4f} USDT'
  def trades():
   rows=engine.store.recent_trades(5)
   if not rows: return 'Recent trades: none'
   return 'Recent trades:\n'+'\n'.join(f'{fmt_time(row.get("closed_at_ms"))} {row.get("symbol")} {row.get("side")} pnl={float(row.get("pnl") or 0):.4f}' for row in rows)
  def pending():
   if not engine.pending: return 'Pending orders: none'
   return 'Pending orders:\n'+'\n'.join(f'{row.symbol} {row.action} {row.status} qty={row.requested_qty:g}' for row in list(engine.pending.values())[:10])
  def alerts():
   rows=engine.store.recent_alerts(5)
   if not rows: return 'Recent alerts: none'
   return 'Recent alerts:\n'+'\n'.join(f'{fmt_time(row.get("ts_ms"))} {row.get("kind")} {row.get("symbol") or ""}' for row in rows)
  def scan():
   try:
    line=engine._ema_status_line()
    colored=line.replace('=WARMUP','🔵 =WARMUP').replace('SIGNAL','🟢 SIGNAL').replace(' wait',' 🟡 wait').replace('=ERR(','🔴 =ERR(')
    return 'Scanner: ACTIVE\n'+colored
   except Exception as exc: return f'Scanner: ERROR\n{exc}'
  def full_status():
   gate='BLOCKED' if kill_path.exists() else 'ENABLED'
   return (engine.status_message('FULL STATUS')+'\n'
     +health()+'\n'
     +f'New entries: {gate}\n'
     +f'Open positions: {len(engine.positions)}\n'
     +pnl()+'\n'
     +positions()+'\n'
     +pending()+'\n'
           +scan()+'\n'
     +digitalocean.summary())
  if command=='/help':
   return ('/start - allow new entries\n/stop - block new entries\n/status - show bot status\n/fullstatus - complete bot and cloud status\n'
           '/health - show streams and errors\n/positions - show open positions\n/pnl - show account PnL\n'
       '/trades - show recent trades\n/pending - show pending orders\n/alerts - show recent alerts\n'
           '/scan - scan all configured symbols now\n'
       '/digitalocean - show DigitalOcean connection, billing, and droplets\n'
       '/do - alias for /digitalocean\n/test - verify Telegram notifications\n/help - show this message')
  if command=='/status':
   gate='BLOCKED' if kill_path.exists() else 'ENABLED'
   return engine.status_message('STATUS')+f'\nNew entries: {gate}\nOpen lots: {len(engine.lots)}'
  if command=='/fullstatus': return full_status()
  if command=='/health': return health()
  if command=='/positions': return positions()
  if command=='/pnl': return pnl()
  if command=='/trades': return trades()
  if command=='/pending': return pending()
  if command=='/alerts': return alerts()
  if command=='/scan': return scan()
  if command in {'/digitalocean','/do'}: return digitalocean.summary()
  if command=='/test': return f'✅ Telegram test successful\nUTC: {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")}'
  if command=='/stop':
   kill_path.touch(exist_ok=True)
   return '🛑 New entries blocked. Existing positions remain open and are still managed.'
  if command=='/start':
   if not engine.running: return '⚠️ Engine is not running. Use systemd to restart the bot process.'
   kill_path.unlink(missing_ok=True)
   return '✅ New entries enabled.'
  return 'Unknown command. Send /help.'
 command_thread=telegram.start_command_listener(telegram_command,command_stop) if SETTINGS.telegram_commands_enabled else None
 try:
  from waitress import serve; serve(app,host=SETTINGS.dashboard_host,port=SETTINGS.dashboard_port,threads=4) # type: ignore
 finally:
  command_stop.set()
  engine.stop(); t.join(timeout=10)
  if command_thread: command_thread.join(timeout=2)
  store.close()
if __name__=='__main__': main()


