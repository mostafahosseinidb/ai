"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { Check, Copy, Plus, Trash2 } from "lucide-react";
import {
  Button,
  ButtonLink,
  Chip,
  EmptyState,
  Field,
  inputClass,
} from "@/components/ui/primitives";
import { Sheet } from "@/components/ui/sheet";
import { useToast } from "@/components/ui/toast";
import { IngredientSearch } from "@/components/pantry/ingredient-search";
import { AISLE_LABEL_FA, AISLE_ORDER } from "@/lib/domain/shopping";
import { UNIT_LABEL_FA } from "@/lib/domain/units";
import { parseNumber, toFaDigits } from "@/lib/i18n/digits";
import type { ShoppingItemDTO } from "@/lib/server/shopping-view";

const KEY = ["shopping"];

async function fetchItems(): Promise<ShoppingItemDTO[]> {
  const res = await fetch("/api/shopping");
  if (!res.ok) throw new Error("load_failed");
  return (await res.json()).items;
}

async function send(url: string, method: string, body?: unknown) {
  const res = await fetch(url, {
    method,
    headers: body ? { "content-type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(String(res.status));
  return res.json();
}

/**
 * Live between household members by polling every 5 s (and on focus): the simplest thing that
 * works on any host. A push channel (Supabase Realtime / SSE) can replace it without UI changes.
 */
export function ShoppingView({
  initial,
  hasPlan,
}: {
  initial: ShoppingItemDTO[];
  hasPlan: boolean;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  const {
    data: items = initial,
    isError,
    refetch,
  } = useQuery({
    queryKey: KEY,
    queryFn: fetchItems,
    initialData: initial,
    refetchInterval: 5000,
    refetchOnWindowFocus: true,
  });
  const [buying, setBuying] = useState<ShoppingItemDTO | null>(null);
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState(false);

  const refresh = () => qc.invalidateQueries({ queryKey: KEY });
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await fn();
    } catch {
      toast({ text: "یه مشکلی پیش اومد." });
    } finally {
      setBusy(false);
      refresh();
    }
  };

  const open = items.filter((i) => !i.checked);
  const done = items.filter((i) => i.checked);

  async function tick(item: ShoppingItemDTO) {
    if (item.ingredientId && item.quantity) return setBuying(item);
    // custom items (sides, drinks, free text) just get ticked
    await act(() => send(`/api/shopping/${item.id}`, "PATCH", { checked: true }));
  }

  async function copy() {
    const text = await (await fetch("/api/shopping?format=text")).text();
    try {
      await navigator.clipboard.writeText(text);
      toast({ text: "کپی شد؛ بفرستش تو تلگرام یا واتس‌اپ." });
    } catch {
      toast({ text: "کپی نشد؛ مرورگر اجازه نداد." });
    }
  }

  if (isError && !items.length)
    return (
      <EmptyState
        emoji="😕"
        text="لیست بار نشد."
        action={<Button onClick={() => refetch()}>دوباره امتحان کن</Button>}
      />
    );

  return (
    <div className="flex flex-col gap-4 px-4">
      <div className="flex gap-2">
        <Button variant="secondary" onClick={() => setAdding(true)}>
          <Plus className="size-4" /> اضافه کن
        </Button>
        <Button variant="secondary" onClick={copy} disabled={!open.length}>
          <Copy className="size-4" /> کپی متن
        </Button>
      </div>

      {open.length === 0 && (
        <EmptyState
          emoji={hasPlan ? "🎉" : "🛒"}
          text={
            hasPlan
              ? "برای برنامه‌ی فعلی همه چی داری!"
              : "هنوز برنامه‌ای نداری؛ با یه برنامه لیست خرید خودش ساخته می‌شه."
          }
          action={hasPlan ? undefined : <ButtonLink href="/plan/new">ساخت برنامه</ButtonLink>}
        />
      )}

      {(["required", "optional"] as const).map((section) => {
        const list = open.filter((i) => i.section === section);
        if (!list.length) return null;
        return (
          <section key={section} aria-labelledby={`s-${section}`} className="flex flex-col gap-2">
            <h2 id={`s-${section}`} className="text-lg font-bold">
              {section === "required" ? "ضروری" : "اختیاری"}
              {section === "optional" && (
                <span className="text-muted ms-2 text-sm font-normal">
                  کنار غذا، ادویه‌ها، اگه تموم شده
                </span>
              )}
            </h2>
            {AISLE_ORDER.map((aisle) => {
              const inAisle = list.filter((i) => i.aisle === aisle);
              if (!inAisle.length) return null;
              return (
                <div key={aisle}>
                  <h3 className="text-muted mb-1 text-xs font-bold">{AISLE_LABEL_FA[aisle]}</h3>
                  <ul className="divide-border border-border bg-surface flex flex-col divide-y rounded-[16px] border">
                    {inAisle.map((i) => (
                      <Row
                        key={i.id}
                        item={i}
                        disabled={busy}
                        onTick={() => tick(i)}
                        onDelete={() => act(() => send(`/api/shopping/${i.id}`, "DELETE"))}
                      />
                    ))}
                  </ul>
                </div>
              );
            })}
          </section>
        );
      })}

      {done.length > 0 && (
        <details className="border-border bg-surface rounded-[16px] border p-3">
          <summary className="flex min-h-11 cursor-pointer items-center justify-between">
            <span>خریدم ({toFaDigits(done.length)})</span>
          </summary>
          <ul className="text-muted mt-2 flex flex-col gap-1">
            {done.map((i) => (
              <li key={i.id} className="line-through">
                {i.name}
              </li>
            ))}
          </ul>
          <Button
            variant="ghost"
            className="mt-2 text-sm"
            onClick={() => act(() => send("/api/shopping/clear-checked", "POST"))}
          >
            پاکشون کن
          </Button>
        </details>
      )}

      {buying && (
        <PurchaseSheet
          item={buying}
          onClose={() => setBuying(null)}
          onDone={async (body) => {
            const item = buying;
            setBuying(null);
            let pantryId: string | null = null;
            await act(async () => {
              pantryId = (await send(`/api/shopping/${item.id}/purchased`, "POST", body))
                .pantry_item_id;
            });
            toast({
              text: `${item.name} رفت تو موجودی.`,
              action: {
                label: "برگردون",
                onClick: () =>
                  act(() =>
                    send(`/api/shopping/${item.id}/purchased`, "DELETE", {
                      pantry_item_id: pantryId,
                    }),
                  ),
              },
            });
          }}
        />
      )}
      {adding && <AddSheet onClose={() => setAdding(false)} onAdded={refresh} />}
    </div>
  );
}

function Row({
  item,
  onTick,
  onDelete,
  disabled,
}: {
  item: ShoppingItemDTO;
  onTick: () => void;
  onDelete: () => void;
  disabled: boolean;
}) {
  return (
    <li className="flex items-center gap-2 px-2 py-1.5">
      <button
        onClick={onTick}
        disabled={disabled}
        aria-label={`خریدم: ${item.name}`}
        className="flex size-11 shrink-0 items-center justify-center"
      >
        <span
          className={clsx(
            "flex size-6 items-center justify-center rounded-md border-2",
            item.checked ? "border-success bg-success text-white" : "border-border",
          )}
        >
          {item.checked && <Check className="size-4" />}
        </span>
      </button>
      <div className="min-w-0 flex-1">
        <p className="font-medium">{item.name}</p>
        {item.localName && (
          <p className="text-muted text-sm" dir="auto">
            {item.localName}
          </p>
        )}
        {(item.packageHint || item.amount) && (
          <p className="text-muted text-xs">{item.packageHint ?? item.amount}</p>
        )}
        {item.localNote && <p className="text-muted text-xs">💡 {item.localNote}</p>}
      </div>
      {item.addedBy !== "auto" && (
        <button
          onClick={onDelete}
          disabled={disabled}
          aria-label={`حذف ${item.name}`}
          className="text-muted flex size-11 items-center justify-center"
        >
          <Trash2 className="size-4" />
        </button>
      )}
    </li>
  );
}

function PurchaseSheet({
  item,
  onClose,
  onDone,
}: {
  item: ShoppingItemDTO;
  onClose: () => void;
  onDone: (body: Record<string, unknown>) => void;
}) {
  const [qty, setQty] = useState(toFaDigits(item.quantity ?? 1));
  return (
    <Sheet open onClose={onClose} title={`${item.name} خریدی؟`}>
      <form
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          const q = parseNumber(qty);
          onDone(q ? { quantity: q, unit: item.unit } : {});
        }}
      >
        <p className="text-muted text-sm">چقدر خریدی؟ همینو تو موجودی می‌ذاریم.</p>
        <Field label={`مقدار (${UNIT_LABEL_FA[item.unit ?? ""] ?? item.unit})`}>
          <input
            className={inputClass}
            inputMode="decimal"
            value={qty}
            onChange={(e) => setQty(e.target.value)}
          />
        </Field>
        <Button type="submit">خریدم</Button>
      </form>
    </Sheet>
  );
}

function AddSheet({ onClose, onAdded }: { onClose: () => void; onAdded: () => void }) {
  const [section, setSection] = useState<"required" | "optional">("required");
  const add = async (body: Record<string, unknown>) => {
    await send("/api/shopping", "POST", { ...body, section });
    onAdded();
    onClose();
  };
  return (
    <Sheet open onClose={onClose} title="چی بخریم؟">
      <div className="flex flex-col gap-3">
        <div className="flex gap-2">
          <Chip active={section === "required"} onClick={() => setSection("required")}>
            ضروری
          </Chip>
          <Chip active={section === "optional"} onClick={() => setSection("optional")}>
            اختیاری
          </Chip>
        </div>
        <IngredientSearch
          placeholder="مثلاً پنیر، دستمال کاغذی…"
          onPick={(i) => add({ ingredient_id: i.id })}
          onCustom={(text) => add({ custom_name: text })}
        />
      </div>
    </Sheet>
  );
}
