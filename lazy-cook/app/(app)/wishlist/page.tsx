import { ComingSoon } from "@/components/layout/coming-soon";
import { fa } from "@/lib/i18n/fa";

export const metadata = { title: fa.more.wishlist };
export default function Page() {
  return (
    <ComingSoon
      title={fa.more.wishlist}
      emoji="⭐"
      text="دستورهایی که می‌خوای بعداً بپزی اینجا جمع می‌شن."
    />
  );
}
