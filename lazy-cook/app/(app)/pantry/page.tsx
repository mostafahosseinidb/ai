import { requireHousehold } from "@/lib/session";
import { db } from "@/lib/db";
import { pantryInclude } from "@/lib/server/pantry";
import { localNames } from "@/lib/server/local-names";
import { expiryStatus, unsuitableLocation, type ShelfLife } from "@/lib/domain/pantry";
import { amountText } from "@/lib/domain/recipe-view";
import { dbDateToISO, todayISO } from "@/lib/i18n/jalali";
import { PageHeader } from "@/components/ui/primitives";
import { fa } from "@/lib/i18n/fa";
import { PantryView, type PantryItemDTO } from "./pantry-view";

export const metadata = { title: fa.nav.pantry };

export default async function PantryPage() {
  const { user, household } = await requireHousehold();
  const today = todayISO(user.timezone);
  const rows = await db.pantryItem.findMany({
    where: { householdId: household.id, status: "active", deletedAt: null },
    include: pantryInclude,
    orderBy: [{ computedExpiryDate: { sort: "asc", nulls: "last" } }, { createdAt: "desc" }],
  });
  const names = await localNames(
    rows.map((r) => r.ingredientId).filter((x): x is string => !!x),
    user.countryCode,
  );
  const items: PantryItemDTO[] = rows.map((r) => {
    const expiry = r.computedExpiryDate ? dbDateToISO(r.computedExpiryDate) : null;
    return {
      id: r.id,
      name: r.recipe ? `${r.recipe.titleFa} (آماده)` : (r.ingredient?.nameFa ?? r.customName ?? ""),
      localName: r.ingredientId ? (names.get(r.ingredientId)?.[0]?.name ?? null) : null,
      quantity: Number(r.quantity),
      unit: r.unit,
      amount: amountText(Number(r.quantity), r.unit),
      location: r.location,
      opened: !!r.openedAt,
      expiry,
      packageExpiry: r.packageExpiryDate ? dbDateToISO(r.packageExpiryDate) : null,
      ...expiryStatus(expiry, today),
      badPlace: unsuitableLocation(
        r.location,
        (r.ingredient?.shelfLifeDays ?? null) as ShelfLife | null,
        !!r.openedAt,
      ),
      isMeal: !!r.recipe,
    };
  });
  return (
    <>
      <PageHeader title={fa.nav.pantry} />
      <PantryView items={items} today={today} />
    </>
  );
}
