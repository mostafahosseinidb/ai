import { requireHousehold } from "@/lib/session";
import { loadShopping } from "@/lib/server/shopping-view";
import { regenerateShopping } from "@/lib/server/shopping";
import { getActivePlan } from "@/lib/server/plans";
import { PageHeader } from "@/components/ui/primitives";
import { fa } from "@/lib/i18n/fa";
import { ShoppingView } from "./shopping-view";

export const metadata = { title: fa.nav.shopping };

export default async function ShoppingPage() {
  const { user, household } = await requireHousehold();
  // fresh on every visit: yesterday's meals drop off, today's pantry counts
  await regenerateShopping(household.id);
  const [items, plan] = await Promise.all([
    loadShopping(household.id, user.countryCode),
    getActivePlan(household.id),
  ]);
  return (
    <>
      <PageHeader
        title="لیست خرید"
        subtitle="برنامه منهای موجودی؛ با تغییر برنامه خودش به‌روز می‌شه."
      />
      <ShoppingView initial={items} hasPlan={!!plan} />
    </>
  );
}
