import { db } from "@/lib/db";
import { ApiError, notFound, parseJson, withHousehold } from "@/lib/server/api";
import { loadRecipe } from "@/lib/server/recipes";
import { recipeInput } from "@/lib/recipe-input";
import { updateRecipe } from "@/lib/server/recipe-write";
import { regenerateShopping } from "@/lib/server/shopping";

export const GET = withHousehold<{ id: string }>(async (_req, { household }, { id }) => {
  const recipe = await loadRecipe(household.id, id);
  if (!recipe) throw notFound();
  return { recipe };
});

/** Edit one of your own recipes (public ones are copied first — see /copy). */
export const PATCH = withHousehold<{ id: string }>(async (req, { household }, { id }) => {
  if (!(await loadRecipe(household.id, id))) throw notFound();
  const input = await parseJson(req, recipeInput);
  const recipe = await updateRecipe(household.id, id, input);
  await regenerateShopping(household.id); // quantities may have changed
  return { recipe: { id: recipe.id } };
});

export const DELETE = withHousehold<{ id: string }>(async (_req, { household }, { id }) => {
  const own = await db.recipe
    .findFirst({ where: { id, ownerHouseholdId: household.id, deletedAt: null } })
    .catch(() => null);
  if (!own) throw new ApiError(404, "not_found_or_not_yours");
  await db.recipe.update({ where: { id }, data: { deletedAt: new Date() } });
  return { ok: true };
});
