from __future__ import annotations
import logging,requests,threading
log=logging.getLogger('TELEGRAM')
BOT_COMMANDS=[
    {'command':'start','description':'Allow new entries'},
    {'command':'stop','description':'Block new entries'},
    {'command':'status','description':'Show bot status'},
    {'command':'fullstatus','description':'Show complete bot and cloud status'},
    {'command':'health','description':'Show engine and websocket health'},
    {'command':'positions','description':'Show open positions'},
    {'command':'pnl','description':'Show account PnL'},
    {'command':'trades','description':'Show recent trades'},
    {'command':'pending','description':'Show pending orders'},
    {'command':'alerts','description':'Show recent alerts'},
    {'command':'scan','description':'Scan configured symbols'},
    {'command':'backtest','description':'Run strategy backtest'},
    {'command':'digitalocean','description':'Show DigitalOcean status'},
    {'command':'test','description':'Test Telegram notifications'},
    {'command':'help','description':'Show available commands'},
]
class Telegram:
    def __init__(self,token,chat_id): self.token=token.strip(); self.chat_id=chat_id.strip()
    @property
    def enabled(self): return bool(self.token and self.chat_id)
    def send(self,text):
        if not self.enabled: return False
        try:
            for start in range(0,len(text),3900):
                r=requests.post(f'https://api.telegram.org/bot{self.token}/sendMessage',json={'chat_id':self.chat_id,'text':text[start:start+3900]},timeout=10); r.raise_for_status()
            return True
        except Exception as exc: log.warning('Telegram send failed: %s',exc); return False
    def set_commands(self):
        if not self.enabled: return False
        try:
            response=requests.post(f'https://api.telegram.org/bot{self.token}/setMyCommands',json={'commands':BOT_COMMANDS},timeout=10)
            response.raise_for_status()
            payload=response.json()
            if not payload.get('ok'): raise RuntimeError('Telegram setMyCommands returned an unsuccessful response')
            return True
        except Exception as exc:
            log.warning('Telegram command menu registration failed: %s',exc)
            return False
    def start_command_listener(self, handler, stop_event):
        if not self.enabled: return None
        self.set_commands()
        thread=threading.Thread(target=self._command_loop,args=(handler,stop_event),name='telegram-commands',daemon=True)
        thread.start()
        return thread
    def _command_loop(self, handler, stop_event):
        offset=-1
        discarding_backlog=True
        while not stop_event.is_set():
            try:
                params={'timeout':25,'allowed_updates':['message']}
                if offset is not None: params['offset']=offset
                response=requests.get(f'https://api.telegram.org/bot{self.token}/getUpdates',params=params,timeout=30)
                response.raise_for_status()
                payload=response.json()
                if not payload.get('ok'): raise RuntimeError('Telegram getUpdates returned an unsuccessful response')
                for update in payload.get('result',[]):
                    offset=int(update.get('update_id',0))+1
                    if discarding_backlog: continue
                    message=update.get('message') or {}
                    chat=message.get('chat') or {}
                    if str(chat.get('id','')) != self.chat_id: continue
                    text=str(message.get('text') or '').strip()
                    if not text.startswith('/'): continue
                    parts=text.split()
                    command=parts[0].lower().split('@',1)[0]
                    reply=handler(command,parts[1:])
                    if reply: self.send(reply)
                if discarding_backlog:
                    offset=0 if offset==-1 else offset
                discarding_backlog=False
            except Exception as exc:
                log.warning('Telegram command listener failed: %s',exc)
                stop_event.wait(5)

