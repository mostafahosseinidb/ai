import "server-only";
import type { Prisma } from "@prisma/client";
import { db } from "@/lib/db";
import { computeExpiry, planDeduction, type Location, type ShelfLife } from "@/lib/domain/pantry";
import { totalPortions } from "@/lib/domain/scaling";
import { dbDateToISO, isoToDbDate, todayISO, type ISODate } from "@/lib/i18n/jalali";
import type { GramsPerUnit } from "@/lib/domain/units";

/** "Today" for a household = today in its owner's time zone. */
export async function householdToday(householdId: string): Promise<ISODate> {
  const h = await db.household.findUnique({
    where: { id: householdId },
    select: { owner: { select: { timezone: true } } },
  });
  return todayISO(h?.owner.timezone ?? "UTC");
}

export function expiryFor(
  item: {
    location: Location;
    purchasedAt: Date | null;
    openedAt: Date | null;
    packageExpiryDate: Date | null;
  },
  shelfLife: ShelfLife | null,
  today: ISODate,
) {
  const iso = computeExpiry({
    location: item.location,
    purchasedAt: item.purchasedAt ? dbDateToISO(item.purchasedAt) : null,
    openedAt: item.openedAt ? dbDateToISO(item.openedAt) : null,
    packageExpiry: item.packageExpiryDate ? dbDateToISO(item.packageExpiryDate) : null,
    shelfLife,
    today,
  });
  return iso ? isoToDbDate(iso) : null;
}

/** Recompute and store an item's expiry (after creating it or changing location / opened date). */
export async function refreshExpiry(itemId: string) {
  const item = await db.pantryItem.findUniqueOrThrow({
    where: { id: itemId },
    include: { ingredient: true },
  });
  const today = await householdToday(item.householdId);
  const computed = expiryFor(
    item,
    (item.ingredient?.shelfLifeDays ?? null) as ShelfLife | null,
    today,
  );
  return db.pantryItem.update({ where: { id: itemId }, data: { computedExpiryDate: computed } });
}

/**
 * When a meal is ticked: take what it used out of the pantry. Each lot change is logged with
 * batchId = the meal's id, so un-ticking puts exactly that back.
 */
export async function deductForMeal(householdId: string, mealId: string) {
  const meal = await db.plannedMeal.findUniqueOrThrow({
    where: { id: mealId },
    include: { recipe: { include: { ingredients: { include: { ingredient: true } } } } },
  });
  if (!meal.recipe || meal.leftoverOfPlannedMealId) return; // leftovers were cooked already
  const portions =
    totalPortions(meal.servingsByMember as Record<string, number>) || meal.recipe.baseServings;
  const factor = portions / meal.recipe.baseServings;
  const needs = meal.recipe.ingredients
    .filter((i) => !i.optional)
    .map((i) => ({
      ingredientId: i.ingredientId,
      quantity: Number(i.quantity) * factor,
      unit: i.unit,
      gramsPerUnit: i.ingredient.gramsPerUnit as GramsPerUnit,
    }));
  const lots = await db.pantryItem.findMany({
    where: {
      householdId,
      status: "active",
      deletedAt: null,
      ingredientId: { in: needs.map((n) => n.ingredientId) },
    },
  });
  const plan = planDeduction(
    needs,
    lots.map((l) => ({
      id: l.id,
      ingredientId: l.ingredientId!,
      quantity: Number(l.quantity),
      unit: l.unit,
      expiry: l.computedExpiryDate ? dbDateToISO(l.computedExpiryDate) : null,
    })),
  );
  if (!plan.length) return;
  await db.$transaction(
    plan.flatMap((p) => {
      const lot = lots.find((l) => l.id === p.id)!;
      const after = { quantity: p.after, status: p.after <= 0 ? "used" : "active" } as const;
      return [
        db.pantryItem.update({ where: { id: p.id }, data: after }),
        db.changeLog.create({
          data: {
            householdId,
            batchId: mealId,
            entity: "pantry_deduction",
            entityId: p.id,
            before: { quantity: p.before, status: lot.status },
            after,
            actor: "system",
          },
        }),
      ];
    }),
  );
}

/** Put back what deductForMeal took for this meal (meal un-ticked, or the tick undone). */
export async function restoreForMeal(householdId: string, mealId: string) {
  const logs = await db.changeLog.findMany({
    where: { householdId, batchId: mealId, entity: "pantry_deduction", revertedAt: null },
  });
  if (!logs.length) return;
  await db.$transaction(
    logs.flatMap((l) => {
      const before = l.before as { quantity: number; status: "active" | "used" | "discarded" };
      return [
        db.pantryItem.update({
          where: { id: l.entityId },
          data: { quantity: before.quantity, status: before.status },
        }),
        db.changeLog.update({ where: { id: l.id }, data: { revertedAt: new Date() } }),
      ];
    }),
  );
}

export const pantryInclude = {
  ingredient: true,
  recipe: { select: { id: true, titleFa: true } },
} satisfies Prisma.PantryItemInclude;
