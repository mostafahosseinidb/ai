"use client";

import Link from "next/link";
import { Star, Trash2 } from "lucide-react";
import clsx from "clsx";
import { Badge } from "@/components/ui/primitives";
import { useApi } from "@/components/meal/use-api";
import { fa } from "@/lib/i18n/fa";
import { toFaDigits } from "@/lib/i18n/digits";
import { formatDayMonth, weekdayFa } from "@/lib/i18n/jalali";

interface Item {
  id: string;
  recipeId: string;
  title: string;
  activeMinutes: number;
  note: string | null;
  priority: number;
  done: boolean;
  scheduled: { date: string; slot: string } | null;
}

export function WishlistView({ items }: { items: Item[] }) {
  const { call, busy } = useApi();
  const open = items.filter((i) => !i.done);
  const done = items.filter((i) => i.done);
  return (
    <div className="flex flex-col gap-4 px-4">
      <ul className="flex flex-col gap-2">
        {open.map((i) => (
          <li
            key={i.id}
            className="border-border bg-surface flex items-center gap-2 rounded-[16px] border px-3 py-2"
          >
            <button
              aria-label={i.priority ? "اولویت عادی" : "اولویت بالا"}
              aria-pressed={i.priority > 0}
              disabled={busy}
              onClick={() =>
                call(`/api/wishlist/${i.id}`, "PATCH", { priority: i.priority ? 0 : 1 })
              }
              className={clsx(
                "flex size-11 items-center justify-center",
                i.priority ? "text-accent" : "text-muted",
              )}
            >
              <Star className="size-5" fill={i.priority ? "currentColor" : "none"} />
            </button>
            <div className="min-w-0 flex-1">
              <Link prefetch={false} href={`/recipes/${i.recipeId}`} className="font-bold">
                {i.title}
              </Link>
              <p className="text-muted text-xs">
                {toFaDigits(i.activeMinutes)} {fa.recipes.minutes}
                {i.note && ` · ${i.note}`}
              </p>
              {i.scheduled && (
                <Badge tone="success" className="mt-1">
                  تو برنامه: {weekdayFa(i.scheduled.date)} {formatDayMonth(i.scheduled.date)} ·{" "}
                  {fa.slots[i.scheduled.slot]}
                </Badge>
              )}
            </div>
            <button
              aria-label={`حذف ${i.title}`}
              disabled={busy}
              onClick={() => call(`/api/wishlist/${i.id}`, "DELETE")}
              className="text-muted flex size-11 items-center justify-center"
            >
              <Trash2 className="size-4" />
            </button>
          </li>
        ))}
      </ul>
      {done.length > 0 && (
        <section>
          <h2 className="text-muted mb-2 text-sm font-bold">پخته شد ✓</h2>
          <ul className="text-muted flex flex-col gap-1">
            {done.map((i) => (
              <li key={i.id}>{i.title}</li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
