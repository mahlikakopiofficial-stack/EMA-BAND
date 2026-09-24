from __future__ import annotations
import logging,requests
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

