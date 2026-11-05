import { notFound, withHousehold } from "@/lib/server/api";
import { loadRecipe } from "@/lib/server/recipes";

export const GET = withHousehold<{ id: string }>(async (_req, { household }, { id }) => {
  const recipe = await loadRecipe(household.id, id);
  if (!recipe) throw notFound();
  return { recipe };
});
