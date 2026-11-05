import { readFileSync, readdirSync } from "node:fs";
import { checkSeed } from "@/lib/seed-schema";

const read = (p: string) =>
  JSON.parse(readFileSync(new URL(`../seed/${p}`, import.meta.url), "utf8"));
const { problems, ingredients, recipes } = checkSeed(
  read("ingredients.json"),
  read("recipes.json"),
);
if (ingredients.length < 250) problems.push(`only ${ingredients.length} ingredients (need ≥ 250)`);
if (recipes.length < 40) problems.push(`only ${recipes.length} recipes (need ≥ 40)`);
const keys = new Set(ingredients.map((i) => i.key));
const localesDir = new URL("../seed/locales/", import.meta.url);
let localeCount = 0;
for (const file of readdirSync(localesDir).filter((f) => f.endsWith(".json"))) {
  const rows = JSON.parse(readFileSync(new URL(file, localesDir), "utf8")) as Array<{
    ingredient: string;
    language: string;
    name: string;
    notes: string | null;
  }>;
  localeCount += rows.length;
  for (const r of rows) {
    if (!keys.has(r.ingredient))
      problems.push(`locales/${file}: unknown ingredient ${r.ingredient}`);
    if (!r.name?.trim() || !r.language)
      problems.push(`locales/${file}: empty name/language for ${r.ingredient}`);
  }
}
if (problems.length) {
  console.error(problems.join("\n"));
  process.exit(1);
}
console.log(
  `seed ok: ${ingredients.length} ingredients, ${recipes.length} recipes, ${localeCount} local names`,
);
