import { z } from "zod";
import { UNITS } from "@/lib/domain/units";

export const AISLES = [
  "produce",
  "dairy",
  "meat",
  "bakery",
  "grains_legumes",
  "spices",
  "canned",
  "frozen",
  "drinks",
  "other",
] as const;
export const FLAGS = [
  "red_meat",
  "pork",
  "poultry",
  "seafood",
  "processed_meat",
  "dairy",
  "lactose",
  "gluten",
  "nuts",
  "egg",
  "soy",
  "alcohol",
  "legume",
  "sweet",
  "spicy",
  "animal_product",
] as const;
export const CUISINES = [
  "iranian",
  "turkish",
  "armenian",
  "greek",
  "italian",
  "mexican",
  "indian",
  "asian",
  "spanish",
  "french",
  "american",
  "other",
] as const;
export const MEAL_TYPES = ["breakfast", "lunch", "dinner", "snack", "dessert", "drink"] as const;
export const EQUIPMENT = [
  "oven",
  "microwave",
  "airfryer",
  "pressure_cooker",
  "blender",
  "mixer",
  "sandwich_maker",
  "toaster",
  "freezer",
] as const;
export const RECIPE_TAGS = [
  "one_pot",
  "no_cook",
  "ten_minute",
  "high_protein",
  "vegetarian",
  "vegan",
  "low_red_meat",
  "light",
  "freezer",
  "leftover_friendly",
  "budget",
  "no_oven",
  "sweet",
  "iranian_classic",
] as const;
export const SLOTS = [
  "breakfast",
  "mid_morning",
  "lunch",
  "after_lunch",
  "snack",
  "dinner",
  "late",
] as const;

const nutrition = z.object({
  kcal: z.number().min(0),
  protein: z.number().min(0),
  carbs: z.number().min(0),
  fat: z.number().min(0),
  fiber: z.number().min(0),
});
export type NutritionT = z.infer<typeof nutrition>;
export const nutritionSchema = nutrition;

const days = z.number().int().min(0).nullable();

export const ingredientSeed = z.object({
  key: z.string().regex(/^[a-z][a-z0-9_]*$/),
  name_fa: z.string().min(1),
  aliases_fa: z.array(z.string()),
  category: z.enum(AISLES),
  default_unit: z.enum(UNITS),
  grams_per_unit: z.record(z.string(), z.number().positive()),
  nutrition_per_100g: nutrition,
  shelf_life_days: z.object({
    fridge: days,
    freezer: days,
    pantry: days,
    after_opening_fridge: days,
  }),
  freezable: z.boolean(),
  flags: z.array(z.enum(FLAGS)),
});
export type IngredientSeed = z.infer<typeof ingredientSeed>;

export const stepSchema = z.object({
  text: z.string().min(1),
  timer_seconds: z.number().int().positive().optional().nullable(),
});
export const prepAheadSchema = z.object({
  text: z.string().min(1),
  hours_before: z.number().positive(),
});

export const recipeSeed = z.object({
  key: z.string().regex(/^[a-z][a-z0-9_]*$/),
  title_fa: z.string().min(1),
  title_original: z.string().nullable(),
  intro_fa: z.string().nullable(),
  cuisine: z.enum(CUISINES),
  meal_types: z.array(z.enum(MEAL_TYPES)).min(1),
  base_servings: z.number().int().min(1).max(12),
  active_minutes: z.number().int().min(0),
  total_minutes: z.number().int().min(0),
  difficulty: z.number().int().min(1).max(3),
  requires_equipment: z.array(z.enum(EQUIPMENT)),
  equipment_alternatives_fa: z.string().nullable(),
  ingredients: z
    .array(
      z.object({
        ingredient: z.string(),
        quantity: z.number().positive(),
        unit: z.enum(UNITS),
        prep_note: z.string().nullable(),
        optional: z.boolean(),
        group: z.string().nullable(),
      }),
    )
    .min(1),
  steps: z.array(stepSchema).min(1).max(7),
  nutrition_per_serving: nutrition,
  freezer_friendly: z.boolean(),
  fridge_days: z.number().int().min(0).nullable(),
  reheat_note: z.string().nullable(),
  prep_ahead: z.array(prepAheadSchema),
  tags: z.array(z.enum(RECIPE_TAGS)),
  source: z.literal("seed"),
  source_credit: z.string().nullable(),
});
export type RecipeSeed = z.infer<typeof recipeSeed>;

/** Validate both seed files together; returns a list of human-readable problems. */
export function checkSeed(
  ingredientsRaw: unknown,
  recipesRaw: unknown,
): { problems: string[]; ingredients: IngredientSeed[]; recipes: RecipeSeed[] } {
  const problems: string[] = [];
  const ing = z.array(ingredientSeed).safeParse(ingredientsRaw);
  const rec = z.array(recipeSeed).safeParse(recipesRaw);
  if (!ing.success)
    problems.push(
      ...ing.error.issues.slice(0, 20).map((i) => `ingredients ${i.path.join(".")}: ${i.message}`),
    );
  if (!rec.success)
    problems.push(
      ...rec.error.issues.slice(0, 20).map((i) => `recipes ${i.path.join(".")}: ${i.message}`),
    );
  const ingredients = ing.success ? ing.data : [];
  const recipes = rec.success ? rec.data : [];
  const byKey = new Map(ingredients.map((i) => [i.key, i]));
  if (byKey.size !== ingredients.length) problems.push("duplicate ingredient keys");
  if (new Set(recipes.map((r) => r.key)).size !== recipes.length)
    problems.push("duplicate recipe keys");
  for (const r of recipes) {
    for (const x of r.ingredients) {
      const i = byKey.get(x.ingredient);
      if (!i) problems.push(`${r.key}: unknown ingredient ${x.ingredient}`);
      else if (!["g", "ml", "tsp", "tbsp", "cup"].includes(x.unit) && !(x.unit in i.grams_per_unit))
        problems.push(`${r.key}: ${x.ingredient} has no gram weight for unit ${x.unit}`);
    }
    if (r.requires_equipment.includes("oven") && r.tags.includes("no_oven"))
      problems.push(`${r.key}: requires oven but tagged no_oven`);
  }
  return { problems, ingredients, recipes };
}
