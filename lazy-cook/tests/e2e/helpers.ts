import { randomUUID } from "node:crypto";
import { PrismaClient } from "@prisma/client";
import type { BrowserContext } from "@playwright/test";

export const db = new PrismaClient();

/** Create a user with a live database session and put its cookie in the browser (no email needed). */
export async function signInAs(context: BrowserContext, email = `e2e-${randomUUID()}@test.local`) {
  const user = await db.user.create({ data: { email, name: "تست", timezone: "Asia/Yerevan" } });
  const sessionToken = randomUUID();
  await db.session.create({
    data: { sessionToken, userId: user.id, expires: new Date(Date.now() + 86_400_000) },
  });
  await context.addCookies([
    {
      name: "authjs.session-token",
      value: sessionToken,
      domain: "localhost",
      path: "/",
      httpOnly: true,
      sameSite: "Lax",
    },
  ]);
  return user;
}

/** Skip the wizard: create a household straight in the database. */
export async function createHousehold(
  userId: string,
  opts: {
    equipment?: string[];
    slots?: string[];
    prefs?: Array<{
      type: "diet" | "avoid" | "limit" | "dislike" | "intolerance";
      value: string;
      limitPerWeek?: number;
    }>;
  } = {},
) {
  const household = await db.household.create({
    data: {
      name: "خونه",
      ownerUserId: userId,
      kitchenEquipment: opts.equipment ?? ["freezer", "blender"],
      activeMealSlots: opts.slots ?? ["breakfast", "lunch", "dinner"],
      users: { create: { userId, role: "owner" } },
      members: {
        create: [
          {
            displayName: "رومینا",
            portionFactor: 0.85,
            preferences: {
              create: (opts.prefs ?? []).map((p) => ({
                type: p.type,
                value: p.value,
                limitPerWeek: p.limitPerWeek ?? null,
              })),
            },
          },
          { displayName: "مصطفی", portionFactor: 1.2, position: 1 },
        ],
      },
    },
  });
  await db.user.update({
    where: { id: userId },
    data: { onboardingCompletedAt: new Date(), countryCode: "AM" },
  });
  return household;
}

export function todayIn(tz = "Asia/Yerevan") {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: tz,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}

export function addDays(iso: string, n: number) {
  const d = new Date(`${iso}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}
