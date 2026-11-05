"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button, Chip, Field } from "@/components/ui/primitives";
import { fa } from "@/lib/i18n/fa";
import { toFaDigits } from "@/lib/i18n/digits";
import { addDaysISO, formatDayMonth, weekdayFa, type ISODate } from "@/lib/i18n/jalali";

const DAY_OPTIONS = [3, 5, 7, 8, 10, 14];

export function NewPlanForm({ today }: { today: ISODate }) {
  const router = useRouter();
  const [start, setStart] = useState(today);
  const [days, setDays] = useState(8);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);

  async function create(autofill: boolean) {
    setBusy(true);
    setError(false);
    const res = await fetch("/api/plans", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ start_date: start, days, autofill }),
    });
    setBusy(false);
    if (!res.ok) return setError(true);
    router.push(autofill ? "/today" : "/plan");
    router.refresh();
  }

  return (
    <div className="flex flex-col gap-6 px-4">
      <Field label={fa.plan.startDate}>
        <div className="no-scrollbar flex gap-2 overflow-x-auto">
          {[0, 1, 2, 3, 4, 5, 6].map((i) => {
            const d = addDaysISO(today, i);
            return (
              <Chip key={d} active={d === start} onClick={() => setStart(d)}>
                {i === 0 ? "امروز" : i === 1 ? "فردا" : weekdayFa(d)} · {formatDayMonth(d)}
              </Chip>
            );
          })}
        </div>
      </Field>
      <Field label={fa.plan.days}>
        <div className="flex flex-wrap gap-2">
          {DAY_OPTIONS.map((n) => (
            <Chip key={n} active={n === days} onClick={() => setDays(n)}>
              {toFaDigits(n)} روز
            </Chip>
          ))}
        </div>
      </Field>
      {error && (
        <p role="alert" className="text-danger">
          {fa.common.error}
        </p>
      )}
      <Button disabled={busy} onClick={() => create(true)} className="min-h-12">
        {busy ? fa.common.loading : fa.plan.autofill}
      </Button>
      <Button variant="secondary" disabled={busy} onClick={() => create(false)}>
        برنامه‌ی خالی، خودم می‌چینم
      </Button>
    </div>
  );
}
