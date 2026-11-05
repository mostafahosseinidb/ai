import { db } from "@/lib/db";
import { withHousehold } from "@/lib/server/api";

export const POST = withHousehold(async (_req, { household }) => {
  const res = await db.shoppingItem.updateMany({
    where: { householdId: household.id, checkedAt: { not: null }, deletedAt: null },
    data: { deletedAt: new Date() },
  });
  return { cleared: res.count };
});
