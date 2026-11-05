import { ComingSoon } from "@/components/layout/coming-soon";
import { fa } from "@/lib/i18n/fa";

export const metadata = { title: fa.more.prep };
export default function Page() {
  return (
    <ComingSoon
      title={fa.more.prep}
      emoji="❄️"
      text="یه روز در هفته چند تا غذا رو با هم آماده کن."
    />
  );
}
