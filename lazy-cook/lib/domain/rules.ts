/**
 * Hard plan rules (spec §5.3). Pure functions, no database, no AI: they validate any plan —
 * built by hand, by the simple filler, or (phase 3) by the model — before it is saved.
 */
import { addDaysISO, diffDaysISO, type ISODate } from "@/lib/i18n/jalali";

export const MAIN_SLOTS = new Set(["lunch", "dinner"]);
export const DISLIKE_BLOCK_DAYS = 90;
export const EXPIRY_WINDOW_DAYS = 3;

export interface RecipeFacts {
  id: string;
  slug?: string | null;
  titleFa: string;
  titleOriginal?: string | null;
  requiresEquipment: string[];
  /** union of the flags of all non-optional ingredients */
  flags: string[];
  ingredientKeys: string[];
  ingredientIds?: string[];
  prepAhead: Array<{ text: string; hours_before: number }>;
  mealTypes: string[];
}

export interface PreferenceFacts {
  type: "diet" | "avoid" | "limit" | "dislike" | "like" | "intolerance";
  value: string;
  limitPerWeek?: number | null;
}

export interface MemberFacts {
  id: string;
  displayName: string;
  preferences: PreferenceFacts[];
}

export interface CandidateMeal {
  date: ISODate;
  slot: string;
  recipeId: string | null;
  leftoverOf?: { date: ISODate; slot: string } | null;
  /** members eating this meal; defaults to all members */
  eaterIds?: string[];
  /** per-member substitutions that make an otherwise forbidden meal OK, e.g. {m1: "بدون ماست"} */
  substitutions?: Record<string, string>;
}

export interface PantryFacts {
  ingredientId: string | null;
  ingredientKey?: string | null;
  expiry: ISODate | null;
  label: string;
}

export interface RuleContext {
  equipment: string[];
  members: MemberFacts[];
  recipes: Map<string, RecipeFacts>;
  /** recipeId → most recent "down" rating date */
  dislikedRecipes?: Map<string, ISODate>;
  pantry?: PantryFacts[];
  /** first day of the plan — needed for the expiry rule */
  startDate?: ISODate;
}

export type RuleCode =
  | "equipment"
  | "forbidden_ingredient"
  | "weekly_limit"
  | "disliked"
  | "repeat_consecutive"
  | "expiring_unused";

export interface Violation {
  code: RuleCode;
  date?: ISODate;
  slot?: string;
  recipeId?: string;
  memberId?: string;
  detail: string;
}

// ---------- preference → forbidden flags / keys ----------

const DIET_FORBIDS: Record<string, string[]> = {
  vegetarian: ["red_meat", "poultry", "seafood", "pork", "processed_meat"],
  vegan: ["animal_product"],
  no_pork: ["pork"],
  halal: ["pork", "alcohol"],
  no_alcohol: ["alcohol"],
  pescatarian: ["red_meat", "poultry", "pork", "processed_meat"],
};

const INTOLERANCE_FORBIDS: Record<string, string[]> = {
  lactose: ["lactose"],
  dairy: ["dairy"],
  gluten: ["gluten"],
  nuts: ["nuts"],
  egg: ["egg"],
  soy: ["soy"],
  seafood: ["seafood"],
};

/** Flags a member must not eat (diet + intolerance + avoid-by-flag). */
export function forbiddenFlags(member: MemberFacts): Set<string> {
  const out = new Set<string>();
  for (const p of member.preferences) {
    if (p.type === "diet") (DIET_FORBIDS[p.value] ?? []).forEach((f) => out.add(f));
    if (p.type === "intolerance")
      (INTOLERANCE_FORBIDS[p.value] ?? [p.value]).forEach((f) => out.add(f));
    if (p.type === "avoid") out.add(p.value);
  }
  return out;
}

/** Does a recipe break this member's diet/intolerance/avoid rules? Returns the offending flag/key. */
export function recipeConflict(recipe: RecipeFacts, member: MemberFacts): string | null {
  const forbidden = forbiddenFlags(member);
  for (const f of recipe.flags) if (forbidden.has(f)) return f;
  for (const k of recipe.ingredientKeys) if (forbidden.has(k)) return k;
  return null;
}

function normalise(s: string): string {
  return s
    .toLowerCase()
    .replace(/[‌\s_-]+/g, " ")
    .replace(/ي/g, "ی")
    .replace(/ك/g, "ک")
    .trim();
}

/** Free-text dislikes ("اوورنایت اوتس", "overnight oats") matched against the recipe's names. */
export function matchesTextDislike(recipe: RecipeFacts, member: MemberFacts): string | null {
  const names = [recipe.titleFa, recipe.titleOriginal ?? "", recipe.slug ?? ""].map(normalise);
  for (const p of member.preferences) {
    if (p.type !== "dislike") continue;
    const v = normalise(p.value);
    if (v.length < 2) continue;
    if (names.some((n) => n && (n.includes(v) || v.includes(n)))) return p.value;
  }
  return null;
}

// ---------- the validator ----------

export function validatePlan(meals: CandidateMeal[], ctx: RuleContext): Violation[] {
  const v: Violation[] = [];
  const allMemberIds = ctx.members.map((m) => m.id);
  const memberById = new Map(ctx.members.map((m) => [m.id, m]));
  const equipment = new Set(ctx.equipment);
  const sorted = [...meals].sort((a, b) => a.date.localeCompare(b.date));

  for (const meal of sorted) {
    if (!meal.recipeId) continue;
    const recipe = ctx.recipes.get(meal.recipeId);
    if (!recipe) continue;
    const at = { date: meal.date, slot: meal.slot, recipeId: recipe.id };

    // 1. equipment
    for (const eq of recipe.requiresEquipment) {
      if (!equipment.has(eq))
        v.push({ ...at, code: "equipment", detail: `«${recipe.titleFa}» به ${eq} نیاز دارد` });
    }

    // 2. forbidden ingredients per member (unless a substitution for that member is given)
    for (const id of meal.eaterIds ?? allMemberIds) {
      const member = memberById.get(id);
      if (!member || meal.substitutions?.[id]) continue;
      const bad = recipeConflict(recipe, member);
      if (bad)
        v.push({
          ...at,
          code: "forbidden_ingredient",
          memberId: id,
          detail: `${member.displayName}: ${bad}`,
        });
    }

    // 4. dislikes — rated down in the last 90 days, or a free-text dislike
    const dislikedOn = ctx.dislikedRecipes?.get(recipe.id);
    if (dislikedOn && diffDaysISO(meal.date, dislikedOn) < DISLIKE_BLOCK_DAYS) {
      v.push({ ...at, code: "disliked", detail: `«${recipe.titleFa}» اخیراً دیسلایک شده` });
    }
    for (const id of meal.eaterIds ?? allMemberIds) {
      const member = memberById.get(id);
      const hit = member && matchesTextDislike(recipe, member);
      if (hit)
        v.push({
          ...at,
          code: "disliked",
          memberId: id,
          detail: `${member.displayName} «${hit}» دوست ندارد`,
        });
    }
  }

  // 3. weekly limits — every rolling 7-day window
  v.push(...weeklyLimitViolations(sorted, ctx));

  // 5. same main dish on consecutive days, unless it is the leftover
  const mainByDate = new Map<ISODate, CandidateMeal[]>();
  for (const m of sorted) {
    if (!m.recipeId || !MAIN_SLOTS.has(m.slot)) continue;
    mainByDate.set(m.date, [...(mainByDate.get(m.date) ?? []), m]);
  }
  for (const [date, todays] of mainByDate) {
    const yesterday = mainByDate.get(addDaysISO(date, -1)) ?? [];
    for (const m of todays) {
      if (m.leftoverOf) continue;
      if (yesterday.some((y) => y.recipeId === m.recipeId)) {
        const title = ctx.recipes.get(m.recipeId!)?.titleFa ?? "";
        v.push({
          code: "repeat_consecutive",
          date,
          slot: m.slot,
          recipeId: m.recipeId!,
          detail: `«${title}» دو روز پشت سر هم`,
        });
      }
    }
  }

  // 6. pantry items expiring within 3 days of the start must be used before they expire
  if (ctx.pantry && ctx.startDate) {
    const limit = addDaysISO(ctx.startDate, EXPIRY_WINDOW_DAYS);
    for (const item of ctx.pantry) {
      if (!item.expiry || item.expiry > limit || item.expiry < ctx.startDate) continue;
      const used = sorted.some((m) => {
        if (!m.recipeId || m.date > item.expiry!) return false;
        const r = ctx.recipes.get(m.recipeId);
        if (!r) return false;
        return (
          (item.ingredientId && r.ingredientIds?.includes(item.ingredientId)) ||
          (item.ingredientKey && r.ingredientKeys.includes(item.ingredientKey))
        );
      });
      if (!used)
        v.push({
          code: "expiring_unused",
          date: item.expiry,
          detail: `«${item.label}» تا ${item.expiry} مصرف نشده`,
        });
    }
  }

  return v;
}

function weeklyLimitViolations(meals: CandidateMeal[], ctx: RuleContext): Violation[] {
  const out: Violation[] = [];
  if (meals.length === 0) return out;
  const allMemberIds = ctx.members.map((m) => m.id);
  const first = meals[0]!.date;
  const last = meals[meals.length - 1]!.date;

  for (const member of ctx.members) {
    for (const pref of member.preferences) {
      if (pref.type !== "limit" || pref.limitPerWeek == null) continue;
      const hits = meals.filter((m) => {
        if (!m.recipeId || !(m.eaterIds ?? allMemberIds).includes(member.id)) return false;
        if (m.substitutions?.[member.id]) return false;
        const r = ctx.recipes.get(m.recipeId);
        return !!r && (r.flags.includes(pref.value) || r.ingredientKeys.includes(pref.value));
      });
      const reported = new Set<string>();
      // windows starting 6 days before the plan, so plans that start mid-week are covered too
      for (let start = addDaysISO(first, -6); start <= last; start = addDaysISO(start, 1)) {
        const end = addDaysISO(start, 6);
        const inWindow = hits.filter((m) => m.date >= start && m.date <= end);
        if (inWindow.length > pref.limitPerWeek) {
          const offending = inWindow[inWindow.length - 1]!;
          const key = `${offending.date}|${offending.slot}`;
          if (reported.has(key)) continue;
          reported.add(key);
          out.push({
            code: "weekly_limit",
            date: offending.date,
            slot: offending.slot,
            recipeId: offending.recipeId!,
            memberId: member.id,
            detail: `${member.displayName}: ${pref.value} بیش از ${pref.limitPerWeek} بار در ۷ روز`,
          });
        }
      }
    }
  }
  return out;
}

// ---------- prep tasks (rule 7) ----------

export interface DerivedPrepTask {
  dueDate: ISODate;
  dueTime: string;
  text: string;
  mealKey: { date: ISODate; slot: string };
}

const SLOT_HOUR: Record<string, number> = {
  breakfast: 8,
  mid_morning: 10,
  lunch: 13,
  after_lunch: 15,
  snack: 17,
  dinner: 20,
  late: 22,
};

/**
 * One prep task per prep_ahead step. Anything needing ≥ 6 hours lead goes on the evening before
 * (default 21:00, or one hour before bedtime); shorter leads go on the same day.
 */
export function derivePrepTasks(
  meals: CandidateMeal[],
  recipes: Map<string, RecipeFacts>,
  bedtime?: string | null,
): DerivedPrepTask[] {
  const eveningTime = bedtime ? shiftTime(bedtime, -60) : "21:00";
  const out: DerivedPrepTask[] = [];
  for (const m of meals) {
    if (!m.recipeId || m.leftoverOf) continue;
    const r = recipes.get(m.recipeId);
    if (!r) continue;
    for (const p of r.prepAhead) {
      const mealHour = SLOT_HOUR[m.slot] ?? 13;
      if (p.hours_before >= 6 || p.hours_before > mealHour) {
        out.push({
          dueDate: addDaysISO(m.date, -1),
          dueTime: eveningTime,
          text: `${p.text} (برای ${r.titleFa})`,
          mealKey: { date: m.date, slot: m.slot },
        });
      } else {
        const h = Math.max(0, mealHour - Math.ceil(p.hours_before));
        out.push({
          dueDate: m.date,
          dueTime: `${String(h).padStart(2, "0")}:00`,
          text: `${p.text} (برای ${r.titleFa})`,
          mealKey: { date: m.date, slot: m.slot },
        });
      }
    }
  }
  return out;
}

function shiftTime(hhmm: string, minutes: number): string {
  const [h, m] = hhmm.split(":").map(Number);
  let total = ((h ?? 21) * 60 + (m ?? 0) + minutes) % 1440;
  if (total < 0) total += 1440;
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}
