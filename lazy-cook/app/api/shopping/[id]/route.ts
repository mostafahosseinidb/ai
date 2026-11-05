import { z } from "zod";
import { db } from "@/lib/db";
import { assertUuid, notFound, parseJson, withHousehold } from "@/lib/server/api";

async function owned(householdId: string, id: string) {
  assertUuid(id);
  const item = await db.shoppingItem.findFirst({ where: { id, householdId, deletedAt: null } });
  if (!item) throw notFound();
  return item;
}

const body = z.object({
  checked: z.boolean().optional(),
  section: z.enum(["required", "optional"]).optional(),
});

/** Tick without moving to the pantry (e.g. custom items), or move between sections. */
export const PATCH = withHousehold<{ id: string }>(async (req, { household }, { id }) => {
  await owned(household.id, id);
  const input = await parseJson(req, body);
  const item = await db.shoppingItem.update({
    where: { id },
    data: {
      checkedAt: input.checked === undefined ? undefined : input.checked ? new Date() : null,
      section: input.section,
    },
  });
  return { item };
});

export const DELETE = withHousehold<{ id: string }>(async (_req, { household }, { id }) => {
  await owned(household.id, id);
  await db.shoppingItem.update({ where: { id }, data: { deletedAt: new Date() } });
  return { ok: true };
});
