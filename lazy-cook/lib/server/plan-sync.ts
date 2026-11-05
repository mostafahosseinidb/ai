import "server-only";
import { db } from "@/lib/db";
import { syncPrepTasks } from "./plans";
import { regenerateShopping } from "./shopping";

/** Everything derived from a plan, recomputed after any change to it (spec §14 phase 2: «بلافاصله»). */
export async function afterPlanChange(planId: string) {
  const plan = await db.mealPlan.findUnique({
    where: { id: planId },
    select: { householdId: true },
  });
  if (!plan) return;
  await syncPrepTasks(planId);
  await regenerateShopping(plan.householdId);
}
