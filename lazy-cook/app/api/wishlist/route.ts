import { z } from "zod";
import { db } from "@/lib/db";
import { ApiError, parseJson, withHousehold } from "@/lib/server/api";

export const GET = withHousehold(async (_req, { household }) => {
  const items = await db.wishlistItem.findMany({
    where: { householdId: household.id, deletedAt: null },
    include: { recipe: { select: { id: true, titleFa: true, activeMinutes: true } } },
    orderBy: [
      { doneAt: { sort: "asc", nulls: "first" } },
      { priority: "desc" },
      { createdAt: "asc" },
    ],
  });
  return { items };
});

const body = z.object({
  recipe_id: z.string().uuid(),
  note: z.string().trim().max(200).nullable().optional(),
  priority: z.number().int().min(0).max(1).optional(),
});

/** «برای برنامه‌ی بعدی»: idempotent per recipe while not done. */
export const POST = withHousehold(async (req, { household }) => {
  const input = await parseJson(req, body);
  const recipe = await db.recipe.findFirst({
    where: {
      id: input.recipe_id,
      deletedAt: null,
      OR: [{ ownerHouseholdId: null }, { ownerHouseholdId: household.id }],
    },
  });
  if (!recipe) throw new ApiError(404, "recipe_not_found");
  const existing = await db.wishlistItem.findFirst({
    where: { householdId: household.id, recipeId: recipe.id, doneAt: null, deletedAt: null },
  });
  if (existing) return { item: existing, existed: true };
  const item = await db.wishlistItem.create({
    data: {
      householdId: household.id,
      recipeId: recipe.id,
      note: input.note ?? null,
      priority: input.priority ?? 0,
    },
  });
  return { item, existed: false };
});
