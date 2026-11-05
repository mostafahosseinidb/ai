"use client";

import { useEffect, useState } from "react";
import { Search } from "lucide-react";
import { inputClass } from "@/components/ui/primitives";

export interface FoundIngredient {
  id: string;
  key: string;
  nameFa: string;
  category: string;
  defaultUnit: string;
  localName: string | null;
}

/** Type a Persian (or local-language) name, pick an ingredient — or keep the text as a custom item. */
export function IngredientSearch({
  onPick,
  onCustom,
  placeholder = "اسم ماده… (شیر، молоко، пиво…)",
}: {
  onPick: (i: FoundIngredient) => void;
  onCustom?: (text: string) => void;
  placeholder?: string;
}) {
  const [q, setQ] = useState("");
  const [found, setFound] = useState<FoundIngredient[]>([]);

  useEffect(() => {
    if (!q.trim()) {
      setFound([]);
      return;
    }
    const ctrl = new AbortController();
    const t = setTimeout(() => {
      fetch(`/api/ingredients?q=${encodeURIComponent(q.trim())}`, { signal: ctrl.signal })
        .then((r) => r.json())
        .then((j) => setFound(j.ingredients ?? []))
        .catch(() => {});
    }, 150);
    return () => {
      clearTimeout(t);
      ctrl.abort();
    };
  }, [q]);

  return (
    <div className="flex flex-col gap-2">
      <div className="relative">
        <Search
          className="text-muted pointer-events-none absolute start-3 top-3 size-5"
          aria-hidden
        />
        <input
          autoFocus
          className={`${inputClass} ps-10`}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={placeholder}
          aria-label="جست‌وجوی ماده"
        />
      </div>
      <ul className="flex max-h-[40dvh] flex-col overflow-y-auto">
        {found.map((i) => (
          <li key={i.id}>
            <button
              type="button"
              onClick={() => onPick(i)}
              className="hover:bg-surface-2 flex min-h-12 w-full items-center justify-between gap-2 rounded-[12px] px-3 text-start"
            >
              <span className="font-medium">{i.nameFa}</span>
              {i.localName && (
                <span className="text-muted truncate text-sm" dir="auto">
                  {i.localName}
                </span>
              )}
            </button>
          </li>
        ))}
        {onCustom && q.trim() && (
          <li>
            <button
              type="button"
              onClick={() => onCustom(q.trim())}
              className="text-primary hover:bg-surface-2 flex min-h-12 w-full items-center rounded-[12px] px-3 text-start"
            >
              «{q.trim()}» رو همین‌طوری اضافه کن
            </button>
          </li>
        )}
      </ul>
    </div>
  );
}
