import { z } from "zod";
import { EQUIPMENT, SLOTS } from "@/lib/seed-schema";

export const preferenceInput = z.object({
  type: z.enum(["diet", "avoid", "limit", "dislike", "like", "intolerance"]),
  value: z.string().trim().min(1).max(80),
  limit_per_week: z.number().int().min(0).max(21).nullable().optional(),
});

export const memberInput = z.object({
  id: z.string().uuid().optional(),
  display_name: z.string().trim().min(1).max(40),
  birth_year: z.number().int().min(1900).max(2030).nullable().optional(),
  sex: z.enum(["female", "male", "other"]).nullable().optional(),
  height_cm: z.number().int().min(80).max(250).nullable().optional(),
  weight_kg: z.number().min(25).max(300).nullable().optional(),
  goal: z.enum(["maintain", "lose_slow", "lose", "gain_muscle"]).default("maintain"),
  activity_level: z.enum(["sedentary", "light", "moderate", "active"]).default("light"),
  /** set only when the user overrides the computed factor */
  portion_factor: z.number().min(0.4).max(2).nullable().optional(),
  preferences: z.array(preferenceInput).max(60).default([]),
});

export const onboardingInput = z.object({
  country_code: z.string().length(2),
  city: z.string().trim().max(80).nullable().optional(),
  timezone: z.string().max(60).optional(),
  members: z.array(memberInput).min(1).max(8),
  household: z.object({
    kitchen_equipment: z.array(z.enum(EQUIPMENT)).default([]),
    burners: z.number().int().min(0).max(8).default(2),
    active_meal_slots: z.array(z.enum(SLOTS)).min(1).default(["breakfast", "lunch", "dinner"]),
    cooking_days_per_week: z.number().int().min(1).max(7).default(4),
    batch_prep_weekday: z.number().int().min(0).max(6).nullable().optional(),
    iranian_food_frequency_days: z.number().int().min(1).max(60).default(7),
    weekly_budget: z.number().min(0).max(100000).nullable().optional(),
    bedtime: z
      .string()
      .regex(/^\d{2}:\d{2}$/)
      .nullable()
      .optional(),
  }),
});
export type OnboardingInput = z.infer<typeof onboardingInput>;
export type MemberInput = z.infer<typeof memberInput>;
