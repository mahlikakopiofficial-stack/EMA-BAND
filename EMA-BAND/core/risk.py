class RiskManager:
 def __init__(self,settings,store): self.settings=settings; self.store=store
 def validate_order(self,symbol,side,qty,positions,pending):
  if side!='LONG': return False,'only LONG is enabled'
  if not self.settings.enable_long: return False,'LONG trading disabled'
  if qty<=0: return False,'quantity must be positive'
  if any(p.symbol==symbol and p.side==side and p.status in {'NEW','PARTIAL','SUBMITTING'} for p in pending.values()): return False,'same-side entry pending'
  if any(p.symbol==symbol and not p.managed for p in positions.values()): return False,'unknown exchange position requires reconciliation'
  return True,'ok'

