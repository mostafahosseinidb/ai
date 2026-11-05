import "server-only";
import { db } from "@/lib/db";
import { buildShoppingList, type IngredientInfo, type NeedLine } from "@/lib/domain/shopping";
import { totalPortions } from "@/lib/domain/scaling";
import { dbDateToISO, isoToDbDate } from "@/lib/i18n/jalali";
import type { GramsPerUnit } from "@/lib/domain/units";
import { householdToday } from "./pantry";

/**
 * Rebuild the automatic part of the shopping list from the active plan (today onwards) minus
 * the pantry. Items the user added, and items already ticked, are left alone.
 */
export async function regenerateShopping(householdId: string) {
  const today = await householdToday(householdId);
  const plan = await db.mealPlan.findFirst({
    where: { householdId, status: "active", deletedAt: null },
    orderBy: { startDate: "desc" },
  });

  const meals = plan
    ? await db.plannedMeal.findMany({
        where: {
          mealPlanId: plan.id,
          deletedAt: null,
          status: "planned",
          date: { gte: isoToDbDate(today) },
          leftoverOfPlannedMealId: null,
        },
        include: { recipe: { include: { ingredients: { include: { ingredient: true } } } } },
      })
    : [];

  const info = new Map<string, IngredientInfo>();
  const needs: NeedLine[] = [];
  const extras = new Set<string>();
  for (const m of meals) {
    if (m.side) extras.add(m.side.trim());
    if (m.drink) extras.add(m.drink.trim());
    if (!m.recipe) continue;
    const factor =
      (totalPortions(m.servingsByMember as Record<string, number>) || m.recipe.baseServings) /
      m.recipe.baseServings;
    for (const ri of m.recipe.ingredients) {
      const ing = ri.ingredient;
      info.set(ing.id, {
        id: ing.id,
        key: ing.canonicalName,
        nameFa: ing.nameFa,
        category: ing.category,
        defaultUnit: ing.defaultUnit,
        gramsPerUnit: ing.gramsPerUnit as GramsPerUnit,
      });
      needs.push({
        ingredientId: ing.id,
        quantity: Number(ri.quantity) * factor,
        unit: ri.unit,
        optional: ri.optional,
      });
    }
  }

  // stock that is still good today
  const stock = await db.pantryItem.findMany({
    where: {
      householdId,
      status: "active",
      deletedAt: null,
      ingredientId: { not: null },
      OR: [{ computedExpiryDate: null }, { computedExpiryDate: { gte: isoToDbDate(today) } }],
    },
  });
  const lines = buildShoppingList(
    needs,
    stock.map((s) => ({
      ingredientId: s.ingredientId!,
      quantity: Number(s.quantity),
      unit: s.unit,
    })),
    info,
  );

  // sides/drinks already ticked shouldn't come back
  const ticked = new Set(
    (
      await db.shoppingItem.findMany({
        where: {
          householdId,
          deletedAt: null,
          checkedAt: { not: null },
          customName: { not: null },
        },
        select: { customName: true },
      })
    ).map((t) => t.customName!),
  );
  for (const t of ticked) extras.delete(t);

  // Diff against the current open auto rows instead of delete-and-recreate, so row ids stay
  // stable while another household member has the list open.
  const wanted = [
    ...lines.map((l) => ({
      key: `i|${l.ingredientId}|${l.unit}`,
      data: {
        ingredientId: l.ingredientId,
        customName: null,
        quantity: l.quantity,
        unit: l.unit,
        packageHint: l.packageHint,
        section: l.section,
        aisle: l.aisle,
      },
    })),
    // sides and drinks the plan mentions («کنارش: نون»): optional, as written
    ...[...extras].map((text) => ({
      key: `c|${text}`,
      data: {
        ingredientId: null,
        customName: text,
        quantity: null,
        unit: null,
        packageHint: null,
        section: "optional" as const,
        aisle: "other",
      },
    })),
  ];
  // One rebuild at a time per household: two members opening the list together (or a page load
  // during a tick) must not both insert the same row. The advisory lock is held until commit.
  await db.$transaction(async (tx) => {
    await tx.$executeRaw`SELECT pg_advisory_xact_lock(hashtext(${`shopping:${householdId}`}))`;
    const existing = await tx.shoppingItem.findMany({
      where: { householdId, addedBy: "auto", checkedAt: null, deletedAt: null },
      orderBy: { createdAt: "asc" },
    });
    const keyOf = (r: (typeof existing)[number]) =>
      r.ingredientId ? `i|${r.ingredientId}|${r.unit}` : `c|${r.customName}`;
    const byKey = new Map<string, (typeof existing)[number]>();
    const stale: string[] = [];
    for (const r of existing) {
      if (byKey.has(keyOf(r)))
        stale.push(r.id); // a duplicate from before: drop it
      else byKey.set(keyOf(r), r);
    }
    for (const w of wanted) {
      const row = byKey.get(w.key);
      byKey.delete(w.key);
      if (!row) {
        await tx.shoppingItem.create({
          data: { householdId, mealPlanId: plan?.id ?? null, addedBy: "auto", ...w.data },
        });
      } else if (
        Number(row.quantity ?? 0) !== Number(w.data.quantity ?? 0) ||
        row.section !== w.data.section ||
        row.packageHint !== w.data.packageHint
      ) {
        await tx.shoppingItem.update({
          where: { id: row.id },
          data: {
            quantity: w.data.quantity,
            section: w.data.section,
            packageHint: w.data.packageHint,
            mealPlanId: plan?.id ?? null,
          },
        });
      }
    }
    // no longer needed
    stale.push(...[...byKey.values()].map((r) => r.id));
    if (stale.length) await tx.shoppingItem.deleteMany({ where: { id: { in: stale } } });
  });
  return { today, planId: plan?.id ?? null, count: lines.length };
}

export function planDateRange(plan: { startDate: Date; days: number }) {
  return { start: dbDateToISO(plan.startDate), days: plan.days };
}
