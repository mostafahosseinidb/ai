import { z } from "zod";
import { db } from "@/lib/db";
import { ApiError, isoDate, parseJson, withHousehold } from "@/lib/server/api";
import { ownedMeal, updateMealLogged } from "@/lib/server/meals";
import { planEnd, violationsIfChanged } from "@/lib/server/plans";
import { afterPlanChange } from "@/lib/server/plan-sync";
import { deductForMeal, restoreForMeal } from "@/lib/server/pantry";
import { dbDateToISO, isoToDbDate } from "@/lib/i18n/jalali";
import { SLOTS } from "@/lib/seed-schema";

const body = z.discriminatedUnion("action", [
  z.object({
    action: z.literal("status"),
    status: z.enum(["planned", "done", "skipped", "eaten_out"]),
    eaten_out_text: z.string().max(300).optional(),
  }),
  z.object({
    action: z.literal("move"),
    date: z.string().regex(isoDate),
    slot: z.enum(SLOTS),
    force: z.boolean().optional(),
  }),
  z.object({
    action: z.literal("replace"),
    recipe_id: z.string().uuid().nullable(),
    free_text: z.string().max(200).nullable().optional(),
    force: z.boolean().optional(),
  }),
  z.object({
    action: z.literal("edit"),
    side: z.string().max(120).nullable().optional(),
    drink: z.string().max(120).nullable().optional(),
    tag: z.string().max(60).nullable().optional(),
  }),
]);

export const PATCH = withHousehold<{ id: string }>(async (req, { household }, { id }) => {
  const meal = await ownedMeal(household.id, id);
  const input = await parseJson(req, body);
  const start = dbDateToISO(meal.mealPlan.startDate);

  if (input.action === "status") {
    const res = await updateMealLogged(household.id, meal.id, {
      status: input.status,
      doneAt: input.status === "done" ? new Date() : null,
      eatenOutText: input.status === "eaten_out" ? input.eaten_out_text?.trim() || null : null,
    });
    // pantry bookkeeping (spec §5.5): ticking takes ingredients out, un-ticking puts them back
    if (meal.status === "done" && input.status !== "done")
      await restoreForMeal(household.id, meal.id);
    if (meal.status !== "done" && input.status === "done" && household.autoDeductPantry)
      await deductForMeal(household.id, meal.id);
    // a wishlist dish that got cooked is done (spec §5.9)
    await db.wishlistItem.updateMany({
      where: { scheduledPlannedMealId: meal.id, deletedAt: null },
      data: { doneAt: input.status === "done" ? new Date() : null },
    });
    await afterPlanChange(meal.mealPlanId);
    return res;
  }

  if (input.action === "move") {
    if (input.date < start || input.date > planEnd(meal.mealPlan))
      throw new ApiError(400, "outside_plan");
    const occupant = await db.plannedMeal.findFirst({
      where: {
        mealPlanId: meal.mealPlanId,
        date: isoToDbDate(input.date),
        slot: input.slot,
        deletedAt: null,
        status: { not: "replaced" },
        id: { not: meal.id },
      },
    });
    const from = { date: dbDateToISO(meal.date), slot: meal.slot };
    if (!input.force) {
      const violations = await violationsIfChanged(household, meal.mealPlanId, start, (meals) =>
        meals.map((m) => {
          if (m.date === from.date && m.slot === from.slot)
            return { ...m, date: input.date, slot: input.slot };
          if (occupant && m.date === input.date && m.slot === input.slot) return { ...m, ...from };
          return m;
        }),
      );
      if (violations.length) throw new ApiError(409, "rule_violation", violations);
    }
    // swap with whatever is in the target cell
    if (occupant)
      await updateMealLogged(household.id, occupant.id, { date: meal.date, slot: meal.slot });
    const res = await updateMealLogged(household.id, meal.id, {
      date: isoToDbDate(input.date),
      slot: input.slot,
    });
    await afterPlanChange(meal.mealPlanId);
    return { ...res, swappedWith: occupant?.id ?? null };
  }

  if (input.action === "replace") {
    if (input.recipe_id) {
      const ok = await db.recipe.findFirst({
        where: {
          id: input.recipe_id,
          deletedAt: null,
          OR: [{ ownerHouseholdId: null }, { ownerHouseholdId: household.id }],
        },
      });
      if (!ok) throw new ApiError(404, "recipe_not_found");
      if (!input.force) {
        const at = { date: dbDateToISO(meal.date), slot: meal.slot };
        const violations = await violationsIfChanged(household, meal.mealPlanId, start, (meals) => {
          const rest = meals.filter((m) => !(m.date === at.date && m.slot === at.slot));
          return [...rest, { ...at, recipeId: input.recipe_id }];
        });
        if (violations.length) throw new ApiError(409, "rule_violation", violations);
      }
    }
    const res = await updateMealLogged(household.id, meal.id, {
      recipeId: input.recipe_id,
      freeText: input.recipe_id ? null : input.free_text?.trim() || null,
      leftoverOfPlannedMealId: null,
      status: "planned",
      doneAt: null,
    });
    await afterPlanChange(meal.mealPlanId);
    return res;
  }

  return updateMealLogged(household.id, meal.id, {
    side: input.side,
    drink: input.drink,
    tag: input.tag,
  });
});

export const DELETE = withHousehold<{ id: string }>(async (_req, { household }, { id }) => {
  const meal = await ownedMeal(household.id, id);
  const res = await updateMealLogged(household.id, meal.id, { deletedAt: new Date() });
  await afterPlanChange(meal.mealPlanId);
  return res;
});
