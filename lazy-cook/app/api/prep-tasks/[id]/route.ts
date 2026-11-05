import { z } from "zod";
import { db } from "@/lib/db";
import { assertUuid, notFound, parseJson, withHousehold } from "@/lib/server/api";

const body = z.object({ done: z.boolean() });

export const PATCH = withHousehold<{ id: string }>(async (req, { household }, { id }) => {
  assertUuid(id);
  const input = await parseJson(req, body);
  const task = await db.prepTask.findFirst({
    where: { id, householdId: household.id, deletedAt: null },
  });
  if (!task) throw notFound();
  return {
    task: await db.prepTask.update({
      where: { id },
      data: { doneAt: input.done ? new Date() : null },
    }),
  };
});
