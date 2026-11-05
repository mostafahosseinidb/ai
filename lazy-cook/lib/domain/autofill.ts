/**
 * Simple, deterministic plan filler for phase 1 (no AI). It only picks from the library and every
 * choice passes the hard rules in rules.ts. Phase 3 replaces this with generatePlan, which is
 * validated by the same rules.
 */
import { addDaysISO, diffDaysISO, type ISODate } from "@/lib/i18n/jalali";
import { validatePlan, type CandidateMeal, type RecipeFacts, type RuleContext } from "./rules";

export const SLOT_MEAL_TYPES: Record<string, string[]> = {
  breakfast: ["breakfast"],
  mid_morning: ["snack", "drink"],
  lunch: ["lunch"],
  after_lunch: ["dessert", "snack", "drink"],
  snack: ["snack", "dessert", "drink"],
  dinner: ["dinner"],
  late: ["drink", "snack"],
};

export interface AutofillRecipe extends RecipeFacts {
  cuisine: string;
  tags: string[];
  fridgeDays: number | null;
  kcal: number | null;
}

export interface AutofillInput {
  startDate: ISODate;
  days: number;
  slots: string[];
  cookingDaysPerWeek: number;
  iranianEveryDays: number;
  recipes: AutofillRecipe[];
  ctx: RuleContext;
  /** meals already in the plan (kept, and counted by the rules) */
  existing?: CandidateMeal[];
  /** recipes the household made recently, most recent date per recipe */
  recentlyCooked?: Map<string, ISODate>;
  seed?: number;
}

/** mulberry32 — tiny seeded PRNG so a given input always yields the same plan. */
function rng(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function hashString(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return h >>> 0;
}

/** Spread N cooking days over a week: e.g. 4 → cook on days 0,2,4,6 of each week. */
export function isCookingDay(dayIndex: number, cookingDaysPerWeek: number): boolean {
  const n = Math.max(1, Math.min(7, cookingDaysPerWeek));
  if (n >= 7) return true;
  const d = dayIndex % 7;
  // Bresenham-style distribution
  return Math.floor(((d + 1) * n) / 7) !== Math.floor((d * n) / 7);
}

export function autofillPlan(input: AutofillInput): CandidateMeal[] {
  const random = rng(input.seed ?? hashString(input.startDate));
  const plan: CandidateMeal[] = [...(input.existing ?? [])];
  const taken = new Set(plan.map((m) => `${m.date}|${m.slot}`));
  const byId = new Map(input.recipes.map((r) => [r.id, r]));
  const usedCount = new Map<string, number>();
  for (const m of plan)
    if (m.recipeId) usedCount.set(m.recipeId, (usedCount.get(m.recipeId) ?? 0) + 1);

  let lastIranian: ISODate | null = null;
  for (const [id, date] of input.recentlyCooked ?? []) {
    if (byId.get(id)?.cuisine === "iranian" && (!lastIranian || date > lastIranian))
      lastIranian = date;
  }

  // pantry items that expire within the window and that no meal has used yet
  const expiryLimit = addDaysISO(input.startDate, 3);
  const unusedExpiring = new Set(
    (input.ctx.pantry ?? []).filter(
      (p) => p.expiry && p.expiry <= expiryLimit && p.expiry >= input.startDate,
    ),
  );
  const expiringUsedBy = (r: AutofillRecipe, date: ISODate) =>
    [...unusedExpiring].filter(
      (p) =>
        date <= p.expiry! &&
        ((p.ingredientId && r.ingredientIds?.includes(p.ingredientId)) ||
          (p.ingredientKey && r.ingredientKeys.includes(p.ingredientKey))),
    );

  const orderedSlots = [
    "breakfast",
    "mid_morning",
    "lunch",
    "after_lunch",
    "snack",
    "dinner",
    "late",
  ].filter((s) => input.slots.includes(s));

  for (let d = 0; d < input.days; d++) {
    const date = addDaysISO(input.startDate, d);
    const cooking = isCookingDay(d, input.cookingDaysPerWeek);

    for (const slot of orderedSlots) {
      if (taken.has(`${date}|${slot}`)) continue;

      // Leftovers are a feature (spec §3.2): on non-cooking days, eat what was cooked last.
      // One leftover lunch per non-cooking day; dinner that day is something quick.
      if (slot === "lunch" && !cooking) {
        const source = latestMain(plan, date, slot);
        const r = source?.recipeId ? byId.get(source.recipeId) : undefined;
        if (source && r && (r.fridgeDays ?? 1) >= diffDaysISO(date, source.date)) {
          const leftover: CandidateMeal = {
            date,
            slot,
            recipeId: r.id,
            leftoverOf: { date: source.date, slot: source.slot },
          };
          const before = validatePlan(plan, input.ctx).length;
          if (validatePlan([...plan, leftover], input.ctx).length === before) {
            plan.push(leftover);
            taken.add(`${date}|${slot}`);
            continue;
          }
        }
      }

      const types = SLOT_MEAL_TYPES[slot] ?? [slot];
      const iranianDue = !lastIranian || diffDaysISO(date, lastIranian) >= input.iranianEveryDays;
      const baseline = validatePlan(plan, input.ctx).length;

      const scored: Array<{ r: AutofillRecipe; score: number }> = [];
      for (const r of input.recipes) {
        if (!r.mealTypes.some((t) => types.includes(t))) continue;
        const candidate: CandidateMeal = { date, slot, recipeId: r.id };
        if (validatePlan([...plan, candidate], input.ctx).length > baseline) continue;

        let score = random();
        score -= (usedCount.get(r.id) ?? 0) * 2;
        const recent = input.recentlyCooked?.get(r.id);
        if (recent && diffDaysISO(date, recent) < 7) score -= 1;
        if (iranianDue && r.cuisine === "iranian" && (slot === "lunch" || slot === "dinner"))
          score += 2.5;
        if (slot === "dinner" && (r.tags.includes("light") || (r.kcal ?? 600) < 450)) score += 0.6;
        if (r.tags.includes("one_pot")) score += 0.3;
        if (!cooking && r.tags.includes("ten_minute")) score += 0.8;
        // avoid two dishes of the same cuisine on the same day
        if (
          plan.some(
            (m) =>
              m.date === date &&
              m.recipeId &&
              byId.get(m.recipeId)?.cuisine === r.cuisine &&
              r.cuisine !== "other",
          )
        )
          score -= 0.7;
        if ((slot === "lunch" || slot === "dinner") && r.mealTypes.includes("dessert")) score -= 3;
        // use up what's about to go off (spec §5.3 rule 6)
        score += 3 * expiringUsedBy(r, date).length;
        scored.push({ r, score });
      }
      scored.sort((a, b) => b.score - a.score);
      const pick = scored[0]?.r;
      if (!pick) continue;
      plan.push({ date, slot, recipeId: pick.id });
      taken.add(`${date}|${slot}`);
      for (const p of expiringUsedBy(pick, date)) unusedExpiring.delete(p);
      usedCount.set(pick.id, (usedCount.get(pick.id) ?? 0) + 1);
      if (pick.cuisine === "iranian" && (slot === "lunch" || slot === "dinner")) lastIranian = date;
    }
  }
  return plan.slice(input.existing?.length ?? 0);
}

/** The most recent cooked (non-leftover) main meal before this date/slot. */
function latestMain(plan: CandidateMeal[], date: ISODate, slot: string): CandidateMeal | undefined {
  const order = (m: CandidateMeal) => `${m.date}|${m.slot === "lunch" ? 1 : 2}`;
  const here = `${date}|${slot === "lunch" ? 1 : 2}`;
  return plan
    .filter(
      (m) =>
        (m.slot === "lunch" || m.slot === "dinner") &&
        m.recipeId &&
        !m.leftoverOf &&
        order(m) < here,
    )
    .sort((a, b) => order(b).localeCompare(order(a)))[0];
}
