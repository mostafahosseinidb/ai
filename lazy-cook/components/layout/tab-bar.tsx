"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import clsx from "clsx";
import { CalendarDays, Ellipsis, Refrigerator, ShoppingCart, Sun } from "lucide-react";
import { fa } from "@/lib/i18n/fa";

// In RTL the first item renders on the right: امروز · برنامه · خرید · موجودی · بیشتر (spec §6.1)
const TABS = [
  { href: "/today", label: fa.nav.today, Icon: Sun },
  { href: "/plan", label: fa.nav.plan, Icon: CalendarDays },
  { href: "/shopping", label: fa.nav.shopping, Icon: ShoppingCart },
  { href: "/pantry", label: fa.nav.pantry, Icon: Refrigerator },
  { href: "/more", label: fa.nav.more, Icon: Ellipsis },
];

const MORE_PATHS = ["/more", "/recipes", "/wishlist", "/prep", "/settings"];

export function TabBar() {
  const path = usePathname();
  return (
    <nav
      aria-label="ناوبری اصلی"
      className="border-border bg-surface/95 fixed inset-x-0 bottom-0 z-40 border-t pb-[env(safe-area-inset-bottom)] backdrop-blur"
    >
      <ul className="mx-auto flex max-w-md">
        {TABS.map(({ href, label, Icon }) => {
          const active =
            href === "/more" ? MORE_PATHS.some((p) => path.startsWith(p)) : path.startsWith(href);
          return (
            <li key={href} className="flex-1">
              <Link
                href={href}
                aria-current={active ? "page" : undefined}
                className={clsx(
                  "flex min-h-14 flex-col items-center justify-center gap-0.5 text-xs",
                  active ? "text-primary font-bold" : "text-muted",
                )}
              >
                <Icon className="size-5" aria-hidden />
                {label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
