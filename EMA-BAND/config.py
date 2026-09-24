from __future__ import annotations
import os
from dataclasses import dataclass,field
from pathlib import Path
from dotenv import load_dotenv
ROOT=Path(__file__).resolve().parent; load_dotenv(ROOT/'.env')
def _bool(n,d):
 r=os.getenv(n); return d if r is None else r.strip().lower() in {'1','true','yes','on'}
def _float(n,d):
 r=os.getenv(n); return d if r is None else float(r)
def _int(n,d):
 r=os.getenv(n); return d if r is None else int(r)
def _str(n,d):
 r=os.getenv(n); return d if r is None else r.strip()
def _symbols(): return tuple(x.strip().upper() for x in os.getenv('SYMBOLS','BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT,ADAUSDT,AVAXUSDT,LINKUSDT,SUIUSDT,NEARUSDT,APTUSDT,LTCUSDT,DOTUSDT,BCHUSDT,TRXUSDT,UNIUSDT,FILUSDT,ETCUSDT,INJUSDT,WIFUSDT,RENDERUSDT,TIAUSDT,ARBUSDT,OPUSDT,ATOMUSDT,SEIUSDT,STXUSDT,ORDIUSDT,AAVEUSDT,ICPUSDT,GALAUSDT,KASUSDT,JASMYUSDT,WLDUSDT,ARUSDT,ALGOUSDT,RUNEUSDT,THETAUSDT,ENAUSDT,NOTUSDT').split(',') if x.strip())

@dataclass(frozen=True)
class Settings:
 bybit_api_key:str=field(default_factory=lambda:os.getenv('BYBIT_API_KEY','').strip())
 bybit_api_secret:str=field(default_factory=lambda:os.getenv('BYBIT_API_SECRET','').strip())
 bybit_testnet:bool=field(default_factory=lambda:_bool('BYBIT_TESTNET',True))
 enable_live_trading:bool=field(default_factory=lambda:_bool('ENABLE_LIVE_TRADING',False))
 category:str='linear'; timeframe:str='15'; symbols:tuple[str,...]=field(default_factory=_symbols)
 enable_long:bool=field(default_factory=lambda:_bool('ENABLE_LONG',True)); enable_short:bool=field(default_factory=lambda:_bool('ENABLE_SHORT',False))

 # --- Position sizing / caps ---
 position_size_pct:float=field(default_factory=lambda:_float('POSITION_SIZE_PCT',10.0))
 max_long_entries:int=field(default_factory=lambda:_int('MAX_LONG_ENTRIES',999))         # "Max trade per asset, 999"
 min_trade_usdt:float=field(default_factory=lambda:_float('MIN_TRADE_USDT',5.0))          # "Minimum position 5 usdt"
 max_order_notional_usdt:float=field(default_factory=lambda:_float('MAX_ORDER_NOTIONAL_USDT',0))  # 0 = disabled; hard cap per order as a fat-finger/config-bug guard

 # --- Leverage & risk ---
 leverage:int=field(default_factory=lambda:_int('LEVERAGE',3))                          # "Leverage 3x"
 stop_loss_pct:float=field(default_factory=lambda:_float('STOP_LOSS_PCT',80.0))         # "SL 80%" - hard circuit breaker, independent of strategy exit logic
 max_daily_loss_usdt:float=field(default_factory=lambda:_float('MAX_DAILY_LOSS_USDT',100))
 max_total_open_lots:int=field(default_factory=lambda:_int('MAX_TOTAL_OPEN_LOTS',50))  # 0=disabled; global cap across ALL symbols
 require_leverage_confirmation:bool=field(default_factory=lambda:_bool('REQUIRE_LEVERAGE_CONFIRMATION',True))  # fail-closed: block a symbol if its leverage could not be set
 max_entry_price_deviation_pct:float=field(default_factory=lambda:_float('MAX_ENTRY_PRICE_DEVIATION_PCT',1.0))  # reject entry if signal price is stale vs live ticker
 stuck_order_timeout_seconds:int=field(default_factory=lambda:_int('STUCK_ORDER_TIMEOUT_SECONDS',120))  # if an order submission throws and Bybit never shows a matching order, give up waiting after this long so it can be retried instead of blocking entries/exits forever

 # --- Strategy selection ---
 # 'ema_band'  = Strategy A: long entry when close is between EMA200/EMA210, cooldown N candles,
 #               exit only after N candles AND RSI>=exit_rsi AND net-profitable.
 # 'ema_rsi'   = Strategy B: long entry when close>EMA200 AND RSI<entry_rsi,
 #               exit when RSI>=exit_rsi AND net-profitable.
 strategy_mode:str=field(default_factory=lambda:_str('STRATEGY_MODE','ema_band'))

 ema_band_fast:int=field(default_factory=lambda:_int('EMA_BAND_FAST',200))
 ema_band_slow:int=field(default_factory=lambda:_int('EMA_BAND_SLOW',210))
 ema_band_rsi_period:int=field(default_factory=lambda:_int('EMA_BAND_RSI_PERIOD',20))
 ema_band_cooldown_candles:int=field(default_factory=lambda:_int('EMA_BAND_COOLDOWN_CANDLES',0))
 ema_band_exit_candles:int=field(default_factory=lambda:_int('EMA_BAND_EXIT_CANDLES',0))
 ema_band_exit_rsi:float=field(default_factory=lambda:_float('EMA_BAND_EXIT_RSI',75.0))

 ema_rsi_ema_period:int=field(default_factory=lambda:_int('EMA_RSI_EMA_PERIOD',200))
 ema_rsi_period:int=field(default_factory=lambda:_int('EMA_RSI_PERIOD',20))
 ema_rsi_entry_rsi:float=field(default_factory=lambda:_float('EMA_RSI_ENTRY_RSI',45.0))
 ema_rsi_exit_rsi:float=field(default_factory=lambda:_float('EMA_RSI_EXIT_RSI',80.0))
 ema_rsi_cooldown_candles:int=field(default_factory=lambda:_int('EMA_RSI_COOLDOWN_CANDLES',0))

 taker_fee_rate:float=field(default_factory=lambda:_float('TAKER_FEE_RATE',0.00055))

 # --- Timing ---
 scanner_interval_seconds:float=field(default_factory=lambda:_float('SCANNER_INTERVAL_SECONDS',120))  # "Scanner 120s"
 reconciliation_seconds:int=field(default_factory=lambda:_int('RECONCILIATION_SECONDS',5))
 pnl_review_minutes:int=field(default_factory=lambda:_int('PNL_REVIEW_MINUTES',30))
 max_slippage_bps:float=field(default_factory=lambda:_float('MAX_SLIPPAGE_BPS',50))

 # --- Telegram ---
 telegram_bot_token:str=field(default_factory=lambda:os.getenv('TELEGRAM_BOT_TOKEN','').strip())
 telegram_chat_id:str=field(default_factory=lambda:os.getenv('TELEGRAM_CHAT_ID','').strip())
 telegram_hourly_pnl:bool=field(default_factory=lambda:_bool('TELEGRAM_HOURLY_PNL',True))          # "PnL 1h"
 telegram_pnl_interval_seconds:int=field(default_factory=lambda:_int('TELEGRAM_PNL_INTERVAL_SECONDS',3600))
 telegram_notify_start_stop:bool=field(default_factory=lambda:_bool('TELEGRAM_NOTIFY_START_STOP',True))

 # --- Security ---
 kill_switch_file:str=field(default_factory=lambda:_str('KILL_SWITCH_FILE','EMERGENCY_STOP'))       # touch this file to block all new entries
 require_env_file_permissions_check:bool=field(default_factory=lambda:_bool('CHECK_ENV_PERMISSIONS',True))

 dashboard_host:str=field(default_factory=lambda:os.getenv('DASHBOARD_HOST','127.0.0.1')); dashboard_port:int=field(default_factory=lambda:_int('DASHBOARD_PORT',8080)); database_path:Path=ROOT/'data'/'trading.db'; log_dir:Path=ROOT/'logs'

 def validate(self):
  e=[]
  if self.category!='linear': e.append('category must be linear')
  if self.timeframe!='15': e.append('timeframe must be 15 minutes')
  if not self.symbols: e.append('SYMBOLS must not be empty')
  if not 0<self.position_size_pct<=100: e.append('POSITION_SIZE_PCT must be >0 and <=100')
  if self.max_long_entries<1: e.append('MAX_LONG_ENTRIES must be >=1')
  if self.min_trade_usdt<=0: e.append('MIN_TRADE_USDT must be >0')
  if self.max_order_notional_usdt<0: e.append('MAX_ORDER_NOTIONAL_USDT must be >=0 (0=disabled)')
  if not 1<=self.leverage<=25: e.append('LEVERAGE must be between 1 and 25')
  if not 1<=self.stop_loss_pct<=99: e.append('STOP_LOSS_PCT must be between 1 and 99')
  if self.max_daily_loss_usdt<0: e.append('MAX_DAILY_LOSS_USDT must be >=0 (0=disabled)')
  if self.max_total_open_lots<0: e.append('MAX_TOTAL_OPEN_LOTS must be >=0 (0=disabled)')
  if self.max_entry_price_deviation_pct<=0: e.append('MAX_ENTRY_PRICE_DEVIATION_PCT must be >0')
  if self.stuck_order_timeout_seconds<30: e.append('STUCK_ORDER_TIMEOUT_SECONDS must be >=30 (too short risks retrying an order that actually landed)')
  if self.strategy_mode not in ('ema_band','ema_rsi'): e.append("STRATEGY_MODE must be 'ema_band' or 'ema_rsi'")
  if self.ema_band_fast<=0 or self.ema_band_slow<=0: e.append('EMA_BAND_FAST/SLOW must be positive')
  if self.ema_band_fast==self.ema_band_slow: e.append('EMA_BAND_FAST and EMA_BAND_SLOW must differ')
  if self.ema_band_rsi_period<=0: e.append('EMA_BAND_RSI_PERIOD must be positive')
  if self.ema_band_cooldown_candles<0: e.append('EMA_BAND_COOLDOWN_CANDLES must be >=0')
  if self.ema_band_exit_candles<0: e.append('EMA_BAND_EXIT_CANDLES must be >=0')
  if not 0<self.ema_band_exit_rsi<=100: e.append('EMA_BAND_EXIT_RSI must be in (0,100]')
  if self.ema_rsi_ema_period<=0: e.append('EMA_RSI_EMA_PERIOD must be positive')
  if self.ema_rsi_period<=0: e.append('EMA_RSI_PERIOD must be positive')
  if not 0<=self.ema_rsi_entry_rsi<100: e.append('EMA_RSI_ENTRY_RSI must be in [0,100)')
  if not 0<self.ema_rsi_exit_rsi<=100: e.append('EMA_RSI_EXIT_RSI must be in (0,100]')
  if self.ema_rsi_cooldown_candles<0: e.append('EMA_RSI_COOLDOWN_CANDLES must be >=0')
  if self.taker_fee_rate<0: e.append('TAKER_FEE_RATE must be >=0')
  if self.scanner_interval_seconds<=0: e.append('SCANNER_INTERVAL_SECONDS must be >0')
  if self.scanner_interval_seconds<1: e.append('SCANNER_INTERVAL_SECONDS below 1s is not supported (risk of API rate-limit bans)')
  if self.reconciliation_seconds<5: e.append('RECONCILIATION_SECONDS must be >=5')
  if self.pnl_review_minutes<1: e.append('PNL_REVIEW_MINUTES must be >=1')
  if self.max_slippage_bps<=0: e.append('MAX_SLIPPAGE_BPS must be positive')
  if self.telegram_pnl_interval_seconds<60: e.append('TELEGRAM_PNL_INTERVAL_SECONDS must be >=60')
  if self.dashboard_host not in {'127.0.0.1','localhost','::1'}: e.append('dashboard must remain localhost')
  if self.enable_live_trading and (not self.bybit_api_key or not self.bybit_api_secret): e.append('live trading requires Bybit credentials')
  if not self.enable_long and not self.enable_short: e.append('at least one direction must be enabled')
  if self.enable_short: e.append('SHORT is intentionally disabled by this strategy build (long-only bot)')
  return e

def check_env_permissions(logger=None):
 """Reject a .env file that is accessible by group/other on POSIX systems.
 The file holds the Bybit API secret and Telegram bot token in plain text.
 No-op on Windows and if the file does not exist yet."""
 import stat,platform
 env_path=ROOT/'.env'
 if platform.system()=='Windows' or not env_path.exists():
  return
 try:
  mode=env_path.stat().st_mode
  if mode & (stat.S_IRWXG|stat.S_IRWXO):
     msg=(f"SECURITY ERROR: {env_path} is accessible by group/other "
        f"(mode {oct(mode)[-3:]}). It contains your Bybit API secret and "
        f"Telegram token. Run: chmod 600 {env_path}")
     if logger: logger.error(msg)
     raise PermissionError(msg)
 except OSError as exc:
   if isinstance(exc, PermissionError):
    raise

SETTINGS=Settings(); SETTINGS.log_dir.mkdir(parents=True,exist_ok=True); SETTINGS.database_path.parent.mkdir(parents=True,exist_ok=True)


