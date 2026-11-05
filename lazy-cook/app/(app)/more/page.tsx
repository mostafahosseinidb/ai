import Link from "next/link";
import { BookOpen, Camera, ChevronLeft, Settings, Snowflake, Star } from "lucide-react";
import { PageHeader } from "@/components/ui/primitives";
import { fa } from "@/lib/i18n/fa";

export const metadata = { title: fa.nav.more };

const ITEMS = [
  { href: "/recipes", label: fa.more.recipes, Icon: BookOpen },
  { href: "/recipes/import", label: fa.more.import, Icon: Camera },
  { href: "/wishlist", label: fa.more.wishlist, Icon: Star },
  { href: "/prep", label: fa.more.prep, Icon: Snowflake },
  { href: "/settings", label: fa.more.settings, Icon: Settings },
];

export default function MorePage() {
  return (
    <>
      <PageHeader title={fa.nav.more} />
      <ul className="divide-border border-border bg-surface mx-4 flex flex-col divide-y rounded-[16px] border">
        {ITEMS.map(({ href, label, Icon }) => (
          <li key={href}>
            <Link href={href} className="flex min-h-14 items-center gap-3 px-4">
              <Icon className="text-primary size-5" aria-hidden />
              <span className="flex-1">{label}</span>
              <ChevronLeft className="text-muted size-4" aria-hidden />
            </Link>
          </li>
        ))}
      </ul>
    </>
  );
}
