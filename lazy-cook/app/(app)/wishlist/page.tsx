import { requireHousehold } from "@/lib/session";
import { db } from "@/lib/db";
import { ButtonLink, EmptyState, PageHeader } from "@/components/ui/primitives";
import { fa } from "@/lib/i18n/fa";
import { dbDateToISO } from "@/lib/i18n/jalali";
import { WishlistView } from "./wishlist-view";

export const metadata = { title: fa.more.wishlist };

export default async function WishlistPage() {
  const { household } = await requireHousehold();
  const rows = await db.wishlistItem.findMany({
    where: { householdId: household.id, deletedAt: null },
    include: {
      recipe: { select: { id: true, titleFa: true, activeMinutes: true } },
      scheduledPlannedMeal: { select: { date: true, slot: true, deletedAt: true } },
    },
    orderBy: [{ priority: "desc" }, { createdAt: "asc" }],
  });
  const items = rows.map((r) => ({
    id: r.id,
    recipeId: r.recipe.id,
    title: r.recipe.titleFa,
    activeMinutes: r.recipe.activeMinutes,
    note: r.note,
    priority: r.priority,
    done: !!r.doneAt,
    scheduled:
      r.scheduledPlannedMeal && !r.scheduledPlannedMeal.deletedAt
        ? { date: dbDateToISO(r.scheduledPlannedMeal.date), slot: r.scheduledPlannedMeal.slot }
        : null,
  }));
  return (
    <>
      <PageHeader title={fa.more.wishlist} subtitle="حداقل یکی‌شون تو برنامه‌ی بعدی میاد." />
      {items.length === 0 ? (
        <EmptyState
          emoji="⭐"
          text="هنوز چیزی اینجا نیست. تو صفحه‌ی هر دستور، «برای برنامه‌ی بعدی» رو بزن."
          action={<ButtonLink href="/recipes">برو به دستورها</ButtonLink>}
        />
      ) : (
        <WishlistView items={items} />
      )}
    </>
  );
}
