from __future__ import annotations

from flask import Flask, jsonify, render_template_string, request
import math
import time
from datetime import datetime, timezone


HTML = r'''
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>EMA + RSI Pro Dashboard</title>
<script src="https://unpkg.com/lightweight-charts@4.2.3/dist/lightweight-charts.standalone.production.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
:root{
 --bg:#07101c; --panel:#0d1726; --panel2:#101d2e; --line:#203249;
 --text:#eaf2fb; --muted:#7f91aa; --cyan:#63c7ff; --green:#32d296;
 --red:#ff6d7f; --yellow:#f6c85f; --purple:#9c8cff; --shadow:0 20px 40px rgba(0,0,0,.24);
}
*{box-sizing:border-box}
body{margin:0;background:radial-gradient(circle at 85% 0%,#102742 0,#07101c 35%);color:var(--text);font-family:Inter,Segoe UI,Arial,sans-serif}
.wrap{max-width:1680px;margin:auto;padding:18px}
.top{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap;margin-bottom:16px}
.brand h1{margin:0;font-size:25px;letter-spacing:.2px}.brand p{margin:5px 0 0;color:var(--muted);font-size:12px}
.statusbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.pill{border:1px solid var(--line);background:rgba(13,23,38,.92);padding:8px 11px;border-radius:10px;font-size:12px;color:var(--muted)}
.pill b{color:var(--text)}.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--green);margin-right:6px;box-shadow:0 0 10px rgba(50,210,150,.7)}
.dot.off{background:var(--red);box-shadow:0 0 10px rgba(255,109,127,.6)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(165px,1fr));gap:10px;margin-bottom:12px}
.card{background:rgba(13,23,38,.96);border:1px solid var(--line);border-radius:20px;box-shadow:var(--shadow);padding:20px}
.metric .label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}.metric .val{margin-top:5px;font-size:20px;font-weight:750}.metric .sub{margin-top:4px;font-size:11px;color:var(--muted)}
.layout{display:grid;grid-template-columns:minmax(0,2.35fr) minmax(340px,1fr);gap:12px}
.chart-card{padding:20px}.chart-head{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;flex-wrap:wrap}
.chart-title{display:flex;align-items:center;gap:10px}.chart-title h2{margin:0;font-size:17px}.chart-title .price{font-size:18px;font-weight:800}.up{color:var(--green)}.down{color:var(--red)}
.controls{display:flex;gap:7px;flex-wrap:wrap}.select,.btn{background:#0a1524;color:var(--text);border:1px solid var(--line);border-radius:8px;padding:7px 9px;font-size:12px;cursor:pointer}.btn.active{border-color:#477da5;background:#12283e}.select:focus,.btn:focus{outline:none;border-color:var(--cyan)}
.chart-box{height:clamp(280px,42vh,520px);margin-top:8px}.subchart{height:clamp(90px,14vh,150px);margin-top:8px}.chart-footer{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;color:var(--muted);font-size:11px;margin-top:7px}
.side{display:flex;flex-direction:column;gap:12px}.section-title{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}.section-title h3{margin:0;font-size:15px}.mini{font-size:11px;color:var(--muted)}
.position{border:1px solid var(--line);background:linear-gradient(180deg,#102238,#0d1726);border-radius:12px;padding:12px;margin-bottom:9px}.position:last-child{margin-bottom:0}
.posrow{display:grid;grid-template-columns:1fr 1fr;gap:9px}.poscell .l{font-size:10px;color:var(--muted)}.poscell .v{margin-top:3px;font-weight:700;font-size:20px}.pnl{font-size:22px!important}.green{color:var(--green)}.red{color:var(--red)}.yellow{color:var(--yellow)}
.watch{padding:0;overflow:hidden}.watch table,.trades table{width:100%;border-collapse:collapse}.watch th,.watch td,.trades th,.trades td{padding:8px 9px;border-bottom:1px solid rgba(32,50,73,.7);font-size:11px;text-align:left}.watch th,.trades th{color:var(--muted);font-weight:650;position:sticky;top:0;background:#0d1726}.watch tbody tr{cursor:pointer}.watch tbody tr:hover{background:#102236}.watch .selected{background:#102a42}
.move{font-weight:700}.signal{font-weight:700}.signal.wait{color:var(--muted)}.signal.open{color:var(--green)}.signal.signal{color:var(--yellow)}
.scroll{max-height:455px;overflow:auto}.empty{padding:18px;color:var(--muted);text-align:center}.error{color:var(--red)}
.footer{margin-top:12px;color:var(--muted);font-size:10px;text-align:right}
.alerts{padding:0;overflow:hidden}.alert-row{display:flex;gap:8px;align-items:flex-start;padding:10px 14px;border-bottom:1px solid rgba(32,50,73,.5);font-size:11px}.alert-row:last-child{border-bottom:none}
.alert-icon{font-size:14px;line-height:1.3}.alert-body{flex:1;min-width:0}.alert-title{font-weight:700;color:var(--text)}.alert-time{color:var(--muted);font-size:10px;margin-top:2px}
.alert-row.warn{background:rgba(246,200,95,.06)}.alert-row.crit{background:rgba(255,109,127,.08)}
@media(min-width:1900px){.wrap{max-width:2200px}.layout{grid-template-columns:minmax(0,2.6fr) minmax(380px,1fr)}.chart-box{height:clamp(280px,48vh,620px)}}
@media(max-width:1200px){.cards{grid-template-columns:repeat(auto-fit,minmax(140px,1fr))}.layout{grid-template-columns:1fr}.chart-box{height:clamp(260px,40vh,460px)}}
@media(max-width:650px){.wrap{padding:10px}.cards{grid-template-columns:repeat(2,1fr)}.chart-box{height:clamp(220px,34vh,360px)}.posrow{grid-template-columns:1fr 1fr}}
</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <div class="brand">
      <h1>EMA + RSI PRO DASHBOARD</h1>
      <p>Live market view • Candlesticks • EMA • Position P&L • Watchlist</p>
    </div>
    <div class="statusbar">
      <div class="pill"><span id="wsDot" class="dot"></span><b id="wsText">CONNECTING</b></div>
      <div class="pill">Mode: <b id="mode">—</b></div>
      <div class="pill">Strategy: <b>EMA + RSI</b></div>
      <div class="pill">Strategy TF: <b id="strategyTf">—</b></div>
    </div>
  </div>

  <div class="cards">
    <div class="card metric"><div class="label">Equity</div><div class="val" id="equity">—</div><div class="sub">USDT</div></div>
    <div class="card metric"><div class="label">Available</div><div class="val" id="available">—</div><div class="sub">USDT</div></div>
    <div class="card metric"><div class="label">Unrealized P&L</div><div class="val" id="unrealized">—</div><div class="sub">Current positions</div></div>
    <div class="card metric"><div class="label">Realized P&L</div><div class="val" id="realized">—</div><div class="sub">Recent closed trades</div></div>
    <div class="card metric"><div class="label">Open Lots</div><div class="val" id="openLots">0</div><div class="sub">Tracked by bot</div></div>
    <div class="card metric"><div class="label">Last Update</div><div class="val" id="lastUpdate">—</div><div class="sub" id="errorText">System normal</div></div>
  </div>

  <div class="layout">
    <main class="card chart-card">
      <div class="chart-head">
        <div class="chart-title">
          <h2 id="chartSymbol">—</h2>
          <span class="price" id="chartPrice">—</span>
          <span class="move" id="chartMove">—</span>
        </div>
        <div class="controls">
          <select id="symbolSelect" class="select"></select>
          <select id="tfSelect" class="select">
            <option value="1">1m</option><option value="3">3m</option><option value="5">5m</option><option value="15" selected>15m</option>
            <option value="15">15m</option><option value="30">30m</option><option value="60">1h</option><option value="240">4h</option>
          </select>
          <button class="btn active" id="emaBtn">EMA 200</button>
        </div>
      </div>
      <div class="chart-box" id="priceChart"></div>
      <div class="subchart" id="rsiChart"></div>
      <div class="chart-footer">
        <span id="candleInfo">Candles: —</span>
        <span id="emaInfo">EMA200: —</span>
        <span id="rsiInfo">RSI20: —</span>
        <span>Chart timeframe is display-only and does not change strategy settings.</span>
      </div>
    </main>

    <aside class="side">
      <section class="card watch alerts">
        <div class="section-title" style="padding:20px 20px 0"><h3>⚠️ Execution Alerts</h3><span class="mini" id="alertCount">0</span></div>
        <div class="scroll" style="max-height:180px" id="alerts"><div class="empty">No alerts — all clear</div></div>
      </section>

      <section class="card">
        <div class="section-title"><h3>Current Position P&L</h3><span class="mini" id="positionCount">0 positions</span></div>
        <div id="positions"><div class="empty">No open positions</div></div>
      </section>

      <section class="card watch">
        <div class="section-title" style="padding:20px 20px 0"><h3>Live Asset Watchlist</h3><span class="mini">click an asset</span></div>
        <div class="scroll">
          <table>
            <thead><tr><th>Asset</th><th>Price</th><th>Move</th><th>EMA200</th><th>RSI20</th><th>Status</th></tr></thead>
            <tbody id="watchlist"></tbody>
          </table>
        </div>
      </section>

      <section class="card trades">
        <div class="section-title"><h3>Recent Trades</h3><span class="mini">latest 8</span></div>
        <div class="scroll" style="max-height:220px">
          <table><thead><tr><th>Time</th><th>Asset</th><th>Side</th><th>P&L</th></tr></thead><tbody id="trades"></tbody></table>
        </div>
      </section>
    </aside>
  </div>

  <div class="footer">Dashboard-only upgrade • No strategy/order code required to use these views</div>
</div>

<script>
let state=null;
let chart=null;
let candleSeries=null;
let emaSeries=null;
let rsiChart=null;
let rsiSeries=null;
let selectedSymbol=null;
let selectedTf='5';
let lastPrices={};
let lastCandleKey='';

function num(v,d=2){const n=Number(v);return Number.isFinite(n)?n.toFixed(d):'—'}
function signed(v,d=2){const n=Number(v);if(!Number.isFinite(n))return'—';return (n>=0?'+':'')+n.toFixed(d)}
function cls(v){return Number(v)>=0?'green':'red'}
function esc(v){return String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[m]))}

function buildCharts(){
  chart=LightweightCharts.createChart(document.getElementById('priceChart'),{
    layout:{background:{type:'solid',color:'#0d1726'},textColor:'#8ea0b8'},
    grid:{vertLines:{color:'#18283b'},horzLines:{color:'#18283b'}},
    rightPriceScale:{borderColor:'#203249'},
    timeScale:{borderColor:'#203249',timeVisible:true,secondsVisible:false},
    crosshair:{mode:LightweightCharts.CrosshairMode.Normal}
  });
  candleSeries=chart.addCandlestickSeries({upColor:'#32d296',downColor:'#ff6d7f',borderUpColor:'#32d296',borderDownColor:'#ff6d7f',wickUpColor:'#32d296',wickDownColor:'#ff6d7f'});
  emaSeries=chart.addLineSeries({color:'#63c7ff',lineWidth:2,priceLineVisible:false,lastValueVisible:true});

  rsiChart=LightweightCharts.createChart(document.getElementById('rsiChart'),{
    layout:{background:{type:'solid',color:'#0d1726'},textColor:'#7488a4'},
    grid:{vertLines:{color:'#18283b'},horzLines:{color:'#18283b'}},
    rightPriceScale:{borderColor:'#203249',scaleMargins:{top:.08,bottom:.08}},
    timeScale:{visible:false,borderColor:'#203249'}
  });
  rsiSeries=rsiChart.addLineSeries({color:'#9c8cff',lineWidth:2,priceLineVisible:false,lastValueVisible:true});

  // FIXED: price chart and RSI subchart were two independent LightweightCharts
  // instances with no shared time axis -- scrolling/zooming one never moved the
  // other, so they only "matched" if you never touched either. Subscribing each
  // chart's visible logical range to drive the other keeps them permanently
  // aligned to the same candles. The syncing flag prevents the mutual
  // subscription from feeding back into an infinite update loop.
  let syncingRange=false;
  chart.timeScale().subscribeVisibleLogicalRangeChange(range=>{
    if(syncingRange||!range)return; syncingRange=true;
    try{rsiChart.timeScale().setVisibleLogicalRange(range)}catch(e){}
    syncingRange=false;
  });
  rsiChart.timeScale().subscribeVisibleLogicalRangeChange(range=>{
    if(syncingRange||!range)return; syncingRange=true;
    try{chart.timeScale().setVisibleLogicalRange(range)}catch(e){}
    syncingRange=false;
  });
  chart.subscribeCrosshairMove(param=>{
    if(param.time)rsiChart.setCrosshairPosition(0,param.time,rsiSeries); else rsiChart.clearCrosshairPosition();
  });
  rsiChart.subscribeCrosshairMove(param=>{
    if(param.time)chart.setCrosshairPosition(0,param.time,candleSeries); else chart.clearCrosshairPosition();
  });

  window.addEventListener('resize',()=>{chart.resize(document.getElementById('priceChart').clientWidth,document.getElementById('priceChart').clientHeight);rsiChart.resize(document.getElementById('rsiChart').clientWidth,document.getElementById('rsiChart').clientHeight)});
}

function chooseSymbol(s){selectedSymbol=s;document.getElementById('symbolSelect').value=s;loadMarket(true)}

async function loadState(){
  try{
    const r=await fetch('/api/dashboard/state?ts='+Date.now(),{cache:'no-store'});
    const d=await r.json(); state=d;
    if(!selectedSymbol && d.symbols?.length) selectedSymbol=d.symbols[0];
    const sel=document.getElementById('symbolSelect');
    if(sel.options.length!==d.symbols.length){sel.innerHTML=d.symbols.map(s=>`<option value="${s}">${s}</option>`).join('')}
    sel.value=selectedSymbol||d.symbols[0];
    document.getElementById('mode').textContent=d.mode||'—';
    document.getElementById('strategyTf').textContent=(d.strategy_timeframe||'15')+'m';
    document.getElementById('equity').textContent=num(d.equity);
    document.getElementById('available').textContent=num(d.available);
    document.getElementById('unrealized').textContent=signed(d.unrealized_pnl,4); document.getElementById('unrealized').className='val '+cls(d.unrealized_pnl);
    document.getElementById('realized').textContent=signed(d.realized_pnl,4); document.getElementById('realized').className='val '+cls(d.realized_pnl);
    document.getElementById('openLots').textContent=d.positions.length;
    document.getElementById('lastUpdate').textContent=new Date().toLocaleTimeString();
    document.getElementById('errorText').textContent=d.last_error||'System normal';
    const dot=document.getElementById('wsDot'); const wt=document.getElementById('wsText');
    dot.classList.toggle('off',!d.ws_ok); wt.textContent=d.ws_ok?'CONNECTED':'CHECK';

    document.getElementById('positions').innerHTML=d.positions.length?d.positions.map(p=>{
      const pnl=Number(p.unrealized_pnl||0); return `<div class="position"><div class="posrow"><div class="poscell"><div class="l">ASSET / SIDE</div><div class="v">${esc(p.symbol)} <span class="${String(p.side).toLowerCase()==='long'?'green':'red'}">${esc(p.side)}</span></div></div><div class="poscell"><div class="l">P&L</div><div class="v pnl ${cls(pnl)}">${signed(pnl,4)} USDT</div></div><div class="poscell"><div class="l">ENTRY</div><div class="v">${num(p.entry_price,8)}</div></div><div class="poscell"><div class="l">MARK</div><div class="v">${num(p.mark_price,8)}</div></div><div class="poscell"><div class="l">QTY</div><div class="v">${num(p.qty,8)}</div></div><div class="poscell"><div class="l">RETURN</div><div class="v ${cls(pnl)}">${signed(p.pnl_pct,2)}%</div></div></div></div>`;
    }).join(''):'<div class="empty">No open positions</div>';
    document.getElementById('positionCount').textContent=d.positions.length+' position'+(d.positions.length===1?'':'s');

    document.getElementById('trades').innerHTML=d.trades.length?d.trades.slice(0,8).map(t=>`<tr><td>${new Date(t.closed_at_ms).toLocaleTimeString()}</td><td>${esc(t.symbol)}</td><td>${esc(t.side)}</td><td class="${cls(t.pnl)}">${signed(t.pnl,4)}</td></tr>`).join(''):'<tr><td colspan="4" class="empty">No trades</td></tr>';

    renderWatchlist(d.tickers||{},d.indicators||{});
    renderAlerts(d.alerts||[]);
  }catch(e){document.getElementById('errorText').textContent='Dashboard API unavailable';console.error(e)}
}

const ALERT_META={
  'EXIT_SUBMISSION_UNCERTAIN':{icon:'⚠️',label:'Exit submission failed',cls:'crit'},
  'ENTRY_SUBMISSION_UNCERTAIN':{icon:'❗',label:'Entry submission uncertain',cls:'crit'},
  'HARD_STOP_LOSS':{icon:'🛑',label:'Hard stop-loss triggered',cls:'crit'},
  'EXIT_SUBMISSION_TIMEOUT':{icon:'⏱️',label:'Exit order timed out',cls:'warn'},
  'ENTRY_SUBMISSION_TIMEOUT':{icon:'⏱️',label:'Entry order timed out',cls:'warn'}
};
function renderAlerts(alerts){
  document.getElementById('alertCount').textContent=alerts.length;
  if(!alerts.length){document.getElementById('alerts').innerHTML='<div class="empty">No alerts — all clear</div>';return}
  document.getElementById('alerts').innerHTML=alerts.map(a=>{
    const meta=ALERT_META[a.kind]||{icon:'ℹ️',label:a.kind,cls:'warn'};
    const detail=a.data&&a.data.error?esc(String(a.data.error).slice(0,90)):'';
    return `<div class="alert-row ${meta.cls}"><div class="alert-icon">${meta.icon}</div><div class="alert-body"><div class="alert-title">${meta.label}${a.symbol?' · '+esc(a.symbol):''}</div>${detail?`<div class="alert-time">${detail}</div>`:''}<div class="alert-time">${new Date(a.ts_ms).toLocaleTimeString()}</div></div></div>`;
  }).join('');
}

function renderWatchlist(tickers,inds){
  const rows=Object.keys(tickers).map(symbol=>{
    const q=tickers[symbol]||{};const ind=inds[symbol]||{};const price=Number(q.last||0);const move=lastPrices[symbol]==null?0:((price-lastPrices[symbol])/lastPrices[symbol])*100;lastPrices[symbol]=price;
    const open=(state?.positions||[]).some(p=>p.symbol===symbol); const signal=ind.signal?'SIGNAL':(open?'OPEN':'WAIT');
    return `<tr class="${symbol===selectedSymbol?'selected':''}" onclick="chooseSymbol('${symbol}')"><td><b>${symbol.replace('USDT','')}</b></td><td>${num(price,ind.decimals||4)}</td><td class="move ${cls(move)}">${signed(move,3)}%</td><td>${num(ind.ema,ind.decimals||4)}</td><td>${num(ind.rsi,2)}</td><td class="signal ${signal.toLowerCase()}">${signal}</td></tr>`;
  }).join('');
  document.getElementById('watchlist').innerHTML=rows||'<tr><td colspan="6" class="empty">No market data</td></tr>';
}

async function loadMarket(force=false){
  if(!selectedSymbol)return;
  try{
    const r=await fetch('/api/dashboard/candles?symbol='+encodeURIComponent(selectedSymbol)+'&interval='+encodeURIComponent(selectedTf)+'&limit=260&ts='+Date.now(),{cache:'no-store'});
    const d=await r.json();
    document.getElementById('chartSymbol').textContent=d.symbol;
    document.getElementById('chartPrice').textContent=num(d.live_price,d.decimals);
    const move=Number(d.move_pct||0); document.getElementById('chartMove').textContent=signed(move,3)+'%';document.getElementById('chartMove').className='move '+cls(move);
    document.getElementById('emaInfo').textContent='EMA200: '+num(d.ema200,d.decimals);
    document.getElementById('rsiInfo').textContent='RSI20: '+num(d.rsi20,2);
    document.getElementById('candleInfo').textContent=d.candles.length+' candles • '+selectedTf+'m';

    const candles=d.candles.map(x=>({time:x.time,open:x.open,high:x.high,low:x.low,close:x.close}));
    const ema=d.candles.filter(x=>x.ema!=null).map(x=>({time:x.time,value:x.ema}));
    const rsi=d.candles.filter(x=>x.rsi!=null).map(x=>({time:x.time,value:x.rsi}));

    candleSeries.setData(candles); emaSeries.setData(ema); rsiSeries.setData(rsi);
    if(force||lastCandleKey!==selectedSymbol+'|'+selectedTf){chart.timeScale().fitContent();rsiChart.timeScale().fitContent();lastCandleKey=selectedSymbol+'|'+selectedTf}
  }catch(e){console.error(e)}
}

document.getElementById('symbolSelect').addEventListener('change',e=>chooseSymbol(e.target.value));
document.getElementById('tfSelect').addEventListener('change',e=>{selectedTf=e.target.value;loadMarket(true)});

buildCharts();
loadState();loadMarket(true);
setInterval(loadState,1000);
setInterval(loadMarket,2000);
</script>
</body>
</html>
'''


def _ema(values, period=200):
    if not values:
        return None
    k = 2.0 / (period + 1.0)
    e = float(values[0])
    for v in values[1:]:
        e = (float(v) * k) + (e * (1.0 - k))
    return e


def _rsi(values, period=20):
    if len(values) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(values)):
        delta = float(values[i]) - float(values[i - 1])
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _decimals(symbol):
    symbol = str(symbol or "")
    if symbol in {"BTCUSDT"}:
        return 2
    if symbol in {"ETHUSDT", "BNBUSDT", "SOLUSDT", "LTCUSDT", "AAVEUSDT", "HYPEUSDT"}:
        return 2
    if symbol.startswith(("DOGE", "XRP", "ADA", "TRX", "DOT", "ATOM", "SUI", "MNT", "APT", "ARB", "NEAR", "FIL", "ICP", "HBAR", "ALGO", "OP")):
        return 4
    if symbol.startswith(("SHIB", "PEPE")):
        return 8
    return 4


def _interval_ms(interval):
    return int(interval) * 60 * 1000


def _get_today_realized(trades):
    today = datetime.now(timezone.utc).date()
    total = 0.0
    for t in trades:
        try:
            dt = datetime.fromtimestamp(float(t.get("closed_at_ms", 0)) / 1000.0, timezone.utc)
            if dt.date() == today:
                total += float(t.get("pnl") or 0)
        except Exception:
            continue
    return total


def _safe_status(engine):
    error = getattr(engine, "last_error", None)
    running = getattr(engine, "running", False)
    return {
        "status": "TRADING DISABLED" if error else ("RUNNING" if running else "STOPPED"),
        "reason": str(error or ""),
        "trading_enabled": bool(running and not error),
    }


def create_app(engine):
    app = Flask(__name__)

    @app.get("/")
    def home():
        return render_template_string(HTML)

    @app.get("/api/state")
    def state_compat():
        return state()

    @app.get("/api/dashboard/state")
    def state():
        try:
            equity, available = engine.bybit.get_balance()
        except Exception:
            equity, available = 0.0, 0.0

        mode = (
            "LIVE TESTNET" if engine.settings.bybit_testnet
            else "LIVE MAINNET"
        ) if engine.settings.enable_live_trading else "ORDER OFF"

        symbols = list(getattr(engine.settings, "symbols", []))

        try:
            tickers = engine.bybit.tickers()
        except Exception:
            tickers = {}

        trades = engine.store.recent_trades(50)
        positions = []
        total_unrealized = 0.0
        indicators = {}

        for symbol in symbols:
            q = tickers.get(symbol, {})
            price = float(q.get("last") or 0.0)
            try:
                df = engine._frame(symbol)
                closes = list(df["close"].astype(float)) if not df.empty else []
                if price > 0:
                    closes = closes[:-1] + [price] if closes else [price]
                ema = _ema(closes, 200)
                rsi = _rsi(closes, 20)
                sig = engine.strategy.signal(symbol, df) if not df.empty else None
            except Exception:
                ema, rsi, sig = None, None, None
            indicators[symbol] = {
                "ema": ema,
                "rsi": rsi,
                "signal": bool(sig),
                "decimals": _decimals(symbol),
            }

        for p in engine.positions.values():
            mark = float(tickers.get(p.symbol, {}).get("last") or p.entry_price or 0.0)
            if str(p.side).upper() == "LONG":
                pnl = (mark - float(p.entry_price)) * float(p.qty)
            else:
                pnl = (float(p.entry_price) - mark) * float(p.qty)
            base = abs(float(p.entry_price) * float(p.qty))
            pnl_pct = (pnl / base * 100.0) if base > 0 else 0.0
            total_unrealized += pnl
            item = p.to_dict()
            item["mark_price"] = mark
            item["unrealized_pnl"] = pnl
            item["pnl_pct"] = pnl_pct
            positions.append(item)

        try:
            ws_ok = bool(engine.ws_market.is_connected())
        except Exception:
            ws_ok = False

        return jsonify({
            "mode": mode,
            "equity": float(equity),
            "available": float(available),
            "unrealized_pnl": float(total_unrealized),
            "realized_pnl": float(_get_today_realized(trades)),
            "symbols": symbols,
            "strategy_timeframe": getattr(engine.settings, "timeframe", "15"),
            "positions": positions,
            "pending": [p.to_dict() for p in engine.pending.values()],
            "trades": trades,
            "alerts": engine.store.recent_alerts(20),
            "snapshots": engine.store.recent_snapshots(240),
            "last_error": engine.last_error,
            "ws_ok": ws_ok,
            "tickers": tickers,
            "indicators": indicators,
            "status": _safe_status(engine),
        })

    @app.get("/api/dashboard/candles")
    def candles():
        symbol = (request.args.get("symbol") or (engine.settings.symbols[0] if engine.settings.symbols else "")).upper()
        interval = request.args.get("interval") or str(getattr(engine.settings, "timeframe", "15"))
        limit = min(max(int(request.args.get("limit") or 260), 50), 500)

        allowed = {"1", "3", "5", "15", "30", "60", "120", "240", "360", "720"}
        if interval not in allowed:
            interval = str(getattr(engine.settings, "timeframe", "15"))
        if symbol not in engine.settings.symbols:
            return jsonify({"error": "Unknown symbol"}), 400

        try:
            rows = engine.bybit._ok(
                engine.bybit.http.get_kline(
                    category="linear",
                    symbol=symbol,
                    interval=interval,
                    limit=limit,
                )
            ).get("result", {}).get("list", [])
        except Exception as exc:
            return jsonify({"error": str(exc), "symbol": symbol, "candles": []}), 200

        # Bybit returns newest first.
        rows = list(reversed(rows))
        parsed = []
        for r in rows:
            try:
                parsed.append({
                    "time": int(int(r[0]) / 1000),
                    "open": float(r[1]),
                    "high": float(r[2]),
                    "low": float(r[3]),
                    "close": float(r[4]),
                })
            except (IndexError, TypeError, ValueError):
                continue

        try:
            tick = engine.bybit.tickers().get(symbol, {})
            live = float(tick.get("last") or (parsed[-1]["close"] if parsed else 0.0))
        except Exception:
            live = parsed[-1]["close"] if parsed else 0.0

        closes = [x["close"] for x in parsed]
        if parsed:
            closes_live = closes[:-1] + [live]
            parsed[-1]["close"] = live
            parsed[-1]["high"] = max(parsed[-1]["high"], live)
            parsed[-1]["low"] = min(parsed[-1]["low"], live)
        else:
            closes_live = [live] if live else []

        # EMA/RSI on the chart dataset; this is display-only and doesn't affect the bot.
        ema_values = []
        k = 2.0 / 201.0
        if closes_live:
            ema_val = closes_live[0]
            for i, close in enumerate(closes_live):
                if i == 0:
                    ema_val = close
                else:
                    ema_val = (close * k) + (ema_val * (1.0 - k))
                ema_values.append(ema_val)

        # RSI values for each candle.
        rsi_values = [None] * len(closes_live)
        if len(closes_live) >= 15:
            gains = []
            losses = []
            for i in range(1, len(closes_live)):
                delta = closes_live[i] - closes_live[i - 1]
                gains.append(max(delta, 0.0))
                losses.append(max(-delta, 0.0))
            avg_gain = sum(gains[:20]) / 20.0
            avg_loss = sum(losses[:20]) / 20.0
            rsi_values[20] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
            for i in range(20, len(gains)):
                avg_gain = ((avg_gain * 13.0) + gains[i]) / 20.0
                avg_loss = ((avg_loss * 13.0) + losses[i]) / 20.0
                rsi_values[i + 1] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)

        for i, item in enumerate(parsed):
            item["ema"] = ema_values[i] if i < len(ema_values) else None
            item["rsi"] = rsi_values[i] if i < len(rsi_values) else None

        move_pct = 0.0
        if len(closes_live) >= 2 and closes_live[-2] != 0:
            move_pct = (closes_live[-1] - closes_live[-2]) / closes_live[-2] * 100.0

        return jsonify({
            "symbol": symbol,
            "interval": interval,
            "decimals": _decimals(symbol),
            "live_price": live,
            "move_pct": move_pct,
            "ema200": ema_values[-1] if ema_values else None,
            "rsi20": rsi_values[-1] if rsi_values else None,
            "candles": parsed,
        })

    return app

