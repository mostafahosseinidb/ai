/** Units used across recipes, pantry and shopping. */
export const UNITS = [
  "g",
  "ml",
  "piece",
  "clove",
  "slice",
  "bunch",
  "tbsp",
  "tsp",
  "cup",
  "pinch",
  "can",
] as const;
export type Unit = (typeof UNITS)[number];

/** Units that represent whole things — scaled quantities are rounded to whole/half items. */
export const COUNT_UNITS: ReadonlySet<string> = new Set([
  "piece",
  "clove",
  "slice",
  "bunch",
  "can",
]);

/** Spoon/cup units — rounded to the nearest ¼. */
export const SPOON_UNITS: ReadonlySet<string> = new Set(["tbsp", "tsp", "cup"]);

/** Fixed metric volumes, used when an ingredient has no per-unit gram weight for a volume unit. */
export const ML_PER_UNIT: Record<string, number> = { ml: 1, tsp: 5, tbsp: 15, cup: 240 };

export const UNIT_LABEL_FA: Record<string, string> = {
  g: "گرم",
  ml: "میلی‌لیتر",
  piece: "عدد",
  clove: "حبه",
  slice: "برش",
  bunch: "دسته",
  tbsp: "قاشق غذاخوری",
  tsp: "قاشق چای‌خوری",
  cup: "پیمانه",
  pinch: "کمی",
  can: "قوطی",
};

export type GramsPerUnit = Partial<Record<string, number>>;

/**
 * Convert a quantity of an ingredient to grams. Returns null when the conversion is unknown
 * (e.g. "1 bunch" of something with no recorded bunch weight).
 */
export function toGrams(
  quantity: number,
  unit: string,
  gramsPerUnit: GramsPerUnit = {},
): number | null {
  if (unit === "g") return quantity;
  const direct = gramsPerUnit[unit];
  if (typeof direct === "number") return quantity * direct;
  // fall back through ml for volume units (water-like density, or the ingredient's ml weight)
  const ml = ML_PER_UNIT[unit];
  if (ml !== undefined) {
    const perMl = gramsPerUnit.ml ?? 1;
    return quantity * ml * perMl;
  }
  return null;
}

/** Convert grams back to a given unit for the same ingredient; null if impossible. */
export function fromGrams(
  grams: number,
  unit: string,
  gramsPerUnit: GramsPerUnit = {},
): number | null {
  const one = toGrams(1, unit, gramsPerUnit);
  if (one === null || one === 0) return null;
  return grams / one;
}

/**
 * Convert between two units of the same ingredient. Same unit → identity.
 * Returns null when either side has no known gram weight.
 */
export function convert(
  quantity: number,
  from: string,
  to: string,
  gramsPerUnit: GramsPerUnit = {},
): number | null {
  if (from === to) return quantity;
  const g = toGrams(quantity, from, gramsPerUnit);
  if (g === null) return null;
  return fromGrams(g, to, gramsPerUnit);
}
