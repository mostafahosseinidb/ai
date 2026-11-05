import { db } from "@/lib/db";
import { withHousehold } from "@/lib/server/api";
import { createInvite } from "@/lib/server/invites";

export const GET = withHousehold(async (_req, { household }) => {
  const invites = await db.householdInvite.findMany({
    where: { householdId: household.id, usedAt: null, expiresAt: { gt: new Date() } },
    orderBy: { createdAt: "desc" },
  });
  return { invites: invites.map((i) => ({ id: i.id, code: i.code, expiresAt: i.expiresAt })) };
});

export const POST = withHousehold(async (req, { household, user }) => {
  const invite = await createInvite(household.id, user.id);
  const origin = new URL(req.url).origin;
  return {
    invite: {
      id: invite.id,
      code: invite.code,
      url: `${origin}/join/${invite.code}`,
      expiresAt: invite.expiresAt,
    },
  };
});
