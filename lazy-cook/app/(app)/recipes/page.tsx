import Link from "next/link";
import { Search } from "lucide-react";
import { requireHousehold } from "@/lib/session";
import { loadRecipes } from "@/lib/server/recipes";
import { searchRecipes } from "@/lib/server/recipe-search";
import { Badge, EmptyState, PageHeader, inputClass } from "@/components/ui/primitives";
import { fa } from "@/lib/i18n/fa";
import { toFaDigits } from "@/lib/i18n/digits";
import { CUISINES, MEAL_TYPES } from "@/lib/seed-schema";
import clsx from "clsx";

export const metadata = { title: fa.recipes.title };

const QUICK_TAGS = [
  "ten_minute",
  "one_pot",
  "vegetarian",
  "high_protein",
  "light",
  "freezer",
  "iranian_classic",
];

type SP = { q?: string; cuisine?: string; type?: string; tag?: string; all?: string };

export default async function RecipesPage({ searchParams }: { searchParams: Promise<SP> }) {
  const { household } = await requireHousehold();
  const sp = await searchParams;
  const all = await loadRecipes(household.id);
  const showAll = sp.all === "1";
  const results = searchRecipes(all, {
    query: sp.q,
    cuisine: sp.cuisine,
    mealType: sp.type,
    tag: sp.tag,
    equipment: showAll ? undefined : household.kitchenEquipment,
  });
  const hidden = showAll
    ? 0
    : searchRecipes(all, { query: sp.q, cuisine: sp.cuisine, mealType: sp.type, tag: sp.tag })
        .length - results.length;

  const href = (patch: Partial<SP>) => {
    const next = { ...sp, ...patch };
    const q = new URLSearchParams(Object.entries(next).filter(([, v]) => v) as [string, string][]);
    return `/recipes${q.size ? `?${q}` : ""}`;
  };

  return (
    <>
      <PageHeader title={fa.recipes.title} subtitle={`${toFaDigits(results.length)} دستور`} />
      <form className="px-4" action="/recipes">
        {Object.entries(sp)
          .filter(([k, v]) => k !== "q" && v)
          .map(([k, v]) => (
            <input key={k} type="hidden" name={k} value={v} />
          ))}
        <div className="relative">
          <Search
            className="text-muted pointer-events-none absolute start-3 top-3 size-5"
            aria-hidden
          />
          <input
            name="q"
            defaultValue={sp.q}
            className={`${inputClass} ps-10`}
            placeholder={fa.recipes.search}
            aria-label={fa.recipes.search}
            type="search"
          />
        </div>
      </form>
      <FilterRow>
        <FilterChip href={href({ type: undefined })} active={!sp.type}>
          {fa.recipes.all}
        </FilterChip>
        {MEAL_TYPES.map((t) => (
          <FilterChip
            key={t}
            href={href({ type: sp.type === t ? undefined : t })}
            active={sp.type === t}
          >
            {fa.mealTypes[t]}
          </FilterChip>
        ))}
      </FilterRow>
      <FilterRow>
        {QUICK_TAGS.map((t) => (
          <FilterChip
            key={t}
            href={href({ tag: sp.tag === t ? undefined : t })}
            active={sp.tag === t}
          >
            {fa.tags[t]}
          </FilterChip>
        ))}
        {CUISINES.map((c) => (
          <FilterChip
            key={c}
            href={href({ cuisine: sp.cuisine === c ? undefined : c })}
            active={sp.cuisine === c}
          >
            {fa.cuisines[c]}
          </FilterChip>
        ))}
      </FilterRow>

      {results.length === 0 ? (
        <EmptyState emoji="🔍" text={fa.recipes.none} />
      ) : (
        <ul className="flex flex-col gap-2 px-4 pt-2">
          {results.map((r) => (
            <li key={r.id}>
              <Link
                prefetch={false}
                href={`/recipes/${r.id}`}
                className="border-border bg-surface flex flex-col gap-1 rounded-[16px] border p-4"
              >
                <span className="flex items-baseline justify-between gap-2">
                  <span className="text-lg font-bold">{r.titleFa}</span>
                  <span className="text-muted shrink-0 text-xs">
                    {toFaDigits(r.activeMinutes)} {fa.recipes.minutes}
                  </span>
                </span>
                {r.introFa && <span className="text-muted text-sm leading-6">{r.introFa}</span>}
                <span className="mt-1 flex flex-wrap gap-1">
                  <Badge tone="primary">{fa.cuisines[r.cuisine]}</Badge>
                  {r.tags
                    .filter((t) => t !== "no_oven")
                    .slice(0, 3)
                    .map((t) => (
                      <Badge key={t}>{fa.tags[t]}</Badge>
                    ))}
                  {r.requiresEquipment.map((e) => (
                    <Badge key={e} tone="warning">
                      {fa.equipment[e]}
                    </Badge>
                  ))}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
      {hidden > 0 && (
        <p className="text-muted px-4 pt-4 text-center text-sm">
          {toFaDigits(hidden)} دستور به وسیله‌ای نیاز داره که نداری.{" "}
          <Link prefetch={false} className="text-primary underline" href={href({ all: "1" })}>
            نشونم بده
          </Link>
        </p>
      )}
    </>
  );
}

function FilterRow({ children }: { children: React.ReactNode }) {
  return <div className="no-scrollbar flex gap-2 overflow-x-auto px-4 pt-3">{children}</div>;
}

function FilterChip({
  href,
  active,
  children,
}: {
  href: string;
  active?: boolean;
  children: React.ReactNode;
}) {
  return (
    <Link
      prefetch={false}
      href={href}
      aria-current={active ? "true" : undefined}
      className={clsx(
        "inline-flex min-h-11 shrink-0 items-center rounded-full border px-4 text-sm",
        active ? "border-primary bg-primary text-primary-contrast" : "border-border bg-surface",
      )}
    >
      {children}
    </Link>
  );
}
