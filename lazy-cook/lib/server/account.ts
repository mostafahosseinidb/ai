import "server-only";
import { db } from "@/lib/db";

/** Everything stored about the user and their household, as plain JSON (spec §5.12, §11.2). */
export async function exportData(userId: string) {
  const user = await db.user.findUniqueOrThrow({
    where: { id: userId },
    select: {
      id: true,
      email: true,
      name: true,
      locale: true,
      countryCode: true,
      city: true,
      timezone: true,
      currency: true,
      calendar: true,
      units: true,
      createdAt: true,
      onboardingCompletedAt: true,
    },
  });
  const links = await db.householdUser.findMany({
    where: { userId, deletedAt: null },
    select: { householdId: true, role: true },
  });
  const households = await Promise.all(
    links.map(async (l) => ({
      role: l.role,
      household: await db.household.findUnique({
        where: { id: l.householdId },
        include: {
          members: { include: { preferences: true } },
          recipes: {
            include: {
              ingredients: {
                include: { ingredient: { select: { canonicalName: true, nameFa: true } } },
              },
            },
          },
          mealPlans: {
            include: {
              meals: { include: { feedback: true, recipe: { select: { titleFa: true } } } },
            },
          },
          freeLogs: true,
          prepTasks: true,
          pantryItems: {
            include: { ingredient: { select: { canonicalName: true, nameFa: true } } },
          },
          shoppingItems: {
            include: { ingredient: { select: { canonicalName: true, nameFa: true } } },
          },
          wishlistItems: { include: { recipe: { select: { titleFa: true } } } },
          recurringRules: true,
          messages: true,
        },
      }),
    })),
  );
  return {
    exported_at: new Date().toISOString(),
    format: "lazy-immigrant-cook/v1",
    user,
    households,
  };
}

/**
 * Delete the account for good (the one hard delete — spec §8). A household shared with other
 * accounts stays (ownership passes to the longest-standing member); otherwise it is deleted with
 * everything in it.
 */
export async function deleteAccount(userId: string) {
  const user = await db.user.findUniqueOrThrow({ where: { id: userId } });
  await db.$transaction(async (tx) => {
    const links = await tx.householdUser.findMany({
      where: { userId },
      include: { household: true },
    });
    for (const l of links) {
      const others = await tx.householdUser.findMany({
        where: { householdId: l.householdId, userId: { not: userId }, deletedAt: null },
        orderBy: { createdAt: "asc" },
      });
      if (others.length === 0) {
        if (l.household.ownerUserId === userId)
          await tx.household.delete({ where: { id: l.householdId } });
      } else if (l.household.ownerUserId === userId) {
        await tx.household.update({
          where: { id: l.householdId },
          data: { ownerUserId: others[0]!.userId },
        });
        await tx.householdUser.update({ where: { id: others[0]!.id }, data: { role: "owner" } });
      }
    }
    // households the user owns but no longer belongs to (left earlier) with nobody else in them
    const orphans = await tx.household.findMany({ where: { ownerUserId: userId } });
    for (const h of orphans) {
      const others = await tx.householdUser.findFirst({
        where: { householdId: h.id, userId: { not: userId }, deletedAt: null },
      });
      if (others)
        await tx.household.update({ where: { id: h.id }, data: { ownerUserId: others.userId } });
      else await tx.household.delete({ where: { id: h.id } });
    }
    if (user.email) {
      await tx.verificationToken.deleteMany({ where: { identifier: user.email } });
      await tx.authAttempt.deleteMany({ where: { key: `email:${user.email}` } });
    }
    await tx.user.delete({ where: { id: userId } }); // cascades accounts, sessions, memberships
  });
}
