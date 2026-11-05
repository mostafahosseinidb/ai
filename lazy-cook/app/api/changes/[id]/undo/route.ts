import { assertUuid, withHousehold } from "@/lib/server/api";
import { undoChange } from "@/lib/server/meals";
import { syncPrepTasks } from "@/lib/server/plans";

export const POST = withHousehold<{ id: string }>(async (_req, { household }, { id }) => {
  assertUuid(id);
  const meal = await undoChange(household.id, id);
  await syncPrepTasks(meal.mealPlanId);
  return { meal };
});
