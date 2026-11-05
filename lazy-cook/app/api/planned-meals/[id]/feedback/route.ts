import { z } from "zod";
import { db } from "@/lib/db";
import { parseJson, withHousehold } from "@/lib/server/api";
import { ownedMeal } from "@/lib/server/meals";

const body = z.object({
  rating: z.enum(["up", "down"]),
  reasons: z
    .array(z.enum(["taste", "texture", "hard", "heavy"]))
    .max(4)
    .optional(),
  comment: z.string().max(500).optional(),
  member_id: z.string().uuid().nullable().optional(),
});

/** One rating per meal per member (or household): re-rating replaces the previous one. */
export const POST = withHousehold<{ id: string }>(async (req, { household }, { id }) => {
  const meal = await ownedMeal(household.id, id);
  const input = await parseJson(req, body);
  const memberId = input.member_id ?? null;
  if (memberId) {
    const m = await db.member.findFirst({ where: { id: memberId, householdId: household.id } });
    if (!m) return Response.json({ error: "member_not_found" }, { status: 404 });
  }
  await db.mealFeedback.updateMany({
    where: { plannedMealId: meal.id, memberId, deletedAt: null },
    data: { deletedAt: new Date() },
  });
  const fb = await db.mealFeedback.create({
    data: {
      plannedMealId: meal.id,
      memberId,
      rating: input.rating,
      reasons: input.reasons ?? [],
      comment: input.comment?.trim() || null,
    },
  });
  return { feedback: fb };
});

export const DELETE = withHousehold<{ id: string }>(async (_req, { household }, { id }) => {
  const meal = await ownedMeal(household.id, id);
  await db.mealFeedback.updateMany({
    where: { plannedMealId: meal.id, memberId: null, deletedAt: null },
    data: { deletedAt: new Date() },
  });
  return { ok: true };
});
