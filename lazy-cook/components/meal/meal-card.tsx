"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import clsx from "clsx";
import {
  Check,
  Clock,
  MoreHorizontal,
  Snowflake,
  ThumbsDown,
  ThumbsUp,
  Utensils,
} from "lucide-react";
import { Badge, Button, Chip, Field, inputClass } from "@/components/ui/primitives";
import { Sheet } from "@/components/ui/sheet";
import { useToast } from "@/components/ui/toast";
import { fa } from "@/lib/i18n/fa";
import { toFaDigits } from "@/lib/i18n/digits";
import { formatDayMonth, weekdayFa, type ISODate } from "@/lib/i18n/jalali";
import type { MealDTO } from "@/lib/server/day";
import { RuleViolationError, useApi, type Violation } from "./use-api";
import { RecipePicker, SLOT_MEAL_TYPE } from "./recipe-picker";

type SheetKind = null | "menu" | "eaten_out" | "move" | "replace" | "dislike";

export function MealCard({
  meal,
  planDays,
  slots,
}: {
  meal: MealDTO;
  planDays: ISODate[];
  slots: string[];
}) {
  const { call, busy } = useApi();
  const toast = useToast();
  const [sheet, setSheet] = useState<SheetKind>(null);
  const [popped, setPopped] = useState(false);
  const done = meal.status === "done";
  const inactive = meal.status === "skipped" || meal.status === "eaten_out";
  const title = meal.recipe?.titleFa ?? meal.freeText ?? "—";

  async function setStatus(status: string, extra: Record<string, unknown> = {}, message?: string) {
    const res = await call<{ changeId: string }>(`/api/planned-meals/${meal.id}`, "PATCH", {
      action: "status",
      status,
      ...extra,
    });
    if (message)
      toast({
        text: message,
        action: {
          label: fa.meal.undo,
          onClick: () => call(`/api/changes/${res.changeId}/undo`, "POST"),
        },
      });
  }

  async function rate(rating: "up" | "down", reasons?: string[], comment?: string) {
    if (meal.rating === rating && !reasons) {
      await call(`/api/planned-meals/${meal.id}/feedback`, "DELETE");
      return;
    }
    await call(`/api/planned-meals/${meal.id}/feedback`, "POST", { rating, reasons, comment });
  }

  return (
    <article
      className={clsx(
        "bg-surface rounded-[16px] border p-4 transition",
        done ? "border-success/40" : "border-border",
        inactive && "opacity-70",
      )}
      aria-label={`${fa.slots[meal.slot] ?? meal.slot}: ${title}`}
    >
      <div className="flex items-start gap-3">
        <button
          onClick={() => {
            setPopped(true);
            setTimeout(() => setPopped(false), 220);
            setStatus(done ? "planned" : "done");
          }}
          disabled={busy || inactive}
          aria-pressed={done}
          aria-label={fa.meal.check}
          className={clsx(
            "flex size-11 shrink-0 items-center justify-center rounded-full border-2 transition",
            done
              ? "border-success bg-success text-white"
              : "border-border hover:border-success text-transparent",
            popped && "pop",
          )}
        >
          <Check className="size-6" strokeWidth={3} />
        </button>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-primary text-xs font-bold">
              {fa.slots[meal.slot] ?? meal.slot}
            </span>
            {meal.leftoverOf && <Badge tone="accent">{fa.meal.leftover}</Badge>}
            {meal.fromFreezer && (
              <Badge tone="primary">
                <Snowflake className="me-1 size-3" />
                {fa.meal.fromFreezer}
              </Badge>
            )}
            {meal.status !== "planned" && meal.status !== "done" && (
              <Badge>{fa.status[meal.status]}</Badge>
            )}
            {meal.tag && <Badge>{meal.tag}</Badge>}
          </div>
          {meal.recipe ? (
            <Link
              prefetch={false}
              href={`/recipes/${meal.recipe.id}`}
              className={clsx(
                "mt-1 block text-lg leading-7 font-bold",
                done && "decoration-success/60 line-through",
              )}
            >
              {title}
            </Link>
          ) : (
            <p className="mt-1 text-lg leading-7 font-bold">{title}</p>
          )}
          {meal.status === "eaten_out" && meal.eatenOutText && (
            <p className="text-muted text-sm">🍽️ {meal.eatenOutText}</p>
          )}
          {(meal.side || meal.drink) && (
            <p className="text-muted mt-0.5 text-sm">
              {[meal.side && `کنارش: ${meal.side}`, meal.drink && `نوشیدنی: ${meal.drink}`]
                .filter(Boolean)
                .join(" · ")}
            </p>
          )}
          {meal.memberNotes.map((n) => (
            <p key={n.name} className="text-muted mt-0.5 text-sm">
              {n.name}: {n.text}
            </p>
          ))}
          {meal.recipe && !meal.leftoverOf && (
            <p className="text-muted mt-1 flex items-center gap-1 text-xs">
              <Clock className="size-3.5" aria-hidden /> {toFaDigits(meal.recipe.activeMinutes)}{" "}
              {fa.meal.active}
            </p>
          )}
        </div>
      </div>

      <div className="mt-2 flex items-center justify-end gap-1">
        <IconToggle
          label={fa.meal.like}
          active={meal.rating === "up"}
          onClick={() => rate("up")}
          disabled={busy}
        >
          <ThumbsUp className="size-5" />
        </IconToggle>
        <IconToggle
          label={fa.meal.dislike}
          active={meal.rating === "down"}
          onClick={() => (meal.rating === "down" ? rate("down") : setSheet("dislike"))}
          disabled={busy}
        >
          <ThumbsDown className="size-5" />
        </IconToggle>
        <IconToggle label={fa.meal.menu} onClick={() => setSheet("menu")}>
          <MoreHorizontal className="size-5" />
        </IconToggle>
      </div>

      <Sheet open={sheet === "menu"} onClose={() => setSheet(null)} title={title}>
        <div className="flex flex-col gap-2">
          <MenuItem onClick={() => setSheet("eaten_out")}>🍽️ {fa.meal.eatenOut}</MenuItem>
          <MenuItem onClick={() => setSheet("move")}>↔️ {fa.meal.moveTo}</MenuItem>
          <MenuItem onClick={() => setSheet("replace")}>🔄 {fa.meal.replace}</MenuItem>
          <MenuItem
            onClick={() => {
              setSheet(null);
              setStatus(
                meal.status === "skipped" ? "planned" : "skipped",
                {},
                meal.status === "skipped" ? undefined : `«${title}» رد شد.`,
              );
            }}
          >
            ⏭️ {meal.status === "skipped" ? fa.meal.undo : fa.meal.skip}
          </MenuItem>
        </div>
      </Sheet>

      <EatenOutSheet
        open={sheet === "eaten_out"}
        onClose={() => setSheet(null)}
        onSubmit={async (text) => {
          setSheet(null);
          await setStatus("eaten_out", { eaten_out_text: text }, fa.meal.eatenOutDone);
        }}
      />
      <DislikeSheet
        open={sheet === "dislike"}
        onClose={() => setSheet(null)}
        onSubmit={async (reasons, comment) => {
          setSheet(null);
          await rate("down", reasons, comment);
        }}
      />
      {sheet === "move" && (
        <MoveSheet meal={meal} planDays={planDays} slots={slots} onClose={() => setSheet(null)} />
      )}
      {sheet === "replace" && <ReplaceSheet meal={meal} onClose={() => setSheet(null)} />}
    </article>
  );
}

function IconToggle({
  label,
  active,
  children,
  ...props
}: React.ComponentProps<"button"> & { label: string; active?: boolean }) {
  return (
    <button
      aria-label={label}
      aria-pressed={active}
      title={label}
      className={clsx(
        "hover:bg-surface-2 flex size-11 items-center justify-center rounded-[12px] transition",
        active ? "text-primary" : "text-muted",
      )}
      {...props}
    >
      {children}
    </button>
  );
}

function MenuItem(props: React.ComponentProps<"button">) {
  return (
    <button
      className="hover:bg-surface-2 flex min-h-12 items-center gap-2 rounded-[12px] px-3 text-start"
      {...props}
    />
  );
}

function EatenOutSheet({
  open,
  onClose,
  onSubmit,
}: {
  open: boolean;
  onClose: () => void;
  onSubmit: (text: string) => void;
}) {
  const [text, setText] = useState("");
  return (
    <Sheet open={open} onClose={onClose} title={fa.meal.eatenOut}>
      <form
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          onSubmit(text);
          setText("");
        }}
      >
        <input
          className={inputClass}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={fa.meal.eatenOutPlaceholder}
          maxLength={300}
        />
        <Button type="submit">{fa.meal.send}</Button>
      </form>
    </Sheet>
  );
}

function DislikeSheet({
  open,
  onClose,
  onSubmit,
}: {
  open: boolean;
  onClose: () => void;
  onSubmit: (reasons: string[], comment?: string) => void;
}) {
  const [reasons, setReasons] = useState<string[]>([]);
  const [comment, setComment] = useState("");
  return (
    <Sheet open={open} onClose={onClose} title={fa.meal.dislikeWhy}>
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap gap-2">
          {Object.entries(fa.meal.dislikeReasons).map(([k, v]) => (
            <Chip
              key={k}
              active={reasons.includes(k)}
              onClick={() =>
                setReasons((r) => (r.includes(k) ? r.filter((x) => x !== k) : [...r, k]))
              }
            >
              {v}
            </Chip>
          ))}
        </div>
        <input
          className={inputClass}
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          placeholder="مثلاً سیر و گوجه‌ش کم بود (اختیاری)"
          maxLength={500}
        />
        <Button
          onClick={() => {
            onSubmit(reasons, comment.trim() || undefined);
            setReasons([]);
            setComment("");
          }}
        >
          {fa.meal.send}
        </Button>
      </div>
    </Sheet>
  );
}

function ViolationList({ violations }: { violations: Violation[] }) {
  return (
    <div role="alert" className="bg-warning/10 rounded-[12px] p-3 text-sm leading-6">
      <p className="font-bold">{fa.plan.violations}</p>
      <ul className="list-inside list-disc">
        {violations.map((v, i) => (
          <li key={i}>{v.detail}</li>
        ))}
      </ul>
    </div>
  );
}

function MoveSheet({
  meal,
  planDays,
  slots,
  onClose,
}: {
  meal: MealDTO;
  planDays: ISODate[];
  slots: string[];
  onClose: () => void;
}) {
  const { call, busy } = useApi();
  const [date, setDate] = useState(meal.date);
  const [slot, setSlot] = useState(meal.slot);
  const [violations, setViolations] = useState<Violation[] | null>(null);

  async function move(force = false) {
    try {
      await call(`/api/planned-meals/${meal.id}`, "PATCH", { action: "move", date, slot, force });
      onClose();
    } catch (e) {
      if (e instanceof RuleViolationError) setViolations(e.violations);
    }
  }

  return (
    <Sheet open onClose={onClose} title={fa.meal.moveTo}>
      <div className="flex flex-col gap-3">
        <Field label="روز">
          <select
            className={inputClass}
            value={date}
            onChange={(e) => {
              setDate(e.target.value);
              setViolations(null);
            }}
          >
            {planDays.map((d) => (
              <option key={d} value={d}>
                {weekdayFa(d)} {formatDayMonth(d)}
              </option>
            ))}
          </select>
        </Field>
        <Field label="وعده">
          <select
            className={inputClass}
            value={slot}
            onChange={(e) => {
              setSlot(e.target.value);
              setViolations(null);
            }}
          >
            {slots.map((s) => (
              <option key={s} value={s}>
                {fa.slots[s]}
              </option>
            ))}
          </select>
        </Field>
        <p className="text-muted text-xs">اگه اون وعده پر باشه، جاشون رو با هم عوض می‌کنیم.</p>
        {violations && <ViolationList violations={violations} />}
        {violations ? (
          <Button variant="secondary" disabled={busy} onClick={() => move(true)}>
            با این حال جابه‌جا کن
          </Button>
        ) : (
          <Button
            disabled={busy || (date === meal.date && slot === meal.slot)}
            onClick={() => move()}
          >
            جابه‌جا کن
          </Button>
        )}
      </div>
    </Sheet>
  );
}

function ReplaceSheet({ meal, onClose }: { meal: MealDTO; onClose: () => void }) {
  const { call, busy } = useApi();
  const [alts, setAlts] = useState<null | Array<{
    id: string;
    titleFa: string;
    activeMinutes: number;
  }>>(null);
  const [browse, setBrowse] = useState(false);
  const [violations, setViolations] = useState<{ id: string; list: Violation[] } | null>(null);

  useEffect(() => {
    fetch(`/api/planned-meals/${meal.id}/alternatives`)
      .then((r) => r.json())
      .then((j) => setAlts(j.alternatives ?? []))
      .catch(() => setAlts([]));
  }, [meal.id]);

  async function pick(id: string, force = false) {
    try {
      await call(`/api/planned-meals/${meal.id}`, "PATCH", {
        action: "replace",
        recipe_id: id,
        force,
      });
      onClose();
    } catch (e) {
      if (e instanceof RuleViolationError) setViolations({ id, list: e.violations });
    }
  }

  return (
    <Sheet open onClose={onClose} title={fa.meal.replaceTitle}>
      {violations ? (
        <div className="flex flex-col gap-3">
          <ViolationList violations={violations.list} />
          <Button variant="secondary" disabled={busy} onClick={() => pick(violations.id, true)}>
            با این حال بذارش
          </Button>
          <Button variant="ghost" onClick={() => setViolations(null)}>
            {fa.common.cancel}
          </Button>
        </div>
      ) : browse ? (
        <RecipePicker
          mealType={SLOT_MEAL_TYPE[meal.slot]}
          disabled={busy}
          onPick={(r) => pick(r.id)}
        />
      ) : (
        <div className="flex flex-col gap-2">
          {alts === null && [0, 1, 2].map((i) => <div key={i} className="skeleton h-14" />)}
          {alts?.length === 0 && <p className="text-muted">{fa.meal.noAlternatives}</p>}
          {alts?.map((a) => (
            <button
              key={a.id}
              disabled={busy}
              onClick={() => pick(a.id, true)}
              className="border-border hover:bg-surface-2 flex min-h-14 items-center justify-between rounded-[12px] border px-3 text-start"
            >
              <span className="flex items-center gap-2 font-medium">
                <Utensils className="text-muted size-4" aria-hidden />
                {a.titleFa}
              </span>
              <span className="text-muted text-xs">
                {toFaDigits(a.activeMinutes)} {fa.recipes.minutes}
              </span>
            </button>
          ))}
          <Button variant="ghost" className="text-primary mt-2" onClick={() => setBrowse(true)}>
            از کتابخونه انتخاب کن
          </Button>
        </div>
      )}
    </Sheet>
  );
}
