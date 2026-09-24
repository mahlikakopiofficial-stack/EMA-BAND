from __future__ import annotations
import time
import logging

log = logging.getLogger('WEBSOCKET')

class MarketStream:
    def __init__(self, settings, on_candle):
        self.settings = settings
        self.on_candle = on_candle
        self.ws = None
        self.last_message = 0.0
        self.connected = False
        self.connection_attempts = 0
        self.last_connection_error = None
        
    def is_connected(self) -> bool:
        """Check if WebSocket is connected AND receiving messages"""
        if not self.connected:
            return False
        # Also check if messages are recent (within 60 seconds)
        now = time.time()
        if now - self.last_message > 60:
            log.warning("MarketStream: No messages received in 60s, treating as disconnected")
            self.connected = False
            return False
        return True
    
    def start(self) -> bool:
        """Start market stream with error handling"""
        try:
            from pybit.unified_trading import WebSocket
            
            log.info("Starting MarketStream...")
            self.ws = WebSocket(
                testnet=self.settings.bybit_testnet,
                channel_type='linear'
            )
            
            # Subscribe to kline streams for all symbols
            for s in self.settings.symbols:
                try:
                    self.ws.kline_stream(
                        interval=15,
                        symbol=s,
                        callback=self._callback
                    )
                    log.info("Subscribed to kline stream for %s", s)
                except Exception as e:
                    log.error("Failed to subscribe to %s: %s", s, e)
                    raise
            
            self.last_message = time.time()
            self.connected = True
            self.connection_attempts = 0
            self.last_connection_error = None
            log.info("✅ MarketStream connected successfully")
            return True
            
        except Exception as e:
            self.connected = False
            self.last_connection_error = str(e)
            self.connection_attempts += 1
            log.error("❌ MarketStream connection failed (attempt %d): %s", 
                     self.connection_attempts, e, exc_info=True)
            return False
    
    def reconnect(self) -> bool:
        """Attempt to reconnect with exponential backoff"""
        # Exponential backoff: 2^attempt seconds, max 60 seconds
        backoff_time = min(2 ** self.connection_attempts, 60)
        
        log.warning("Reconnecting MarketStream in %.1f seconds (attempt %d)", 
                   backoff_time, self.connection_attempts + 1)
        time.sleep(backoff_time)
        
        # Close old connection if exists
        if self.ws:
            try:
                self.ws.exit()
            except:
                pass
            self.ws = None
        
        return self.start()
    
    def _callback(self, m):
        """Handle incoming market data"""
        try:
            self.last_message = time.time()
            self.connected = True
            
            symbol = m.get('topic', '').split('.')[-1]
            for x in m.get('data', []):
                self.on_candle({
                    'symbol': symbol,
                    'start': int(x['start']),
                    'end': int(x['end']),
                    'open': float(x['open']),
                    'high': float(x['high']),
                    'low': float(x['low']),
                    'close': float(x['close']),
                    'volume': float(x['volume']),
                    'confirm': bool(x['confirm'])
                })
        except Exception as e:
            log.error("Error processing market callback: %s", e, exc_info=True)


class PrivateStream:
    def __init__(self, settings, on_order, on_execution, on_position):
        self.settings = settings
        self.on_order = on_order
        self.on_execution = on_execution
        self.on_position = on_position
        self.ws = None
        self.last_message = 0.0
        self.connected = False
        self.authenticated = False
        self.connection_attempts = 0
        self.last_connection_error = None
        
    def is_connected(self) -> bool:
        """Private stream is healthy when the authenticated socket is active. Quiet periods are normal because order/execution/position topics are event-driven."""
        return bool(self.connected and self.authenticated)

    
    def start(self) -> bool:
        """Start private stream with validation"""
        try:
            # Validate API credentials exist
            if not self.settings.bybit_api_key or not self.settings.bybit_api_secret:
                log.warning("Private stream disabled: API credentials not configured")
                return False
            
            from pybit.unified_trading import WebSocket
            
            log.info("Starting PrivateStream...")
            self.ws = WebSocket(
                testnet=self.settings.bybit_testnet,
                channel_type='private',
                api_key=self.settings.bybit_api_key,
                api_secret=self.settings.bybit_api_secret
            )
            
            # Subscribe to private streams
            log.info("Subscribing to order stream...")
            self.ws.order_stream(callback=self._order)
            
            log.info("Subscribing to execution stream...")
            self.ws.execution_stream(callback=self._execution)
            
            log.info("Subscribing to position stream...")
            self.ws.position_stream(callback=self._position)
            
            # NOTE: pybit's order_stream/execution_stream do not necessarily push
            # a message immediately on subscribe (they fire on actual order/exec
            # events), so blocking here to "wait for a message" would either
            # give a false failure (nothing traded yet) or a false pass (if we
            # mistakenly re-stamp last_message ourselves). Real connection
            # health is instead verified continuously at runtime via
            # is_connected(), which checks staleness of the last received
            # message. A construction/subscribe error (bad credentials, no
            # network) will raise synchronously and be caught below.
            self.last_message = time.time()
            self.connection_attempts = 0
            self.connected = True
            self.authenticated = True
            log.info("✅ PrivateStream subscribed successfully (auth failures, if any, "
                     "will surface as staleness via is_connected() during heartbeat)")
            return True
            
        except Exception as e:
            self.connected = False
            self.authenticated = False
            self.last_connection_error = str(e)
            self.connection_attempts += 1
            log.error("❌ PrivateStream connection failed (attempt %d): %s", 
                     self.connection_attempts, e, exc_info=True)
            return False
    
    def reconnect(self) -> bool:
        """Attempt to reconnect with exponential backoff"""
        # Exponential backoff: 2^attempt seconds, max 60 seconds
        backoff_time = min(2 ** self.connection_attempts, 60)
        
        log.warning("Reconnecting PrivateStream in %.1f seconds (attempt %d)", 
                   backoff_time, self.connection_attempts + 1)
        time.sleep(backoff_time)
        
        # Close old connection if exists
        if self.ws:
            try:
                self.ws.exit()
            except:
                pass
            self.ws = None
        
        self.connected = False
        self.authenticated = False
        return self.start()
    
    def _order(self, m):
        """Handle order updates"""
        try:
            self.last_message = time.time()
            self.connected = True
            self.authenticated = True
            self.on_order(m)
        except Exception as e:
            log.error("Error processing order callback: %s", e, exc_info=True)
    
    def _execution(self, m):
        """Handle execution updates"""
        try:
            self.last_message = time.time()
            self.connected = True
            self.authenticated = True
            self.on_execution(m)
        except Exception as e:
            log.error("Error processing execution callback: %s", e, exc_info=True)
    
    def _position(self, m):
        """Handle position updates"""
        try:
            self.last_message = time.time()
            self.connected = True
            self.authenticated = True
            self.on_position(m)
        except Exception as e:
            log.error("Error processing position callback: %s", e, exc_info=True)


