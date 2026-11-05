import { z } from "zod";
import { db } from "@/lib/db";
import { ApiError, isoDate, notFound, parseJson, withHousehold } from "@/lib/server/api";
import { defaultServings, planEnd, violationsIfChanged } from "@/lib/server/plans";
import { afterPlanChange } from "@/lib/server/plan-sync";
import { dbDateToISO, isoToDbDate } from "@/lib/i18n/jalali";
import { SLOTS } from "@/lib/seed-schema";

const body = z
  .object({
    date: z.string().regex(isoDate),
    slot: z.enum(SLOTS),
    recipe_id: z.string().uuid().nullable().optional(),
    free_text: z.string().max(200).nullable().optional(),
    leftover_of_planned_meal_id: z.string().uuid().nullable().optional(),
    force: z.boolean().optional(),
  })
  .refine((b) => b.recipe_id || b.free_text?.trim() || b.leftover_of_planned_meal_id, {
    message: "recipe_id, free_text or leftover_of_planned_meal_id required",
  });

/** Add a meal to a plan by hand. */
export const POST = withHousehold<{ id: string }>(async (req, { household }, { id }) => {
  const plan = await db.mealPlan.findFirst({
    where: { id, householdId: household.id, deletedAt: null },
  });
  if (!plan) throw notFound();
  const input = await parseJson(req, body);
  const start = dbDateToISO(plan.startDate);
  if (input.date < start || input.date > planEnd(plan)) throw new ApiError(400, "outside_plan");

  let leftover: { date: string; slot: string } | null = null;
  if (input.leftover_of_planned_meal_id) {
    const src = await db.plannedMeal.findFirst({
      where: { id: input.leftover_of_planned_meal_id, mealPlanId: plan.id, deletedAt: null },
    });
    if (!src || !src.recipeId) throw new ApiError(404, "leftover_source_not_found");
    leftover = { date: dbDateToISO(src.date), slot: src.slot };
    input.recipe_id = src.recipeId; // a leftover is the same dish
  }

  if (input.recipe_id) {
    const recipe = await db.recipe.findFirst({
      where: {
        id: input.recipe_id,
        deletedAt: null,
        OR: [{ ownerHouseholdId: null }, { ownerHouseholdId: household.id }],
      },
    });
    if (!recipe) throw new ApiError(404, "recipe_not_found");
    if (!input.force) {
      const violations = await violationsIfChanged(household, plan.id, start, (meals) => [
        ...meals.filter((m) => !(m.date === input.date && m.slot === input.slot)),
        { date: input.date, slot: input.slot, recipeId: input.recipe_id!, leftoverOf: leftover },
      ]);
      if (violations.length) throw new ApiError(409, "rule_violation", violations);
    }
  }

  const existing = await db.plannedMeal.findFirst({
    where: {
      mealPlanId: plan.id,
      date: isoToDbDate(input.date),
      slot: input.slot,
      deletedAt: null,
    },
  });
  if (existing)
    await db.plannedMeal.update({ where: { id: existing.id }, data: { deletedAt: new Date() } });

  const meal = await db.plannedMeal.create({
    data: {
      mealPlanId: plan.id,
      date: isoToDbDate(input.date),
      slot: input.slot,
      recipeId: input.recipe_id ?? null,
      freeText: input.recipe_id ? null : input.free_text!.trim(),
      leftoverOfPlannedMealId: input.leftover_of_planned_meal_id ?? null,
      servingsByMember: await defaultServings(household.id),
    },
  });
  await afterPlanChange(plan.id);
  return { meal };
});
