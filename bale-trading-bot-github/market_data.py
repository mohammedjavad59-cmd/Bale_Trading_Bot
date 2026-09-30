import threading, time
from datetime import datetime, timezone, timedelta
import requests

class MarketDataCache:
    """Shared short-TTL cache so independent strategy workers do not duplicate API calls."""
    def __init__(self, cfg):
        self.cfg=cfg; self.lock=threading.RLock(); self.bars_cache={}; self.quote_cache=None
        self.ttl=float(cfg.get('MARKET_DATA_CACHE_SECONDS',5))

    def get_bars(self, symbol='XAU/USD', outputsize=500, timezone_name='UTC'):
        key=(symbol,timezone_name)
        now=time.monotonic()
        with self.lock:
            item=self.bars_cache.get(key)
            if item and now-item[0] < self.ttl and len(item[1]) >= min(outputsize, len(item[1])):
                return item[1][-outputsize:]
        params={'symbol':symbol,'interval':'1min','outputsize':max(500,outputsize),'timezone':timezone_name,'apikey':self.cfg['TWELVEDATA_API_KEY']}
        r=requests.get('https://api.twelvedata.com/time_series',params=params,timeout=20); r.raise_for_status(); d=r.json()
        if 'values' not in d: raise RuntimeError(d.get('message',str(d)))
        values=d['values']
        with self.lock: self.bars_cache[key]=(now,values)
        return values[-outputsize:]

    def get_quote(self,symbol='XAU/USD'):
        now=time.monotonic()
        with self.lock:
            if self.quote_cache and now-self.quote_cache[0] < self.ttl: return self.quote_cache[1]
        try:
            d=requests.get('https://api.twelvedata.com/quote',params={'symbol':symbol,'apikey':self.cfg['TWELVEDATA_API_KEY']},timeout=10).json()
            bid=d.get('bid'); ask=d.get('ask')
            value=None if bid is None or ask is None else (float(bid),float(ask))
        except Exception:
            value=None
        with self.lock: self.quote_cache=(now,value)
        return value
