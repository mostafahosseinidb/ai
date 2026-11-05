import { db } from "@/lib/db";
import { assertUuid, withHousehold } from "@/lib/server/api";

/** Revoke an unused invite. */
export const DELETE = withHousehold<{ id: string }>(async (_req, { household }, { id }) => {
  assertUuid(id);
  await db.householdInvite.updateMany({
    where: { id, householdId: household.id, usedAt: null },
    data: { expiresAt: new Date() },
  });
  return { ok: true };
});
