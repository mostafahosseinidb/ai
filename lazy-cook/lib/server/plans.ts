import "server-only";
import type { Household, PlannedMeal } from "@prisma/client";
import { db } from "@/lib/db";
import { addDaysISO, dbDateToISO, isoToDbDate, type ISODate } from "@/lib/i18n/jalali";
import { autofillPlan } from "@/lib/domain/autofill";
import {
  derivePrepTasks,
  validatePlan,
  type CandidateMeal,
  type RecipeFacts,
} from "@/lib/domain/rules";
import { loadRuleContext } from "./household";

export async function getActivePlan(householdId: string) {
  return db.mealPlan.findFirst({
    where: { householdId, deletedAt: null, status: "active" },
    orderBy: { startDate: "desc" },
  });
}

export function planEnd(plan: { startDate: Date; days: number }): ISODate {
  return addDaysISO(dbDateToISO(plan.startDate), plan.days - 1);
}

export function toCandidate(
  m: Pick<PlannedMeal, "date" | "slot" | "recipeId" | "memberNotes" | "servingsByMember"> & {
    leftoverOf?: { date: Date; slot: string } | null;
  },
): CandidateMeal {
  const servings = (m.servingsByMember ?? {}) as Record<string, number>;
  const eaters = Object.keys(servings);
  return {
    date: dbDateToISO(m.date),
    slot: m.slot,
    recipeId: m.recipeId,
    leftoverOf: m.leftoverOf
      ? { date: dbDateToISO(m.leftoverOf.date), slot: m.leftoverOf.slot }
      : null,
    eaterIds: eaters.length ? eaters : undefined,
    substitutions: (m.memberNotes ?? {}) as Record<string, string>,
  };
}

/** Meals that still count for the rules: everything except skipped / eaten out / replaced. */
export async function planCandidates(planId: string) {
  const meals = await db.plannedMeal.findMany({
    where: { mealPlanId: planId, deletedAt: null, status: { in: ["planned", "done"] } },
    include: { leftoverOf: { select: { date: true, slot: true } } },
  });
  return meals.map(toCandidate);
}

export async function defaultServings(householdId: string): Promise<Record<string, number>> {
  const members = await db.member.findMany({ where: { householdId, deletedAt: null } });
  return Object.fromEntries(members.map((m) => [m.id, Number(m.portionFactor)]));
}

/**
 * Re-derive the open prep tasks of a plan from its meals. Tasks already done are kept; open
 * auto tasks are replaced, so moving or swapping a meal moves its "tonight" task too.
 */
export async function syncPrepTasks(planId: string) {
  const plan = await db.mealPlan.findUniqueOrThrow({
    where: { id: planId },
    include: { household: true },
  });
  const meals = await db.plannedMeal.findMany({
    where: { mealPlanId: planId, deletedAt: null, status: "planned", recipeId: { not: null } },
    include: { recipe: true, leftoverOf: { select: { date: true, slot: true } } },
  });
  const recipes = new Map<string, RecipeFacts>();
  for (const m of meals) {
    const r = m.recipe!;
    recipes.set(r.id, {
      id: r.id,
      titleFa: r.titleFa,
      requiresEquipment: r.requiresEquipment,
      flags: [],
      ingredientKeys: [],
      prepAhead: (r.prepAhead ?? []) as RecipeFacts["prepAhead"],
      mealTypes: r.mealTypes,
    });
  }
  const derived = derivePrepTasks(meals.map(toCandidate), recipes, plan.household.bedtime);
  const mealId = new Map(meals.map((m) => [`${dbDateToISO(m.date)}|${m.slot}`, m.id]));

  await db.$transaction([
    db.prepTask.deleteMany({ where: { mealPlanId: planId, doneAt: null } }),
    db.prepTask.createMany({
      data: derived.map((t) => ({
        householdId: plan.householdId,
        mealPlanId: planId,
        dueDate: isoToDbDate(t.dueDate),
        dueTime: t.dueTime,
        text: t.text,
        relatedPlannedMealId: mealId.get(`${t.mealKey.date}|${t.mealKey.slot}`) ?? null,
      })),
    }),
  ]);
}

/** Create a plan (manual: empty; or filled by the simple rule-based filler). The new plan becomes active. */
export async function createPlan(
  household: Household,
  startDate: ISODate,
  days: number,
  autofill: boolean,
) {
  const servings = await defaultServings(household.id);
  let meals: CandidateMeal[] = [];
  if (autofill) {
    const { ctx, facts } = await loadRuleContext(household, startDate);
    const recent = await recentlyCooked(household.id, startDate);
    meals = autofillPlan({
      startDate,
      days,
      slots: household.activeMealSlots,
      cookingDaysPerWeek: household.cookingDaysPerWeek,
      iranianEveryDays: household.iranianFoodFrequencyDays,
      recipes: facts,
      ctx,
      recentlyCooked: recent,
    });
  }

  const plan = await db.$transaction(async (tx) => {
    await tx.mealPlan.updateMany({
      where: { householdId: household.id, status: "active" },
      data: { status: "completed" },
    });
    const plan = await tx.mealPlan.create({
      data: {
        householdId: household.id,
        startDate: isoToDbDate(startDate),
        days,
        status: "active",
        generationInput: { method: autofill ? "simple_autofill" : "manual" },
      },
    });
    const ids = new Map<string, string>();
    // cooked meals first so leftovers can point at them
    const ordered = [...meals].sort((a, b) => Number(!!a.leftoverOf) - Number(!!b.leftoverOf));
    for (const [position, m] of ordered.entries()) {
      const row = await tx.plannedMeal.create({
        data: {
          mealPlanId: plan.id,
          date: isoToDbDate(m.date),
          slot: m.slot,
          recipeId: m.recipeId,
          servingsByMember: servings,
          leftoverOfPlannedMealId: m.leftoverOf
            ? (ids.get(`${m.leftoverOf.date}|${m.leftoverOf.slot}`) ?? null)
            : null,
          position,
        },
      });
      ids.set(`${m.date}|${m.slot}`, row.id);
    }
    return plan;
  });
  await syncPrepTasks(plan.id);
  return plan;
}

async function recentlyCooked(householdId: string, before: ISODate) {
  const rows = await db.plannedMeal.findMany({
    where: {
      mealPlan: { householdId },
      recipeId: { not: null },
      status: "done",
      date: { gte: isoToDbDate(addDaysISO(before, -21)) },
    },
    select: { recipeId: true, date: true },
  });
  const out = new Map<string, ISODate>();
  for (const r of rows) {
    const d = dbDateToISO(r.date);
    if (!out.has(r.recipeId!) || out.get(r.recipeId!)! < d) out.set(r.recipeId!, d);
  }
  return out;
}

/** Check a proposed change against the hard rules; returns new violations it would introduce. */
export async function violationsIfChanged(
  household: Household,
  planId: string,
  startDate: ISODate,
  change: (meals: CandidateMeal[]) => CandidateMeal[],
) {
  const { ctx } = await loadRuleContext(household, startDate);
  const current = await planCandidates(planId);
  const before = new Set(
    validatePlan(current, ctx).map((v) => `${v.code}|${v.date}|${v.slot}|${v.memberId}`),
  );
  return validatePlan(change(current), ctx).filter(
    (v) => !before.has(`${v.code}|${v.date}|${v.slot}|${v.memberId}`),
  );
}
