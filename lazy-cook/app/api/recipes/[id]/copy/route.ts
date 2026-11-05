import { notFound, withHousehold } from "@/lib/server/api";
import { loadRecipe } from "@/lib/server/recipes";
import { createRecipe, toInput } from "@/lib/server/recipe-write";

/** «نسخه‌ی خودم»: copy any visible recipe into the household's own, linked by parent_recipe_id. */
export const POST = withHousehold<{ id: string }>(async (_req, { household }, { id }) => {
  const source = await loadRecipe(household.id, id);
  if (!source) throw notFound();
  const { ingredientNames: _names, ...input } = toInput(source);
  void _names;
  const copy = await createRecipe(
    household.id,
    { ...input, title_fa: `${source.titleFa} (نسخه‌ی من)`.slice(0, 80) },
    source.id,
  );
  return { recipe: { id: copy.id } };
});
