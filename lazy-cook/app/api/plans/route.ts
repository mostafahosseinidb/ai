import { z } from "zod";
import { isoDate, parseJson, withHousehold } from "@/lib/server/api";
import { createPlan } from "@/lib/server/plans";
import { afterPlanChange } from "@/lib/server/plan-sync";

const body = z.object({
  start_date: z.string().regex(isoDate),
  days: z.number().int().min(3).max(14),
  autofill: z.boolean().default(true),
});

/** Phase 1: synchronous, non-AI. Phase 3 turns AI generation into a background job returning job_id. */
export const POST = withHousehold(async (req, { household }) => {
  const input = await parseJson(req, body);
  const plan = await createPlan(household, input.start_date, input.days, input.autofill ?? true);
  await afterPlanChange(plan.id);
  return { plan: { id: plan.id } };
});
