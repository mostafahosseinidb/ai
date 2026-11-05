import { requireHousehold } from "@/lib/session";
import { signOut } from "@/auth";
import { Button, ButtonLink, Card, PageHeader } from "@/components/ui/primitives";
import { fa } from "@/lib/i18n/fa";
import { SettingsToggles } from "./toggles";
import { HouseholdAccounts } from "./household-accounts";
import { YourData } from "./your-data";
import { db } from "@/lib/db";

export const metadata = { title: fa.settings.title };

export default async function SettingsPage() {
  const { user, household } = await requireHousehold();
  const links = await db.householdUser.findMany({
    where: { householdId: household.id, deletedAt: null },
    include: { user: { select: { id: true, name: true, email: true } } },
    orderBy: { createdAt: "asc" },
  });
  const accounts = links.map((l) => ({
    userId: l.userId,
    label: l.user.name || l.user.email || "—",
    role: household.ownerUserId === l.userId ? ("owner" as const) : ("member" as const),
    isMe: l.userId === user.id,
  }));
  return (
    <>
      <PageHeader title={fa.settings.title} />
      <div className="flex flex-col gap-4 px-4">
        <SettingsToggles calendar={user.calendar} units={user.units} />
        <ButtonLink href="/onboarding" variant="secondary">
          {fa.settings.editOnboarding}
        </ButtonLink>
        <HouseholdAccounts accounts={accounts} iAmOwner={household.ownerUserId === user.id} />
        <YourData sharedWithOthers={accounts.length > 1} />
        <Card className="text-muted text-sm leading-6">{fa.healthDisclaimer}</Card>
        <form
          action={async () => {
            "use server";
            await signOut({ redirectTo: "/" });
          }}
        >
          <Button type="submit" variant="ghost" className="text-danger w-full">
            {fa.more.signOut}
          </Button>
        </form>
        {user.email && (
          <p className="text-muted text-center text-xs" dir="ltr">
            {user.email}
          </p>
        )}
      </div>
    </>
  );
}
