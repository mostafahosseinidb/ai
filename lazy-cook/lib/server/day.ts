import "server-only";
import { db } from "@/lib/db";
import { addDaysISO, dbDateToISO, isoToDbDate, type ISODate } from "@/lib/i18n/jalali";
import { SLOTS } from "@/lib/seed-schema";

export interface MealDTO {
  id: string;
  date: ISODate;
  slot: string;
  status: string;
  recipe: { id: string; titleFa: string; activeMinutes: number; cuisine: string } | null;
  freeText: string | null;
  side: string | null;
  drink: string | null;
  tag: string | null;
  eatenOutText: string | null;
  leftoverOf: { date: ISODate; slot: string } | null;
  fromFreezer: boolean;
  rating: "up" | "down" | null;
  memberNotes: Array<{ name: string; text: string }>;
}

export interface DaySummary {
  date: ISODate;
  total: number;
  done: number;
}

const slotOrder = (s: string) => SLOTS.indexOf(s as (typeof SLOTS)[number]);

export async function loadPlanMeals(planId: string, householdId: string): Promise<MealDTO[]> {
  const [meals, members] = await Promise.all([
    db.plannedMeal.findMany({
      where: { mealPlanId: planId, deletedAt: null },
      include: {
        recipe: { select: { id: true, titleFa: true, activeMinutes: true, cuisine: true } },
        leftoverOf: { select: { date: true, slot: true } },
        feedback: {
          where: { deletedAt: null, memberId: null },
          orderBy: { createdAt: "desc" },
          take: 1,
        },
      },
    }),
    db.member.findMany({ where: { householdId }, select: { id: true, displayName: true } }),
  ]);
  const names = new Map(members.map((m) => [m.id, m.displayName]));
  return meals
    .map((m) => ({
      id: m.id,
      date: dbDateToISO(m.date),
      slot: m.slot,
      status: m.status,
      recipe: m.recipe,
      freeText: m.freeText,
      side: m.side,
      drink: m.drink,
      tag: m.tag,
      eatenOutText: m.eatenOutText,
      leftoverOf: m.leftoverOf
        ? { date: dbDateToISO(m.leftoverOf.date), slot: m.leftoverOf.slot }
        : null,
      fromFreezer: !!m.fromFreezerPantryItemId,
      rating: (m.feedback[0]?.rating ?? null) as MealDTO["rating"],
      memberNotes: Object.entries((m.memberNotes ?? {}) as Record<string, string>).map(
        ([id, text]) => ({ name: names.get(id) ?? "", text }),
      ),
    }))
    .sort((a, b) => a.date.localeCompare(b.date) || slotOrder(a.slot) - slotOrder(b.slot));
}

export function daySummaries(start: ISODate, days: number, meals: MealDTO[]): DaySummary[] {
  return Array.from({ length: days }, (_, i) => {
    const date = addDaysISO(start, i);
    const todays = meals.filter((m) => m.date === date && m.status !== "replaced");
    return {
      date,
      total: todays.length,
      done: todays.filter((m) => m.status !== "planned").length,
    };
  });
}

export async function loadPrepTasks(householdId: string, date: ISODate) {
  const rows = await db.prepTask.findMany({
    where: { householdId, deletedAt: null, dueDate: isoToDbDate(date) },
    orderBy: { dueTime: "asc" },
  });
  return rows.map((t) => ({ id: t.id, text: t.text, dueTime: t.dueTime, done: !!t.doneAt }));
}

export async function loadFreeLogs(householdId: string, date: ISODate) {
  const rows = await db.freeLog.findMany({
    where: { householdId, deletedAt: null, date: isoToDbDate(date) },
    orderBy: { createdAt: "asc" },
  });
  return rows.map((l) => ({ id: l.id, text: l.text }));
}
