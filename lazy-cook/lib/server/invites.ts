import "server-only";
import { randomBytes } from "node:crypto";
import { db } from "@/lib/db";

export const INVITE_DAYS = 7;
const ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"; // no 0/o/1/l/i

export function newInviteCode(len = 10): string {
  const bytes = randomBytes(len);
  return Array.from(bytes, (b) => ALPHABET[b % ALPHABET.length]).join("");
}

export async function createInvite(householdId: string, userId: string) {
  return db.householdInvite.create({
    data: {
      householdId,
      createdByUserId: userId,
      code: newInviteCode(),
      expiresAt: new Date(Date.now() + INVITE_DAYS * 86_400_000),
    },
  });
}

export type InviteState = "ok" | "not_found" | "used" | "expired";

export async function findInvite(code: string) {
  if (!/^[a-z0-9]{6,32}$/.test(code)) return { state: "not_found" as InviteState, invite: null };
  const invite = await db.householdInvite.findUnique({
    where: { code },
    include: {
      household: {
        include: {
          owner: { select: { name: true, email: true, countryCode: true, timezone: true } },
          members: { where: { deletedAt: null }, select: { displayName: true } },
        },
      },
    },
  });
  if (!invite || invite.household.deletedAt)
    return { state: "not_found" as InviteState, invite: null };
  if (invite.usedAt) return { state: "used" as InviteState, invite };
  if (invite.expiresAt < new Date()) return { state: "expired" as InviteState, invite };
  return { state: "ok" as InviteState, invite };
}

/**
 * Join the household behind an invite. A user belongs to one household at a time: their
 * previous membership is closed (soft-deleted), its data is left untouched.
 */
export async function acceptInvite(userId: string, code: string): Promise<InviteState | "joined"> {
  const { state, invite } = await findInvite(code);
  if (state !== "ok" || !invite) return state;
  await db.$transaction(async (tx) => {
    // claim the code atomically so it can't be used twice
    const claimed = await tx.householdInvite.updateMany({
      where: { id: invite.id, usedAt: null },
      data: { usedAt: new Date(), usedByUserId: userId },
    });
    if (claimed.count === 0) throw new Error("invite_race");
    await tx.householdUser.updateMany({
      where: { userId, deletedAt: null, householdId: { not: invite.householdId } },
      data: { deletedAt: new Date() },
    });
    const existing = await tx.householdUser.findUnique({
      where: { householdId_userId: { householdId: invite.householdId, userId } },
    });
    if (existing)
      await tx.householdUser.update({ where: { id: existing.id }, data: { deletedAt: null } });
    else
      await tx.householdUser.create({
        data: { householdId: invite.householdId, userId, role: "member" },
      });
    const user = await tx.user.findUniqueOrThrow({ where: { id: userId } });
    await tx.user.update({
      where: { id: userId },
      data: {
        onboardingCompletedAt: user.onboardingCompletedAt ?? new Date(),
        countryCode: user.countryCode ?? invite.household.owner.countryCode,
        timezone: user.timezone === "UTC" ? invite.household.owner.timezone : user.timezone,
      },
    });
  });
  return "joined";
}

/** Remove an account from a household (owner removing a member, or a member leaving). */
export async function removeHouseholdUser(
  householdId: string,
  actorId: string,
  targetUserId: string,
) {
  const household = await db.household.findUniqueOrThrow({ where: { id: householdId } });
  if (targetUserId === household.ownerUserId) return "owner_cannot_leave" as const;
  if (actorId !== household.ownerUserId && actorId !== targetUserId) return "forbidden" as const;
  await db.householdUser.updateMany({
    where: { householdId, userId: targetUserId, deletedAt: null },
    data: { deletedAt: new Date() },
  });
  return "removed" as const;
}
