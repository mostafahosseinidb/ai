# آشپز تنبل مهاجر — Lazy Immigrant Cook

برنامه‌ی غذایی هفتگیِ فارسی که با زندگی واقعی خانه تغییر می‌کند. PWA موبایل‌اول، راست‌به‌چپ.

Built from the product spec in phases (spec §14). **Status: phases 0, 1 and 2 done.**

## Run it

Requirements: Node 20+ and PostgreSQL 14+.

```bash
cp .env.example .env          # then set DATABASE_URL and AUTH_SECRET (openssl rand -base64 32)
npm install                   # also runs prisma generate
npx prisma migrate deploy     # create the tables
npm run db:seed               # 363 ingredients, 50 recipes, local names for 9 countries (idempotent)
npm run dev                   # http://localhost:3000
```

Sign in with any email: without `EMAIL_SERVER` the 6-digit code is printed in the server console.

| Command                              | What it does                                                                                             |
| ------------------------------------ | -------------------------------------------------------------------------------------------------------- |
| `npm run dev` / `build` / `start`    | Next.js                                                                                                  |
| `npm test`                           | unit tests (Vitest): digits, Jalali dates, scaling, nutrition, plan rules, filler                        |
| `npm run test:e2e`                   | Playwright at 390×844 against `next start` on port 3100 (run `npm run build` first; needs the seeded DB) |
| `npm run lint` · `npm run typecheck` | ESLint · `tsc --noEmit`                                                                                  |
| `npm run seed:check`                 | validates `seed/*.json` (schema, ≥250 ingredients, ≥40 recipes, every key resolves)                      |

## Environment variables

See `.env.example`. Used now: `DATABASE_URL`, `AUTH_SECRET`/`NEXTAUTH_SECRET`, `EMAIL_SERVER`, `EMAIL_FROM`,
`GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` (Google button appears only when set). The rest
(`ANTHROPIC_API_KEY`, `AI_MODEL_*`, `STORAGE_*`, `STRIPE_*`, `VAPID_*`, `SENTRY_DSN`, `POSTHOG_KEY`,
`AI_DAILY_LIMIT_*`) are reserved for later phases.

## What works

**Phase 0 — foundation:** Next.js App Router + strict TypeScript, Tailwind with logical (RTL) utilities,
Vazirmatn, colour tokens with dark mode, Persian strings and digits everywhere (inputs accept both digit
sets), Jalali dates with the Gregorian date beside them, the full data model of spec §8 in Prisma, email
one-time-code + Google sign-in (Auth.js, database sessions), tab bar (امروز · برنامه · خرید · موجودی · بیشتر)
and the assistant button, installable PWA (manifest `dir: rtl`, service worker), CI.

**Phase 1 — recipes and manual plans:**

- 5-step onboarding: country (guesses local languages), members with Mifflin-St Jeor estimate and
  portion factor, per-member diet / weekly limits / intolerances / dislikes, kitchen equipment, plan style.
- Recipe library with search (Persian/English names, ingredients, tags) and filters; recipes needing
  equipment you don't have are hidden by default.
- Recipe page: scaling for one person / the household / N people with smart rounding (whole eggs,
  ¼ spoons, 5 g), step timers, approximate nutrition, freezer/fridge/reheat notes, "night before" steps,
  warnings when a dish doesn't suit a member.
- Plans of 3–14 days: empty (pick each meal, a leftover, or free text) or filled by a simple
  deterministic filler that obeys the household's rules. Every edit is checked against the hard rules
  of §5.3; breaking one asks first.
- Today view: day strip with progress rings, tick, 👍/👎 with "what about it?", eaten out (with
  undo), move to another day/slot (swaps), replace (3 rule-safe alternatives or the library), skip,
  "we also ate…", and tonight's prep tasks generated from recipes' `prep_ahead`.

**Phase 2 — pantry and shopping list:**

- Pantry in three places (fridge, freezer, cupboard): add by Persian or local shelf name, amount,
  opened or not, package date. Expiry is the earliest of package date, shelf life and "after opening";
  green / yellow / red badges and a banner for what's about to go off.
- Ticking a meal takes its ingredients out of the pantry (soonest-expiring first); un-ticking puts
  them back. Plans favour recipes that use what's about to expire.
- Automatic shopping list: plan minus pantry, with unit conversion, rounded up to what you can buy,
  required vs optional, grouped by aisle, local names (e.g. «تخم‌مرغ — Ձու · Яйца куриные») and
  package hints («نیم کیلو», «۱ بطری ۱ لیتری»). Updates on every plan or pantry change.
- «خریدم» puts the bought amount in the pantry (with undo); add your own items; copy as plain text
  for a messenger; shared live between household members.

**Not yet** (later phases): assistant chat and AI planning, recipe import, adding from a photo,
wishlist, batch prep, channel posts, notifications, payments, the offline tick queue.

## Layout

```
app/(app)/…        signed-in pages (today, plan, recipes, settings, placeholders)
app/api/…          route handlers (auth + household check + Zod on every one)
lib/domain/        pure logic: units, scaling, nutrition, rules, autofill (unit-tested)
lib/i18n/          fa.ts (all UI strings), digits, jalali, emergency numbers
lib/server/        DB queries and services
prisma/            schema, migrations, seed script
seed/              ingredients.json, recipes.json, SCHEMA.md
docs/              DECISIONS.md, AI_PROMPTS.md
tests/unit, tests/e2e
```

پیشنهادهای این اپ عمومی است و جای مشاوره با پزشک یا متخصص تغذیه را نمی‌گیرد.
