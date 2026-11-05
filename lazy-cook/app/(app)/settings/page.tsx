import { requireHousehold } from "@/lib/session";
import { signOut } from "@/auth";
import { Button, ButtonLink, Card, PageHeader } from "@/components/ui/primitives";
import { fa } from "@/lib/i18n/fa";
import { SettingsToggles } from "./toggles";

export const metadata = { title: fa.settings.title };

export default async function SettingsPage() {
  const { user } = await requireHousehold();
  return (
    <>
      <PageHeader title={fa.settings.title} />
      <div className="flex flex-col gap-4 px-4">
        <SettingsToggles calendar={user.calendar} units={user.units} />
        <ButtonLink href="/onboarding" variant="secondary">
          {fa.settings.editOnboarding}
        </ButtonLink>
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
