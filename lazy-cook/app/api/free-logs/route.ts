import { z } from "zod";
import { db } from "@/lib/db";
import { isoDate, parseJson, withHousehold } from "@/lib/server/api";
import { isoToDbDate } from "@/lib/i18n/jalali";
import { SLOTS } from "@/lib/seed-schema";

const body = z.object({
  date: z.string().regex(isoDate),
  slot: z.enum(SLOTS).nullable().optional(),
  text: z.string().trim().min(1).max(300),
});

export const POST = withHousehold(async (req, { household }) => {
  const input = await parseJson(req, body);
  const log = await db.freeLog.create({
    data: {
      householdId: household.id,
      date: isoToDbDate(input.date),
      slot: input.slot ?? null,
      text: input.text,
    },
  });
  return { log };
});
