import { requireHousehold } from "@/lib/session";
import { todayISO } from "@/lib/i18n/jalali";
import { PageHeader } from "@/components/ui/primitives";
import { fa } from "@/lib/i18n/fa";
import { NewPlanForm } from "./form";

export const metadata = { title: fa.plan.newPlan };

export default async function NewPlanPage() {
  const { user } = await requireHousehold();
  return (
    <>
      <PageHeader
        title={fa.plan.newPlan}
        subtitle="فعلاً بدون هوش مصنوعی: یا خودت بچین، یا بذار با قانون‌های خونه‌ت پرش کنیم."
      />
      <NewPlanForm today={todayISO(user.timezone)} />
    </>
  );
}
