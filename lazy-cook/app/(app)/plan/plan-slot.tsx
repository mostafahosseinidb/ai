"use client";

import Link from "next/link";
import { useState } from "react";
import clsx from "clsx";
import { Plus, Trash2 } from "lucide-react";
import { Badge, Button, inputClass } from "@/components/ui/primitives";
import { Sheet } from "@/components/ui/sheet";
import { RecipePicker, SLOT_MEAL_TYPE } from "@/components/meal/recipe-picker";
import { RuleViolationError, useApi, type Violation } from "@/components/meal/use-api";
import { fa } from "@/lib/i18n/fa";
import { formatDayMonth, weekdayFa, type ISODate } from "@/lib/i18n/jalali";
import type { MealDTO } from "@/lib/server/day";

interface LeftoverSource {
  id: string;
  title: string;
  date: ISODate;
  slot: string;
}

export function PlanSlot({
  planId,
  date,
  slot,
  meal,
  leftoverSources,
}: {
  planId: string;
  date: ISODate;
  slot: string;
  meal: MealDTO | null;
  leftoverSources: LeftoverSource[];
}) {
  const { call, busy } = useApi();
  const [open, setOpen] = useState(false);
  const [freeText, setFreeText] = useState("");
  const [pending, setPending] = useState<{
    body: Record<string, unknown>;
    violations: Violation[];
  } | null>(null);

  async function add(body: Record<string, unknown>, force = false) {
    try {
      await call(`/api/plans/${planId}/meals`, "POST", { date, slot, ...body, force });
      setOpen(false);
      setPending(null);
      setFreeText("");
    } catch (e) {
      if (e instanceof RuleViolationError) setPending({ body, violations: e.violations });
    }
  }

  const title = meal?.recipe?.titleFa ?? meal?.freeText;
  return (
    <li className="flex min-h-12 items-center gap-2 py-1">
      <span className="text-muted w-20 shrink-0 text-xs">{fa.slots[slot]}</span>
      {meal ? (
        <>
          <div
            className={clsx(
              "min-w-0 flex-1",
              meal.status !== "planned" && meal.status !== "done" && "text-muted line-through",
            )}
          >
            {meal.recipe ? (
              <Link prefetch={false} href={`/recipes/${meal.recipe.id}`} className="font-medium">
                {title}
              </Link>
            ) : (
              <span className="font-medium">{title}</span>
            )}
            {meal.leftoverOf && (
              <Badge tone="accent" className="ms-2">
                {fa.meal.leftover}
              </Badge>
            )}
            {meal.status === "done" && <span className="text-success ms-2">✓</span>}
          </div>
          <button
            aria-label={`${fa.plan.remove} ${title}`}
            disabled={busy}
            onClick={() => call(`/api/planned-meals/${meal.id}`, "DELETE")}
            className="text-muted hover:bg-surface-2 flex size-11 items-center justify-center rounded-[12px]"
          >
            <Trash2 className="size-4" />
          </button>
        </>
      ) : (
        <button
          onClick={() => setOpen(true)}
          className="text-primary flex min-h-11 flex-1 items-center gap-1 text-sm"
        >
          <Plus className="size-4" /> {fa.plan.add}
        </button>
      )}

      <Sheet
        open={open}
        onClose={() => {
          setOpen(false);
          setPending(null);
        }}
        title={`${weekdayFa(date)} ${formatDayMonth(date)} · ${fa.slots[slot]}`}
      >
        {pending ? (
          <div className="flex flex-col gap-3">
            <div role="alert" className="bg-warning/10 rounded-[12px] p-3 text-sm leading-6">
              <p className="font-bold">{fa.plan.violations}</p>
              <ul className="list-inside list-disc">
                {pending.violations.map((v, i) => (
                  <li key={i}>{v.detail}</li>
                ))}
              </ul>
            </div>
            <Button variant="secondary" disabled={busy} onClick={() => add(pending.body, true)}>
              با این حال بذارش
            </Button>
            <Button variant="ghost" onClick={() => setPending(null)}>
              {fa.common.cancel}
            </Button>
          </div>
        ) : (
          <div className="flex flex-col gap-4">
            {leftoverSources.length > 0 && (slot === "lunch" || slot === "dinner") && (
              <div className="flex flex-col gap-1">
                <p className="text-sm font-medium">{fa.plan.leftoverOf}…</p>
                {leftoverSources.map((s) => (
                  <button
                    key={s.id}
                    disabled={busy}
                    onClick={() => add({ leftover_of_planned_meal_id: s.id })}
                    className="hover:bg-surface-2 flex min-h-11 items-center gap-2 rounded-[12px] px-3 text-start"
                  >
                    🍲 {s.title}{" "}
                    <span className="text-muted text-xs">
                      ({weekdayFa(s.date)} {fa.slots[s.slot]})
                    </span>
                  </button>
                ))}
              </div>
            )}
            <RecipePicker
              mealType={SLOT_MEAL_TYPE[slot]}
              disabled={busy}
              onPick={(r) => add({ recipe_id: r.id })}
            />
            <form
              className="flex gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                if (freeText.trim()) add({ free_text: freeText });
              }}
            >
              <input
                className={inputClass}
                value={freeText}
                onChange={(e) => setFreeText(e.target.value)}
                placeholder={fa.plan.freeTextPlaceholder}
                maxLength={200}
              />
              <Button type="submit" variant="secondary" disabled={busy || !freeText.trim()}>
                {fa.plan.add}
              </Button>
            </form>
          </div>
        )}
      </Sheet>
    </li>
  );
}
