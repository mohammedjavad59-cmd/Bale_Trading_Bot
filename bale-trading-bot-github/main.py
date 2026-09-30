import json, logging, threading, time, os
from datetime import datetime, timezone
from pathlib import Path
import requests
from flask import Flask, jsonify, render_template_string, request
from strategies.ny_orb import NYORBStrategy
from strategies.vwap_wick_rejection import VWAPWickRejectionStrategy
from market_data import MarketDataCache

BASE=Path(__file__).resolve().parent
CONFIG=BASE/"config.json"
CFG=json.loads(CONFIG.read_text(encoding="utf-8"))
# GitHub Actions: secrets can override local config without being committed.
for _k in ("BALE_TOKEN", "BALE_CHAT_ID", "OWNER_CHAT_ID", "TWELVEDATA_API_KEY"):
    _v=os.getenv(_k)
    if _v:
        CFG[_k]=_v
DATA=BASE/"data"; DATA.mkdir(exist_ok=True)
SETTINGS_PATH=BASE/CFG["SETTINGS_FILE"]; HISTORY_PATH=BASE/CFG["HISTORY_FILE"]
SETTINGS=json.loads(SETTINGS_PATH.read_text(encoding="utf-8")) if SETTINGS_PATH.exists() else {}
HISTORY=json.loads(HISTORY_PATH.read_text(encoding="utf-8")) if HISTORY_PATH.exists() else []
SETTINGS.setdefault("bot", {})
SETTINGS["bot"].setdefault("bale_enabled", True)
SETTINGS.setdefault("strategies", {})

def normalize_strategy_settings():
    defaults={
        "ny_orb":{"enabled":True,"scan_interval":20,"cooldown":0,"max_signals_per_day":None,"one_signal_per_candle":True},
        "vwap_wick_rejection":{"enabled":True,"scan_interval":20,"cooldown":0,"max_signals_per_day":None,"one_signal_per_candle":True},
    }
    old=SETTINGS.get("strategies",{})
    for key,d in defaults.items():
        cur=old.get(key,{})
        if isinstance(cur,bool): cur={"enabled":cur}
        for k,v in d.items(): cur.setdefault(k,v)
        old[key]=cur
    SETTINGS["strategies"]=old
normalize_strategy_settings()

BALE=f"https://tapi.bale.ai/bot{CFG['BALE_TOKEN']}"
logging.basicConfig(level=logging.INFO,format="%(asctime)s | %(levelname)s | %(message)s")
SETTINGS_LOCK=threading.RLock()
HISTORY_LOCK=threading.RLock()
WORKER_EVENTS={"ny_orb":threading.Event(),"vwap_wick_rejection":threading.Event()}
RUNTIME={k:{"status":"idle","last_scan":None,"last_error":None} for k in WORKER_EVENTS}


def save_settings():
    with SETTINGS_LOCK:
        SETTINGS_PATH.write_text(json.dumps(SETTINGS,ensure_ascii=False,indent=2),encoding="utf-8")

def save_history():
    with HISTORY_LOCK:
        HISTORY_PATH.write_text(json.dumps(HISTORY[-500:],ensure_ascii=False,indent=2),encoding="utf-8")

def bale(method,payload=None):
    if not CFG.get("BALE_TOKEN"):
        raise RuntimeError("BALE_TOKEN is empty")
    r=requests.post(f"{BALE}/{method}",json=payload or {},timeout=20); r.raise_for_status()
    d=r.json()
    if not d.get("ok"): raise RuntimeError(d.get("description",str(d)))
    return d.get("result")

def owner_chat_id():
    # Explicit owner ID wins; fall back to the existing BALE_CHAT_ID for backwards compatibility.
    return str(CFG.get("OWNER_CHAT_ID") or CFG.get("BALE_CHAT_ID") or "").strip()

def is_owner_private_chat(chat):
    if not isinstance(chat, dict):
        return False
    if str(chat.get("type", "")) != "private":
        return False
    owner = owner_chat_id()
    return bool(owner) and str(chat.get("id")) == owner

def is_owner_callback(cq):
    msg = cq.get("message") or {}
    chat = msg.get("chat") or {}
    return is_owner_private_chat(chat)

def send(chat,text,markup=None):
    if not SETTINGS["bot"].get("bale_enabled",True): return None
    owner = owner_chat_id()
    # All bot output is restricted to the configured owner.
    if not owner or str(chat) != owner: return None
    payload={"chat_id":owner,"text":text}
    if markup: payload["reply_markup"]=markup
    return bale("sendMessage",payload)

def answer_callback(callback_id,text=""):
    try: bale("answerCallbackQuery",{"callback_query_id":callback_id,"text":text})
    except Exception as e: logging.warning("answerCallbackQuery: %s",e)

def inline(rows): return {"inline_keyboard":rows}

def main_menu():
    return inline([
        [{"text":"💰 قیمت‌ها","callback_data":"menu:prices"},{"text":"📚 تاریخچه","callback_data":"menu:history"}],
        [{"text":"🧠 استراتژی‌ها","callback_data":"menu:strategies"},{"text":"📊 وضعیت سیستم","callback_data":"menu:status"}],
    ])

def price_menu():
    return inline([
        [{"text":"🥇 XAU/USD","callback_data":"price:XAU/USD"},{"text":"💵 EUR/USD","callback_data":"price:EUR/USD"}],
        [{"text":"💷 GBP/JPY","callback_data":"price:GBP/JPY"}],
        [{"text":"↩️ منوی اصلی","callback_data":"menu:home"}],
    ])

def strategy_menu():
    rows=[]
    names={"ny_orb":"NY Open Range Breakout","vwap_wick_rejection":"VWAP Wick Rejection"}
    for k,v in SETTINGS["strategies"].items():
        enabled=v.get("enabled",False) if isinstance(v,dict) else bool(v)
        rows.append([{"text":("🟢 " if enabled else "🔴 ")+names.get(k,k),"callback_data":"strategy:info:"+k}])
    rows.append([{"text":"↩️ منوی اصلی","callback_data":"menu:home"}])
    return inline(rows)

def strategy_info(key):
    v=SETTINGS["strategies"].get(key,{})
    names={"ny_orb":"NY Open Range Breakout","vwap_wick_rejection":"VWAP Wick Rejection"}
    enabled=v.get("enabled",False)
    interval=v.get("scan_interval",20); cooldown=v.get("cooldown",0); mx=v.get("max_signals_per_day")
    mx_text="∞" if mx in (None,0,"0","infinity") else str(mx)
    return (f"🧠 {names.get(key,key)}\n\n"
            f"وضعیت: {'🟢 فعال' if enabled else '🔴 خاموش'}\n"
            f"اسکن: هر {interval} ثانیه\n"
            f"Cooldown: {cooldown} ثانیه\n"
            f"حداکثر سیگنال روزانه: {mx_text}")

def alias(s):
    s=s.strip(); return CFG.get("SYMBOL_ALIASES",{}).get(s.lower(),s.upper())

def get_price(s):
    sym=alias(s)
    p={"symbol":sym,"interval":"1min","outputsize":1,"apikey":CFG["TWELVEDATA_API_KEY"]}
    d=requests.get("https://api.twelvedata.com/time_series",params=p,timeout=15).json()
    if not d.get("values"): raise RuntimeError(d.get("message",str(d)))
    v=d["values"][0]; return sym,float(v["close"]),v["datetime"]

def record_signal(strategy,symbol,direction,entry,sl,tp,text,extra=None):
    with HISTORY_LOCK:
        item={"id":len(HISTORY)+1,"time_utc":datetime.now(timezone.utc).isoformat(),"strategy":strategy,"symbol":symbol,"direction":direction,"entry":entry,"sl":sl,"tp":tp,"message":text}
        if extra: item.update(extra)
        HISTORY.append(item); save_history()

def can_emit(strategy_key):
    s=SETTINGS["strategies"].get(strategy_key,{})
    cooldown=max(0,int(s.get("cooldown",0) or 0))
    max_daily=s.get("max_signals_per_day")
    now=datetime.now(timezone.utc)
    if max_daily not in (None,0,"0","infinity"):
        try: max_daily=int(max_daily)
        except: max_daily=None
        if max_daily is not None:
            today=now.date().isoformat()
            count=sum(1 for x in HISTORY if x.get("strategy")==strategy_key and str(x.get("time_utc","")).startswith(today))
            if count>=max_daily: return False,"daily_limit"
    if cooldown>0:
        last=None
        for x in reversed(HISTORY):
            if x.get("strategy")==strategy_key:
                try: last=datetime.fromisoformat(x["time_utc"].replace("Z","+00:00")); break
                except: pass
        if last and (now-last).total_seconds()<cooldown: return False,"cooldown"
    return True,None

def record_and_send(strategy,symbol,direction,entry,sl,tp,text,extra=None):
    ok,reason=can_emit(strategy.key)
    if not ok:
        return False
    if CFG.get("BALE_CHAT_ID") and SETTINGS["bot"].get("bale_enabled",True):
        send(CFG["BALE_CHAT_ID"],text,signal_menu())
    record_signal(strategy.key,symbol,direction,entry,sl,tp,text,extra)
    return True

def signal_menu():
    return inline([[{"text":"📚 تاریخچه","callback_data":"menu:history"},{"text":"🧠 استراتژی‌ها","callback_data":"menu:strategies"}],
                   [{"text":"💰 قیمت XAU/USD","callback_data":"price:XAU/USD"},{"text":"🏠 منوی اصلی","callback_data":"menu:home"}]])

def bale_loop():
    offset=0
    while True:
        try:
            updates=bale("getUpdates",{"offset":offset,"timeout":10})
            for u in updates:
                offset=max(offset,int(u.get("update_id",0))+1)
                if not SETTINGS["bot"].get("bale_enabled",True):
                    continue
                cq=u.get("callback_query")
                if cq:
                    # Ignore callbacks from groups, channels, and every non-owner private chat.
                    if not is_owner_callback(cq):
                        continue
                    chat=((cq.get("message") or {}).get("chat") or {}).get("id")
                    data=cq.get("data",""); answer_callback(cq.get("id"))
                    if not chat: continue
                    if data=="menu:home": send(chat,"🏠 منوی اصلی\n\nیکی از گزینه‌ها را انتخاب کن:",main_menu())
                    elif data=="menu:prices": send(chat,"💰 قیمت کدام نماد؟",price_menu())
                    elif data.startswith("price:"):
                        try:
                            sym,px,dt=get_price(data.split(":",1)[1]); send(chat,f"💰 {sym}\nPrice: {px}\nTime: {dt}",price_menu())
                        except Exception as e: send(chat,f"❌ قیمت دریافت نشد.\n{e}",price_menu())
                    elif data=="menu:history": send_history(chat)
                    elif data=="menu:strategies": send(chat,"🧠 وضعیت استراتژی‌ها:",strategy_menu())
                    elif data=="menu:status":
                        b=SETTINGS["bot"].get("bale_enabled",True); send(chat,f"📊 سیستم روشن است\nبله: {'🟢 فعال' if b else '🔴 خاموش'}\n\nاستراتژی‌ها مستقل اجرا می‌شوند.",main_menu())
                    elif data.startswith("strategy:info:"):
                        send(chat,strategy_info(data.split(":",2)[2]),strategy_menu())
                    continue
                m=u.get("message") or {}; chat_obj=m.get("chat") or {}; chat=chat_obj.get("id")
                text=(m.get("text") or "").strip()
                # Private chat + configured owner are mandatory. Never auto-discover an owner from an arbitrary message.
                if not is_owner_private_chat(chat_obj):
                    continue
                if not chat: continue
                p=text.split(); cmd=p[0].lower().split("@")[0] if p else ""
                if cmd=="/start": send(chat,"🤖 ربات آماده است. از دکمه‌ها استفاده کن:",main_menu())
                elif cmd=="/help": send(chat,"برای استفاده از ربات نیازی به تایپ دستور نیست؛ از دکمه‌های شیشه‌ای استفاده کن.",main_menu())
                elif cmd=="/price" and len(p)>=2:
                    try:
                        sym,px,dt=get_price(" ".join(p[1:])); send(chat,f"💰 {sym}\nPrice: {px}\nTime: {dt}",price_menu())
                    except Exception as e: send(chat,f"❌ قیمت دریافت نشد.\n{e}",price_menu())
                elif cmd=="/history": send_history(chat)
                elif cmd=="/strategies": send(chat,"🧠 وضعیت استراتژی‌ها:",strategy_menu())
        except Exception as e:
            logging.error("Bale: %s",e); time.sleep(5)

def send_history(chat):
    if not HISTORY: send(chat,"📚 هنوز سیگنالی ثبت نشده.",main_menu()); return
    lines=["📚 آخرین 10 سیگنال:"]
    for x in HISTORY[-10:][::-1]: lines.append(f"{x['strategy']} | {x['symbol']} | {x['direction']} | Entry {x['entry']} | TP {x['tp']}")
    send(chat,"\n".join(lines),main_menu())

def worker_loop(strategy):
    key=strategy.key; event=WORKER_EVENTS[key]
    while True:
        try:
            cfgs=SETTINGS["strategies"].get(key,{})
            enabled=cfgs.get("enabled",False)
            interval=max(1,int(cfgs.get("scan_interval",20) or 20))
            if enabled:
                RUNTIME[key]["status"]="scanning"; RUNTIME[key]["last_scan"]=datetime.now(timezone.utc).isoformat(); RUNTIME[key]["last_error"]=None
                strategy.run_once(); RUNTIME[key]["status"]="ok"
            else:
                RUNTIME[key]["status"]="disabled"
            event.wait(interval); event.clear()
        except Exception as e:
            RUNTIME[key]["status"]="error"; RUNTIME[key]["last_error"]=str(e); logging.error("%s worker: %s",key,e)
            event.wait(2); event.clear()

app=Flask(__name__)
HTML="""<!doctype html><html lang='fa' dir='rtl'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Trading Control Center</title>
<style>*{box-sizing:border-box}body{margin:0;min-height:100vh;font-family:Vazirmatn,Tahoma,Arial;background:#07101d;color:#eef4ff}.wrap{max-width:1000px;margin:auto;padding:24px 14px}.glass{background:rgba(255,255,255,.065);border:1px solid rgba(255,255,255,.11);box-shadow:0 18px 50px #0005;backdrop-filter:blur(18px);border-radius:20px}.header{padding:20px;margin-bottom:14px}.title{font-size:21px;font-weight:800}.muted{color:#94a3b8;font-size:12px;margin-top:5px}.toolbar{display:flex;gap:10px;flex-wrap:wrap;margin-top:15px}.btn{border:1px solid #ffffff1c;background:#ffffff0d;color:#fff;border-radius:12px;padding:10px 14px;cursor:pointer}.btn.active{background:#16a34a55}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px}.card{padding:18px}.row{display:flex;align-items:center;justify-content:space-between;gap:10px}.name{font-size:16px;font-weight:800}.pill{font-size:11px;color:#b9c6d8;margin-top:5px}.settings{margin-top:16px;padding-top:14px;border-top:1px solid #ffffff10;display:grid;gap:10px}.field{display:flex;align-items:center;justify-content:space-between;gap:12px}.field label{font-size:12px;color:#cbd5e1}.input{width:115px;background:#07111f;border:1px solid #ffffff18;color:#fff;border-radius:10px;padding:9px;text-align:center}.small{font-size:11px;color:#8291a7}.switch{width:52px;height:29px;position:relative;display:inline-block}.switch input{display:none}.slider{position:absolute;inset:0;border-radius:99px;background:#334155;cursor:pointer}.slider:before{content:'';position:absolute;width:21px;height:21px;right:4px;top:4px;background:white;border-radius:50%;transition:.2s}input:checked+.slider{background:#16a34a}input:checked+.slider:before{transform:translateX(-23px)}table{width:100%;border-collapse:collapse;font-size:11px;margin-top:12px}td,th{padding:9px;border-bottom:1px solid #ffffff0b;text-align:right}.status{display:flex;align-items:center;gap:8px}.dot{width:9px;height:9px;border-radius:50%;background:#22c55e;box-shadow:0 0 14px #22c55e}.dot.off{background:#ef4444;box-shadow:0 0 14px #ef4444}.modal{position:fixed;inset:0;background:#0008;display:none;align-items:center;justify-content:center;padding:18px}.modal.open{display:flex}.modalbox{width:min(560px,100%);padding:20px}.save{width:100%;margin-top:8px;background:#ffffff0d;border:1px solid #ffffff1b;color:#fff;padding:11px;border-radius:11px;cursor:pointer}</style></head><body><div class='wrap'>
<div class='glass header'><div class='row'><div><div class='title'>🧠 Trading Control Center</div><div class='muted'>کنترل مستقل استراتژی‌ها، زمان‌بندی و بله</div></div><div class='status'><span class='dot {{"off" if not bot.bale_enabled else ""}}'></span><span>{{"بله خاموش" if not bot.bale_enabled else "بله روشن"}}</span></div></div><div class='toolbar'><button class='btn {{"active" if bot.bale_enabled else ""}}' onclick='toggleBale()'>{{"🔴 خاموش کردن بله" if bot.bale_enabled else "🟢 روشن کردن بله"}}</button></div></div>
<div class='grid'>{% for k,v in strategies.items() %}<div class='glass card'><div class='row'><div><div class='name'>{{names.get(k,k)}}</div><div class='pill'>{{descs.get(k,'')}} · {{runtime[k].status}} · آخرین اسکن: {{runtime[k].last_scan or '—'}}</div></div><label class='switch'><input id='en-{{k}}' type='checkbox' {% if v.enabled %}checked{% endif %} onchange='saveStrategy("{{k}}")'><span class='slider'></span></label></div><div class='settings'><div class='field'><label>فاصله اسکن (ثانیه)</label><input class='input' id='int-{{k}}' type='number' min='1' value='{{v.scan_interval}}'></div><div class='field'><label>Cooldown (ثانیه)</label><input class='input' id='cool-{{k}}' type='number' min='0' value='{{v.cooldown}}'></div><div class='field'><label>حداکثر سیگنال روزانه</label><input class='input' id='max-{{k}}' type='text' value='{{"∞" if v.max_signals_per_day in [None,0] else v.max_signals_per_day}}' placeholder='∞'></div><div class='field'><label>یک سیگنال برای هر کندل</label><label class='switch'><input id='one-{{k}}' type='checkbox' {% if v.one_signal_per_candle %}checked{% endif %}><span class='slider'></span></label></div><button class='save' onclick='saveStrategy("{{k}}")'>ذخیره تنظیمات</button></div></div>{% endfor %}</div>
<div class='glass card' style='margin-top:14px'><div class='name'>📚 آخرین سیگنال‌ها</div><table><tr><th>استراتژی</th><th>نماد</th><th>جهت</th><th>Entry</th><th>SL</th><th>TP</th><th>UTC</th></tr>{% for x in history[-20:][::-1] %}<tr><td>{{x.strategy}}</td><td>{{x.symbol}}</td><td>{{x.direction}}</td><td>{{x.entry}}</td><td>{{x.sl}}</td><td>{{x.tp}}</td><td>{{x.time_utc[:16]}}</td></tr>{% endfor %}</table></div></div>
<script>async function saveStrategy(k){const raw=document.getElementById('max-'+k).value.trim();let max=null;if(raw&&raw!=='∞'&&raw.toLowerCase()!=='infinity'){max=parseInt(raw);if(isNaN(max)||max<1){max=null;document.getElementById('max-'+k).value='∞'}}await fetch('/api/strategy/'+k,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:document.getElementById('en-'+k).checked,scan_interval:parseInt(document.getElementById('int-'+k).value)||20,cooldown:parseInt(document.getElementById('cool-'+k).value)||0,max_signals_per_day:max,one_signal_per_candle:document.getElementById('one-'+k).checked})});location.reload()}async function toggleBale(){await fetch('/api/bale',{method:'POST'});location.reload()}</script></body></html>"""

@app.get('/')
def dashboard():
    return render_template_string(HTML,strategies=SETTINGS['strategies'],history=HISTORY,bot=SETTINGS['bot'],runtime=RUNTIME,names={'ny_orb':'NY Open Range Breakout','vwap_wick_rejection':'VWAP Wick Rejection'},descs={'ny_orb':'شکست محدوده ۵ دقیقه اول نیویورک','vwap_wick_rejection':'رد سایه از VWAP؛ سه‌شنبه و ساعات مشخص'})

@app.post('/api/strategy/<key>')
def update_strategy(key):
    if key not in SETTINGS['strategies']: return jsonify(ok=False,error='unknown strategy'),404
    b=request.get_json(silent=True) or {}; s=SETTINGS['strategies'][key]
    s['enabled']=bool(b.get('enabled',s.get('enabled',True))); s['scan_interval']=max(1,int(b.get('scan_interval',s.get('scan_interval',20)))); s['cooldown']=max(0,int(b.get('cooldown',s.get('cooldown',0))))
    mx=b.get('max_signals_per_day',s.get('max_signals_per_day'))
    s['max_signals_per_day']=None if mx in (None,'',0,'0','∞','infinity') else max(1,int(mx)); s['one_signal_per_candle']=bool(b.get('one_signal_per_candle',True)); save_settings(); WORKER_EVENTS[key].set()
    return jsonify(ok=True,settings=s)

@app.post('/api/bale')
def toggle_bale():
    SETTINGS['bot']['bale_enabled']=not SETTINGS['bot'].get('bale_enabled',True); save_settings(); return jsonify(ok=True,enabled=SETTINGS['bot']['bale_enabled'])

@app.get('/api/history')
def history_api(): return jsonify(HISTORY)
@app.get('/api/status')
def status_api(): return jsonify({'bale_enabled':SETTINGS['bot'].get('bale_enabled',True),'strategies':SETTINGS['strategies'],'runtime':RUNTIME})

if __name__=='__main__':
    market_data=MarketDataCache(CFG)
    strategies=[NYORBStrategy(CFG,SETTINGS,record_and_send,send,market_data),VWAPWickRejectionStrategy(CFG,SETTINGS,record_and_send,send,market_data)]
    threading.Thread(target=bale_loop,daemon=True,name='bale-loop').start()
    for s in strategies: threading.Thread(target=worker_loop,args=(s,),daemon=True,name=s.key).start()
    port=int(CFG.get('PORT',8787)); print(f'Dashboard: http://127.0.0.1:{port}')
    app.run(host='127.0.0.1',port=port,debug=False,use_reloader=False)
