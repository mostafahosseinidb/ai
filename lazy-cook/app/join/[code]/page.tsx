import { redirect } from "next/navigation";
import { auth } from "@/auth";
import { db } from "@/lib/db";
import { acceptInvite, findInvite } from "@/lib/server/invites";
import { Button, ButtonLink, Card } from "@/components/ui/primitives";

export const metadata = { title: "دعوت به خانوار" };

const MESSAGES: Record<string, string> = {
  not_found: "این لینک دعوت پیدا نشد. از کسی که فرستاده بخواه یه لینک تازه بسازه.",
  used: "این لینک قبلاً استفاده شده. هر لینک فقط یه بار کار می‌کنه.",
  expired: "این لینک منقضی شده. از کسی که فرستاده بخواه یه لینک تازه بسازه.",
};

export default async function JoinPage({ params }: { params: Promise<{ code: string }> }) {
  const { code } = await params;
  const session = await auth();
  if (!session?.user?.id) redirect(`/signin?next=${encodeURIComponent(`/join/${code}`)}`);
  const userId = session.user.id;

  const { state, invite } = await findInvite(code);
  const already = invite
    ? await db.householdUser.findFirst({
        where: { householdId: invite.householdId, userId, deletedAt: null },
      })
    : null;
  if (already) redirect("/today");

  async function join() {
    "use server";
    const res = await acceptInvite(userId, code);
    if (res === "joined") redirect("/today");
    redirect(`/join/${code}`);
  }

  const own = await db.householdUser.findFirst({
    where: { userId, deletedAt: null },
    include: {
      household: { select: { _count: { select: { mealPlans: true, pantryItems: true } } } },
    },
  });

  return (
    <main className="mx-auto flex min-h-dvh max-w-md flex-col justify-center gap-5 px-4">
      <div className="text-5xl" aria-hidden>
        🏠
      </div>
      {state !== "ok" || !invite ? (
        <>
          <p className="leading-7">{MESSAGES[state]}</p>
          <ButtonLink href="/today" variant="secondary">
            برو به اپ
          </ButtonLink>
        </>
      ) : (
        <>
          <h1 className="text-2xl font-extrabold">
            بیا به خونه‌ی{" "}
            {invite.household.owner.name || invite.household.members[0]?.displayName || "ما"}!
          </h1>
          <p className="text-muted leading-7">
            با پیوستن، برنامه‌ی غذایی، موجودی و لیست خرید رو با هم می‌بینید و هر تیکی که یکی‌تون
            بزنه، اون یکی هم می‌بینه.
          </p>
          {invite.household.members.length > 0 && (
            <Card className="text-sm">
              اعضا: {invite.household.members.map((m) => m.displayName).join("، ")}
            </Card>
          )}
          {own && (own.household._count.mealPlans > 0 || own.household._count.pantryItems > 0) && (
            <p role="note" className="bg-warning/10 rounded-[12px] p-3 text-sm leading-6">
              الان خودت یه خانوار جدا داری. با پیوستن، از اون بیرون می‌آی و برنامه و موجودیش دیگه
              نشونت داده نمی‌شه.
            </p>
          )}
          <form action={join}>
            <Button type="submit" className="min-h-12 w-full">
              بپیوند
            </Button>
          </form>
        </>
      )}
    </main>
  );
}
