from datetime import datetime, timedelta, timezone
import requests

class VWAPWickRejectionStrategy:
    key = "vwap_wick_rejection"

    def __init__(self, cfg, settings, record_signal, bale_send, market_data=None):
        self.cfg = cfg
        self.settings = settings
        self.record_signal = record_signal
        self.bale_send = bale_send
        self.market_data = market_data
        # The original MT5 EA uses broker/server timestamps.
        # Current bot configuration: broker server = UTC-03:30.
        offset=self.cfg.get("BROKER_UTC_OFFSET","-03:30")
        sign=-1 if str(offset).startswith("-") else 1
        raw=str(offset).lstrip("+-"); hh,mm=[int(x) for x in raw.split(":")]
        self.server_tz = timezone(sign*timedelta(hours=hh,minutes=mm))
        self.last_signal_bar = None

    def fetch(self):
        if self.market_data:
            values=self.market_data.get_bars("XAU/USD",500,"UTC")
        else:
            params={"symbol":"XAU/USD","interval":"1min","outputsize":500,"timezone":"UTC","apikey":self.cfg["TWELVEDATA_API_KEY"]}
            r=requests.get("https://api.twelvedata.com/time_series",params=params,timeout=20); r.raise_for_status(); d=r.json()
            if "values" not in d: raise RuntimeError(d.get("message",str(d)))
            values=d["values"]
        out=[]
        for x in values:
            dt=datetime.strptime(x["datetime"],"%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            out.append({"dt":dt.astimezone(self.server_tz),"open":float(x["open"]),"high":float(x["high"]),"low":float(x["low"]),"close":float(x["close"]),"volume":float(x.get("volume",0) or 0)})
        return sorted(out,key=lambda z:z["dt"])

    def is_allowed_time(self, bar_time):
        # MQL5: Sunday=0, Monday=1, Tuesday=2...
        return bar_time.weekday() == 1 and bar_time.hour in (6, 9, 17, 18, 23)

    def daily_vwap(self, bars, now):
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        day = [b for b in bars if day_start <= b["dt"] <= now]
        if not day:
            return 0.0

        pv = 0.0
        vol = 0.0
        for b in day:
            typical = (b["high"] + b["low"] + b["close"]) / 3.0
            v = b["volume"]
            pv += typical * v
            vol += v
        return pv / vol if vol > 0 else 0.0

    def get_quote(self):
        """Best-effort quote for the spread filter.
        Twelve Data may not expose bid/ask for every market-data plan.
        If unavailable, return None and do not invent a spread.
        """
        try:
            if self.market_data: return self.market_data.get_quote("XAU/USD")
            p = {"symbol": "XAU/USD", "apikey": self.cfg["TWELVEDATA_API_KEY"]}
            d = requests.get("https://api.twelvedata.com/quote", params=p, timeout=10).json()
            bid = d.get("bid"); ask = d.get("ask")
            if bid is None or ask is None: return None
            return float(bid), float(ask)
        except Exception:
            return None

    def run_once(self):
        now = datetime.now(self.server_tz)

        # The original EA checks the current M1 bar timestamp.
        if not self.is_allowed_time(now):
            return

        bars = self.fetch()
        if len(bars) < 3:
            return

        # Signal is evaluated once per new M1 bar using the just-closed M1 candle.
        current_minute = now.replace(second=0, microsecond=0)
        closed = [b for b in bars if b["dt"] < current_minute]
        if not closed:
            return
        c = closed[-1]

        # Prevent duplicate processing of the same closed candle.
        if self.settings["strategies"].get(self.key, {}).get("one_signal_per_candle", True) and self.last_signal_bar == c["dt"]:
            return
        self.last_signal_bar = c["dt"]

        # Preserve the EA's max spread rule when bid/ask is available.
        quote = self.get_quote()
        point = float(self.cfg.get("XAUUSD_POINT", 0.01))
        max_spread_points = int(self.cfg.get("VWAP_MAX_SPREAD_POINTS", 80))
        if quote is not None:
            bid, ask = quote
            spread_points = (ask - bid) / point
            if spread_points > max_spread_points:
                return
        else:
            spread_points = None

        vwap = self.daily_vwap(bars, now)
        if vwap <= 0:
            return

        band_points = float(self.cfg.get("VWAP_BAND_POINTS", 100))
        entry_buffer_points = float(self.cfg.get("VWAP_ENTRY_BUFFER_POINTS", 5))
        band = band_points * point
        entry_buffer = entry_buffer_points * point

        # Exact logical conditions from the MT5 EA:
        # bullish = low touches/crosses VWAP, open and close stay above VWAP, close > open
        # bearish = high touches/crosses VWAP, open and close stay below VWAP, close < open
        bullish = (
            c["low"] <= vwap
            and c["close"] > vwap
            and c["open"] > vwap
            and c["close"] > c["open"]
        )
        bearish = (
            c["high"] >= vwap
            and c["close"] < vwap
            and c["open"] < vwap
            and c["close"] < c["open"]
        )

        if not bullish and not bearish:
            return

        # External signal bot has no broker execution price.
        # Use the latest closed-candle close as the signal entry proxy.
        entry = c["close"]
        rr = float(self.cfg.get("VWAP_RR", 1.50))

        if bullish:
            sl = min(c["low"], vwap - band)
            direction = "BUY"
            risk = entry - sl
            if risk <= 0:
                return
            tp = entry + risk * rr
        else:
            sl = max(c["high"], vwap + band)
            direction = "SELL"
            risk = sl - entry
            if risk <= 0:
                return
            tp = entry - risk * rr

        # Keep EntryBufferPoints explicit for compatibility with the original EA.
        # The original code declares it but does not use it in OpenTrade(),
        # so it is intentionally not applied to the entry price here either.
        _ = entry_buffer

        text = (
            f"{'🟢' if bullish else '🔴'} XAUUSD {direction} SIGNAL\n\n"
            f"Strategy: VWAP Wick Rejection\n"
            f"Timeframe: M1\n"
            f"Entry: {entry:.2f}\n"
            f"SL: {sl:.2f}\n"
            f"TP: {tp:.2f}\n"
            f"RR: 1:{rr:g}\n\n"
            f"VWAP: {vwap:.2f}\n"
            f"VWAP Band: {band:.2f}\n"
            f"Candle: {c['dt'].strftime('%Y-%m-%d %H:%M')} server\n"
            f"Allowed hours: 06, 09, 17, 18, 23\n"
            f"Day: Tuesday\n"
        )
        if spread_points is not None:
            text += f"Spread: {spread_points:.0f} points\n"
        else:
            text += "Spread: unavailable from current data API\n"
        text += "\n⚠️ فقط سیگنال؛ معامله خودکار انجام نمی‌شود."

        self.record_signal(
            self.key,
            "XAU/USD",
            direction,
            entry,
            sl,
            tp,
            text,
        )
