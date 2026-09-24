from __future__ import annotations
import logging,requests,threading
log=logging.getLogger('TELEGRAM')
class Telegram:
    def __init__(self,token,chat_id): self.token=token.strip(); self.chat_id=chat_id.strip()
    @property
    def enabled(self): return bool(self.token and self.chat_id)
    def send(self,text):
        if not self.enabled: return False
        try:
            r=requests.post(f'https://api.telegram.org/bot{self.token}/sendMessage',json={'chat_id':self.chat_id,'text':text},timeout=10); r.raise_for_status(); return True
        except Exception as exc: log.warning('Telegram send failed: %s',exc); return False
    def start_command_listener(self, handler, stop_event):
        if not self.enabled: return None
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
                    command=text.split()[0].lower().split('@',1)[0]
                    reply=handler(command)
                    if reply: self.send(reply)
                if discarding_backlog:
                    offset=0 if offset==-1 else offset
                discarding_backlog=False
            except Exception as exc:
                log.warning('Telegram command listener failed: %s',exc)
                stop_event.wait(5)

