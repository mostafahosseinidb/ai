import { z } from "zod";
import { db } from "@/lib/db";
import { assertUuid, notFound, parseJson, withHousehold } from "@/lib/server/api";

async function owned(householdId: string, id: string) {
  assertUuid(id);
  const item = await db.wishlistItem.findFirst({ where: { id, householdId, deletedAt: null } });
  if (!item) throw notFound();
  return item;
}

const body = z.object({
  note: z.string().trim().max(200).nullable().optional(),
  priority: z.number().int().min(0).max(1).optional(),
});

export const PATCH = withHousehold<{ id: string }>(async (req, { household }, { id }) => {
  await owned(household.id, id);
  const input = await parseJson(req, body);
  return {
    item: await db.wishlistItem.update({
      where: { id },
      data: { note: input.note, priority: input.priority },
    }),
  };
});

export const DELETE = withHousehold<{ id: string }>(async (_req, { household }, { id }) => {
  await owned(household.id, id);
  await db.wishlistItem.update({ where: { id }, data: { deletedAt: new Date() } });
  return { ok: true };
});
