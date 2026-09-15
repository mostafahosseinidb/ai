# استقرار روی سروری که کار دیگری هم می‌کند

این سند دربارهٔ بردن ChainMind روی ماشینی است که از قبل چیز مهم‌تری روی آن اجرا می‌شود — مثلاً یک بات معاملاتی.

---

## خلاصه: بله، ولی نه بی‌احتیاط

از نظر فنی سبک است: فقط کتابخانهٔ استاندارد پایتون، بدون پایگاه‌داده، بدون کارگزار پیام، بدون GPU. یک بار اجرای عامل چند ده میلی‌ثانیه CPU و چند مگابایت حافظه می‌خواهد.

سه نگرانی واقعی وجود دارد و هر سه قابل رفع‌اند.

## ۱. رقابت بر سر CPU

`--sandbox` بودجهٔ اعتباری عامل را به `RLIMIT_CPU` تبدیل می‌کند. سقف پیش‌فرض `--max-compute-ms` برابر ۳۰٬۰۰۰ است، یعنی **یک ابزارِ از کنترل خارج‌شده می‌تواند ۳۰ ثانیه CPU بسوزاند**. روی یک ماشین دو هسته‌ای که باتی حساس به تأخیر دارد، این یعنی از دست رفتن چند تیک.

این یک مبادله است، نه یک باگ: همان سقفی که جلوی خرجِ بی‌حساب را می‌گیرد، اگر بزرگ باشد اجازهٔ اشغال طولانی می‌دهد. پس کوچکش کنید:

```bash
chainmind run "..." --agent atlas --sandbox --max-compute-ms 2000
```

و در لایهٔ سیستم هم مهارش کنید — واحدهای `deploy/` این کار را می‌کنند:

```ini
Nice=15
CPUSchedulingPolicy=batch
IOSchedulingClass=idle
CPUQuota=25%          # یک‌چهارم یک هسته، سقف سخت
MemoryMax=512M
TasksMax=64
```

`CPUQuota` روی cgroup v2 سقف واقعی است. با `systemd-detect-virt` و `/sys/fs/cgroup/cgroup.controllers` می‌توانید نسخه‌اش را ببینید؛ اسکریپت بررسی این را گزارش می‌کند.

## ۲. دفترکل فقط رشد می‌کند

`ledger.jsonl` هرس نمی‌شود — هنوز نه snapshot دارد نه pruning. برای عاملی که هر ساعت کار می‌کند، هر اجرا چند کیلوبایت اضافه می‌کند. روی دیسک کوچک، این را زیر نظر بگیرید یا دفترکل را روی پارتیشنی جدا از دادهٔ بات بگذارید.

تا وقتی snapshot اضافه نشده، راه ساده این است که دوره‌ای زنجیره را بایگانی و از نو شروع کنید — به شرط نگه‌داشتن فایل قدیمی، چون هر بایگانی یک سند حسابرسی کامل است.

## ۳. مهم‌ترین نکته: این ماشین کلید صرافی دارد

بقیهٔ نگرانی‌ها فنی‌اند؛ این یکی نیست.

سروری که بات معاملاتی روی آن است، کلید API صرافی دارد. اضافه‌کردن یک سرویسِ رو به شبکه به چنین ماشینی تصمیمی است که دلیل بهتری از «راحت‌تر است» می‌خواهد.

* **داشبورد را روی loopback نگه دارید.** پیش‌فرض همین است. `--host 0.0.0.0` موجودی‌ها، نشانی‌ها و کل تاریخچهٔ فعالیت عامل را در دسترس شبکه می‌گذارد و سرور نه TLS دارد نه احراز هویت.
* **برای دیدن داشبورد از تونل SSH استفاده کنید**، نه از باز کردن پورت:

  ```bash
  ssh -N -L 8787:127.0.0.1:8787 you@your-server
  # سپس در مرورگر خودتان: http://127.0.0.1:8787
  ```

* **کاربر جداگانه.** ChainMind را با کاربر `chainmind` اجرا کنید، نه با کاربر بات و نه با root. اگر روزی چیزی در ابزارها اشتباه از آب درآمد، نباید بتواند فایل‌های بات را بخواند.

  ```bash
  sudo useradd --system --home /var/lib/chainmind --shell /usr/sbin/nologin chainmind
  sudo install -d -o chainmind -g chainmind -m 750 /var/lib/chainmind
  ```

* **`keys/` را جدی بگیرید.** هر که `atlas.key` را داشته باشد، همان عامل است. `chainmind init` مجوز فایل را روی ۶۰۰ می‌گذارد؛ آن را در پشتیبان‌گیریِ مشترک با بات نریزید.

## نصب

```bash
sudo git clone <repo> /opt/chainmind
cd /opt/chainmind
python3 -m unittest discover -s tests          # قبل از اعتماد، اجرا کنید

sudo useradd --system --home /var/lib/chainmind --shell /usr/sbin/nologin chainmind
sudo install -d -o chainmind -g chainmind -m 750 /var/lib/chainmind

sudo -u chainmind python3 -m chainmind.cli --workspace /var/lib/chainmind \
     init --chain-id prod --supply 1000 --agent-name atlas \
     --limit tool_call=20 --limit llm_output_tokens=50000 --epoch-length 24
sudo -u chainmind python3 -m chainmind.cli --workspace /var/lib/chainmind grant atlas 5

sudo cp deploy/chainmind-*.service deploy/chainmind-agent.timer /etc/systemd/system/
sudo cp deploy/chainmind.env.example /etc/default/chainmind   # هدف عامل را اینجا بنویسید
sudo systemctl daemon-reload
sudo systemctl enable --now chainmind-agent.timer chainmind-dashboard.service
```

بررسی:

```bash
systemctl list-timers chainmind-agent.timer
journalctl -u chainmind-agent -n 50
sudo -u chainmind python3 -m chainmind.cli --workspace /var/lib/chainmind verify
```

## چرا زمان‌سنج، نه سرویس دائمی

واحد عامل `Type=oneshot` است و با یک `timer` بیدار می‌شود. این شکلِ صادقانهٔ کار است: عامل کار می‌کند، آنچه خرج کرده را تسویه می‌کند و تمام می‌شود. بین اجراها چیزی در حافظه انباشته نمی‌شود، و اجرایی که گیر کند با `TimeoutStartSec` محدود می‌شود نه با امید.

`RandomizedDelaySec=300` هم هست تا عامل دقیقاً سرِ ساعت — همان لحظه‌ای که بات معمولاً شلوغ‌ترین است — بیدار نشود.

## آنچه هنوز برای تولید آماده نیست

صریح باشیم:

* **تک‌گره.** لایهٔ شبکه پیاده نشده. اگر آن سرور از بین برود، دفترکل هم می‌رود. پشتیبان بگیرید.
* **بدون هرس.** دفترکل بی‌نهایت رشد می‌کند.
* **مرجع تک‌امضایی.** هر که کلید `authority` را داشته باشد می‌تواند قیمت‌ها را صفر کند. حکمرانی چنداَمضایی هنوز نیست.
* **`--sandbox` فقط POSIX.** روی ویندوز به اندازه‌گیری درون‌فرایندی برمی‌گردد و آن شکاف دوباره باز می‌شود.

## قبل از هر چیز: بررسی میزبان

```bash
bash scripts/survey-host.sh > chainmind-survey.txt
```

فقط می‌خواند؛ چیزی نصب یا اجرا نمی‌کند. و عمداً **آرگومان فرایندها، متغیرهای محیطی، فایل‌های `.env` و هیچ کلیدی را چاپ نمی‌کند** — چون بات معاملاتی معمولاً با کلید API در خط فرمانش اجرا می‌شود و `ps aux` آن را لو می‌دهد.
