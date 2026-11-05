import { z } from "zod";
import { db } from "@/lib/db";
import { ApiError, assertUuid, notFound, parseJson, withHousehold } from "@/lib/server/api";
import { householdToday, refreshExpiry } from "@/lib/server/pantry";
import { regenerateShopping } from "@/lib/server/shopping";
import { defaultLocation, type ShelfLife } from "@/lib/domain/pantry";
import { UNITS } from "@/lib/domain/units";
import { isoToDbDate } from "@/lib/i18n/jalali";

const body = z.object({
  quantity: z.number().positive().max(100000).optional(),
  unit: z.enum(UNITS).optional(),
  location: z.enum(["fridge", "freezer", "pantry"]).optional(),
  package_expiry_date: z
    .string()
    .regex(/^\d{4}-\d{2}-\d{2}$/)
    .nullable()
    .optional(),
});

/** «خریدم»: tick the item and put what was actually bought into the pantry. */
export const POST = withHousehold<{ id: string }>(async (req, { household }, { id }) => {
  assertUuid(id);
  const input = await parseJson(req, body);
  const item = await db.shoppingItem.findFirst({
    where: { id, householdId: household.id, deletedAt: null },
    include: { ingredient: true },
  });
  if (!item) throw notFound();
  if (item.checkedAt) throw new ApiError(409, "already_purchased");
  const quantity = input.quantity ?? (item.quantity ? Number(item.quantity) : null);
  const unit = input.unit ?? item.unit ?? item.ingredient?.defaultUnit ?? "piece";
  const today = await householdToday(household.id);

  let pantryItemId: string | null = null;
  await db.$transaction(async (tx) => {
    await tx.shoppingItem.update({ where: { id }, data: { checkedAt: new Date() } });
    if (quantity) {
      const life = (item.ingredient?.shelfLifeDays ?? null) as ShelfLife | null;
      const p = await tx.pantryItem.create({
        data: {
          householdId: household.id,
          ingredientId: item.ingredientId,
          customName: item.ingredientId ? null : item.customName,
          quantity,
          unit,
          location: input.location ?? defaultLocation(item.ingredient?.category ?? "other", life),
          purchasedAt: isoToDbDate(today),
          packageExpiryDate: input.package_expiry_date
            ? isoToDbDate(input.package_expiry_date)
            : null,
        },
      });
      pantryItemId = p.id;
    }
  });
  if (pantryItemId) await refreshExpiry(pantryItemId);
  await regenerateShopping(household.id);
  return { pantry_item_id: pantryItemId };
});

/** Undo «خریدم»: remove what it put in the pantry and untick, in one step (no duplicate rows). */
export const DELETE = withHousehold<{ id: string }>(async (req, { household }, { id }) => {
  assertUuid(id);
  const { pantry_item_id } = await parseJson(
    req,
    z.object({ pantry_item_id: z.string().uuid().nullable().optional() }),
  );
  const item = await db.shoppingItem.findFirst({
    where: { id, householdId: household.id, deletedAt: null },
  });
  if (!item) throw notFound();
  await db.$transaction([
    ...(pantry_item_id
      ? [
          db.pantryItem.updateMany({
            where: { id: pantry_item_id, householdId: household.id },
            data: { deletedAt: new Date() },
          }),
        ]
      : []),
    db.shoppingItem.update({ where: { id }, data: { checkedAt: null } }),
  ]);
  await regenerateShopping(household.id);
  return { ok: true };
});
