"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Plus, Trash2 } from "lucide-react";
import { Button, Card, Chip, Field, inputClass } from "@/components/ui/primitives";
import { Sheet } from "@/components/ui/sheet";
import { IngredientSearch } from "@/components/pantry/ingredient-search";
import { fa } from "@/lib/i18n/fa";
import { parseNumber, toFaDigits } from "@/lib/i18n/digits";
import { UNIT_LABEL_FA, UNITS } from "@/lib/domain/units";
import { CUISINES, EQUIPMENT, MEAL_TYPES, RECIPE_TAGS } from "@/lib/seed-schema";
import type { RecipeInput } from "@/lib/recipe-input";

type Ing = {
  ingredient_id: string;
  name: string;
  quantity: string;
  unit: string;
  prep_note: string;
  optional: boolean;
};
type Step = { text: string; timer: string };

export interface RecipeFormInitial extends Partial<RecipeInput> {
  ingredientNames?: Record<string, string>;
}

const num = (s: string) => parseNumber(s);
const str = (n: number | null | undefined) => (n == null ? "" : toFaDigits(n));

export function RecipeForm({
  initial,
  recipeId,
}: {
  initial?: RecipeFormInitial;
  recipeId?: string;
}) {
  const router = useRouter();
  const [title, setTitle] = useState(initial?.title_fa ?? "");
  const [original, setOriginal] = useState(initial?.title_original ?? "");
  const [intro, setIntro] = useState(initial?.intro_fa ?? "");
  const [cuisine, setCuisine] = useState<string>(initial?.cuisine ?? "iranian");
  const [mealTypes, setMealTypes] = useState<string[]>(initial?.meal_types ?? ["lunch", "dinner"]);
  const [servings, setServings] = useState(str(initial?.base_servings ?? 2));
  const [active, setActive] = useState(str(initial?.active_minutes ?? 20));
  const [total, setTotal] = useState(str(initial?.total_minutes ?? 30));
  const [difficulty, setDifficulty] = useState(initial?.difficulty ?? 1);
  const [equipment, setEquipment] = useState<string[]>(initial?.requires_equipment ?? []);
  const [alt, setAlt] = useState(initial?.equipment_alternatives_fa ?? "");
  const [ings, setIngs] = useState<Ing[]>(
    (initial?.ingredients ?? []).map((i) => ({
      ingredient_id: i.ingredient_id,
      name: initial?.ingredientNames?.[i.ingredient_id] ?? "",
      quantity: str(i.quantity),
      unit: i.unit,
      prep_note: i.prep_note ?? "",
      optional: i.optional ?? false,
    })),
  );
  const [steps, setSteps] = useState<Step[]>(
    initial?.steps?.length
      ? initial.steps.map((s) => ({
          text: s.text,
          timer: s.timer_seconds ? str(Math.round(s.timer_seconds / 60)) : "",
        }))
      : [{ text: "", timer: "" }],
  );
  const [prep, setPrep] = useState(initial?.prep_ahead?.[0]?.text ?? "");
  const [prepHours, setPrepHours] = useState(str(initial?.prep_ahead?.[0]?.hours_before ?? 12));
  const [freezer, setFreezer] = useState(initial?.freezer_friendly ?? false);
  const [fridgeDays, setFridgeDays] = useState(str(initial?.fridge_days ?? 2));
  const [reheat, setReheat] = useState(initial?.reheat_note ?? "");
  const [tags, setTags] = useState<string[]>(initial?.tags ?? []);
  const [credit, setCredit] = useState(initial?.source_credit ?? "");
  const [picking, setPicking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);

  const toggle = (list: string[], set: (v: string[]) => void, v: string) =>
    set(list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

  function build(): { body?: RecipeInput; problems: string[] } {
    const problems: string[] = [];
    if (!title.trim()) problems.push("اسم غذا رو بنویس.");
    if (!mealTypes.length) problems.push("حداقل یه وعده انتخاب کن.");
    const ingredients = ings.map((i) => ({
      ingredient_id: i.ingredient_id,
      quantity: num(i.quantity) ?? 0,
      unit: i.unit as RecipeInput["ingredients"][number]["unit"],
      prep_note: i.prep_note.trim() || null,
      optional: i.optional,
    }));
    if (!ingredients.length) problems.push("حداقل یه ماده اضافه کن.");
    if (ingredients.some((i) => !(i.quantity > 0))) problems.push("مقدار همه‌ی مواد رو بنویس.");
    const cleanSteps = steps
      .filter((s) => s.text.trim())
      .map((s) => ({
        text: s.text.trim(),
        timer_seconds: num(s.timer) ? Math.round(num(s.timer)! * 60) : null,
      }));
    if (!cleanSteps.length) problems.push("حداقل یه مرحله بنویس.");
    if (problems.length) return { problems };
    return {
      problems,
      body: {
        title_fa: title.trim(),
        title_original: original.trim() || null,
        intro_fa: intro.trim() || null,
        cuisine: cuisine as RecipeInput["cuisine"],
        meal_types: mealTypes as RecipeInput["meal_types"],
        base_servings: Math.max(1, Math.round(num(servings) ?? 2)),
        active_minutes: Math.round(num(active) ?? 0),
        total_minutes: Math.round(num(total) ?? 0),
        difficulty,
        requires_equipment: equipment as RecipeInput["requires_equipment"],
        equipment_alternatives_fa: alt.trim() || null,
        ingredients,
        steps: cleanSteps,
        freezer_friendly: freezer,
        fridge_days: num(fridgeDays) != null ? Math.round(num(fridgeDays)!) : null,
        reheat_note: reheat.trim() || null,
        prep_ahead: prep.trim() ? [{ text: prep.trim(), hours_before: num(prepHours) ?? 12 }] : [],
        tags: tags as RecipeInput["tags"],
        source_credit: credit.trim() || null,
      },
    };
  }

  async function save() {
    const { body, problems } = build();
    setErrors(problems);
    if (!body) return;
    setBusy(true);
    const res = await fetch(recipeId ? `/api/recipes/${recipeId}` : "/api/recipes", {
      method: recipeId ? "PATCH" : "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    setBusy(false);
    if (!res.ok) return setErrors([fa.common.error]);
    const { recipe } = await res.json();
    router.push(`/recipes/${recipe.id}`);
    router.refresh();
  }

  return (
    <form
      className="flex flex-col gap-4 px-4"
      onSubmit={(e) => {
        e.preventDefault();
        save();
      }}
    >
      <Card className="flex flex-col gap-3">
        <Field label="اسم غذا">
          <input
            className={inputClass}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
            maxLength={80}
          />
        </Field>
        <Field label="اسم اصلی (اختیاری)">
          <input
            className={inputClass}
            dir="auto"
            value={original}
            onChange={(e) => setOriginal(e.target.value)}
            maxLength={80}
            placeholder="Shakshuka"
          />
        </Field>
        <Field label="یه جمله‌ی تنبلانه (اختیاری)">
          <input
            className={inputClass}
            value={intro}
            onChange={(e) => setIntro(e.target.value)}
            maxLength={200}
          />
        </Field>
        <Field label="آشپزی">
          <select
            className={inputClass}
            value={cuisine}
            onChange={(e) => setCuisine(e.target.value)}
          >
            {CUISINES.map((c) => (
              <option key={c} value={c}>
                {fa.cuisines[c]}
              </option>
            ))}
          </select>
        </Field>
        <fieldset>
          <legend className="mb-2 text-sm font-medium">برای کدوم وعده؟</legend>
          <div className="flex flex-wrap gap-2">
            {MEAL_TYPES.map((t) => (
              <Chip
                key={t}
                active={mealTypes.includes(t)}
                onClick={() => toggle(mealTypes, setMealTypes, t)}
              >
                {fa.mealTypes[t]}
              </Chip>
            ))}
          </div>
        </fieldset>
        <div className="grid grid-cols-3 gap-2">
          <Field label="چند نفر">
            <input
              className={inputClass}
              inputMode="numeric"
              value={servings}
              onChange={(e) => setServings(e.target.value)}
            />
          </Field>
          <Field label="آشپزی فعال (دقیقه)">
            <input
              className={inputClass}
              inputMode="numeric"
              value={active}
              onChange={(e) => setActive(e.target.value)}
            />
          </Field>
          <Field label="زمان کل (دقیقه)">
            <input
              className={inputClass}
              inputMode="numeric"
              value={total}
              onChange={(e) => setTotal(e.target.value)}
            />
          </Field>
        </div>
        <fieldset>
          <legend className="mb-2 text-sm font-medium">سختی</legend>
          <div className="flex gap-2">
            {[1, 2, 3].map((d) => (
              <Chip key={d} active={difficulty === d} onClick={() => setDifficulty(d)}>
                {fa.recipes.difficulty[d]}
              </Chip>
            ))}
          </div>
        </fieldset>
      </Card>

      <Card className="flex flex-col gap-3">
        <h2 className="font-bold">{fa.recipes.ingredients}</h2>
        {ings.map((i, idx) => (
          <div key={idx} className="border-border flex flex-col gap-2 border-b pb-3 last:border-0">
            <div className="flex items-center justify-between">
              <span className="font-medium">{i.name}</span>
              <button
                type="button"
                aria-label={`حذف ${i.name}`}
                onClick={() => setIngs(ings.filter((_, j) => j !== idx))}
                className="text-muted flex size-11 items-center justify-center"
              >
                <Trash2 className="size-4" />
              </button>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <input
                aria-label={`مقدار ${i.name}`}
                className={inputClass}
                inputMode="decimal"
                value={i.quantity}
                onChange={(e) =>
                  setIngs(ings.map((x, j) => (j === idx ? { ...x, quantity: e.target.value } : x)))
                }
              />
              <select
                aria-label={`واحد ${i.name}`}
                className={inputClass}
                value={i.unit}
                onChange={(e) =>
                  setIngs(ings.map((x, j) => (j === idx ? { ...x, unit: e.target.value } : x)))
                }
              >
                {UNITS.map((u) => (
                  <option key={u} value={u}>
                    {UNIT_LABEL_FA[u]}
                  </option>
                ))}
              </select>
            </div>
            <input
              aria-label={`توضیح ${i.name}`}
              className={inputClass}
              value={i.prep_note}
              placeholder="ریزخرد، خیس‌خورده… (اختیاری)"
              onChange={(e) =>
                setIngs(ings.map((x, j) => (j === idx ? { ...x, prep_note: e.target.value } : x)))
              }
            />
            <label className="flex min-h-11 items-center gap-2 text-sm">
              <input
                type="checkbox"
                className="size-5"
                checked={i.optional}
                onChange={(e) =>
                  setIngs(
                    ings.map((x, j) => (j === idx ? { ...x, optional: e.target.checked } : x)),
                  )
                }
              />
              {fa.recipes.optional}
            </label>
          </div>
        ))}
        <Button
          type="button"
          variant="secondary"
          onClick={() => setPicking(true)}
          disabled={ings.length >= 40}
        >
          <Plus className="size-4" /> ماده اضافه کن
        </Button>
      </Card>

      <Card className="flex flex-col gap-3">
        <h2 className="font-bold">
          {fa.recipes.steps}{" "}
          <span className="text-muted text-sm font-normal">(حداکثر ۷ مرحله، هر کدوم یه جمله)</span>
        </h2>
        {steps.map((s, idx) => (
          <div key={idx} className="flex flex-col gap-2">
            <div className="flex items-start gap-2">
              <span className="text-primary mt-3 w-6 text-center font-bold">
                {toFaDigits(idx + 1)}
              </span>
              <textarea
                aria-label={`مرحله‌ی ${toFaDigits(idx + 1)}`}
                className={`${inputClass} min-h-16 py-2`}
                value={s.text}
                maxLength={240}
                onChange={(e) =>
                  setSteps(steps.map((x, j) => (j === idx ? { ...x, text: e.target.value } : x)))
                }
              />
              {steps.length > 1 && (
                <button
                  type="button"
                  aria-label={`حذف مرحله‌ی ${toFaDigits(idx + 1)}`}
                  onClick={() => setSteps(steps.filter((_, j) => j !== idx))}
                  className="text-muted flex size-11 items-center justify-center"
                >
                  <Trash2 className="size-4" />
                </button>
              )}
            </div>
            <input
              aria-label={`تایمر مرحله‌ی ${toFaDigits(idx + 1)}`}
              className={`${inputClass} ms-8 w-40`}
              inputMode="numeric"
              value={s.timer}
              placeholder="تایمر (دقیقه)"
              onChange={(e) =>
                setSteps(steps.map((x, j) => (j === idx ? { ...x, timer: e.target.value } : x)))
              }
            />
          </div>
        ))}
        {steps.length < 7 && (
          <Button
            type="button"
            variant="ghost"
            className="text-primary self-start"
            onClick={() => setSteps([...steps, { text: "", timer: "" }])}
          >
            <Plus className="size-4" /> یه مرحله‌ی دیگه
          </Button>
        )}
      </Card>

      <Card className="flex flex-col gap-3">
        <h2 className="font-bold">وسایل و نگه‌داری</h2>
        <fieldset>
          <legend className="mb-2 text-sm font-medium">{fa.recipes.needs}</legend>
          <div className="flex flex-wrap gap-2">
            {EQUIPMENT.map((e) => (
              <Chip
                key={e}
                active={equipment.includes(e)}
                onClick={() => toggle(equipment, setEquipment, e)}
              >
                {fa.equipment[e]}
              </Chip>
            ))}
          </div>
        </fieldset>
        {equipment.length > 0 && (
          <Field label="نسخه‌ی بدون این وسیله (اختیاری)">
            <input
              className={inputClass}
              value={alt}
              onChange={(e) => setAlt(e.target.value)}
              maxLength={300}
              placeholder="بدون فر: تو تابه‌ی دربسته روی شعله‌ی کم"
            />
          </Field>
        )}
        <Field label="کار شبِ قبل (اختیاری)">
          <input
            className={inputClass}
            value={prep}
            onChange={(e) => setPrep(e.target.value)}
            placeholder="لوبیا رو از شب قبل خیس کن."
            maxLength={200}
          />
        </Field>
        {prep.trim() && (
          <Field label="چند ساعت قبل؟">
            <input
              className={inputClass}
              inputMode="numeric"
              value={prepHours}
              onChange={(e) => setPrepHours(e.target.value)}
            />
          </Field>
        )}
        <label className="flex min-h-11 items-center gap-2">
          <input
            type="checkbox"
            className="size-5"
            checked={freezer}
            onChange={(e) => setFreezer(e.target.checked)}
          />
          فریز می‌شه
        </label>
        <div className="grid grid-cols-2 gap-2">
          <Field label="چند روز تو یخچال می‌مونه">
            <input
              className={inputClass}
              inputMode="numeric"
              value={fridgeDays}
              onChange={(e) => setFridgeDays(e.target.value)}
            />
          </Field>
          <Field label="گرم کردن">
            <input
              className={inputClass}
              value={reheat}
              onChange={(e) => setReheat(e.target.value)}
              maxLength={200}
            />
          </Field>
        </div>
        <fieldset>
          <legend className="mb-2 text-sm font-medium">برچسب‌ها</legend>
          <div className="flex flex-wrap gap-2">
            {RECIPE_TAGS.filter((t) => t !== "no_oven").map((t) => (
              <Chip key={t} active={tags.includes(t)} onClick={() => toggle(tags, setTags, t)}>
                {fa.tags[t]}
              </Chip>
            ))}
          </div>
        </fieldset>
        <Field
          label="منبع (اختیاری)"
          hint="اگه از جایی برداشتی، اسمش رو بنویس؛ متن کامل پست دیگران رو کپی نکن."
        >
          <input
            className={inputClass}
            dir="auto"
            value={credit}
            onChange={(e) => setCredit(e.target.value)}
            maxLength={120}
            placeholder="@someone"
          />
        </Field>
      </Card>

      {errors.length > 0 && (
        <ul role="alert" className="bg-danger/10 text-danger rounded-[12px] p-3 text-sm leading-6">
          {errors.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      )}
      <p className="text-muted text-xs">
        کالری و پروتئین رو خودمون از روی مواد حساب می‌کنیم (تقریبی).
      </p>
      <Button type="submit" disabled={busy} className="min-h-12">
        {busy ? fa.common.loading : fa.common.save}
      </Button>

      <Sheet open={picking} onClose={() => setPicking(false)} title="ماده اضافه کن">
        {picking && (
          <IngredientSearch
            onPick={(i) => {
              setIngs([
                ...ings,
                {
                  ingredient_id: i.id,
                  name: i.nameFa,
                  quantity: "",
                  unit: i.defaultUnit,
                  prep_note: "",
                  optional: false,
                },
              ]);
              setPicking(false);
            }}
          />
        )}
      </Sheet>
    </form>
  );
}
