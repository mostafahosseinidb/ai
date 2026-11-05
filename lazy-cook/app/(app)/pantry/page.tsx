import { ComingSoon } from "@/components/layout/coming-soon";
import { fa } from "@/lib/i18n/fa";

export const metadata = { title: fa.nav.pantry };
export default function Page() {
  return (
    <ComingSoon title={fa.nav.pantry} emoji="🧊" text="یخچالت خالیه یا هنوز چیزی ثبت نکردی؟" />
  );
}
