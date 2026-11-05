import { db } from "@/lib/db";
import { assertUuid, withHousehold } from "@/lib/server/api";
import { undoChange } from "@/lib/server/meals";
import { afterPlanChange } from "@/lib/server/plan-sync";
import { deductForMeal, restoreForMeal } from "@/lib/server/pantry";

export const POST = withHousehold<{ id: string }>(async (_req, { household }, { id }) => {
  assertUuid(id);
  const before = await db.changeLog.findFirst({
    where: { id, householdId: household.id },
    select: { entityId: true },
  });
  const wasDone = before
    ? (
        await db.plannedMeal.findUnique({
          where: { id: before.entityId },
          select: { status: true },
        })
      )?.status === "done"
    : false;
  const meal = await undoChange(household.id, id);
  if (wasDone && meal.status !== "done") await restoreForMeal(household.id, meal.id);
  if (!wasDone && meal.status === "done" && household.autoDeductPantry)
    await deductForMeal(household.id, meal.id);
  await afterPlanChange(meal.mealPlanId);
  return { meal };
});
