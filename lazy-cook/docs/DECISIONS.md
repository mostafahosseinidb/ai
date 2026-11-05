# Decisions

Choices the spec leaves open (spec §0.3), one line of reasoning each.

## Repository & tooling

- **App lives in `lazy-cook/`** — this repository already contains an unrelated Python project (ChainMind); a subfolder keeps both intact.
- **Pinned to mature majors: Next 15.5, Prisma 6, TypeScript 5.9, ESLint 9, Vitest 3, Zod 3** — newer majors exist, but these are stable and well-documented; upgrading later is mechanical.
- **Playwright pinned to 1.63.0** — matches the browser preinstalled in CI/dev containers.
- **No Husky/lint-staged** — git hooks live at the repository root, which belongs to another project; CI enforces lint, types, tests and build instead.
- **ESLint CLI, not `next lint`** — `next lint` is deprecated.

## Data

- **camelCase fields, snake_case table names (`@@map`)** — Prisma/TS idiom in code, spec names in the database; Auth.js adapter fields keep their required names.
- **Plan dates are `@db.Date`, handled as `YYYY-MM-DD` strings in domain code** — calendar days have no time zone; avoids off-by-one bugs around midnight/DST.
- **`recipes.slug`** — stable key so the seeder is idempotent and tests can refer to seed recipes.
- **Ingredient `flags`** (red_meat, lactose, gluten, animal_product…) — diet, intolerance and weekly-limit rules are computed in code from ingredients, never guessed.
- **`households.autoDeductPantry`, `members.position`, `change_log.batchId`, `recipe_ingredients.position`, `recipes.equipmentAlternativesFa`** — small additions the spec's features need (pantry toggle §5.5, member order, undoing a batch of assistant changes, ingredient order, "pan version" text §5.2).
- **Encryption at rest is the database's (Supabase/Postgres disk encryption)** — column-level encryption would block querying; access is restricted per household in every route.
- **Seed recipes were written for this project** (titles of well-known dishes, our own wording); ingredient keys are reconciled and checked by `npm run seed:check`.

## Rules & planning

- **Hard rules live in `lib/domain/rules.ts`, pure and unit-tested** — the same validator checks manual edits now and AI plans in phase 3 (`lib/ai/validators.ts` will call it).
- **A per-member substitution (`member_notes[memberId]`) waives that member's forbidden-ingredient and weekly-limit check** — spec §5.3 rule 2 "unless with a specific substitute".
- **Leftovers count toward weekly limits** — the member eats the red meat again.
- **Weekly limits are checked over every rolling 7-day window, including windows that start before the plan** — matches acceptance #2 "any 7 consecutive days".
- **Free-text dislikes match recipe names (fa/en/slug) after normalisation** — e.g. «اوورنایت اوتس» blocks `overnight_oats`.
- **Phase 1 "fill it for me" is a deterministic, seeded, rule-checked greedy filler** — spec phase 1 asks for manual plans; 24 manual picks is tedious, and this keeps the rules honest before AI arrives.
- **Leftovers in the filler: on non-cooking days, lunch is the previous cooked main** (if within its `fridge_days`), dinner is something quick.
- **Prep tasks: lead ≥ 6 h → evening before at 21:00 (or 1 h before bedtime); shorter → same day** — "the night before" (§5.3 rule 7) for soaking; same-day for marinating etc.
- **Prep tasks are re-derived after every meal change** (open ones replaced, done ones kept) — moving a meal moves its task.
- **Manual edits that break a hard rule return 409 with the list; the user may "do it anyway" (`force`)** — the app adapts rather than blocks (§3.4), but never silently.
- **Moving a meal onto an occupied slot swaps the two.**
- **One rating per meal per member (null = household); re-rating replaces it.**

## Auth

- **Email one-time code via Auth.js's email provider with a 6-digit token** — Auth.js stores it hashed; the code page calls the standard callback. Codes expire in 10 minutes. Without `EMAIL_SERVER` the code is printed to the server log (development only).
- **Database sessions** — simplest secure cookie session with the Prisma adapter; route protection happens in the server layout and in every API handler.
- **TODO before launch:** rate-limit `/api/auth/signin` and code attempts per email/IP (spec doesn't specify; needs a store such as Redis/Upstash).
- **`NEXTAUTH_URL` is optional** (`trustHost`) — set it only behind proxies.

## UI

- **Tailwind v4 with logical utilities (`ms-`, `pe-`, `start-`)**, colour tokens as CSS variables with a `prefers-color-scheme` dark set — spec §6.2.
- **Vazirmatn self-hosted via `@fontsource`** — works offline and in CI without fetching Google Fonts.
- **Bottom sheets use native `<dialog>`** — focus trap, Esc and backdrop without a dependency; shadcn/ui can be layered on later.
- **Server components + small JSON route handlers + `router.refresh()`** for phase 1; TanStack Query is installed and provided for the offline queue in phase 3.
- **Loading skeletons only on `/recipes/[id]`** — a `loading.tsx` above pages that navigate to themselves with new search params (`/today?d=`, `/recipes?type=`) made those navigations silently not commit in production builds of Next 15.5; those pages render fast server-side anyway.
- **`prefetch={false}` on long link lists** (recipe library, day strip, meal titles) — prefetching ~50 dynamic recipe pages per visit costs ~50 server renders and delayed taps.
- **Hand-written service worker (`public/sw.js`)** rather than Serwist/next-pwa — network-first pages with cache fallback is all phase 0 needs, and it avoids a webpack plugin.
