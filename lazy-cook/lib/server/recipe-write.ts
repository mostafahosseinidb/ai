import "server-only";
import type { Prisma } from "@prisma/client";
import { db } from "@/lib/db";
import { nutritionFromIngredients, type Nutrition } from "@/lib/domain/nutrition";
import type { GramsPerUnit } from "@/lib/domain/units";
import type { RecipeInput } from "@/lib/recipe-input";
import { ApiError } from "./api";

/** Approximate nutrition from the ingredients (code, not AI — spec §9.5). */
async function computeNutrition(input: RecipeInput): Promise<Nutrition | null> {
  const ings = await db.ingredient.findMany({
    where: { id: { in: input.ingredients.map((i) => i.ingredient_id) } },
  });
  if (ings.length !== new Set(input.ingredients.map((i) => i.ingredient_id)).size)
    throw new ApiError(400, "unknown_ingredient");
  const byId = new Map(ings.map((i) => [i.id, i]));
  const { perServing } = nutritionFromIngredients(
    input.ingredients.map((x) => ({
      quantity: x.quantity,
      unit: x.unit,
      optional: x.optional,
      gramsPerUnit: byId.get(x.ingredient_id)!.gramsPerUnit as GramsPerUnit,
      per100g: byId.get(x.ingredient_id)!.nutritionPer100g as unknown as Nutrition,
    })),
    input.base_servings,
  );
  const round = (n: number) => Math.round(n);
  return {
    kcal: round(perServing.kcal),
    protein: round(perServing.protein),
    carbs: round(perServing.carbs),
    fat: round(perServing.fat),
    fiber: round(perServing.fiber),
  };
}

function recipeData(input: RecipeInput, nutrition: Nutrition | null) {
  const tags = new Set(input.tags);
  if (!input.requires_equipment.includes("oven")) tags.add("no_oven");
  else tags.delete("no_oven");
  return {
    titleFa: input.title_fa,
    titleOriginal: input.title_original || null,
    introFa: input.intro_fa || null,
    cuisine: input.cuisine,
    mealTypes: input.meal_types,
    baseServings: input.base_servings,
    activeMinutes: input.active_minutes,
    totalMinutes: Math.max(input.total_minutes, input.active_minutes),
    difficulty: input.difficulty,
    requiresEquipment: input.requires_equipment,
    equipmentAlternativesFa: input.equipment_alternatives_fa || null,
    steps: input.steps.map((s) =>
      s.timer_seconds ? { text: s.text, timer_seconds: s.timer_seconds } : { text: s.text },
    ) as Prisma.InputJsonValue,
    nutritionPerServing: (nutrition ?? undefined) as Prisma.InputJsonValue | undefined,
    freezerFriendly: input.freezer_friendly,
    fridgeDays: input.fridge_days ?? null,
    reheatNote: input.reheat_note || null,
    prepAhead: input.prep_ahead as Prisma.InputJsonValue,
    tags: [...tags],
    sourceCredit: input.source_credit || null,
  };
}

function ingredientRows(recipeId: string, input: RecipeInput) {
  return input.ingredients.map((x, position) => ({
    recipeId,
    ingredientId: x.ingredient_id,
    quantity: x.quantity,
    unit: x.unit,
    prepNote: x.prep_note || null,
    optional: x.optional,
    position,
  }));
}

export async function createRecipe(
  householdId: string,
  input: RecipeInput,
  parentRecipeId?: string | null,
) {
  const nutrition = await computeNutrition(input);
  return db.$transaction(async (tx) => {
    const r = await tx.recipe.create({
      data: {
        ...recipeData(input, nutrition),
        ownerHouseholdId: householdId,
        source: "user_manual",
        parentRecipeId: parentRecipeId ?? null,
      },
    });
    await tx.recipeIngredient.createMany({ data: ingredientRows(r.id, input) });
    return r;
  });
}

export async function updateRecipe(householdId: string, id: string, input: RecipeInput) {
  const own = await db.recipe.findFirst({
    where: { id, ownerHouseholdId: householdId, deletedAt: null },
  });
  if (!own) throw new ApiError(404, "not_found_or_not_yours");
  const nutrition = await computeNutrition(input);
  return db.$transaction(async (tx) => {
    const r = await tx.recipe.update({ where: { id }, data: recipeData(input, nutrition) });
    await tx.recipeIngredient.deleteMany({ where: { recipeId: id } });
    await tx.recipeIngredient.createMany({ data: ingredientRows(id, input) });
    return r;
  });
}

/** A recipe as form input — used to edit your own, or to start "my version" of a public one. */
export function toInput(
  r: Prisma.RecipeGetPayload<{ include: { ingredients: { include: { ingredient: true } } } }>,
): RecipeInput & { ingredientNames: Record<string, string> } {
  return {
    title_fa: r.titleFa,
    title_original: r.titleOriginal,
    intro_fa: r.introFa,
    cuisine: r.cuisine as RecipeInput["cuisine"],
    meal_types: r.mealTypes as RecipeInput["meal_types"],
    base_servings: r.baseServings,
    active_minutes: r.activeMinutes,
    total_minutes: r.totalMinutes,
    difficulty: r.difficulty,
    requires_equipment: r.requiresEquipment as RecipeInput["requires_equipment"],
    equipment_alternatives_fa: r.equipmentAlternativesFa,
    ingredients: r.ingredients.map((i) => ({
      ingredient_id: i.ingredientId,
      quantity: Number(i.quantity),
      unit: i.unit as RecipeInput["ingredients"][number]["unit"],
      prep_note: i.prepNote,
      optional: i.optional,
    })),
    steps: (r.steps as Array<{ text: string; timer_seconds?: number }>).map((s) => ({
      text: s.text,
      timer_seconds: s.timer_seconds ?? null,
    })),
    freezer_friendly: r.freezerFriendly,
    fridge_days: r.fridgeDays,
    reheat_note: r.reheatNote,
    prep_ahead: (r.prepAhead ?? []) as RecipeInput["prep_ahead"],
    tags: r.tags.filter((t) => t !== "no_oven") as RecipeInput["tags"],
    source_credit: r.sourceCredit,
    ingredientNames: Object.fromEntries(
      r.ingredients.map((i) => [i.ingredientId, i.ingredient.nameFa]),
    ),
  };
}
