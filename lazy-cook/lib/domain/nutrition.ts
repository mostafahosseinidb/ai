import { toGrams, type GramsPerUnit } from "./units";

export interface Nutrition {
  kcal: number;
  protein: number;
  carbs: number;
  fat: number;
  fiber: number;
}

export const ZERO_NUTRITION: Nutrition = { kcal: 0, protein: 0, carbs: 0, fat: 0, fiber: 0 };

export function addNutrition(a: Nutrition, b: Nutrition): Nutrition {
  return {
    kcal: a.kcal + b.kcal,
    protein: a.protein + b.protein,
    carbs: a.carbs + b.carbs,
    fat: a.fat + b.fat,
    fiber: a.fiber + b.fiber,
  };
}

export function scaleNutrition(n: Nutrition, f: number): Nutrition {
  return { kcal: n.kcal * f, protein: n.protein * f, carbs: n.carbs * f, fat: n.fat * f, fiber: n.fiber * f };
}

/**
 * Compute a recipe's per-serving nutrition from its ingredients. Ingredients whose amount
 * can't be converted to grams are skipped and reported, so the caller can fall back to a stored value.
 */
export function nutritionFromIngredients(
  items: Array<{ quantity: number; unit: string; gramsPerUnit: GramsPerUnit; per100g: Nutrition; optional?: boolean }>,
  servings: number,
): { perServing: Nutrition; unconverted: number } {
  let total = ZERO_NUTRITION;
  let unconverted = 0;
  for (const it of items) {
    if (it.optional) continue;
    const g = toGrams(it.quantity, it.unit, it.gramsPerUnit);
    if (g === null) {
      unconverted++;
      continue;
    }
    total = addNutrition(total, scaleNutrition(it.per100g, g / 100));
  }
  return { perServing: scaleNutrition(total, 1 / Math.max(1, servings)), unconverted };
}

export type Sex = "female" | "male" | "other";
export type Goal = "maintain" | "lose_slow" | "lose" | "gain_muscle";
export type Activity = "sedentary" | "light" | "moderate" | "active";

const ACTIVITY_FACTOR: Record<Activity, number> = {
  sedentary: 1.2,
  light: 1.375,
  moderate: 1.55,
  active: 1.725,
};

/** Daily kcal adjustment per goal; weight loss never targets more than ~0.5 kg/week (spec §11.1 caps at 1). */
const GOAL_DELTA: Record<Goal, number> = { maintain: 0, lose_slow: -250, lose: -500, gain_muscle: 250 };

/** Safety floors from spec §11.1. */
export const KCAL_FLOOR = { female: 1200, male: 1500, other: 1200 } as const;

/**
 * Mifflin–St Jeor estimate. Only an *estimate* for display; returns null when data is missing.
 */
export function estimateDailyKcal(input: {
  sex?: Sex | null;
  birthYear?: number | null;
  heightCm?: number | null;
  weightKg?: number | null;
  activity: Activity;
  goal: Goal;
  currentYear?: number;
}): number | null {
  const { sex, birthYear, heightCm, weightKg } = input;
  if (!sex || !birthYear || !heightCm || !weightKg) return null;
  const age = (input.currentYear ?? new Date().getFullYear()) - birthYear;
  if (age < 14 || age > 110) return null; // formula isn't meant for children
  const s = sex === "male" ? 5 : -161;
  const bmr = 10 * weightKg + 6.25 * heightCm - 5 * age + s;
  const tdee = bmr * ACTIVITY_FACTOR[input.activity] + GOAL_DELTA[input.goal];
  const floor = KCAL_FLOOR[sex];
  return Math.round(Math.max(floor, tdee) / 10) * 10;
}

/**
 * Portion factor: how big this member's serving is relative to a standard ~2000 kcal one.
 * Clamped to 0.6–1.6 and rounded to 0.05. Defaults to 1.0 without enough data.
 */
export function portionFactor(input: Parameters<typeof estimateDailyKcal>[0]): number {
  const kcal = estimateDailyKcal(input);
  if (kcal === null) {
    const byGoal: Record<Goal, number> = { maintain: 1, lose_slow: 0.9, lose: 0.85, gain_muscle: 1.15 };
    return byGoal[input.goal];
  }
  const f = kcal / 2000;
  return Math.round(Math.min(1.6, Math.max(0.6, f)) * 20) / 20;
}
