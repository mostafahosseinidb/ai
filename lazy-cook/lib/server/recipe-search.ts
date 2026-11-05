import "server-only";
import type { RecipeFull } from "./recipes";
import { fa } from "@/lib/i18n/fa";

export interface RecipeFilters {
  query?: string;
  cuisine?: string;
  mealType?: string;
  tag?: string;
  /** only recipes the household's kitchen can make */
  equipment?: string[];
}

function norm(s: string) {
  return s
    .toLowerCase()
    .replace(/[‌\s]+/g, " ")
    .replace(/ي/g, "ی")
    .replace(/ك/g, "ک")
    .trim();
}

/** In-memory search: the library is small (hundreds of recipes), so this is fast and simple. */
export function searchRecipes(recipes: RecipeFull[], f: RecipeFilters): RecipeFull[] {
  const q = f.query ? norm(f.query) : "";
  const terms = q ? q.split(" ") : [];
  return recipes.filter((r) => {
    if (f.cuisine && r.cuisine !== f.cuisine) return false;
    if (f.mealType && !r.mealTypes.includes(f.mealType)) return false;
    if (f.tag && !r.tags.includes(f.tag)) return false;
    if (f.equipment && r.requiresEquipment.some((e) => !f.equipment!.includes(e))) return false;
    if (!terms.length) return true;
    const hay = norm(
      [
        r.titleFa,
        r.titleOriginal ?? "",
        r.introFa ?? "",
        fa.cuisines[r.cuisine] ?? "",
        ...r.tags.map((t) => fa.tags[t] ?? t),
        ...r.ingredients.flatMap((i) => [i.ingredient.nameFa, ...i.ingredient.aliasesFa]),
      ].join(" "),
    );
    return terms.every((t) => hay.includes(t));
  });
}
