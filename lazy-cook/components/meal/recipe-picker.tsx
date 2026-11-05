"use client";

import { useEffect, useState } from "react";
import { Search } from "lucide-react";
import { inputClass } from "@/components/ui/primitives";
import { fa } from "@/lib/i18n/fa";
import { toFaDigits } from "@/lib/i18n/digits";

/** The library meal type to suggest for each slot. */
export const SLOT_MEAL_TYPE: Record<string, string> = {
  breakfast: "breakfast",
  lunch: "lunch",
  dinner: "dinner",
  mid_morning: "snack",
  after_lunch: "snack",
  snack: "snack",
  late: "snack",
};

export interface PickedRecipe {
  id: string;
  titleFa: string;
  activeMinutes: number;
  cuisine: string;
}

/** Search the library (only recipes this kitchen can make) and pick one. */
export function RecipePicker({
  mealType,
  onPick,
  disabled,
}: {
  mealType?: string;
  onPick: (r: PickedRecipe) => void;
  disabled?: boolean;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<PickedRecipe[] | null>(null);
  const [onlyType, setOnlyType] = useState(!!mealType);

  useEffect(() => {
    const ctrl = new AbortController();
    const t = setTimeout(() => {
      const q = new URLSearchParams({ equipment: "mine" });
      if (query.trim()) q.set("query", query.trim());
      if (onlyType && mealType) q.set("meal_type", mealType);
      fetch(`/api/recipes?${q}`, { signal: ctrl.signal })
        .then((r) => r.json())
        .then((j) => setResults(j.recipes ?? []))
        .catch(() => {});
    }, 200);
    return () => {
      clearTimeout(t);
      ctrl.abort();
    };
  }, [query, onlyType, mealType]);

  return (
    <div className="flex flex-col gap-2">
      <div className="relative">
        <Search
          className="text-muted pointer-events-none absolute start-3 top-3 size-5"
          aria-hidden
        />
        <input
          className={`${inputClass} ps-10`}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={fa.recipes.search}
          aria-label={fa.recipes.search}
        />
      </div>
      {mealType && (
        <label className="flex min-h-11 items-center gap-2 text-sm">
          <input
            type="checkbox"
            className="size-5"
            checked={onlyType}
            onChange={(e) => setOnlyType(e.target.checked)}
          />
          فقط {fa.mealTypes[mealType] ?? mealType}
        </label>
      )}
      <ul className="flex max-h-[45dvh] flex-col gap-1 overflow-y-auto">
        {results === null && [0, 1, 2, 3].map((i) => <li key={i} className="skeleton h-12" />)}
        {results?.length === 0 && (
          <li className="text-muted py-4 text-center">{fa.recipes.none}</li>
        )}
        {results?.map((r) => (
          <li key={r.id}>
            <button
              disabled={disabled}
              onClick={() => onPick(r)}
              className="hover:bg-surface-2 flex min-h-12 w-full items-center justify-between rounded-[12px] px-3 text-start"
            >
              <span className="font-medium">{r.titleFa}</span>
              <span className="text-muted text-xs">
                {fa.cuisines[r.cuisine]} · {toFaDigits(r.activeMinutes)} {fa.recipes.minutes}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
