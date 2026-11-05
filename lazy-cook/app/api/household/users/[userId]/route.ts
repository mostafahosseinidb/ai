import { ApiError, assertUuid, withHousehold } from "@/lib/server/api";
import { removeHouseholdUser } from "@/lib/server/invites";

export const DELETE = withHousehold<{ userId: string }>(
  async (_req, { household, user }, { userId }) => {
    assertUuid(userId);
    const res = await removeHouseholdUser(household.id, user.id, userId);
    if (res !== "removed") throw new ApiError(res === "forbidden" ? 403 : 400, res);
    return { ok: true };
  },
);
