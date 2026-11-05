import "server-only";
import { redirect } from "next/navigation";
import { auth } from "@/auth";
import { db } from "@/lib/db";

/** The signed-in user, or redirect to sign-in. */
export async function requireUser() {
  const session = await auth();
  const id = session?.user?.id;
  if (!id) redirect("/signin");
  const user = await db.user.findUnique({ where: { id } });
  if (!user || user.deletedAt) redirect("/signin");
  return user;
}

/** The user's household (first membership). Redirects to onboarding if there is none yet. */
export async function requireHousehold() {
  const user = await requireUser();
  const link = await db.householdUser.findFirst({
    where: { userId: user.id, deletedAt: null, household: { deletedAt: null } },
    include: { household: true },
    orderBy: { createdAt: "asc" },
  });
  if (!link || !user.onboardingCompletedAt) redirect("/onboarding");
  return { user, household: link.household, role: link.role };
}

/** For route handlers: returns null instead of redirecting. */
export async function currentHousehold() {
  const session = await auth();
  const id = session?.user?.id;
  if (!id) return null;
  const link = await db.householdUser.findFirst({
    where: { userId: id, deletedAt: null, household: { deletedAt: null } },
    include: { household: true, user: true },
    orderBy: { createdAt: "asc" },
  });
  if (!link) return null;
  return { user: link.user, household: link.household, role: link.role };
}
