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
 t=threading.Thread(target=run,name='trading-engine',daemon=True); t.start(); ready.wait(); app=create_app(engine)
 try:
  from waitress import serve; serve(app,host=SETTINGS.dashboard_host,port=SETTINGS.dashboard_port,threads=4) # type: ignore
 finally: engine.stop(); t.join(timeout=10); store.close()
if __name__=='__main__': main()


