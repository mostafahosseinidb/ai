import { db } from "@/lib/db";
import { assertUuid, notFound, parseJson, withHousehold } from "@/lib/server/api";
import { householdToday, pantryInclude, refreshExpiry } from "@/lib/server/pantry";
import { regenerateShopping } from "@/lib/server/shopping";
import { pantryUpdate } from "@/lib/pantry-schema";
import { isoToDbDate } from "@/lib/i18n/jalali";

async function owned(householdId: string, id: string) {
  assertUuid(id);
  const item = await db.pantryItem.findFirst({ where: { id, householdId, deletedAt: null } });
  if (!item) throw notFound();
  return item;
}

export const PATCH = withHousehold<{ id: string }>(async (req, { household }, { id }) => {
  const item = await owned(household.id, id);
  const input = await parseJson(req, pantryUpdate);
  const today = await householdToday(household.id);
  await db.pantryItem.update({
    where: { id },
    data: {
      quantity: input.quantity,
      unit: input.unit,
      location: input.location,
      openedAt:
        input.opened === undefined
          ? undefined
          : input.opened
            ? (item.openedAt ?? isoToDbDate(today))
            : null,
      packageExpiryDate:
        input.package_expiry_date === undefined
          ? undefined
          : input.package_expiry_date
            ? isoToDbDate(input.package_expiry_date)
            : null,
      status: input.status ?? (input.quantity === 0 ? "used" : undefined),
      note: input.note,
    },
  });
  await refreshExpiry(id);
  await regenerateShopping(household.id);
  return { item: await db.pantryItem.findUnique({ where: { id }, include: pantryInclude }) };
});

export const DELETE = withHousehold<{ id: string }>(async (_req, { household }, { id }) => {
  await owned(household.id, id);
  await db.pantryItem.update({ where: { id }, data: { deletedAt: new Date() } });
  await regenerateShopping(household.id);
  return { ok: true };
});
