import { ComingSoon } from "@/components/layout/coming-soon";
import { fa } from "@/lib/i18n/fa";

export const metadata = { title: fa.nav.shopping };
export default function Page() {
  return (
    <ComingSoon
      title={fa.nav.shopping}
      emoji="🛒"
      text="لیست خرید خودکار از روی برنامه منهای موجودی."
    />
  );
}
