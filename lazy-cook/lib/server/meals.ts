import "server-only";
import type { Prisma } from "@prisma/client";
import { db } from "@/lib/db";
import { ApiError, notFound } from "./api";

/** A planned meal, only if it belongs to the household. */
export async function ownedMeal(householdId: string, id: string) {
  if (!/^[0-9a-f-]{36}$/i.test(id)) throw notFound();
  const meal = await db.plannedMeal.findFirst({
    where: { id, deletedAt: null, mealPlan: { householdId, deletedAt: null } },
    include: { mealPlan: true },
  });
  if (!meal) throw notFound();
  return meal;
}

const SNAPSHOT_FIELDS = [
  "date",
  "slot",
  "recipeId",
  "freeText",
  "status",
  "eatenOutText",
  "doneAt",
  "leftoverOfPlannedMealId",
  "side",
  "drink",
  "tag",
  "deletedAt",
] as const;

type Snapshot = Partial<Record<(typeof SNAPSHOT_FIELDS)[number], string | number | boolean | null>>;

function snapshot(m: Record<string, unknown>): Snapshot {
  const out: Snapshot = {};
  for (const f of SNAPSHOT_FIELDS) {
    const v = m[f];
    out[f] =
      v instanceof Date ? v.toISOString() : ((v ?? null) as string | number | boolean | null);
  }
  return out;
}

/** Update a planned meal and write a change_log row so it can be undone. */
export async function updateMealLogged(
  householdId: string,
  mealId: string,
  data: Prisma.PlannedMealUncheckedUpdateInput,
  actor: "user" | "assistant" | "system" = "user",
) {
  return db.$transaction(async (tx) => {
    const before = await tx.plannedMeal.findUniqueOrThrow({ where: { id: mealId } });
    const after = await tx.plannedMeal.update({ where: { id: mealId }, data });
    const log = await tx.changeLog.create({
      data: {
        householdId,
        entity: "planned_meal",
        entityId: mealId,
        before: snapshot(before),
        after: snapshot(after),
        actor,
      },
    });
    return { meal: after, changeId: log.id };
  });
}

const DATE_FIELDS = new Set(["date", "doneAt", "deletedAt"]);

/** Revert one change_log row (planned meals in phase 1; more entities join in phase 3). */
export async function undoChange(householdId: string, changeId: string) {
  const log = await db.changeLog.findFirst({ where: { id: changeId, householdId } });
  if (!log) throw notFound();
  if (log.revertedAt) throw new ApiError(409, "already_reverted");
  if (log.entity !== "planned_meal") throw new ApiError(400, "unsupported_entity");
  const before = (log.before ?? {}) as Record<string, unknown>;
  const data: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(before))
    data[k] = DATE_FIELDS.has(k) && typeof v === "string" ? new Date(v) : v;
  await db.$transaction([
    db.plannedMeal.update({
      where: { id: log.entityId },
      data: data as Prisma.PlannedMealUncheckedUpdateInput,
    }),
    db.changeLog.update({ where: { id: log.id }, data: { revertedAt: new Date() } }),
  ]);
  const meal = await db.plannedMeal.findUniqueOrThrow({ where: { id: log.entityId } });
  return meal;
}
