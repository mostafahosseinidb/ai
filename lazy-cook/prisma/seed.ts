/* Seed ingredients, local names and public recipes. Idempotent: safe to run repeatedly. */
import { readFileSync, readdirSync, existsSync } from "node:fs";
import { PrismaClient, Prisma } from "@prisma/client";
import { checkSeed } from "../lib/seed-schema";

const db = new PrismaClient();
const seedDir = new URL("../seed/", import.meta.url);
const read = (p: string) => JSON.parse(readFileSync(new URL(p, seedDir), "utf8"));

async function main() {
  const { problems, ingredients, recipes } = checkSeed(read("ingredients.json"), read("recipes.json"));
  if (problems.length) throw new Error(`seed data invalid:\n${problems.join("\n")}`);

  const idByKey = new Map<string, string>();
  for (const i of ingredients) {
    const data = {
      nameFa: i.name_fa,
      aliasesFa: i.aliases_fa,
      category: i.category,
      defaultUnit: i.default_unit,
      gramsPerUnit: i.grams_per_unit,
      nutritionPer100g: i.nutrition_per_100g,
      shelfLifeDays: i.shelf_life_days,
      freezable: i.freezable,
      flags: i.flags,
    };
    const row = await db.ingredient.upsert({
      where: { canonicalName: i.key },
      create: { canonicalName: i.key, ...data },
      update: data,
      select: { id: true },
    });
    idByKey.set(i.key, row.id);
  }

  // local names: seed/locales/XX.json = [{ ingredient, language, name, notes? }]
  const localesDir = new URL("locales/", seedDir);
  if (existsSync(localesDir)) {
    for (const file of readdirSync(localesDir).filter((f) => f.endsWith(".json"))) {
      const country = file.replace(".json", "").toUpperCase();
      const rows = JSON.parse(readFileSync(new URL(file, localesDir), "utf8")) as Array<{ ingredient: string; language: string; name: string; notes?: string }>;
      await db.ingredientLocalName.deleteMany({ where: { countryCode: country, source: "seed" } });
      await db.ingredientLocalName.createMany({
        data: rows
          .filter((r) => idByKey.has(r.ingredient))
          .map((r) => ({ ingredientId: idByKey.get(r.ingredient)!, countryCode: country, language: r.language, name: r.name, notes: r.notes ?? null, source: "seed" as const })),
      });
    }
  }

  for (const r of recipes) {
    const data = {
      ownerHouseholdId: null,
      titleFa: r.title_fa,
      titleOriginal: r.title_original,
      introFa: r.intro_fa,
      cuisine: r.cuisine,
      mealTypes: r.meal_types,
      baseServings: r.base_servings,
      activeMinutes: r.active_minutes,
      totalMinutes: r.total_minutes,
      difficulty: r.difficulty,
      requiresEquipment: r.requires_equipment,
      equipmentAlternativesFa: r.equipment_alternatives_fa,
      steps: r.steps as Prisma.InputJsonValue,
      nutritionPerServing: r.nutrition_per_serving,
      freezerFriendly: r.freezer_friendly,
      fridgeDays: r.fridge_days,
      reheatNote: r.reheat_note,
      prepAhead: r.prep_ahead,
      tags: r.tags,
      source: "seed" as const,
      sourceCredit: r.source_credit,
    };
    const row = await db.recipe.upsert({ where: { slug: r.key }, create: { slug: r.key, ...data }, update: data, select: { id: true } });
    await db.recipeIngredient.deleteMany({ where: { recipeId: row.id } });
    await db.recipeIngredient.createMany({
      data: r.ingredients.map((x, position) => ({
        recipeId: row.id,
        ingredientId: idByKey.get(x.ingredient)!,
        quantity: x.quantity,
        unit: x.unit,
        prepNote: x.prep_note,
        optional: x.optional,
        group: x.group,
        position,
      })),
    });
  }

  console.log(`seeded ${ingredients.length} ingredients, ${recipes.length} recipes`);
}

main()
  .catch((e) => {
    console.error(e);
    process.exit(1);
  })
  .finally(() => db.$disconnect());
