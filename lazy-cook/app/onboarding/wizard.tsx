"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Plus, Trash2 } from "lucide-react";
import { Button, Card, Chip, Field, inputClass } from "@/components/ui/primitives";
import { fa } from "@/lib/i18n/fa";
import { formatNumber, parseNumber, toFaDigits } from "@/lib/i18n/digits";
import { COUNTRIES, LANGUAGE_NAME_FA } from "@/lib/countries";
import { estimateDailyKcal, portionFactor } from "@/lib/domain/nutrition";
import { EQUIPMENT, SLOTS } from "@/lib/seed-schema";
import { WEEKDAY_NAMES_FA } from "@/lib/i18n/jalali";
import type { MemberInput, OnboardingInput } from "@/lib/onboarding-schema";

type Member = Omit<MemberInput, "goal" | "activity_level" | "preferences"> & {
  goal: MemberInput["goal"];
  activity_level: MemberInput["activity_level"];
  preferences: MemberInput["preferences"];
};
export type WizardInitial = Omit<OnboardingInput, "members" | "household" | "timezone"> & {
  members: Member[];
  household: OnboardingInput["household"];
};

const STEPS = 5;
const t = fa.onboarding;

export function Wizard({ initial, editing }: { initial: WizardInitial; editing: boolean }) {
  const router = useRouter();
  const [step, setStep] = useState(1);
  const [state, setState] = useState(initial);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canNext =
    step === 1
      ? !!state.country_code
      : step === 2
        ? state.members.length > 0 && state.members.every((m) => m.display_name.trim())
        : true;

  async function submit() {
    setSaving(true);
    setError(null);
    const res = await fetch("/api/onboarding", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        ...state,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      }),
    });
    setSaving(false);
    if (!res.ok) {
      setError(fa.common.error);
      return;
    }
    router.push(editing ? "/settings" : "/plan/new");
    router.refresh();
  }

  const setMember = (i: number, patch: Partial<Member>) =>
    setState((s) => ({
      ...s,
      members: s.members.map((m, j) => (j === i ? { ...m, ...patch } : m)),
    }));

  return (
    <main className="mx-auto flex min-h-dvh max-w-md flex-col px-4 pt-6 pb-28">
      <p className="text-muted text-sm">{t.step(toFaDigits(step), toFaDigits(STEPS))}</p>
      <div className="bg-surface-2 mt-2 h-1.5 overflow-hidden rounded-full" aria-hidden>
        <div
          className="bg-primary h-full transition-all"
          style={{ width: `${(step / STEPS) * 100}%` }}
        />
      </div>

      {step === 1 && <StepWhere state={state} setState={setState} />}
      {step === 2 && (
        <section className="mt-6 flex flex-col gap-4">
          <h1 className="text-2xl font-extrabold">{t.who}</h1>
          {state.members.map((m, i) => (
            <MemberCard
              key={m.id ?? i}
              m={m}
              onChange={(p) => setMember(i, p)}
              onRemove={
                state.members.length > 1
                  ? () => setState((s) => ({ ...s, members: s.members.filter((_, j) => j !== i) }))
                  : undefined
              }
            />
          ))}
          <Button
            variant="secondary"
            onClick={() =>
              setState((s) => ({
                ...s,
                members: [
                  ...s.members,
                  { display_name: "", goal: "maintain", activity_level: "light", preferences: [] },
                ],
              }))
            }
          >
            <Plus className="size-4" /> {t.addMember}
          </Button>
          <p className="text-muted text-xs leading-6">{fa.healthDisclaimer}</p>
        </section>
      )}
      {step === 3 && (
        <section className="mt-6 flex flex-col gap-4">
          <h1 className="text-2xl font-extrabold">{t.restrictions}</h1>
          {state.members.map((m, i) => (
            <RestrictionsCard
              key={m.id ?? i}
              m={m}
              onChange={(preferences) => setMember(i, { preferences })}
            />
          ))}
        </section>
      )}
      {step === 4 && (
        <section className="mt-6 flex flex-col gap-4">
          <h1 className="text-2xl font-extrabold">{t.kitchen}</h1>
          <div className="flex flex-wrap gap-2">
            {EQUIPMENT.map((e) => {
              const on = state.household.kitchen_equipment.includes(e);
              return (
                <Chip
                  key={e}
                  active={on}
                  onClick={() =>
                    setState((s) => ({
                      ...s,
                      household: {
                        ...s.household,
                        kitchen_equipment: on
                          ? s.household.kitchen_equipment.filter((x) => x !== e)
                          : [...s.household.kitchen_equipment, e],
                      },
                    }))
                  }
                >
                  {fa.equipment[e]}
                </Chip>
              );
            })}
          </div>
          {!state.household.kitchen_equipment.includes("oven") && (
            <p className="bg-accent/15 rounded-[12px] p-3 text-sm leading-6">{t.noOvenNote}</p>
          )}
          <NumberField
            label={t.burners}
            value={state.household.burners}
            min={0}
            max={8}
            onChange={(burners) =>
              setState((s) => ({ ...s, household: { ...s.household, burners: burners ?? 2 } }))
            }
          />
        </section>
      )}
      {step === 5 && <StepStyle state={state} setState={setState} />}

      {error && (
        <p role="alert" className="text-danger mt-4">
          {error}
        </p>
      )}

      <div className="border-border bg-surface fixed inset-x-0 bottom-0 border-t px-4 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
        <div className="mx-auto flex max-w-md gap-2">
          {step > 1 && (
            <Button variant="secondary" onClick={() => setStep(step - 1)}>
              {t.back}
            </Button>
          )}
          <div className="flex-1" />
          {step > 2 && step < STEPS && (
            <Button variant="ghost" onClick={() => setStep(step + 1)}>
              {t.skip}
            </Button>
          )}
          {step < STEPS ? (
            <Button disabled={!canNext} onClick={() => setStep(step + 1)}>
              {t.next}
            </Button>
          ) : (
            <Button disabled={saving} onClick={submit}>
              {saving ? fa.common.loading : editing ? fa.common.save : t.finish}
            </Button>
          )}
        </div>
      </div>
    </main>
  );
}

function StepWhere({
  state,
  setState,
}: {
  state: WizardInitial;
  setState: React.Dispatch<React.SetStateAction<WizardInitial>>;
}) {
  const country = COUNTRIES.find((c) => c.code === state.country_code);
  return (
    <section className="mt-6 flex flex-col gap-4">
      <h1 className="text-2xl font-extrabold">{t.where}</h1>
      <Field label={t.country}>
        <select
          className={inputClass}
          value={state.country_code}
          onChange={(e) => setState((s) => ({ ...s, country_code: e.target.value }))}
        >
          <option value="">—</option>
          {COUNTRIES.map((c) => (
            <option key={c.code} value={c.code}>
              {c.nameFa}
            </option>
          ))}
        </select>
      </Field>
      <Field label={t.city}>
        <input
          className={inputClass}
          value={state.city ?? ""}
          onChange={(e) => setState((s) => ({ ...s, city: e.target.value }))}
          placeholder="ایروان"
        />
      </Field>
      {country && (
        <p className="text-muted text-sm">
          {t.localLanguages} {country.languages.map((l) => LANGUAGE_NAME_FA[l] ?? l).join("، ")}
        </p>
      )}
    </section>
  );
}

function NumberField({
  label,
  value,
  onChange,
  min,
  max,
  hint,
  placeholder,
}: {
  label: string;
  value: number | null | undefined;
  onChange: (v: number | null) => void;
  min?: number;
  max?: number;
  hint?: string;
  placeholder?: string;
}) {
  const [text, setText] = useState(value == null ? "" : toFaDigits(value));
  return (
    <Field label={label} hint={hint}>
      <input
        className={inputClass}
        inputMode="decimal"
        placeholder={placeholder}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          const n = parseNumber(e.target.value);
          if (n === null) onChange(null);
          else onChange(Math.min(max ?? Infinity, Math.max(min ?? -Infinity, n)));
        }}
      />
    </Field>
  );
}

function MemberCard({
  m,
  onChange,
  onRemove,
}: {
  m: Member;
  onChange: (p: Partial<Member>) => void;
  onRemove?: () => void;
}) {
  const est = {
    sex: m.sex ?? null,
    birthYear: m.birth_year ?? null,
    heightCm: m.height_cm ?? null,
    weightKg: m.weight_kg ?? null,
    activity: m.activity_level,
    goal: m.goal,
  };
  const kcal = estimateDailyKcal(est);
  const computed = portionFactor(est);
  return (
    <Card className="flex flex-col gap-3">
      <div className="flex items-end gap-2">
        <div className="flex-1">
          <Field label={t.memberName}>
            <input
              className={inputClass}
              value={m.display_name}
              onChange={(e) => onChange({ display_name: e.target.value })}
              required
            />
          </Field>
        </div>
        {onRemove && (
          <Button variant="ghost" onClick={onRemove} aria-label={t.removeMember}>
            <Trash2 className="size-5" />
          </Button>
        )}
      </div>
      <div className="grid grid-cols-2 gap-3">
        <NumberField
          label={t.birthYear}
          value={m.birth_year}
          min={1900}
          max={2030}
          onChange={(v) => onChange({ birth_year: v && v >= 1900 ? Math.round(v) : null })}
        />
        <Field label="جنسیت (اختیاری)">
          <select
            className={inputClass}
            value={m.sex ?? ""}
            onChange={(e) => onChange({ sex: (e.target.value || null) as Member["sex"] })}
          >
            <option value="">—</option>
            {Object.entries(fa.sex).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </Field>
        <NumberField
          label={t.height}
          value={m.height_cm}
          onChange={(v) => onChange({ height_cm: v && v >= 80 ? Math.round(v) : null })}
        />
        <NumberField
          label={t.weight}
          value={m.weight_kg}
          onChange={(v) => onChange({ weight_kg: v && v >= 25 ? v : null })}
        />
        <Field label={t.goal}>
          <select
            className={inputClass}
            value={m.goal}
            onChange={(e) => onChange({ goal: e.target.value as Member["goal"] })}
          >
            {Object.entries(fa.goals).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t.activity}>
          <select
            className={inputClass}
            value={m.activity_level}
            onChange={(e) =>
              onChange({ activity_level: e.target.value as Member["activity_level"] })
            }
          >
            {Object.entries(fa.activity).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <NumberField
        label={t.portion}
        value={m.portion_factor}
        min={0.4}
        max={2}
        placeholder={`خودکار: ${formatNumber(computed, 2)}`}
        onChange={(v) => onChange({ portion_factor: v })}
        hint={
          kcal
            ? `${t.kcalEstimate}: ${fa.approx} ${formatNumber(kcal, 0)} کالری (فقط یه تخمینه)`
            : "خالی بذاری خودمون حساب می‌کنیم."
        }
      />
    </Card>
  );
}

function RestrictionsCard({
  m,
  onChange,
}: {
  m: Member;
  onChange: (p: Member["preferences"]) => void;
}) {
  const prefs = m.preferences;
  const has = (type: string, value: string) =>
    prefs.some((p) => p.type === type && p.value === value);
  const toggle = (
    type: Member["preferences"][number]["type"],
    value: string,
    extra: Partial<Member["preferences"][number]> = {},
  ) =>
    onChange(
      has(type, value)
        ? prefs.filter((p) => !(p.type === type && p.value === value))
        : [...prefs, { type, value, ...extra }],
    );
  const dislikes = useMemo(
    () =>
      prefs
        .filter((p) => p.type === "dislike")
        .map((p) => p.value)
        .join("، "),
    [prefs],
  );
  const [dislikeText, setDislikeText] = useState(dislikes);

  return (
    <Card className="flex flex-col gap-4">
      <h2 className="font-bold">{m.display_name}</h2>
      <Group label={t.diet}>
        {Object.entries(fa.diets).map(([k, v]) => (
          <Chip key={k} active={has("diet", k)} onClick={() => toggle("diet", k)}>
            {v}
          </Chip>
        ))}
      </Group>
      <Group label={t.limit}>
        {Object.entries(fa.limits).map(([k, v]) => (
          <Chip
            key={k}
            active={has("limit", k)}
            onClick={() => toggle("limit", k, k === "red_meat" ? { limit_per_week: 2 } : {})}
          >
            {v}
          </Chip>
        ))}
      </Group>
      {prefs
        .filter(
          (p) =>
            p.type === "limit" &&
            ["red_meat", "dairy", "sweet", "legume", "spicy"].includes(p.value),
        )
        .map((p) => (
          <NumberField
            key={p.value}
            label={`${fa.limits[p.value]}: ${t.perWeek}`}
            value={p.limit_per_week ?? null}
            min={0}
            max={21}
            onChange={(v) =>
              onChange(
                prefs.map((x) =>
                  x === p ? { ...x, limit_per_week: v == null ? null : Math.round(v) } : x,
                ),
              )
            }
          />
        ))}
      <Group label={t.intolerance}>
        {Object.entries(fa.intolerances).map(([k, v]) => (
          <Chip key={k} active={has("intolerance", k)} onClick={() => toggle("intolerance", k)}>
            {v}
          </Chip>
        ))}
      </Group>
      <Field label={t.dislikes}>
        <input
          className={inputClass}
          value={dislikeText}
          placeholder={t.dislikesPlaceholder}
          onChange={(e) => setDislikeText(e.target.value)}
          onBlur={() => {
            const values = dislikeText
              .split(/[,،\n]/)
              .map((s) => s.trim())
              .filter(Boolean);
            onChange([
              ...prefs.filter((p) => p.type !== "dislike"),
              ...values.map((value) => ({ type: "dislike" as const, value })),
            ]);
          }}
        />
      </Field>
    </Card>
  );
}

function Group({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <fieldset>
      <legend className="mb-2 text-sm font-medium">{label}</legend>
      <div className="flex flex-wrap gap-2">{children}</div>
    </fieldset>
  );
}

function StepStyle({
  state,
  setState,
}: {
  state: WizardInitial;
  setState: React.Dispatch<React.SetStateAction<WizardInitial>>;
}) {
  const h = state.household;
  const set = (patch: Partial<WizardInitial["household"]>) =>
    setState((s) => ({ ...s, household: { ...s.household, ...patch } }));
  return (
    <section className="mt-6 flex flex-col gap-5">
      <h1 className="text-2xl font-extrabold">{t.style}</h1>
      <Group label={t.slots}>
        {SLOTS.map((s) => {
          const on = h.active_meal_slots.includes(s);
          return (
            <Chip
              key={s}
              active={on}
              onClick={() =>
                set({
                  active_meal_slots: on
                    ? h.active_meal_slots.filter((x) => x !== s)
                    : SLOTS.filter((x) => x === s || h.active_meal_slots.includes(x)),
                })
              }
            >
              {fa.slots[s]}
            </Chip>
          );
        })}
      </Group>
      <NumberField
        label={t.cookingDays}
        value={h.cooking_days_per_week}
        min={1}
        max={7}
        onChange={(v) => set({ cooking_days_per_week: v ? Math.round(v) : 4 })}
      />
      <Field label={t.batchDay}>
        <select
          className={inputClass}
          value={h.batch_prep_weekday ?? ""}
          onChange={(e) =>
            set({ batch_prep_weekday: e.target.value === "" ? null : Number(e.target.value) })
          }
        >
          <option value="">{t.none}</option>
          {[6, 0, 1, 2, 3, 4, 5].map((d) => (
            <option key={d} value={d}>
              {WEEKDAY_NAMES_FA[d]}
            </option>
          ))}
        </select>
      </Field>
      <NumberField
        label={t.iranianEvery}
        value={h.iranian_food_frequency_days}
        min={1}
        max={60}
        onChange={(v) => set({ iranian_food_frequency_days: v ? Math.round(v) : 7 })}
      />
      <NumberField
        label={t.budget}
        value={h.weekly_budget}
        min={0}
        onChange={(v) => set({ weekly_budget: v })}
      />
      <Field label={t.bedtime}>
        <input
          type="time"
          dir="ltr"
          className={inputClass}
          value={h.bedtime ?? ""}
          onChange={(e) => set({ bedtime: e.target.value || null })}
        />
      </Field>
    </section>
  );
}
