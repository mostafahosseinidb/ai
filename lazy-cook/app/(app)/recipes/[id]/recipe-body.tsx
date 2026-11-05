"use client";

import { useEffect, useRef, useState } from "react";
import { Pause, Play, RotateCcw } from "lucide-react";
import { Chip } from "@/components/ui/primitives";
import { fa } from "@/lib/i18n/fa";
import { formatNumber, toFaDigits } from "@/lib/i18n/digits";
import { scaleRecipeLines, type ViewIngredient } from "@/lib/domain/recipe-view";

export function RecipeBody({
  baseServings,
  householdPortions,
  ingredients,
  steps,
}: {
  baseServings: number;
  householdPortions: number;
  ingredients: ViewIngredient[];
  steps: Array<{ text: string; timer_seconds?: number | null }>;
}) {
  const [portions, setPortions] = useState(householdPortions);
  const lines = scaleRecipeLines(ingredients, baseServings, portions);
  const groups = [...new Set(lines.map((l) => l.group))];

  return (
    <>
      <section aria-labelledby="ing" className="flex flex-col gap-3">
        <h2 id="ing" className="text-xl font-bold">
          {fa.recipes.ingredients}
        </h2>
        <div
          className="no-scrollbar flex gap-2 overflow-x-auto"
          role="group"
          aria-label={fa.recipes.servings}
        >
          <Chip active={portions === 1} onClick={() => setPortions(1)}>
            {fa.recipes.forOne}
          </Chip>
          <Chip
            active={portions === householdPortions}
            onClick={() => setPortions(householdPortions)}
          >
            {fa.recipes.forHousehold} ({formatNumber(householdPortions, 2)})
          </Chip>
          {[2, 3, 4, 6].map((n) => (
            <Chip
              key={n}
              active={portions === n && n !== householdPortions}
              onClick={() => setPortions(n)}
            >
              {toFaDigits(n)} نفر
            </Chip>
          ))}
        </div>
        {groups.map((g) => (
          <div key={g ?? "_"}>
            {g && <h3 className="text-muted mb-1 text-sm font-bold">{g}</h3>}
            <ul className="divide-border border-border bg-surface flex flex-col divide-y rounded-[16px] border">
              {lines
                .filter((l) => l.group === g)
                .map((l) => (
                  <li
                    key={l.key + l.unit}
                    className="flex items-baseline justify-between gap-3 px-4 py-2.5"
                  >
                    <span>
                      {l.nameFa}
                      {l.prepNote && <span className="text-muted text-sm">، {l.prepNote}</span>}
                      {l.optional && (
                        <span className="text-muted text-xs"> ({fa.recipes.optional})</span>
                      )}
                    </span>
                    <span className="shrink-0 font-medium" data-testid={`qty-${l.key}`}>
                      {l.amountText}
                    </span>
                  </li>
                ))}
            </ul>
          </div>
        ))}
      </section>

      <section aria-labelledby="steps" className="flex flex-col gap-3">
        <h2 id="steps" className="text-xl font-bold">
          {fa.recipes.steps}
        </h2>
        <ol className="flex flex-col gap-3">
          {steps.map((s, i) => (
            <li key={i} className="border-border bg-surface flex gap-3 rounded-[16px] border p-4">
              <span className="bg-primary/10 text-primary flex size-8 shrink-0 items-center justify-center rounded-full font-bold">
                {toFaDigits(i + 1)}
              </span>
              <div className="flex flex-1 flex-col gap-2">
                <p className="leading-7">{s.text}</p>
                {s.timer_seconds ? <Timer seconds={s.timer_seconds} /> : null}
              </div>
            </li>
          ))}
        </ol>
      </section>
    </>
  );
}

function fmt(sec: number) {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return toFaDigits(`${m}:${String(s).padStart(2, "0")}`);
}

function Timer({ seconds }: { seconds: number }) {
  const [left, setLeft] = useState(seconds);
  const [running, setRunning] = useState(false);
  const endAt = useRef<number | null>(null);

  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => {
      const remaining = Math.max(
        0,
        Math.round(((endAt.current ?? Date.now()) - Date.now()) / 1000),
      );
      setLeft(remaining);
      if (remaining === 0) {
        setRunning(false);
        navigator.vibrate?.([300, 100, 300]);
      }
    }, 500);
    return () => clearInterval(id);
  }, [running]);

  return (
    <div className="bg-surface-2 flex items-center gap-2 self-start rounded-[12px] ps-3">
      <span className="min-w-14 font-bold tabular-nums" dir="ltr" aria-live="polite">
        {left === 0 ? fa.recipes.timerDone : fmt(left)}
      </span>
      <button
        className="flex size-11 items-center justify-center"
        aria-label={running ? "توقف" : fa.recipes.timerStart}
        onClick={() => {
          if (running) setRunning(false);
          else {
            const base = left === 0 ? seconds : left;
            setLeft(base);
            endAt.current = Date.now() + base * 1000;
            setRunning(true);
          }
        }}
      >
        {running ? <Pause className="size-5" /> : <Play className="size-5" />}
      </button>
      <button
        className="flex size-11 items-center justify-center"
        aria-label="از اول"
        onClick={() => {
          setRunning(false);
          setLeft(seconds);
        }}
      >
        <RotateCcw className="size-4" />
      </button>
    </div>
  );
}
