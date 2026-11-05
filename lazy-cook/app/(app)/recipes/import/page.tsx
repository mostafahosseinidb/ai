import { ComingSoon } from "@/components/layout/coming-soon";
import { fa } from "@/lib/i18n/fa";

export const metadata = { title: fa.more.import };
export default function Page() {
  return (
    <ComingSoon
      title={fa.more.import}
      emoji="📸"
      text="از عکس یا اسکرین‌شات اینستاگرام، دستور رو وارد کن."
    />
  );
}
