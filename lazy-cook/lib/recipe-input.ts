import { z } from "zod";
import { CUISINES, EQUIPMENT, MEAL_TYPES, RECIPE_TAGS } from "@/lib/seed-schema";
import { UNITS } from "@/lib/domain/units";

/** A recipe typed in by the user (spec §5.2, source = user_manual). Same rules as seed recipes. */
export const recipeInput = z.object({
  title_fa: z.string().trim().min(1).max(80),
  title_original: z.string().trim().max(80).nullable().optional(),
  intro_fa: z.string().trim().max(200).nullable().optional(),
  cuisine: z.enum(CUISINES),
  meal_types: z.array(z.enum(MEAL_TYPES)).min(1),
  base_servings: z.number().int().min(1).max(12),
  active_minutes: z.number().int().min(0).max(600),
  total_minutes: z.number().int().min(0).max(4320),
  difficulty: z.number().int().min(1).max(3),
  requires_equipment: z.array(z.enum(EQUIPMENT)).default([]),
  equipment_alternatives_fa: z.string().trim().max(300).nullable().optional(),
  ingredients: z
    .array(
      z.object({
        ingredient_id: z.string().uuid(),
        quantity: z.number().positive().max(100000),
        unit: z.enum(UNITS),
        prep_note: z.string().trim().max(80).nullable().optional(),
        optional: z.boolean().default(false),
      }),
    )
    .min(1)
    .max(40),
  // «حداکثر ۷ مرحله، هر مرحله یک جمله‌ی کوتاه» (spec §3.1)
  steps: z
    .array(
      z.object({
        text: z.string().trim().min(1).max(240),
        timer_seconds: z.number().int().positive().max(86400).nullable().optional(),
      }),
    )
    .min(1)
    .max(7),
  freezer_friendly: z.boolean().default(false),
  fridge_days: z.number().int().min(0).max(30).nullable().optional(),
  reheat_note: z.string().trim().max(200).nullable().optional(),
  prep_ahead: z
    .array(
      z.object({
        text: z.string().trim().min(1).max(200),
        hours_before: z.number().positive().max(72),
      }),
    )
    .max(3)
    .default([]),
  tags: z.array(z.enum(RECIPE_TAGS)).default([]),
  source_credit: z.string().trim().max(120).nullable().optional(),
});
export type RecipeInput = z.infer<typeof recipeInput>;
