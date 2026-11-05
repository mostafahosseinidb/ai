import { notFound, withHousehold } from "@/lib/server/api";
import { loadRecipe } from "@/lib/server/recipes";
import { scaleRecipeLines } from "@/lib/domain/recipe-view";
import { parseNumber } from "@/lib/i18n/digits";

export const GET = withHousehold<{ id: string }>(async (req, { household }, { id }) => {
  const recipe = await loadRecipe(household.id, id);
  if (!recipe) throw notFound();
  const raw = new URL(req.url).searchParams.get("servings");
  const servings = Math.min(20, Math.max(0.25, (raw && parseNumber(raw)) || recipe.baseServings));
  const lines = scaleRecipeLines(
    recipe.ingredients.map((i) => ({
      key: i.ingredient.canonicalName,
      nameFa: i.ingredient.nameFa,
      quantity: Number(i.quantity),
      unit: i.unit,
      prepNote: i.prepNote,
      optional: i.optional,
      group: i.group,
    })),
    recipe.baseServings,
    servings,
  );
  return { recipe_id: recipe.id, servings, ingredients: lines };
});
