"use client";

import clsx from "clsx";
import { Check, Moon } from "lucide-react";
import { Card } from "@/components/ui/primitives";
import { fa } from "@/lib/i18n/fa";
import { toFaDigits } from "@/lib/i18n/digits";
import { useApi } from "./use-api";

export function PrepTasks({
  tasks,
}: {
  tasks: Array<{ id: string; text: string; dueTime: string; done: boolean }>;
}) {
  const { call, busy } = useApi();
  if (!tasks.length) return null;
  return (
    <Card className="border-accent/50 bg-accent/10 flex flex-col gap-2">
      <h2 className="flex items-center gap-2 font-bold">
        <Moon className="size-5" aria-hidden /> {fa.today.tonight}
      </h2>
      <ul className="flex flex-col gap-1">
        {tasks.map((t) => (
          <li key={t.id}>
            <button
              disabled={busy}
              onClick={() => call(`/api/prep-tasks/${t.id}`, "PATCH", { done: !t.done })}
              aria-pressed={t.done}
              className="flex min-h-11 w-full items-center gap-3 text-start"
            >
              <span
                className={clsx(
                  "flex size-7 shrink-0 items-center justify-center rounded-full border-2",
                  t.done ? "border-success bg-success text-white" : "border-border",
                )}
              >
                {t.done && <Check className="size-4" strokeWidth={3} />}
              </span>
              <span className={clsx("flex-1 leading-6", t.done && "text-muted line-through")}>
                {t.text}
              </span>
              <span className="text-muted text-xs" dir="ltr">
                {toFaDigits(t.dueTime)}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </Card>
  );
}
