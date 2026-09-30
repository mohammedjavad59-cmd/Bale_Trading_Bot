ربات چنداستراتژی Bale — NY ORB + VWAP Wick Rejection

استراتژی‌های فعلی:
1) NY ORB — روشن پیش‌فرض
2) VWAP Wick Rejection — روشن پیش‌فرض

VWAP Wick Rejection از منطق اکسپرت VWAP_Wick_Rejection_Tuesday_M1 نسخه 2.10 منتقل شده است:
- XAU/USD
- تایم‌فریم M1
- سه‌شنبه فقط
- ساعت‌های سرور: 06، 09، 17، 18، 23
- Daily VWAP بر پایه Typical Price × Tick Volume
- VWAP band = 100 points
- BUY: low <= VWAP و open/close بالای VWAP و close > open
- SELL: high >= VWAP و open/close زیر VWAP و close < open
- SL: مطابق منطق اکسپرت
- RR = 1.50
- حداکثر spread = 80 points، فقط اگر API فعلی bid/ask را ارائه کند
- EntryBufferPoints در اکسپرت تعریف شده ولی در منطق معامله استفاده نشده؛ در این نسخه نیز عمداً اعمال نشده است.

نکته مهم درباره تفاوت MT5 و Signal Bot:
- اکسپرت MT5 از قیمت Bid/Ask واقعی بروکر و tick_volume بروکر استفاده می‌کند.
- این ربات سیگنال از Twelve Data استفاده می‌کند؛ بنابراین قیمت/حجم/VWAP ممکن است دقیقاً با XAUUSD.x بروکر یکسان نباشد.
- چون ربات معامله واقعی باز نمی‌کند، Entry سیگنال برابر Close آخرین کندل بسته‌شده است؛ اکسپرت اصلی در زمان اجرای معامله از Ask برای BUY و Bid برای SELL استفاده می‌کند.
- ساعت‌های این استراتژی از timestamp سرور بروکر با offset فعلی UTC-03:30 شبیه‌سازی شده‌اند. اگر ساعت سرور بروکر تغییر کرد، باید BROKER_UTC_OFFSET و منطق timezone به‌روز شود.

اجرا:
1) BALE_TOKEN و TWELVEDATA_API_KEY را در config.json قرار بده.
2) run.bat را اجرا کن.
3) در Bale به bot پیام /start بده.
4) پنل: http://127.0.0.1:8787
5) برای روشن/خاموش کردن استراتژی‌ها از پنل استفاده کن.

این پروژه فقط سیگنال می‌دهد و معامله خودکار انجام نمی‌دهد.


## محدودیت دسترسی بله
- ربات فقط در چت خصوصی (`private`) با `OWNER_CHAT_ID` پاسخ می‌دهد.
- پیام، دکمه یا callback از هر کاربر/گروه دیگر نادیده گرفته می‌شود.
- همه پیام‌های خروجی ربات نیز فقط به `OWNER_CHAT_ID` ارسال می‌شوند.
- برای امنیت، شناسه مالک به‌صورت خودکار از اولین پیام ناشناس کشف نمی‌شود.
