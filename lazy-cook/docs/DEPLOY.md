# راه‌اندازی واقعی — Deploy guide

هدف: اپ روی یک آدرس واقعی (مثلاً `https://ashpaz.example.com`)، قابل نصب روی گوشی، برای خودتان و چند خانوار آشنا.
پیشنهاد: **Vercel** (اپ) + **Supabase** (پایگاه داده) + **Resend** یا **Brevo** (ایمیل کد ورود). برای این حجم هر سه پلن رایگان دارند.

زمان تقریبی: ۴۵ دقیقه.

---

## ۱. پایگاه داده — Supabase

1. در [supabase.com](https://supabase.com) یک پروژه بسازید. Region نزدیک کاربرها (برای اروپا و ارمنستان: Frankfurt).
2. Project Settings → Database → **Connection string** → URI. دو آدرس لازم است:
   - **Transaction pooler** (پورت `6543`) → این می‌شود `DATABASE_URL`. آخرش اضافه کنید: `?pgbouncer=true&connection_limit=1`
   - **Session pooler / Direct** (پورت `5432`) → این می‌شود `DIRECT_URL` (فقط برای مهاجرت‌ها).
3. از کامپیوتر خودتان، یک بار جدول‌ها و داده‌ی اولیه را بسازید:

```bash
cd lazy-cook
DATABASE_URL="…6543…?pgbouncer=true&connection_limit=1" DIRECT_URL="…5432…" npx prisma migrate deploy
DATABASE_URL="…5432…" npm run db:seed
```

(seed را با آدرس مستقیم اجرا کنید؛ ۳۶۳ ماده، ۵۰ دستور و اسم‌های محلی را می‌نویسد و تکرارش بی‌خطر است.)

## ۲. ایمیل کد ورود

یکی از این‌ها:

- **Resend** ([resend.com](https://resend.com)): دامنه را اضافه و تأیید کنید (رکوردهای DNS)، یک API key بسازید.
  `EMAIL_SERVER="smtp://resend:API_KEY@smtp.resend.com:587"`
- **Brevo** ([brevo.com](https://www.brevo.com)): SMTP & API → SMTP key.
  `EMAIL_SERVER="smtp://LOGIN:SMTP_KEY@smtp-relay.brevo.com:587"`

`EMAIL_FROM="آشپز تنبل <no-reply@your-domain>"` — دامنه‌ی فرستنده باید همان دامنه‌ی تأییدشده باشد (SPF/DKIM)، وگرنه کدها به اسپم می‌روند.

> بدون `EMAIL_SERVER` کد فقط در لاگ سرور چاپ می‌شود؛ برای محیط واقعی حتماً تنظیمش کنید، یا فقط ورود با Google را فعال نگه دارید.

## ۳. ورود با Google (اختیاری، پیشنهادی)

1. [Google Cloud Console](https://console.cloud.google.com/) → APIs & Services → OAuth consent screen (External، اسم اپ، ایمیل پشتیبانی).
2. Credentials → Create OAuth client ID → Web application.
3. Authorized redirect URI: `https://YOUR-DOMAIN/api/auth/callback/google`
4. `GOOGLE_CLIENT_ID` و `GOOGLE_CLIENT_SECRET` را بردارید. دکمه‌ی «ورود با گوگل» فقط وقتی این دو تنظیم شده باشند نمایش داده می‌شود.

## ۴. اپ — Vercel

1. در [vercel.com](https://vercel.com) → Add New Project → همین مخزن GitHub.
2. **Root Directory: `lazy-cook`** (مهم؛ مخزن پروژه‌ی دیگری هم دارد).
3. Framework: Next.js (خودکار). Build command را خالی بگذارید: اسکریپت `vercel-build` در `package.json` اول مهاجرت‌های پایگاه داده (`prisma migrate deploy`) و بعد build را اجرا می‌کند، پس هر انتشار جدید جدول‌ها را هم به‌روز می‌کند.
4. Environment Variables:

| متغیر                                      | مقدار                                                      |
| ------------------------------------------ | ---------------------------------------------------------- |
| `DATABASE_URL`                             | آدرس pooler (۶۵۴۳) با `?pgbouncer=true&connection_limit=1` |
| `DIRECT_URL`                               | آدرس مستقیم (۵۴۳۲)                                         |
| `AUTH_SECRET`                              | خروجی `openssl rand -base64 32` (هرگز در کد نگذارید)       |
| `EMAIL_SERVER`, `EMAIL_FROM`               | از مرحله‌ی ۲                                               |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | از مرحله‌ی ۳ (اختیاری)                                     |

5. Settings → Functions → Region را همان نزدیکی پایگاه داده بگذارید (مثلاً `fra1`).
6. Deploy. بعد Settings → Domains برای دامنه‌ی خودتان؛ اگر دامنه عوض شد، redirect URI گوگل را هم عوض کنید.

## ۵. امتحان بعد از انتشار (۵ دقیقه)

- [ ] صفحه‌ی اول باز می‌شود، راست‌به‌چپ و فارسی.
- [ ] ورود با ایمیل: کد می‌رسد (پوشه‌ی اسپم را هم ببینید) و وارد می‌شوید.
- [ ] onboarding کامل می‌شود و «خودت بچین» برنامه می‌سازد.
- [ ] تنظیمات → «دعوت یه نفر دیگه» → لینک را با گوشی دوم باز کنید → «بپیوند» → همان برنامه دیده می‌شود.
- [ ] در «خرید» یکی تیک می‌زند، دیگری ظرف چند ثانیه می‌بیند.

## ۶. نصب روی گوشی

- **Android (Chrome):** منوی ⋮ → «Add to Home screen» / «Install app».
- **iPhone (Safari):** دکمه‌ی Share → «Add to Home Screen».

## ۷. نگه‌داری

- **پشتیبان:** Supabase پلن رایگان پشتیبان خودکار روزانه ندارد؛ هر از گاهی Database → Backups یا `pg_dump` با `DIRECT_URL`.
- **انتشار نسخه‌ی جدید:** push به شاخه‌ی اصلی؛ Vercel خودش build و مهاجرت را انجام می‌دهد.
- **داده‌ی اولیه‌ی جدید** (دستور یا اسم محلی تازه): `npm run db:seed` با `DATABASE_URL` مستقیم؛ تکرارش بی‌خطر است.
- **حریم خصوصی:** هر کاربر از تنظیمات → «اطلاعاتت» می‌تواند همه‌چیز را دانلود یا حسابش را برای همیشه حذف کند.

## چیزهایی که هنوز نیست (و برای بتای کوچک لازم هم نیست)

دستیار هوش مصنوعی و وارد کردن از عکس (فاز ۳)، اعلان‌ها و پرداخت (فاز ۴)، صف تیک در حالت آفلاین، Sentry/PostHog.
