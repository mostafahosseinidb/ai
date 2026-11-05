import type { AutofillRecipe } from "./autofill";

export interface FactsSourceRecipe {
  id: string;
  slug?: string | null;
  titleFa: string;
  titleOriginal?: string | null;
  cuisine: string;
  mealTypes: string[];
  requiresEquipment: string[];
  tags: string[];
  fridgeDays: number | null;
  prepAhead: unknown;
  nutritionPerServing: unknown;
  ingredients: Array<{ key: string; id?: string; flags: string[]; optional: boolean }>;
}

/** Turn a recipe (from the DB or the seed files) into the flat facts the rule engine needs. */
export function recipeFacts(r: FactsSourceRecipe): AutofillRecipe {
  const required = r.ingredients.filter((i) => !i.optional);
  const flags = new Set<string>();
  for (const i of required) i.flags.forEach((f) => flags.add(f));
  const prep = Array.isArray(r.prepAhead)
    ? (r.prepAhead as Array<{ text: string; hours_before: number }>)
    : [];
  const n = r.nutritionPerServing as { kcal?: number } | null;
  return {
    id: r.id,
    slug: r.slug ?? null,
    titleFa: r.titleFa,
    titleOriginal: r.titleOriginal ?? null,
    requiresEquipment: r.requiresEquipment,
    flags: [...flags],
    ingredientKeys: required.map((i) => i.key),
    ingredientIds: required.map((i) => i.id).filter((x): x is string => !!x),
    prepAhead: prep,
    mealTypes: r.mealTypes,
    cuisine: r.cuisine,
    tags: r.tags,
    fridgeDays: r.fridgeDays,
    kcal: n?.kcal ?? null,
  };
}
