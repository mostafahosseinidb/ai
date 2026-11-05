import { COUNT_UNITS, SPOON_UNITS } from "./units";

/**
 * Smart rounding for scaled quantities (spec §5.2, acceptance #9):
 *  - countable things (eggs, onions, cloves…) → whole numbers; ½ is allowed only when rounding
 *    to a whole number would be far off (e.g. 0.5 of one onion stays "½", but 1.0 egg stays 1).
 *  - spoons and cups → nearest ¼ (never below ¼).
 *  - grams / ml → nearest 5 (nearest 1 under 10, so small spice amounts don't vanish).
 *  - pinch → whole pinches, at least 1.
 */
export function roundQuantity(quantity: number, unit: string, opts: { halvesAllowed?: boolean } = {}): number {
  if (!Number.isFinite(quantity) || quantity <= 0) return 0;

  if (COUNT_UNITS.has(unit)) {
    const whole = Math.round(quantity);
    if (whole >= 1 && Math.abs(whole - quantity) <= 0.25) return whole;
    if (whole >= 2) return whole; // 2.4 eggs → 2, 2.6 → 3
    // below ~1.25: prefer one whole item; use a half only if allowed and clearly closer
    if (opts.halvesAllowed !== false && quantity < 0.75) return 0.5;
    return Math.max(1, whole);
  }

  if (SPOON_UNITS.has(unit)) return Math.max(0.25, Math.round(quantity * 4) / 4);

  if (unit === "pinch") return Math.max(1, Math.round(quantity));

  if (unit === "g" || unit === "ml") {
    if (quantity < 10) return Math.max(1, Math.round(quantity));
    return Math.round(quantity / 5) * 5;
  }

  return Math.round(quantity * 100) / 100;
}

/** Countable ingredients that can't sensibly be halved — an egg is an egg. */
const NEVER_HALVE = new Set(["egg", "tortilla", "lavash_bread", "pita_bread", "toast_bread"]);

export function scaleQuantity(
  quantity: number,
  unit: string,
  factor: number,
  ingredientKey?: string,
): number {
  const halvesAllowed = !(ingredientKey && NEVER_HALVE.has(ingredientKey));
  return roundQuantity(quantity * factor, unit, { halvesAllowed });
}

/** Sum of portion factors for the members eating a meal. */
export function totalPortions(servingsByMember: Record<string, number>): number {
  return Object.values(servingsByMember).reduce((a, b) => a + (Number(b) || 0), 0);
}

export interface ScalableIngredient {
  key: string;
  quantity: number;
  unit: string;
}

/** Scale a recipe's ingredients from base servings to the desired number of portions. */
export function scaleIngredients<T extends ScalableIngredient>(
  items: T[],
  baseServings: number,
  portions: number,
): Array<T & { scaledQuantity: number }> {
  const factor = baseServings > 0 ? portions / baseServings : 1;
  return items.map((it) => ({
    ...it,
    scaledQuantity: scaleQuantity(it.quantity, it.unit, factor, it.key),
  }));
}
