from datetime import datetime,timedelta,time as dtime
from zoneinfo import ZoneInfo
import requests

class NYORBStrategy:
    key="ny_orb"
    def __init__(self,cfg,settings,record_signal,bale_send,market_data=None):
        self.cfg=cfg; self.settings=settings; self.record_signal=record_signal; self.bale_send=bale_send; self.market_data=market_data
        self.ny=ZoneInfo("America/New_York"); self.last=None
    def fetch(self):
        p={"symbol":"XAU/USD","interval":"1min","outputsize":150,"timezone":"America/New_York","apikey":self.cfg["TWELVEDATA_API_KEY"]}
        values=self.market_data.get_bars("XAU/USD",150,"America/New_York") if self.market_data else requests.get("https://api.twelvedata.com/time_series",params=p,timeout=20).json().get("values",[])
        if not values: raise RuntimeError("No market data")
        a=[]
        for x in values:
            dt=datetime.strptime(x["datetime"],"%Y-%m-%d %H:%M:%S").replace(tzinfo=self.ny)
            a.append({"dt":dt,"open":float(x["open"]),"high":float(x["high"]),"low":float(x["low"]),"close":float(x["close"])})
        return sorted(a,key=lambda z:z["dt"])
    def enabled_day(self,dt):
        flags=[self.cfg.get("TRADE_MONDAY",False),self.cfg.get("TRADE_TUESDAY",False),self.cfg.get("TRADE_WEDNESDAY",True),self.cfg.get("TRADE_THURSDAY",False),self.cfg.get("TRADE_FRIDAY",False)]
        return dt.weekday()<5 and flags[dt.weekday()]
    def m5(self,a):
        d={}
        for x in a:
            st=x["dt"].replace(minute=(x["dt"].minute//5)*5,second=0,microsecond=0); d.setdefault(st,[]).append(x)
        return [{"dt":st,"open":b[0]["open"],"high":max(x["high"] for x in b),"low":min(x["low"] for x in b),"close":b[-1]["close"]} for st,b in sorted(d.items())]
    def atr(self,a,n=5):
        b=a[:-1]
        if len(b)<n+1:return 0
        t=[]
        for i in range(len(b)-n,len(b)):
            c,p=b[i],b[i-1]; t.append(max(c["high"]-c["low"],abs(c["high"]-p["close"]),abs(c["low"]-p["close"])))
        return sum(t)/len(t)
    def run_once(self):
        now=datetime.now(self.ny)
        if not self.enabled_day(now): return
        start=datetime.combine(now.date(),dtime(9,30),tzinfo=self.ny); end=start+timedelta(minutes=5); stop=datetime.combine(now.date(),dtime(10,0),tzinfo=self.ny)
        if not end<=now<=stop:return
        a=self.fetch(); opening=[x for x in a if start<=x["dt"]<end]
        if len(opening)<5:return
        hi=max(x["high"] for x in opening); lo=min(x["low"] for x in opening)
        m=self.m5(a); cur=now.replace(minute=(now.minute//5)*5,second=0,microsecond=0); closed=[x for x in m if end<=x["dt"]<cur]
        if not closed:return
        c=closed[-1]; rng=c["high"]-c["low"]; body=abs(c["close"]-c["open"]); atr=self.atr(a)
        if rng<=0 or atr<=0 or body<.8*atr or body/rng*100<60:return
        buy=c["close"]>hi and c["open"]<=hi; sell=c["close"]<lo and c["open"]>=lo
        if not(buy or sell):return
        key=c["dt"].isoformat()
        if self.settings["strategies"].get(self.key, {}).get("one_signal_per_candle", True) and key==self.last:return
        self.last=key; entry=c["close"]
        if buy: sl=c["low"]-.10; direction="BUY"; tp=entry+abs(entry-sl)*1.5
        else: sl=c["high"]+.10; direction="SELL"; tp=entry-abs(entry-sl)*1.5
        text=f"{'🟢' if buy else '🔴'} XAUUSD {direction} SIGNAL\n\nEntry: {entry:.2f}\nSL: {sl:.2f}\nTP: {tp:.2f}\nRR: 1:1.5\nOR High: {hi:.2f}\nOR Low: {lo:.2f}\nTime: {c['dt'].strftime('%Y-%m-%d %H:%M')} NY\n\n⚠️ فقط سیگنال؛ معامله خودکار انجام نمی‌شود."
        self.record_signal(self.key,"XAU/USD",direction,entry,sl,tp,text)
