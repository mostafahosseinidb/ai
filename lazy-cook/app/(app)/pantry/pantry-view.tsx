"use client";

import { useState } from "react";
import clsx from "clsx";
import { Plus } from "lucide-react";
import { Badge, Button, Chip, EmptyState, Field, inputClass } from "@/components/ui/primitives";
import { Sheet } from "@/components/ui/sheet";
import { useToast } from "@/components/ui/toast";
import { useApi } from "@/components/meal/use-api";
import { IngredientSearch, type FoundIngredient } from "@/components/pantry/ingredient-search";
import { UNIT_LABEL_FA, UNITS } from "@/lib/domain/units";
import { parseNumber, toFaDigits } from "@/lib/i18n/digits";
import { formatDayMonth, type ISODate } from "@/lib/i18n/jalali";
import type { ExpiryTone } from "@/lib/domain/pantry";

type Location = "fridge" | "freezer" | "pantry";

export interface PantryItemDTO {
  id: string;
  name: string;
  localName: string | null;
  quantity: number;
  unit: string;
  amount: string;
  location: Location;
  opened: boolean;
  expiry: ISODate | null;
  packageExpiry: ISODate | null;
  tone: ExpiryTone;
  daysLeft: number | null;
  badPlace: boolean;
  isMeal: boolean;
}

const LOCATIONS: Array<{ id: Location; label: string; emoji: string }> = [
  { id: "fridge", label: "یخچال", emoji: "🧊" },
  { id: "freezer", label: "فریزر", emoji: "❄️" },
  { id: "pantry", label: "کابینت", emoji: "🗄️" },
];

function expiryLabel(i: PantryItemDTO): string | null {
  if (i.daysLeft === null) return null;
  if (i.daysLeft < 0) return `${toFaDigits(-i.daysLeft)} روز گذشته`;
  if (i.daysLeft === 0) return "تا امروز";
  if (i.daysLeft === 1) return "تا فردا";
  return `${toFaDigits(i.daysLeft)} روز دیگه`;
}

const TONE_CLASS: Record<ExpiryTone, string> = {
  ok: "bg-success/15 text-success",
  soon: "bg-warning/15 text-warning",
  expired: "bg-danger/15 text-danger",
  unknown: "bg-surface-2 text-muted",
};

export function PantryView({ items, today }: { items: PantryItemDTO[]; today: ISODate }) {
  const [loc, setLoc] = useState<Location>("fridge");
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<PantryItemDTO | null>(null);
  const urgent = items.filter((i) => i.tone === "soon" || i.tone === "expired");
  const shown = items.filter((i) => i.location === loc);

  return (
    <div className="flex flex-col gap-3 px-4">
      {urgent.length > 0 && (
        <div
          role="status"
          className="border-warning/40 bg-warning/10 rounded-[16px] border p-3 text-sm leading-6"
        >
          ⏰ {urgent.map((i) => i.name).join("، ")} {urgent.length === 1 ? "داره" : "دارن"} خراب
          می‌شه. تو برنامه‌های بعدی اول از {urgent.length === 1 ? "این" : "این‌ها"} استفاده می‌کنیم.
        </div>
      )}
      <div className="flex gap-2" role="tablist">
        {LOCATIONS.map((l) => (
          <Chip
            key={l.id}
            role="tab"
            aria-selected={loc === l.id}
            active={loc === l.id}
            onClick={() => setLoc(l.id)}
          >
            {l.emoji} {l.label} ({toFaDigits(items.filter((i) => i.location === l.id).length)})
          </Chip>
        ))}
      </div>

      {items.length === 0 ? (
        <EmptyState
          emoji="🧊"
          text="یخچالت خالیه یا هنوز چیزی ثبت نکردی؟ هر چی داری اضافه کن تا لیست خرید فقط کمبودها رو بگه."
        />
      ) : shown.length === 0 ? (
        <p className="text-muted py-8 text-center">اینجا چیزی نیست.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {shown.map((i) => (
            <li key={i.id}>
              <button
                onClick={() => setEditing(i)}
                className="border-border bg-surface flex min-h-14 w-full items-center gap-3 rounded-[16px] border px-4 py-3 text-start"
              >
                <span className="min-w-0 flex-1">
                  <span className="block font-bold">{i.name}</span>
                  {i.localName && (
                    <span className="text-muted block truncate text-xs" dir="auto">
                      {i.localName}
                    </span>
                  )}
                  <span className="mt-1 flex flex-wrap gap-1">
                    <Badge>{i.amount}</Badge>
                    {i.opened && <Badge tone="accent">باز شده</Badge>}
                    {i.badPlace && <Badge tone="danger">اینجا نگه‌داری نمی‌شه</Badge>}
                  </span>
                </span>
                {i.tone !== "unknown" && (
                  <span
                    className={clsx(
                      "shrink-0 rounded-full px-2.5 py-1 text-xs font-bold",
                      TONE_CLASS[i.tone],
                    )}
                    data-tone={i.tone}
                  >
                    {expiryLabel(i)}
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}

      <Button onClick={() => setAdding(true)} className="sticky bottom-36 self-start shadow-lg">
        <Plus className="size-5" /> اضافه کن
      </Button>

      {adding && <AddSheet location={loc} today={today} onClose={() => setAdding(false)} />}
      {editing && <EditSheet item={editing} onClose={() => setEditing(null)} />}
    </div>
  );
}

function AddSheet({
  location,
  today,
  onClose,
}: {
  location: Location;
  today: ISODate;
  onClose: () => void;
}) {
  const { call, busy } = useApi();
  const [picked, setPicked] = useState<{ ingredient?: FoundIngredient; custom?: string } | null>(
    null,
  );
  const [qty, setQty] = useState("1");
  const [unit, setUnit] = useState("piece");
  const [loc, setLoc] = useState<Location | "auto">("auto");
  const [opened, setOpened] = useState(false);
  const [pkg, setPkg] = useState("");

  async function save() {
    const quantity = parseNumber(qty);
    if (!quantity || !picked) return;
    await call("/api/pantry", "POST", {
      ingredient_id: picked.ingredient?.id ?? null,
      custom_name: picked.custom ?? null,
      quantity,
      unit,
      location: loc === "auto" ? (picked.custom ? location : undefined) : loc,
      opened,
      package_expiry_date: pkg || null,
    });
    onClose();
  }

  return (
    <Sheet open onClose={onClose} title="چی داری؟">
      {!picked ? (
        <IngredientSearch
          onPick={(i) => {
            setPicked({ ingredient: i });
            setUnit(i.defaultUnit);
            setQty(i.defaultUnit === "g" ? "۵۰۰" : i.defaultUnit === "ml" ? "۱۰۰۰" : "۱");
          }}
          onCustom={(text) => setPicked({ custom: text })}
        />
      ) : (
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            save();
          }}
        >
          <p className="font-bold">
            {picked.ingredient?.nameFa ?? picked.custom}{" "}
            <button
              type="button"
              className="text-primary text-sm font-normal"
              onClick={() => setPicked(null)}
            >
              (عوض کن)
            </button>
          </p>
          <div className="grid grid-cols-2 gap-3">
            <Field label="مقدار">
              <input
                className={inputClass}
                inputMode="decimal"
                value={qty}
                onChange={(e) => setQty(e.target.value)}
                required
              />
            </Field>
            <Field label="واحد">
              <select className={inputClass} value={unit} onChange={(e) => setUnit(e.target.value)}>
                {UNITS.map((u) => (
                  <option key={u} value={u}>
                    {UNIT_LABEL_FA[u]}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <Field label="کجا؟">
            <select
              className={inputClass}
              value={loc}
              onChange={(e) => setLoc(e.target.value as Location | "auto")}
            >
              <option value="auto">خودت تشخیص بده</option>
              {LOCATIONS.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.label}
                </option>
              ))}
            </select>
          </Field>
          <label className="flex min-h-11 items-center gap-2">
            <input
              type="checkbox"
              className="size-5"
              checked={opened}
              onChange={(e) => setOpened(e.target.checked)}
            />
            باز شده (از امروز)
          </label>
          <Field
            label="تاریخ روی بسته (اختیاری)"
            hint="اگه زودتر از ماندگاری معمولش باشه، همین حساب می‌شه."
          >
            <input
              type="date"
              dir="ltr"
              min={today}
              className={inputClass}
              value={pkg}
              onChange={(e) => setPkg(e.target.value)}
            />
          </Field>
          <Button type="submit" disabled={busy}>
            اضافه کن
          </Button>
        </form>
      )}
    </Sheet>
  );
}

function EditSheet({ item, onClose }: { item: PantryItemDTO; onClose: () => void }) {
  const { call, busy } = useApi();
  const toast = useToast();
  const [qty, setQty] = useState(toFaDigits(item.quantity));

  const patch = async (body: Record<string, unknown>, message?: string) => {
    await call(`/api/pantry/${item.id}`, "PATCH", body);
    if (message) toast({ text: message });
    onClose();
  };

  return (
    <Sheet open onClose={onClose} title={item.name}>
      <div className="flex flex-col gap-3">
        {item.expiry && (
          <p className="text-muted text-sm">
            خراب می‌شه: {formatDayMonth(item.expiry)}{" "}
            {item.packageExpiry === item.expiry
              ? "(تاریخ روی بسته)"
              : item.opened
                ? "(از وقتی باز شده)"
                : ""}
          </p>
        )}
        <form
          className="flex items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            const q = parseNumber(qty);
            if (q !== null) patch({ quantity: q });
          }}
        >
          <Field label={`مقدار (${UNIT_LABEL_FA[item.unit] ?? item.unit})`}>
            <input
              className={inputClass}
              inputMode="decimal"
              value={qty}
              onChange={(e) => setQty(e.target.value)}
            />
          </Field>
          <Button type="submit" variant="secondary" disabled={busy}>
            ذخیره
          </Button>
        </form>
        <Field label="جاش">
          <div className="flex gap-2">
            {LOCATIONS.map((l) => (
              <Chip
                key={l.id}
                disabled={busy}
                active={item.location === l.id}
                onClick={() => item.location !== l.id && patch({ location: l.id })}
              >
                {l.label}
              </Chip>
            ))}
          </div>
        </Field>
        {!item.isMeal && (
          <Button
            variant="secondary"
            disabled={busy}
            onClick={() => patch({ opened: !item.opened })}
          >
            {item.opened ? "هنوز باز نشده" : "بازش کردم"}
          </Button>
        )}
        <div className="grid grid-cols-2 gap-2">
          <Button
            variant="secondary"
            disabled={busy}
            onClick={() => patch({ status: "used" }, "تموم شد. نوش جان!")}
          >
            تموم شد
          </Button>
          <Button
            variant="secondary"
            disabled={busy}
            onClick={() => patch({ status: "discarded" }, "دفعه‌ی بعد زودتر برنامه‌ریزیش می‌کنیم.")}
          >
            دور ریختم
          </Button>
        </div>
      </div>
    </Sheet>
  );
}
