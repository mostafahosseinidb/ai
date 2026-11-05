import { db } from "@/lib/db";
import { withHousehold } from "@/lib/server/api";
import { getActivePlan } from "@/lib/server/plans";

export const GET = withHousehold(async (_req, { household }) => {
  const plan = await getActivePlan(household.id);
  if (!plan) return { plan: null };
  const meals = await db.plannedMeal.findMany({
    where: { mealPlanId: plan.id, deletedAt: null },
    include: { recipe: { select: { id: true, titleFa: true, activeMinutes: true } } },
    orderBy: [{ date: "asc" }, { position: "asc" }],
  });
  return { plan, meals };
});
