import { withHousehold } from "@/lib/server/api";
import { ownedMeal } from "@/lib/server/meals";
import { loadRuleContext } from "@/lib/server/household";
import { planCandidates } from "@/lib/server/plans";
import { validatePlan } from "@/lib/domain/rules";
import { SLOT_MEAL_TYPES } from "@/lib/domain/autofill";
import { dbDateToISO } from "@/lib/i18n/jalali";

/** «عوضش کن»: three alternatives for this slot that break none of the hard rules. */
export const GET = withHousehold<{ id: string }>(async (_req, { household }, { id }) => {
  const meal = await ownedMeal(household.id, id);
  const start = dbDateToISO(meal.mealPlan.startDate);
  const { ctx, facts, recipes } = await loadRuleContext(household, start);
  const at = { date: dbDateToISO(meal.date), slot: meal.slot };
  const rest = (await planCandidates(meal.mealPlanId)).filter(
    (m) => !(m.date === at.date && m.slot === at.slot),
  );
  const baseline = validatePlan(rest, ctx).length;
  const used = new Set(rest.map((m) => m.recipeId));
  const types = SLOT_MEAL_TYPES[meal.slot] ?? [meal.slot];
  const pantryIds = new Set((ctx.pantry ?? []).map((p) => p.ingredientId).filter(Boolean));

  const ok = facts
    .filter((f) => f.id !== meal.recipeId && f.mealTypes.some((t) => types.includes(t)))
    .filter((f) => validatePlan([...rest, { ...at, recipeId: f.id }], ctx).length === baseline)
    .map((f) => ({
      f,
      score:
        (f.ingredientIds ?? []).filter((i) => pantryIds.has(i)).length * 2 +
        (used.has(f.id) ? -3 : 0) +
        (f.tags.includes("ten_minute") ? 0.5 : 0) +
        // stable per-meal shuffle
        ((parseInt(f.id.slice(0, 6), 16) ^ parseInt(meal.id.slice(0, 6), 16)) % 100) / 100,
    }))
    .sort((a, b) => b.score - a.score)
    .slice(0, 3);

  const byId = new Map(recipes.map((r) => [r.id, r]));
  return {
    alternatives: ok.map(({ f }) => {
      const r = byId.get(f.id)!;
      return { id: r.id, titleFa: r.titleFa, cuisine: r.cuisine, activeMinutes: r.activeMinutes };
    }),
  };
});
