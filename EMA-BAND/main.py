from __future__ import annotations
import logging,os,threading
from config import SETTINGS,check_env_permissions
from core.engine import Engine
from core.persistence import Store
from core.telegram import Telegram
from web.dashboard import create_app
logging.basicConfig(level=logging.INFO,format='%(asctime)s | %(levelname)s | %(name)s | %(message)s')
def main():
 if SETTINGS.require_env_file_permissions_check: check_env_permissions(logging.getLogger('SECURITY'))
 config_errors=SETTINGS.validate()
 if config_errors: raise RuntimeError('Configuration errors: '+'; '.join(config_errors))
 store=Store(SETTINGS.database_path); telegram=Telegram(SETTINGS.telegram_bot_token,SETTINGS.telegram_chat_id); engine=Engine(SETTINGS,store,telegram); startup_error=[]; ready=threading.Event()
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
  if command=='/help':
   return '/start - allow new entries\n/stop - block new entries\n/status - show bot status\n/help - show this message'
  if command=='/status':
   gate='BLOCKED' if kill_path.exists() else 'ENABLED'
   return engine.status_message('STATUS')+f'\nNew entries: {gate}\nOpen lots: {len(engine.lots)}'
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


