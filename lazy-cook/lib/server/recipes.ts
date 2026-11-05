import "server-only";
import type { Prisma } from "@prisma/client";
import { db } from "@/lib/db";
import { recipeFacts } from "@/lib/domain/facts";
import type { AutofillRecipe } from "@/lib/domain/autofill";

export const recipeWithIngredients = {
  ingredients: { include: { ingredient: true }, orderBy: { position: "asc" } },
} satisfies Prisma.RecipeInclude;

export type RecipeFull = Prisma.RecipeGetPayload<{ include: typeof recipeWithIngredients }>;

/** Public (seed) recipes plus the household's own. */
export function visibleRecipesWhere(householdId: string): Prisma.RecipeWhereInput {
  return { deletedAt: null, OR: [{ ownerHouseholdId: null }, { ownerHouseholdId: householdId }] };
}

export async function loadRecipes(householdId: string): Promise<RecipeFull[]> {
  return db.recipe.findMany({
    where: visibleRecipesWhere(householdId),
    include: recipeWithIngredients,
    orderBy: { titleFa: "asc" },
  });
}

export async function loadRecipe(householdId: string, id: string): Promise<RecipeFull | null> {
  if (!/^[0-9a-f-]{36}$/i.test(id)) return null;
  return db.recipe.findFirst({
    where: { id, ...visibleRecipesWhere(householdId) },
    include: recipeWithIngredients,
  });
}

export function toFacts(r: RecipeFull): AutofillRecipe {
  return recipeFacts({
    id: r.id,
    slug: r.slug,
    titleFa: r.titleFa,
    titleOriginal: r.titleOriginal,
    cuisine: r.cuisine,
    mealTypes: r.mealTypes,
    requiresEquipment: r.requiresEquipment,
    tags: r.tags,
    fridgeDays: r.fridgeDays,
    prepAhead: r.prepAhead,
    nutritionPerServing: r.nutritionPerServing,
    ingredients: r.ingredients.map((x) => ({
      key: x.ingredient.canonicalName,
      id: x.ingredientId,
      flags: x.ingredient.flags,
      optional: x.optional,
    })),
  });
}
