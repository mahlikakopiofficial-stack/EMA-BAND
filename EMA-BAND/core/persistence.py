from __future__ import annotations
import json,sqlite3,threading,time,logging
from pathlib import Path
from .models import Position,PendingOrder,EntryLot,Trade
from dataclasses import fields as _dc_fields

def _coerce(cls,payload):
    """Build a dataclass from a stored JSON payload, ignoring keys that no longer
    exist on the model (schema changed) so an old database row cannot crash startup."""
    known={f.name for f in _dc_fields(cls)}
    dropped=[k for k in payload if k not in known]
    if dropped:
        log.warning('%s: ignoring stale field(s) %s from stored row',cls.__name__,','.join(sorted(dropped)))
    return cls(**{k:v for k,v in payload.items() if k in known})

log = logging.getLogger('PERSISTENCE')
EPSILON = 1e-8  # ✅ NEW: Global epsilon for float comparisons

class Store:
    def __init__(self,path:Path):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True); self.lock=threading.RLock()
        self.db=sqlite3.connect(self.path,check_same_thread=False,timeout=10); self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL'); self.db.execute('PRAGMA busy_timeout=10000'); self._init()
    
    def _init(self):
        with self.lock:
            self.db.executescript('''CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,ts_ms INTEGER,kind TEXT,symbol TEXT,side TEXT,payload TEXT);
CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY AUTOINCREMENT,closed_at_ms INTEGER,symbol TEXT,side TEXT,qty REAL,entry_price REAL,exit_price REAL,pnl REAL,fee REAL,opened_at_ms INTEGER,cycle_id TEXT,reason TEXT,entry_order_id TEXT,exit_order_id TEXT);
CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY AUTOINCREMENT,ts_ms INTEGER,equity REAL,available REAL,realized REAL,unrealized REAL,open_positions INTEGER);
CREATE TABLE IF NOT EXISTS positions(symbol TEXT,side TEXT,payload TEXT,PRIMARY KEY(symbol,side));
CREATE TABLE IF NOT EXISTS pending_orders(order_id TEXT PRIMARY KEY,payload TEXT);
CREATE TABLE IF NOT EXISTS executions(exec_id TEXT PRIMARY KEY,ts_ms INTEGER,order_id TEXT,payload TEXT);
CREATE TABLE IF NOT EXISTS entry_lots(lot_id TEXT PRIMARY KEY,symbol TEXT,side TEXT,status TEXT,payload TEXT);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts_ms); CREATE INDEX IF NOT EXISTS idx_trades_closed ON trades(closed_at_ms); CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON snapshots(ts_ms);'''); self.db.commit()
    
    def save_lot(self,lot):
        with self.lock:
            self.db.execute(
                "INSERT INTO entry_lots(lot_id,symbol,side,status,payload) VALUES(?,?,?,?,?) "
                "ON CONFLICT(lot_id) DO UPDATE SET symbol=excluded.symbol,side=excluded.side,"
                "status=excluded.status,payload=excluded.payload",
                (lot.lot_id,lot.symbol,lot.side,lot.status,json.dumps(lot.to_dict()))
            )
            self.db.commit()

    def load_lots(self,status='OPEN'):
        with self.lock:
            rows=self.db.execute(
                'SELECT payload FROM entry_lots WHERE status=?',(status,)
            ).fetchall()
        return [_coerce(EntryLot,json.loads(r['payload'])) for r in rows]

    def delete_lot(self,lot_id):
        with self.lock:
            self.db.execute('DELETE FROM entry_lots WHERE lot_id=?',(lot_id,))
            self.db.commit()

    def lot_count(self,symbol,side='LONG'):
        with self.lock:
            row=self.db.execute(
                'SELECT COUNT(*) n FROM entry_lots WHERE symbol=? AND side=? AND status=?',
                (symbol,side,'OPEN')
            ).fetchone()
        return int(row['n'])

    def event(self,kind,payload,symbol=None,side=None):
        with self.lock: 
            self.db.execute('INSERT INTO events(ts_ms,kind,symbol,side,payload) VALUES(?,?,?,?,?)',
                           (int(time.time()*1000),kind,symbol,side,json.dumps(payload,default=str)))
            self.db.commit()
    
    def trade(self,t):
        d=t.to_dict()
        with self.lock: 
            self.db.execute('INSERT INTO trades(closed_at_ms,symbol,side,qty,entry_price,exit_price,pnl,fee,opened_at_ms,cycle_id,reason,entry_order_id,exit_order_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                           tuple(d[k] for k in ['closed_at_ms','symbol','side','qty','entry_price','exit_price','pnl','fee','opened_at_ms','cycle_id','reason','entry_order_id','exit_order_id']))
            self.db.commit()
    
    def save_position(self,p):
        with self.lock: 
            self.db.execute('INSERT INTO positions(symbol,side,payload) VALUES(?,?,?) ON CONFLICT(symbol,side) DO UPDATE SET payload=excluded.payload',
                           (p.symbol,p.side,json.dumps(p.to_dict())))
            self.db.commit()
    
    def delete_position(self,symbol,side):
        with self.lock: 
            self.db.execute('DELETE FROM positions WHERE symbol=? AND side=?',(symbol,side))
            self.db.commit()
    
    def load_positions(self):
        with self.lock: 
            rows=self.db.execute('SELECT payload FROM positions').fetchall()
        return [_coerce(Position,json.loads(r['payload'])) for r in rows]
    
    def save_pending(self,p):
        with self.lock: 
            self.db.execute('INSERT INTO pending_orders(order_id,payload) VALUES(?,?) ON CONFLICT(order_id) DO UPDATE SET payload=excluded.payload',
                           (p.order_id,json.dumps(p.to_dict())))
            self.db.commit()
    
    def delete_pending(self,order_id):
        with self.lock: 
            self.db.execute('DELETE FROM pending_orders WHERE order_id=?',(order_id,))
            self.db.commit()
    
    def load_pending(self):
        with self.lock: 
            rows=self.db.execute('SELECT payload FROM pending_orders').fetchall()
        return [_coerce(PendingOrder,json.loads(r['payload'])) for r in rows]
    
    def execution_totals(self,order_id):
        with self.lock:
            rows=self.db.execute(
                'SELECT payload FROM executions WHERE order_id=?',
                (order_id,)
            ).fetchall()
        
        qty=0.0
        fee=0.0
        notional=0.0
        
        for r in rows:
            d=json.loads(r['payload'])
            q=float(d.get('execQty') or 0)
            px=float(d.get('execPrice') or 0)
            f=float(d.get('execFee') or 0)
            qty += q
            fee += f
            notional += q*px
        
        # ✅ FIXED: Avoid division by zero
        if qty > EPSILON:
            avg_price = notional / qty
        else:
            avg_price = 0.0
        
        return qty, fee, avg_price

    def save_execution(self,exec_id,order_id,payload,ts_ms):
        with self.lock:
            cur=self.db.execute('INSERT OR IGNORE INTO executions(exec_id,ts_ms,order_id,payload) VALUES(?,?,?,?)',
                               (exec_id,ts_ms,order_id,json.dumps(payload,default=str)))
            self.db.commit()
            return cur.rowcount==1
    
    def snapshot(self,equity,available,realized,unrealized,open_positions):
        with self.lock: 
            self.db.execute('INSERT INTO snapshots(ts_ms,equity,available,realized,unrealized,open_positions) VALUES(?,?,?,?,?,?)',
                           (int(time.time()*1000),equity,available,realized,unrealized,open_positions))
            self.db.commit()
    
    def recent_trades(self,limit=25):
        with self.lock: 
            rows=self.db.execute('SELECT closed_at_ms,symbol,side,qty,entry_price,exit_price,pnl,fee,opened_at_ms,cycle_id,reason,entry_order_id,exit_order_id FROM trades ORDER BY closed_at_ms DESC LIMIT ?',
                               (limit,)).fetchall()
        return [dict(r) for r in rows]

    # NEW: surfaces execution-risk events (failed/uncertain/timed-out submissions,
    # hard stops, daily-loss/kill-switch trips) for the dashboard's Alerts panel.
    # These already land in the events table via self.store.event(...) -- this just
    # reads a filtered, recent slice of them back out.
    ALERT_KINDS=('EXIT_SUBMISSION_UNCERTAIN','ENTRY_SUBMISSION_UNCERTAIN','HARD_STOP_LOSS',
                 'EXIT_SUBMISSION_TIMEOUT','ENTRY_SUBMISSION_TIMEOUT')

    def recent_alerts(self,limit=30):
        placeholders=','.join('?'*len(self.ALERT_KINDS))
        with self.lock:
            rows=self.db.execute(
                f'SELECT ts_ms,kind,symbol,side,payload FROM events WHERE kind IN ({placeholders}) '
                f'ORDER BY id DESC LIMIT ?',
                (*self.ALERT_KINDS,limit)
            ).fetchall()
        out=[]
        for r in rows:
            try: data=json.loads(r['payload']) if r['payload'] else {}
            except Exception: data={}
            out.append({'ts_ms':r['ts_ms'],'kind':r['kind'],'symbol':r['symbol'],'side':r['side'],'data':data})
        return out

    
    def recent_snapshots(self,limit=240):
        with self.lock: 
            rows=self.db.execute('SELECT ts_ms,equity,available,realized,unrealized,open_positions FROM snapshots ORDER BY ts_ms DESC LIMIT ?',
                               (limit,)).fetchall()
        return list(reversed([dict(r) for r in rows]))
    
    def close(self):
        with self.lock: 
            try:
                self.db.close()
                log.info("✅ Database connection closed")
            except Exception as e:
                log.error("Error closing database: %s", e)

