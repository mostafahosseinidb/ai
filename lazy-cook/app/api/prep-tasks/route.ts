import { db } from "@/lib/db";
import { withHousehold } from "@/lib/server/api";
import { isoToDbDate } from "@/lib/i18n/jalali";

export const GET = withHousehold(async (req, { household }) => {
  const date = new URL(req.url).searchParams.get("date");
  const tasks = await db.prepTask.findMany({
    where: {
      householdId: household.id,
      deletedAt: null,
      ...(date && /^\d{4}-\d{2}-\d{2}$/.test(date) ? { dueDate: isoToDbDate(date) } : {}),
    },
    orderBy: [{ dueDate: "asc" }, { dueTime: "asc" }],
  });
  return { tasks };
});
