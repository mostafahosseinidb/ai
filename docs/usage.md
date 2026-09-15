# راهنمای کامل استفاده

از صفر تا داشبورد. هیچ‌چیز جز پایتون ۳٫۱۰ به بالا لازم نیست.

---

## ۰. نصب

```bash
git clone <repo> && cd ai
python3 -m unittest discover -s tests      # ۱۷۸ آزمون، باید همه سبز باشند
```

برای داشتن فرمان `chainmind` در مسیر سیستم (اختیاری):

```bash
pip install -e .
```

بدون نصب هم همه‌چیز کار می‌کند؛ کافی است به‌جای `chainmind` بنویسید `python3 -m chainmind.cli`.

---

## ۱. ساختن شبکه

```bash
chainmind init --chain-id my-net --supply 500 --agent-name atlas \
               --limit tool_call=6 --limit llm_output_tokens=4000 \
               --epoch-length 200
```

این کار یک پوشهٔ کاری (پیش‌فرض `.chainmind/`) می‌سازد شامل:

```
.chainmind/
  ledger.jsonl        دفترکل: خط اول پیکربندی جنسیس، بقیه بلاک‌ها
  keys/authority.key  کلید خصوصی مرجع  (۶۰۰، فقط برای شما خواندنی)
  keys/atlas.key      کلید خصوصی عامل
```

| گزینه | معنی |
|---|---|
| `--supply` | کل اعتباری که تا ابد وجود خواهد داشت (به اعتبار) |
| `--limit R=N` | سقف مصرف منبع `R` در هر دوره؛ تکرارشدنی |
| `--price R=N` | تغییر قیمت پایه به میکرو-اعتبار بر واحد؛ تکرارشدنی |
| `--epoch-length` | طول هر دوره بر حسب بلاک |
| `--consensus` | `poa` (پیش‌فرض) یا `pow` |

> **کلیدهای خصوصی را جدی بگیرید.** هر که `atlas.key` را داشته باشد، *همان* عامل است. این پوشه را در گیت نگذارید — `.gitignore` پروژه از قبل جلویش را گرفته.

---

## ۲. تأمین بودجهٔ عامل

```bash
chainmind grant atlas 3                 # سه اعتبار از خزانه به atlas
chainmind grant atlas 0.5 --memo "شارژ دورهٔ دوم"
```

اعتبار از هیچ ساخته نمی‌شود؛ از خزانهٔ مرجع منتقل می‌شود. مقصد می‌تواند نام کلید در پوشهٔ کاری باشد یا یک نشانی ۶۴ حرفی.

---

## ۳. به کار انداختن عامل

```bash
chainmind run "یک سیاست جیره‌بندی برای خودت بنویس" --agent atlas
```

خروجی، سرنوشت هر اقدام را می‌گوید:

```
goal      یک سیاست جیره‌بندی برای خودت بنویس
agent     9ae6eeee9975ca50...
metering  in-process

  ok  think: 0.000201 cr
  refused  remember: epoch quota for storage_bytes allows 0 more units, the action needs 58

spent     0.000201 cr
balance   2.999799 cr
```

`refused` یعنی ابزار **اجرا نشد** — نه اینکه اجرا شد و بعد هشدار گرفت.

### با اندازه‌گیری بیرون‌فرایندی

```bash
chainmind run "کار سنگین انجام بده" --agent atlas --sandbox --max-compute-ms 5000
```

با `--sandbox`، ابزار در یک فرایند فرزند اجرا می‌شود و:

* زمان CPU از **هسته** خوانده می‌شود (`os.wait4`)، نه از اظهار ابزار؛
* بودجهٔ اعتباری عامل به `RLIMIT_CPU` واقعی تبدیل می‌شود، پس مصرف بیش از توان پرداخت **ناممکن** است نه فقط غیرمجاز؛
* سقوط یا حلقهٔ بی‌نهایتِ ابزار، فرایند اصلی را از پا درنمی‌آورد.

هزینه‌اش: آرگومان‌ها و مقدار بازگشتی باید از JSON عبور کنند، و تغییر اشیای درون‌حافظه‌ای به فرایند والد برنمی‌گردد.

---

## ۳٫۵ پرامپت بده، جواب بگیر

این رایج‌ترین شکل استفاده است.

```bash
pip install 'chainmind[model]'
export ANTHROPIC_API_KEY=...        # یا: ant auth login

chainmind ask "خلاصهٔ این معماری را در سه جمله بنویس"
chainmind ask - < prompt.txt                     # پرامپت از ورودی استاندارد
chainmind ask "..." --quiet > answer.txt         # فقط جواب
chainmind ask "..." --effort low --max-tokens 500
chainmind ask "..." --sandbox                    # زمان CPU هم اندازه‌گیری شود
```

جواب روی `stdout` می‌رود و حسابداری روی `stderr`، پس تغییر مسیر خروجی فقط جواب را نگه می‌دارد.

| کد خروج | یعنی |
|---|---|
| `0` | جواب گرفته شد |
| `1` | تماس شکست خورد، یا خودِ مدل درخواست را رد کرد |
| `2` | **زنجیره اجازه نداد** — هیچ درخواستی فرستاده نشد |

ترتیب کار همان ترتیب همیشگی است، ولی این بار برآورد دقیق است نه تخمینی:

1. `messages.count_tokens` تعداد **واقعی** توکن ورودی را می‌گیرد.
2. زنجیره همان عدد به‌علاوهٔ سقف خروجی (`--max-tokens`) را تأیید یا رد می‌کند.
3. اگر تأیید شد، تماس زده می‌شود.
4. `response.usage` — یعنی شمارش خودِ ارائه‌دهنده — به‌عنوان `measured=provider` ثبت می‌شود.

متغیرهای محیطی: `CHAINMIND_MODEL` و `CHAINMIND_EFFORT` پیش‌فرض‌ها را عوض می‌کنند.

> `--max-tokens` فقط سقف جواب نیست؛ **همان چیزی است که از پیش تأیید می‌شود**. عدد بزرگ یعنی رزرو بزرگ، حتی اگر جواب کوتاه باشد. اگر عامل بی‌دلیل رد می‌شود، اول این را کم کنید.

## ۴. حکمرانی: تغییر قیمت و سهمیه

```bash
chainmind policy --limit llm_output_tokens=200 --memo "کنترل پرگویی"
chainmind policy --price tool_call=20000
```

فوراً اثر می‌کند. عاملی که وسط کار است، در اقدام بعدی با سقف تازه روبه‌رو می‌شود. فقط مرجعِ جنسیس می‌تواند این کار را بکند.

---

## ۵. حسابرسی و اعتبارسنجی

```bash
chainmind status                       # حساب‌ها، قیمت‌ها، سهمیه‌ها
chainmind audit --address atlas        # ریز مصرف‌ها
chainmind audit --all-types --limit 50 # همراه با تراکنش‌های غیرمصرفی
chainmind verify                       # بازپخش کامل از جنسیس
```

`verify` هیچ‌چیزِ در حافظه را باور نمی‌کند: از بلاک جنسیس شروع می‌کند، هر تراکنش را دوباره اجرا می‌کند و ریشهٔ حالتِ هر بلاک را می‌سنجد. اگر کسی یک رکورد مصرف را دستکاری کرده باشد، اینجا لو می‌رود.

هر فرمان `--json` هم می‌پذیرد، برای وصل‌کردن به ابزارهای دیگر.

---

## ۶. داشبورد بصری

```bash
chainmind serve                        # http://127.0.0.1:8787
chainmind serve --port 9000
```

داشبورد فارسی و راست‌به‌چپ است و نشان می‌دهد:

* شاخص‌های کلیدی، از جمله **سهم اندازه‌گیری هسته** — چه کسری از صورتحساب واقعاً تأیید شده و چه کسری مورد اعتماد است؛
* تفکیک مصرف بر حسب منبع، با رنگی که منشأ عدد را می‌گوید؛
* هزینهٔ تجمعی در طول زنجیره؛
* موجودی عامل‌ها، رویدادها (هدف‌ها و امتناع‌ها)، رکوردهای مصرف و بلاک‌ها؛
* دکمهٔ «بازپخش و اعتبارسنجی» که همان `verify` را از داخل مرورگر اجرا می‌کند.

نمای جدولی، تم روشن/تاریک و به‌روزرسانی خودکار هم دارد. فونت وزیرمتن داخل خود بسته است، پس بدون اینترنت هم درست نمایش داده می‌شود.

---

## ۷. به‌عنوان کتابخانه

```python
from chainmind import (
    AgentKernel, Chain, GenesisConfig, LocalLedger,
    ProofOfAuthority, SandboxExecutor, SigningKey, build_grant,
)

authority = SigningKey.generate()
chain = Chain.create(
    GenesisConfig(authorities={authority.public_hex(): 1_000_000_000},
                  limits={"tool_call": 10}),
    authority, ProofOfAuthority([authority.public_hex()]),
    path="ledger.jsonl",
)
ledger = LocalLedger(chain, authority)

atlas = AgentKernel(SigningKey.generate(), ledger, label="atlas",
                    executor=SandboxExecutor())      # اندازه‌گیری بیرون‌فرایندی
chain.seal_block(authority, [build_grant(authority, 0,
                 beneficiary=atlas.address, amount=1_000_000)])

for outcome in atlas.run("امروز چه کاری بیشترین ارزش را دارد؟"):
    print(outcome.summary())

chain.verify()
chain.state.check_invariants()
```

### افزودن ابزار خودتان

```python
from chainmind import Meter, ResourceKind, Tool, default_registry

def summarise(meter: Meter, text: str) -> dict:
    # اگر عدد را از خود سرویس می‌گیرید، با source="provider" ثبتش کنید تا
    # حسابرس بداند این رقم اظهارِ شما نیست.
    meter.record(ResourceKind.LLM_INPUT_TOKENS, len(text) // 4, source="provider")
    meter.record(ResourceKind.LLM_OUTPUT_TOKENS, 120, source="provider")
    return {"summary": text[:200]}          # برای جعبهٔ شنی باید JSON-پذیر باشد

registry = default_registry()
registry.register(Tool(
    name="summarise",
    description="خلاصه‌سازی یک متن",
    run=summarise,
    estimate=lambda kw: {                    # برآورد پیش از اجرا، الزامی است
        ResourceKind.LLM_INPUT_TOKENS: len(str(kw.get("text", ""))) // 4,
        ResourceKind.LLM_OUTPUT_TOKENS: 150,
    },
))
```

`estimate` اختیاری نیست: زنجیره پیش از اجرا باید بداند چه چیزی را تأیید می‌کند.

---

## ۸. آیا سرور لازم است؟

**برای کار کردن، نه.** همه‌چیز محلی است: دفترکل یک فایل است و `serve` فقط یک نمای فقط-خواندنی روی همان فایل باز می‌کند که به `127.0.0.1` گوش می‌دهد.

سرور وقتی لازم می‌شود که یکی از این‌ها را بخواهید:

| خواسته | حداقلِ لازم |
|---|---|
| عامل ۲۴ ساعته کار کند | یک VPS کوچک: ۱ vCPU، ۱GB رم، ۱۰GB دیسک |
| داشبورد از بیرون قابل دسترس باشد | همان، به‌علاوهٔ HTTPS و احراز هویت جلوی آن |
| چند گره، اجماع واقعی | ۳ گره یا بیشتر، هرکدام ۲ vCPU / ۲GB — ولی **لایهٔ شبکه هنوز پیاده نشده** |
| مدل واقعی (`chainmind ask`) | فقط دسترسی شبکه به ارائه‌دهنده و یک کلید API؛ GPU لازم نیست |

نکته‌ها اگر روی سرور بردید:

* `chainmind serve --host 0.0.0.0` داشبورد را در دسترس شبکه می‌گذارد. **این کار موجودی‌ها، نشانی‌ها و کل تاریخچهٔ فعالیت عامل را عمومی می‌کند.** پشت یک reverse proxy با TLS و احراز هویت بگذاریدش؛ سرور خودش هیچ‌کدام را ندارد.
* پوشهٔ `keys/` هرگز نباید روی ماشینی باشد که سرویس عمومی می‌دهد. سرور آن پوشه را باز نمی‌کند، ولی هر کس که به ماشین دسترسی داشته باشد می‌تواند.
* دفترکل فقط رشد می‌کند. برای عاملی که مدام کار می‌کند، فایل به‌مرور بزرگ می‌شود؛ هرس‌کردن یا snapshot هنوز پیاده نشده.
* `--sandbox` به `fork` و `wait4` نیاز دارد، یعنی لینوکس یا مک. روی ویندوز به اندازه‌گیری درون‌فرایندی برمی‌گردد.
