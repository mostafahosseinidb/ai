import { z } from "zod";
import { db } from "@/lib/db";
import { parseJson, withHousehold } from "@/lib/server/api";
import { loadMembers } from "@/lib/server/household";

export const GET = withHousehold(async (_req, { household, user }) => {
  const members = await loadMembers(household.id);
  return {
    household,
    members,
    user: {
      calendar: user.calendar,
      units: user.units,
      countryCode: user.countryCode,
      timezone: user.timezone,
    },
  };
});

const body = z.object({
  calendar: z.enum(["jalali", "gregorian"]).optional(),
  units: z.enum(["metric", "cups"]).optional(),
  auto_deduct_pantry: z.boolean().optional(),
});

export const PATCH = withHousehold(async (req, { household, user }) => {
  const input = await parseJson(req, body);
  if (input.calendar || input.units)
    await db.user.update({
      where: { id: user.id },
      data: { calendar: input.calendar, units: input.units },
    });
  if (input.auto_deduct_pantry !== undefined)
    await db.household.update({
      where: { id: household.id },
      data: { autoDeductPantry: input.auto_deduct_pantry },
    });
  return { ok: true };
});
