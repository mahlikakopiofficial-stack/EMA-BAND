from __future__ import annotations
from decimal import Decimal,ROUND_DOWN,ROUND_UP,ROUND_HALF_UP
import time
import logging

log = logging.getLogger('BYBIT_ADAPTER')

class BybitAdapter:
    def __init__(self, settings):
        from pybit.unified_trading import HTTP
        self.settings = settings
        self.http = HTTP(
            testnet=settings.bybit_testnet,
            api_key=settings.bybit_api_key,
            api_secret=settings.bybit_api_secret
        )
        self.instruments = {}
        self.last_http_refresh = time.time()
        self.http_refresh_interval = 3600  # Refresh HTTP session every hour
    
    def _refresh_http_if_needed(self):
        """Refresh HTTP client periodically to prevent session staleness"""
        now = time.time()
        if now - self.last_http_refresh > self.http_refresh_interval:
            try:
                log.info("Refreshing HTTP client session...")
                from pybit.unified_trading import HTTP
                self.http = HTTP(
                    testnet=self.settings.bybit_testnet,
                    api_key=self.settings.bybit_api_key,
                    api_secret=self.settings.bybit_api_secret
                )
                self.last_http_refresh = now
                log.info("✅ HTTP client refreshed")
            except Exception as e:
                log.error("Failed to refresh HTTP client: %s", e)
                # Don't crash, just log and continue
    
    def _with_retry(self, func, max_retries=3, timeout=30):
        """Decorator for API calls with exponential backoff retry"""
        for attempt in range(max_retries):
            try:
                log.debug(f"Calling {func.__name__} (attempt {attempt + 1}/{max_retries})")
                return func()
            except ValueError as e:
                # FIXED: ValueError here comes from our own client-side checks
                # (_round_qty_down/_round_qty_up/size_from_balance rejecting a qty
                # outside exchange limits, bad price, etc). It is deterministic --
                # no order was ever sent to Bybit, and retrying it will fail the
                # same way every time while burning the backoff delay and then
                # surfacing to the user as "SUBMISSION UNCERTAIN", which wrongly
                # implies an order might actually have reached the exchange.
                # Fail fast instead so the caller sees the real config/sizing error.
                log.error(f"{func.__name__} rejected before any Bybit call (not retried): {e}")
                raise
            except Exception as e:
                if attempt < max_retries - 1:
                    # Exponential backoff: 1s, 2s, 4s
                    backoff = 2 ** attempt
                    log.warning(
                        f"API call {func.__name__} failed (attempt {attempt + 1}), "
                        f"retrying in {backoff}s: {e}"
                    )
                    time.sleep(backoff)
                else:
                    log.error(f"API call {func.__name__} failed after {max_retries} attempts: {e}")
                    raise
    
    @staticmethod
    def _ok(r):
        """Validate Bybit API response"""
        if r.get('retCode', 0) != 0:
            error_msg = f"Bybit error {r.get('retCode')}: {r.get('retMsg')}"
            log.error(error_msg)
            raise RuntimeError(error_msg)
        return r
    
    def set_leverage(self, symbol, leverage):
        """Set isolated/cross leverage for a symbol. Bybit returns retCode
        110043 ('leverage not modified') when it's already set to this
        value -- that is not an error, just a no-op, so it's swallowed."""
        self._refresh_http_if_needed()
        lev = str(leverage)
        try:
            r = self.http.set_leverage(category='linear', symbol=symbol,
                                        buyLeverage=lev, sellLeverage=lev)
            if r.get('retCode', 0) not in (0, 110043):
                self._ok(r)
            log.info("Leverage set: %s -> %sx", symbol, lev)
        except Exception as e:
            msg = str(e)
            if '110043' in msg or 'leverage not modified' in msg.lower():
                log.debug("Leverage already %sx for %s", lev, symbol)
            else:
                log.error("Failed to set leverage for %s to %sx: %s", symbol, lev, e)
                raise

    def load_instruments(self):
        """Load instrument info with retry"""
        self._refresh_http_if_needed()
        
        for s in self.settings.symbols:
            try:
                def fetch():
                    return self._ok(
                        self.http.get_instruments_info(category='linear', symbol=s)
                    ).get('result', {}).get('list', [])
                
                rows = self._with_retry(fetch, max_retries=3)
                
                if not rows:
                    raise RuntimeError(f'Unavailable symbol: {s}')
                
                self.instruments[s] = rows[0]
                min_notional = rows[0].get('lotSizeFilter', {}).get('minNotionalValue')
                if min_notional:
                    log.info(f"✅ Loaded instrument info for {s} (exchange min notional: {min_notional} USDT)")
                else:
                    log.info(f"✅ Loaded instrument info for {s}")
                
            except Exception as e:
                log.error(f"Failed to load instrument {s}: {e}")
                raise
    
    def _min_notional(self, s):
        """Bybit's own server-side minimum order value for this symbol, if it
        publishes one. Returns 0.0 if absent (older/some symbols omit it) so
        callers can safely max() it against their own configured minimum
        without ever lowering it."""
        try:
            v = self.instruments.get(s, {}).get('lotSizeFilter', {}).get('minNotionalValue')
            return float(v) if v else 0.0
        except (TypeError, ValueError):
            return 0.0
    
    def get_account_info(self):
        """Get account info with retry"""
        self._refresh_http_if_needed()
        
        def fetch():
            return self._ok(self.http.get_account_info()).get('result', {})
        
        return self._with_retry(fetch, max_retries=2)
    
    def is_portfolio_margin(self):
        """Check if account is portfolio margin with retry"""
        try:
            x = self.get_account_info()
            is_pm = (str(x.get('marginMode') or '').upper() == 'PORTFOLIO_MARGIN' or 
                    str(x.get('unifiedMarginStatus') or '') in {'6', '7'})
            log.debug(f"Portfolio margin status: {is_pm}")
            return is_pm
        except Exception as e:
            log.error(f"Failed to check portfolio margin status: {e}")
            return False
    
    def get_balance(self):
        """Get wallet balance with retry"""
        self._refresh_http_if_needed()
        
        def fetch():
            rows = self._ok(
                self.http.get_wallet_balance(accountType='UNIFIED', coin='USDT')
            ).get('result', {}).get('list', [])
            
            if not rows:
                raise RuntimeError('Bybit returned no unified USDT account balance')
            
            a = rows[0]
            total_equity = float(a.get('totalEquity') or 0)
            available = float(a.get('totalAvailableBalance') or 0)
            
            log.debug(f"Balance: equity={total_equity:.8f}, available={available:.8f}")
            return total_equity, available
        
        return self._with_retry(fetch, max_retries=3)
    
    def get_positions(self):
        """Get positions with retry"""
        self._refresh_http_if_needed()
        
        def fetch():
            return self._ok(
                self.http.get_positions(category='linear', settleCoin='USDT')
            ).get('result', {}).get('list', [])
        
        return self._with_retry(fetch, max_retries=2)
    
    def tickers(self):
        """Get market tickers with retry"""
        self._refresh_http_if_needed()
        
        def fetch():
            rows = self._ok(
                self.http.get_tickers(category='linear')
            ).get('result', {}).get('list', [])
            
            wanted = set(self.settings.symbols)
            out = {}
            
            for x in rows:
                s = x.get('symbol')
                if s in wanted:
                    out[s] = {
                        'last': float(x.get('lastPrice') or 0),
                        'bid': float(x.get('bid1Price') or 0),
                        'ask': float(x.get('ask1Price') or 0),
                        'pct24': float(x.get('price24hPcnt') or 0) * 100,
                        'vol24': float(x.get('volume24h') or 0)
                    }
            
            return out
        
        return self._with_retry(fetch, max_retries=3)
    
    def candles(self, symbol, limit=500):
        """Get kline candles with retry"""
        self._refresh_http_if_needed()
        
        def fetch():
            rows = self._ok(
                self.http.get_kline(
                    category='linear',
                    symbol=symbol,
                    interval=self.settings.timeframe,
                    limit=limit + 1
                )
            ).get('result', {}).get('list', [])
            
            if rows:
                interval_ms = 900000
                try:
                    if int(rows[0][0]) + interval_ms > __import__('time').time() * 1000:
                        rows = rows[1:]
                except (ValueError, TypeError, IndexError):
                    pass
            
            return [
                {
                    'start': int(r[0]),
                    'open': float(r[1]),
                    'high': float(r[2]),
                    'low': float(r[3]),
                    'close': float(r[4]),
                    'volume': float(r[5]),
                    'confirm': True
                }
                for r in reversed(rows)
            ]
        
        return self._with_retry(fetch, max_retries=2)
    
    def get_order_history(self, order_id=None, order_link_id=None, symbol=None, limit=50):
        """Get order history with retry"""
        self._refresh_http_if_needed()
        
        def fetch():
            kw = {'category': 'linear', 'limit': limit}
            if order_id:
                kw['orderId'] = order_id
            elif order_link_id:
                kw['orderLinkId'] = order_link_id
            elif symbol:
                kw['symbol'] = symbol
            
            return self._ok(self.http.get_order_history(**kw)).get('result', {}).get('list', [])
        
        return self._with_retry(fetch, max_retries=2)
    
    def get_executions(self, symbol=None, order_id=None, order_link_id=None, limit=100):
        """Get executions with retry"""
        self._refresh_http_if_needed()
        
        def fetch():
            kw = {'category': 'linear', 'limit': limit}
            if order_id:
                kw['orderId'] = order_id
            elif order_link_id:
                kw['orderLinkId'] = order_link_id
            elif symbol:
                kw['symbol'] = symbol
            
            return self._ok(self.http.get_executions(**kw)).get('result', {}).get('list', [])
        
        return self._with_retry(fetch, max_retries=2)
    
    def _filters(self, s):
        return self.instruments[s]['lotSizeFilter'], self.instruments[s]['priceFilter']
    
    def _round_qty_down(self, s, qty):
        f, _ = self._filters(s)
        step = Decimal(str(f['qtyStep']))
        mn = Decimal(str(f['minOrderQty']))
        mx = Decimal(str(f.get('maxOrderQty') or '999999999999'))
        q = (Decimal(str(qty)) / step).to_integral_value(rounding=ROUND_DOWN) * step
        
        if q < mn:
            raise ValueError(f'{s}: quantity {q} below exchange minimum {mn}')
        if q > mx:
            raise ValueError(f'{s}: quantity {q} exceeds exchange maximum {mx}')
        
        return float(q)
    
    def _round_qty_up(self, s, qty):
        f, _ = self._filters(s)
        step = Decimal(str(f['qtyStep']))
        mn = Decimal(str(f['minOrderQty']))
        mx = Decimal(str(f.get('maxOrderQty') or '999999999999'))
        q = (Decimal(str(qty)) / step).to_integral_value(rounding=ROUND_UP) * step
        q = max(q, mn)
        
        if q > mx:
            raise ValueError(f'{s}: quantity {q} exceeds exchange maximum {mx}')
        
        return float(q)
    
    def size_from_balance(self, s, available_usdt, pct, price):
        if available_usdt <= 0 or price <= 0:
            raise ValueError('available balance and price must be positive')
        
        # FIXED: previously only enforced the user-configured MIN_TRADE_USDT.
        # Bybit publishes its own per-symbol minNotionalValue via
        # get_instruments_info(), which can differ from the configured minimum
        # and, if higher, would cause the exchange to reject the order even
        # though the bot's own check passed. Taking the max of both never
        # lowers the configured minimum -- it only raises the effective floor
        # when the exchange genuinely requires more.
        exchange_min = self._min_notional(s)
        effective_min_usdt = max(self.settings.min_trade_usdt, exchange_min)
        if exchange_min > self.settings.min_trade_usdt:
            log.debug(f"{s}: exchange minNotionalValue {exchange_min} > configured "
                      f"MIN_TRADE_USDT {self.settings.min_trade_usdt}, using {exchange_min}")
        
        desired = max(available_usdt * pct / 100.0, effective_min_usdt)
        qty = self._round_qty_up(s, desired / price)
        actual = qty * price
        
        if actual < effective_min_usdt:
            qty = self._round_qty_up(s, effective_min_usdt / price)
            actual = qty * price
        
        if actual > available_usdt:
            raise ValueError(
                f'{s}: order notional {actual:.6f} USDT exceeds available balance {available_usdt:.6f} USDT'
            )
        
        log.info(f"Calculated order size: {s} qty={qty} notional={actual:.8f}")
        return qty
    
    def place_market(self, s, side, qty, link, position_idx=0):
        """Place market order with retry"""
        self._refresh_http_if_needed()
        
        def place():
            q = self._round_qty_down(s, qty)
            log.info(f"Placing market order: {s} {side} qty={q} link={link}")
            
            result = self._ok(
                self.http.place_order(
                    category='linear',
                    symbol=s,
                    side='Buy' if side == 'LONG' else 'Sell',
                    orderType='Market',
                    qty=str(q),
                    positionIdx=0,
                    orderLinkId=link,
                    reduceOnly=False,
                    slippageToleranceType='Percent',
                    slippageTolerance=str(self.settings.max_slippage_bps / 100)
                )
            )
            
            log.info(f"✅ Order placed: {result}")
            return result
        
        return self._with_retry(place, max_retries=2)
    
    def close_market(self, s, side, qty, link, position_idx=0):
        """Close position with market order with retry"""
        self._refresh_http_if_needed()
        
        def close():
            q = self._round_qty_down(s, qty)
            log.info(f"Closing position: {s} {side} qty={q} link={link}")
            
            result = self._ok(
                self.http.place_order(
                    category='linear',
                    symbol=s,
                    side='Sell' if side == 'LONG' else 'Buy',
                    orderType='Market',
                    qty=str(q),
                    positionIdx=0,
                    orderLinkId=link,
                    reduceOnly=True
                )
            )
            
            log.info(f"✅ Close order placed: {result}")
            return result
        
        return self._with_retry(close, max_retries=2)

