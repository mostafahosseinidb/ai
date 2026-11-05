import { readFileSync } from "node:fs";
import { checkSeed } from "@/lib/seed-schema";

const read = (p: string) => JSON.parse(readFileSync(new URL(`../seed/${p}`, import.meta.url), "utf8"));
const { problems, ingredients, recipes } = checkSeed(read("ingredients.json"), read("recipes.json"));
if (ingredients.length < 250) problems.push(`only ${ingredients.length} ingredients (need ≥ 250)`);
if (recipes.length < 40) problems.push(`only ${recipes.length} recipes (need ≥ 40)`);
if (problems.length) {
  console.error(problems.join("\n"));
  process.exit(1);
}
console.log(`seed ok: ${ingredients.length} ingredients, ${recipes.length} recipes`);
