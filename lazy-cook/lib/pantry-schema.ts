import { z } from "zod";
import { UNITS } from "@/lib/domain/units";

const isoDate = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);

export const pantryCreate = z
  .object({
    ingredient_id: z.string().uuid().nullable().optional(),
    custom_name: z.string().trim().min(1).max(80).nullable().optional(),
    recipe_id: z.string().uuid().nullable().optional(),
    quantity: z.number().positive().max(100000),
    unit: z.enum(UNITS),
    location: z.enum(["fridge", "freezer", "pantry"]).optional(),
    purchased_at: isoDate.nullable().optional(),
    opened: z.boolean().optional(),
    opened_at: isoDate.nullable().optional(),
    package_expiry_date: isoDate.nullable().optional(),
    note: z.string().max(200).nullable().optional(),
  })
  .refine((b) => b.ingredient_id || b.custom_name, {
    message: "ingredient_id or custom_name required",
  });

export const pantryUpdate = z.object({
  quantity: z.number().min(0).max(100000).optional(),
  unit: z.enum(UNITS).optional(),
  location: z.enum(["fridge", "freezer", "pantry"]).optional(),
  opened: z.boolean().optional(),
  package_expiry_date: isoDate.nullable().optional(),
  status: z.enum(["active", "used", "discarded"]).optional(),
  note: z.string().max(200).nullable().optional(),
});
