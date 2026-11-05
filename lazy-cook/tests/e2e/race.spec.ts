import { expect, test } from "@playwright/test";
import { cleanupUsers, createHousehold, createPlanWith, db, signInAs } from "./helpers";

test.afterAll(async () => {
  await cleanupUsers();
  await db.$disconnect();
});

// Checks the end state only: the lock in regenerateShopping prevents transient duplicates, and the
// duplicate clean-up heals any that slipped in before — this test can't tell those two apart.
test("concurrent rebuilds leave no duplicate rows on the shopping list", async ({
  page,
  context,
}) => {
  const user = await signInAs(context);
  const household = await createHousehold(user.id);
  await createPlanWith(household.id, [
    { slug: "shakshuka", slot: "breakfast", day: 1 },
    { slug: "mujaddara", slot: "lunch", day: 1 },
  ]);
  // eight simultaneous page loads = eight simultaneous rebuilds
  await page.goto("/settings");
  await page.evaluate(() =>
    Promise.all(Array.from({ length: 8 }, () => fetch("/shopping").then((r) => r.text()))),
  );
  const rows = await db.shoppingItem.findMany({
    where: { householdId: household.id, deletedAt: null, checkedAt: null },
  });
  const keys = rows.map((r) => `${r.ingredientId ?? r.customName}|${r.unit}`);
  expect(rows.length).toBeGreaterThan(5);
  expect(new Set(keys).size).toBe(keys.length);
});
