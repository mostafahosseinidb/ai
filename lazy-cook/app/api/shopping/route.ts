import { z } from "zod";
import { db } from "@/lib/db";
import { ApiError, parseJson, withHousehold } from "@/lib/server/api";
import { loadShopping, shoppingText } from "@/lib/server/shopping-view";
import { AISLE_ORDER } from "@/lib/domain/shopping";
import { UNITS } from "@/lib/domain/units";

export const GET = withHousehold(async (req, { household, user }) => {
  const items = await loadShopping(household.id, user.countryCode);
  if (new URL(req.url).searchParams.get("format") === "text") {
    return new Response(shoppingText(items), {
      headers: { "content-type": "text/plain; charset=utf-8" },
    });
  }
  return { items };
});

const body = z
  .object({
    ingredient_id: z.string().uuid().nullable().optional(),
    custom_name: z.string().trim().min(1).max(80).nullable().optional(),
    quantity: z.number().positive().max(100000).nullable().optional(),
    unit: z.enum(UNITS).nullable().optional(),
    section: z.enum(["required", "optional"]).default("required"),
  })
  .refine((b) => b.ingredient_id || b.custom_name, {
    message: "ingredient_id or custom_name required",
  });

/** Add an item by hand; it survives regeneration (added_by = user). */
export const POST = withHousehold(async (req, { household }) => {
  const input = await parseJson(req, body);
  let aisle: string = "other";
  if (input.ingredient_id) {
    const ing = await db.ingredient.findUnique({ where: { id: input.ingredient_id } });
    if (!ing) throw new ApiError(404, "ingredient_not_found");
    aisle = AISLE_ORDER.includes(ing.category as never) ? ing.category : "other";
  }
  const item = await db.shoppingItem.create({
    data: {
      householdId: household.id,
      ingredientId: input.ingredient_id ?? null,
      customName: input.ingredient_id ? null : input.custom_name,
      quantity: input.quantity ?? null,
      unit: input.unit ?? null,
      section: input.section ?? "required",
      aisle,
      addedBy: "user",
    },
  });
  return { item };
});
