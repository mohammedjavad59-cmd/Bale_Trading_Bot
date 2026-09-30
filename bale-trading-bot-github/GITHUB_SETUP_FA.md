# راه‌اندازی ربات روی GitHub Actions

## مهم
GitHub-hosted runner دائمی نیست و هر Job حداکثر ۶ ساعت اجرا می‌شود. این پروژه هر حدود ۵ ساعت و ۴۰ دقیقه اجرای بعدی را با `repository_dispatch` شروع می‌کند و یک Schedule هر ۶ ساعت نیز به‌عنوان fallback دارد.

برای استفاده رایگان از GitHub-hosted runner، Repository را Public نگه دارید؛ GitHub برای Repositoryهای عمومی استفاده از standard runnerها را رایگان اعلام می‌کند. برای Private، GitHub Free فقط ۲۰۰۰ دقیقه در ماه سهمیه دارد.

## Secrets
در Repository به:
Settings → Secrets and variables → Actions → New repository secret

این ۴ Secret را اضافه کنید:

- `BALE_TOKEN` = توکن ربات بله
- `BALE_CHAT_ID` = شناسه چت/کاربر بله، در صورت نیاز
- `OWNER_CHAT_ID` = شناسه مالک/کاربر مجاز
- `TWELVEDATA_API_KEY` = کلید Twelve Data

کد از Environment Variables استفاده می‌کند و Secretها داخل فایل‌های Repository ذخیره نمی‌شوند.

## اجرا
Actions → Trading Bot 24x7 → Run workflow

پس از اجرا، لاگ `Run trading bot` را باز کنید.

## نکته
GitHub Actions برای رباتی که به‌هیچ‌وجه نباید وقفه داشته باشد معادل VPS نیست؛ بین Runnerها احتمال چند ثانیه وقفه و تأخیر زمان‌بندی وجود دارد. همچنین Workflowهای زمان‌بندی‌شده در GitHub ممکن است با تأخیر اجرا شوند.
