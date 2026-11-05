import { withHousehold } from "@/lib/server/api";
import { loadRecipes } from "@/lib/server/recipes";
import { searchRecipes } from "@/lib/server/recipe-search";

export const GET = withHousehold(async (req, { household }) => {
  const p = new URL(req.url).searchParams;
  const all = await loadRecipes(household.id);
  const found = searchRecipes(all, {
    query: p.get("query") ?? undefined,
    cuisine: p.get("cuisine") ?? undefined,
    mealType: p.get("meal_type") ?? undefined,
    tag: p.get("tags") ?? undefined,
    equipment: p.get("equipment") === "mine" ? household.kitchenEquipment : undefined,
  });
  return {
    recipes: found.map((r) => ({
      id: r.id,
      titleFa: r.titleFa,
      titleOriginal: r.titleOriginal,
      cuisine: r.cuisine,
      mealTypes: r.mealTypes,
      activeMinutes: r.activeMinutes,
      tags: r.tags,
      requiresEquipment: r.requiresEquipment,
    })),
  };
});
