from __future__ import annotations
from dataclasses import dataclass,asdict
@dataclass
class Position:
    symbol:str; side:str; qty:float; entry_price:float; entry_time_ms:int
    entry_order_id:str=''; cycle_id:str=''; position_idx:int=0; managed:bool=True; unrealized_pnl:float=0.0; realized_pnl:float=0.0; entry_fee:float=0.0; entry_count:int=0
    def to_dict(self): return asdict(self)
@dataclass
class PendingOrder:
    order_id:str; order_link_id:str; symbol:str; side:str; action:str; position_idx:int; requested_qty:float
    filled_qty:float=0.0; avg_fill_price:float=0.0; fee:float=0.0; status:str='NEW'; reason:str=''; candle_start:int=0; created_at_ms:int=0; cycle_id:str=''; lot_id:str=''; processed_fill_qty:float=0.0; processed_fill_fee:float=0.0
    def to_dict(self): return asdict(self)
@dataclass
class EntryLot:
    lot_id:str; symbol:str; side:str; qty:float; entry_price:float; entry_time_ms:int
    entry_order_id:str=''; cycle_id:str=''; entry_fee:float=0.0; status:str='OPEN'; exit_order_id:str=''; exit_fee:float=0.0; exit_armed:bool=False; entry_candle_start:int=0
    def to_dict(self): return asdict(self)
@dataclass
class Trade:
    symbol:str; side:str; qty:float; entry_price:float; exit_price:float; pnl:float; fee:float
    opened_at_ms:int; closed_at_ms:int; cycle_id:str; reason:str; entry_order_id:str=''; exit_order_id:str=''
    def to_dict(self): return asdict(self)

