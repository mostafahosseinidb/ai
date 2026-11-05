import "server-only";
import type { Household } from "@prisma/client";
import { db } from "@/lib/db";
import { dbDateToISO, addDaysISO, type ISODate } from "@/lib/i18n/jalali";
import type { MemberFacts, RuleContext } from "@/lib/domain/rules";
import { loadRecipes, toFacts } from "./recipes";

export async function loadMembers(householdId: string) {
  return db.member.findMany({
    where: { householdId, deletedAt: null },
    include: { preferences: { where: { deletedAt: null } } },
    orderBy: { position: "asc" },
  });
}

export function memberFacts(members: Awaited<ReturnType<typeof loadMembers>>): MemberFacts[] {
  return members.map((m) => ({
    id: m.id,
    displayName: m.displayName,
    preferences: m.preferences.map((p) => ({
      type: p.type,
      value: p.value,
      limitPerWeek: p.limitPerWeek,
    })),
  }));
}

/** Most recent "down" rating per recipe within the last 90 days. */
export async function loadDislikes(
  householdId: string,
  since: ISODate,
): Promise<Map<string, ISODate>> {
  const rows = await db.mealFeedback.findMany({
    where: {
      rating: "down",
      deletedAt: null,
      createdAt: { gte: new Date(`${addDaysISO(since, -90)}T00:00:00Z`) },
      plannedMeal: { recipeId: { not: null }, mealPlan: { householdId } },
    },
    select: { createdAt: true, plannedMeal: { select: { recipeId: true } } },
  });
  const out = new Map<string, ISODate>();
  for (const r of rows) {
    const id = r.plannedMeal.recipeId!;
    const d = r.createdAt.toISOString().slice(0, 10);
    if (!out.has(id) || out.get(id)! < d) out.set(id, d);
  }
  return out;
}

export async function loadPantryFacts(householdId: string) {
  const items = await db.pantryItem.findMany({
    where: { householdId, status: "active", deletedAt: null },
    include: { ingredient: true },
  });
  return items.map((i) => ({
    ingredientId: i.ingredientId,
    ingredientKey: i.ingredient?.canonicalName ?? null,
    expiry: i.computedExpiryDate ? dbDateToISO(i.computedExpiryDate) : null,
    label: i.ingredient?.nameFa ?? i.customName ?? "",
  }));
}

/** Everything the rule engine needs about a household. */
export async function loadRuleContext(household: Household, startDate: ISODate) {
  const [members, recipes, disliked, pantry] = await Promise.all([
    loadMembers(household.id),
    loadRecipes(household.id),
    loadDislikes(household.id, startDate),
    loadPantryFacts(household.id),
  ]);
  const facts = recipes.map(toFacts);
  const ctx: RuleContext = {
    equipment: household.kitchenEquipment,
    members: memberFacts(members),
    recipes: new Map(facts.map((f) => [f.id, f])),
    dislikedRecipes: disliked,
    pantry,
    startDate,
  };
  return { ctx, facts, recipes, members };
}
