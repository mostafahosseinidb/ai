import { readFileSync } from "node:fs";
import { join } from "node:path";
import { checkSeed } from "@/lib/seed-schema";
import { recipeFacts } from "@/lib/domain/facts";

const dir = join(__dirname, "../../seed");
const read = (f: string) => JSON.parse(readFileSync(join(dir, f), "utf8"));

export const seed = checkSeed(read("ingredients.json"), read("recipes.json"));
export const ingredientsByKey = new Map(seed.ingredients.map((i) => [i.key, i]));

export const facts = seed.recipes.map((r) =>
  recipeFacts({
    id: r.key,
    slug: r.key,
    titleFa: r.title_fa,
    titleOriginal: r.title_original,
    cuisine: r.cuisine,
    mealTypes: r.meal_types,
    requiresEquipment: r.requires_equipment,
    tags: r.tags,
    fridgeDays: r.fridge_days,
    prepAhead: r.prep_ahead,
    nutritionPerServing: r.nutrition_per_serving,
    ingredients: r.ingredients.map((x) => ({ key: x.ingredient, flags: ingredientsByKey.get(x.ingredient)?.flags ?? [], optional: x.optional })),
  }),
);
export const factsById = new Map(facts.map((f) => [f.id, f]));
