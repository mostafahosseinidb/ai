import { db } from "@/lib/db";
import { ApiError, parseJson, withHousehold } from "@/lib/server/api";
import { householdToday, pantryInclude, refreshExpiry } from "@/lib/server/pantry";
import { regenerateShopping } from "@/lib/server/shopping";
import { defaultLocation, type ShelfLife } from "@/lib/domain/pantry";
import { pantryCreate } from "@/lib/pantry-schema";
import { isoToDbDate } from "@/lib/i18n/jalali";

export const GET = withHousehold(async (_req, { household }) => {
  const items = await db.pantryItem.findMany({
    where: { householdId: household.id, status: "active", deletedAt: null },
    include: pantryInclude,
    orderBy: [{ computedExpiryDate: { sort: "asc", nulls: "last" } }, { createdAt: "desc" }],
  });
  return { items };
});

export const POST = withHousehold(async (req, { household }) => {
  const input = await parseJson(req, pantryCreate);
  const today = await householdToday(household.id);
  let category = "other";
  let life: ShelfLife | null = null;
  if (input.ingredient_id) {
    const ing = await db.ingredient.findUnique({ where: { id: input.ingredient_id } });
    if (!ing) throw new ApiError(404, "ingredient_not_found");
    category = ing.category;
    life = ing.shelfLifeDays as unknown as ShelfLife;
  }
  if (input.recipe_id) {
    const r = await db.recipe.findFirst({
      where: {
        id: input.recipe_id,
        OR: [{ ownerHouseholdId: null }, { ownerHouseholdId: household.id }],
      },
    });
    if (!r) throw new ApiError(404, "recipe_not_found");
  }
  const opened = input.opened_at ?? (input.opened ? today : null);
  const item = await db.pantryItem.create({
    data: {
      householdId: household.id,
      ingredientId: input.ingredient_id ?? null,
      customName: input.ingredient_id ? null : input.custom_name,
      recipeId: input.recipe_id ?? null,
      quantity: input.quantity,
      unit: input.unit,
      location:
        input.location ?? (input.recipe_id ? "freezer" : defaultLocation(category, life, !!opened)),
      purchasedAt: isoToDbDate(input.purchased_at ?? today),
      openedAt: opened ? isoToDbDate(opened) : null,
      packageExpiryDate: input.package_expiry_date ? isoToDbDate(input.package_expiry_date) : null,
      note: input.note ?? null,
    },
  });
  await refreshExpiry(item.id);
  await regenerateShopping(household.id);
  return {
    item: await db.pantryItem.findUnique({ where: { id: item.id }, include: pantryInclude }),
  };
});
