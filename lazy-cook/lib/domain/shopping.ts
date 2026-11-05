/**
 * Shopping list (spec §5.6): everything the plan needs, minus what's in the pantry, in one
 * unit per ingredient, grouped by aisle, with a package-size hint instead of exact grams.
 */
import { formatQuantity, toFaDigits } from "@/lib/i18n/digits";
import { roundQuantity } from "./scaling";
import { convert, toGrams, type GramsPerUnit } from "./units";

export const AISLE_ORDER = [
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

export const AISLE_LABEL_FA: Record<string, string> = {
  produce: "میوه و سبزی",
  dairy: "لبنیات",
  meat: "گوشت و مرغ",
  bakery: "نان",
  grains_legumes: "حبوبات و غلات",
  spices: "ادویه",
  canned: "کنسرو",
  frozen: "یخ‌زده",
  drinks: "نوشیدنی",
  other: "بقیه",
};

/** Never on a list. */
export const NEVER_BUY = new Set(["water"]);

/** Staples most kitchens already have — listed as optional ("if you've run out"), not required. */
export function isStaple(key: string, category: string): boolean {
  return (
    category === "spices" ||
    ["salt", "black_pepper", "sugar", "flour", "vegetable_oil", "olive_oil", "vinegar"].includes(
      key,
    )
  );
}

export interface IngredientInfo {
  id: string;
  key: string;
  nameFa: string;
  category: string;
  defaultUnit: string;
  gramsPerUnit: GramsPerUnit;
}

export interface NeedLine {
  ingredientId: string;
  quantity: number;
  unit: string;
  optional: boolean;
}

export interface StockLine {
  ingredientId: string;
  quantity: number;
  unit: string;
}

export interface ShoppingLine {
  ingredientId: string;
  quantity: number;
  unit: string;
  section: "required" | "optional";
  aisle: string;
  packageHint: string;
}

/**
 * Sum needs per ingredient in one unit: the ingredient's default unit when every line converts
 * to it, otherwise grams; lines that convert to neither stay as their own entry.
 */
export function aggregate(lines: NeedLine[], info: Map<string, IngredientInfo>) {
  const out = new Map<
    string,
    { ingredientId: string; quantity: number; unit: string; optional: boolean }
  >();
  for (const l of lines) {
    const ing = info.get(l.ingredientId);
    if (!ing) continue;
    const target =
      convert(1, l.unit, ing.defaultUnit, ing.gramsPerUnit) !== null
        ? ing.defaultUnit
        : toGrams(1, l.unit, ing.gramsPerUnit) !== null
          ? "g"
          : l.unit;
    const qty = convert(l.quantity, l.unit, target, ing.gramsPerUnit) ?? l.quantity;
    const key = `${l.ingredientId}|${target}`;
    const prev = out.get(key);
    // optional only if every use is optional
    out.set(key, {
      ingredientId: l.ingredientId,
      unit: target,
      quantity: (prev?.quantity ?? 0) + qty,
      optional: (prev?.optional ?? true) && l.optional,
    });
  }
  return [...out.values()];
}

export function buildShoppingList(
  needs: NeedLine[],
  stock: StockLine[],
  info: Map<string, IngredientInfo>,
): ShoppingLine[] {
  const have = new Map<string, StockLine[]>();
  for (const s of stock) have.set(s.ingredientId, [...(have.get(s.ingredientId) ?? []), s]);

  const result: ShoppingLine[] = [];
  for (const need of aggregate(needs, info)) {
    const ing = info.get(need.ingredientId)!;
    if (NEVER_BUY.has(ing.key)) continue;
    let missing = need.quantity;
    for (const s of have.get(need.ingredientId) ?? []) {
      const inUnit = convert(s.quantity, s.unit, need.unit, ing.gramsPerUnit);
      if (inUnit !== null) missing -= inUnit;
    }
    // a sliver short (rounding, a slightly smaller pack) isn't worth a trip
    if (missing <= Math.max(1e-6, need.quantity * 0.1)) continue;
    const quantity = roundUpForShopping(missing, need.unit);
    result.push({
      ingredientId: need.ingredientId,
      quantity,
      unit: need.unit,
      section: need.optional || isStaple(ing.key, ing.category) ? "optional" : "required",
      aisle: ing.category,
      packageHint: packageHint(missing, need.unit, ing),
    });
  }
  return result.sort(
    (a, b) => AISLE_ORDER.indexOf(a.aisle as never) - AISLE_ORDER.indexOf(b.aisle as never),
  );
}

/** Shopping rounds up — you can't buy 0.3 of a cucumber, and buying less leaves you short. */
export function roundUpForShopping(q: number, unit: string): number {
  if (["piece", "clove", "bunch", "can", "slice", "pinch"].includes(unit))
    return Math.ceil(q - 1e-9);
  if (unit === "g" || unit === "ml") return q < 10 ? Math.ceil(q) : Math.ceil(q / 5) * 5;
  if (["tbsp", "tsp", "cup"].includes(unit)) return Math.ceil(q * 4 - 1e-9) / 4;
  return roundQuantity(q, unit);
}

const fa = (n: number) => toFaDigits(n);

/** A shopper-friendly amount: «۱ بسته‌ی کوچک», «نیم کیلو», «۳ عدد», «۱ بطری ۱ لیتری». */
export function packageHint(
  quantity: number,
  unit: string,
  ing: Pick<IngredientInfo, "key" | "category" | "gramsPerUnit">,
): string {
  if (unit === "piece") return `${fa(Math.ceil(quantity))} عدد`;
  if (unit === "clove")
    return ing.key === "garlic"
      ? `${fa(Math.max(1, Math.ceil(quantity / 10)))} بوته`
      : `${fa(Math.ceil(quantity))} حبه`;
  if (unit === "bunch") return `${fa(Math.ceil(quantity))} دسته`;
  if (unit === "can") return `${fa(Math.ceil(quantity))} قوطی`;
  if (unit === "slice")
    return Math.ceil(quantity) <= 12 ? "۱ بسته" : `${fa(Math.ceil(quantity / 12))} بسته`;

  if (unit === "ml" || (["tbsp", "tsp", "cup"].includes(unit) && ing.category === "drinks")) {
    const ml = convert(quantity, unit, "ml", { ...ing.gramsPerUnit, ml: 1 }) ?? quantity;
    if (ml <= 250) return "۱ بسته‌ی کوچک";
    if (ml <= 500) return "۱ بطری نیم‌لیتری";
    if (ml <= 1000) return "۱ بطری ۱ لیتری";
    return `${fa(Math.ceil(ml / 1000))} لیتر`;
  }

  const g = unit === "g" ? quantity : toGrams(quantity, unit, ing.gramsPerUnit);
  if (g === null) return "۱ بسته‌ی کوچک";
  if (ing.category === "spices" || g <= 60) return "۱ بسته‌ی کوچک";
  if (ing.category === "produce" || ing.category === "meat") {
    if (g <= 300) return "حدود ۲۵۰ گرم";
    if (g <= 550) return "نیم کیلو";
    if (g <= 1050) return "۱ کیلو";
    return `${formatQuantity(Math.ceil(g / 500) / 2)} کیلو`;
  }
  if (g <= 250) return "۱ بسته‌ی کوچک";
  if (g <= 500) return "۱ بسته‌ی نیم‌کیلویی";
  if (g <= 1000) return "۱ بسته‌ی ۱ کیلویی";
  return `${fa(Math.ceil(g / 1000))} بسته‌ی ۱ کیلویی`;
}
