import Link from "next/link";
import clsx from "clsx";
import { jalaliDayNumber, weekdayShortFa, type ISODate } from "@/lib/i18n/jalali";
import type { DaySummary } from "@/lib/server/day";

/** Horizontal day selector with a progress ring per day (spec §5.4). */
export function DayStrip({
  days,
  selected,
  today,
  basePath = "/today",
}: {
  days: DaySummary[];
  selected: ISODate;
  today: ISODate;
  basePath?: string;
}) {
  return (
    <nav aria-label="روزهای برنامه" className="no-scrollbar flex gap-2 overflow-x-auto px-4 pb-2">
      {days.map((d) => {
        const pct = d.total ? d.done / d.total : 0;
        const active = d.date === selected;
        return (
          <Link
            key={d.date}
            prefetch={false}
            href={`${basePath}?d=${d.date}`}
            aria-current={active ? "date" : undefined}
            aria-label={`${d.date}${d.date === today ? " (امروز)" : ""}`}
            className={clsx(
              "flex min-w-14 shrink-0 flex-col items-center gap-1 rounded-[12px] px-2 py-2",
              active ? "bg-primary text-primary-contrast" : "bg-surface",
            )}
          >
            <span
              className={clsx("text-xs", d.date === today && !active && "text-primary font-bold")}
            >
              {weekdayShortFa(d.date)}
            </span>
            <span className="relative flex size-9 items-center justify-center">
              <svg viewBox="0 0 36 36" className="absolute inset-0 -rotate-90" aria-hidden>
                <circle
                  cx="18"
                  cy="18"
                  r="15"
                  fill="none"
                  stroke="currentColor"
                  strokeOpacity="0.2"
                  strokeWidth="3"
                />
                <circle
                  cx="18"
                  cy="18"
                  r="15"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="3"
                  strokeDasharray={`${pct * 94.2} 94.2`}
                  strokeLinecap="round"
                />
              </svg>
              <span className="text-sm font-bold">{jalaliDayNumber(d.date)}</span>
            </span>
          </Link>
        );
      })}
    </nav>
  );
}
