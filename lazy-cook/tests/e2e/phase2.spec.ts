import { expect, test, type Page } from "@playwright/test";
import {
  addDays,
  createHousehold,
  createPlanWith,
  db,
  ingredientId,
  joinHousehold,
  signInAs,
  todayIn,
} from "./helpers";
import { cleanupUsers } from "./helpers";

test.afterAll(async () => {
  await cleanupUsers();
  await db.$disconnect();
});

async function api(page: Page, url: string, method: string, body?: unknown) {
  return page.evaluate(
    async ([u, m, b]) => {
      const r = await fetch(u as string, {
        method: m as string,
        headers: { "content-type": "application/json" },
        body: b ? JSON.stringify(b) : undefined,
      });
      return { status: r.status, json: await r.json().catch(() => null) };
    },
    [url, method, body] as const,
  );
}

test("pantry: add by Persian or local name, expiry colours green / yellow / red", async ({
  page,
  context,
}) => {
  const user = await signInAs(context);
  await createHousehold(user.id);
  await page.goto("/pantry");
  await expect(page.getByText(/یخچالت خالیه/)).toBeVisible();

  // search by Russian shelf name (household is in Armenia)
  await page.getByRole("button", { name: "اضافه کن" }).click();
  await page.getByLabel("جست‌وجوی ماده").fill("Молоко");
  await page.locator("dialog[open]").getByRole("button", { name: /^شیر/ }).first().click();
  await page.locator("dialog[open]").getByLabel("مقدار").fill("۱۰۰۰");
  await page.locator("dialog[open]").getByRole("button", { name: "اضافه کن" }).click();
  const milk = page.getByRole("button", { name: /^شیر/ });
  await expect(milk).toBeVisible();
  await expect(milk.locator("[data-tone]")).toHaveAttribute("data-tone", "ok");

  const today = todayIn();
  await api(page, "/api/pantry", "POST", {
    custom_name: "سس خونگی",
    quantity: 1,
    unit: "piece",
    location: "fridge",
    package_expiry_date: addDays(today, 2),
  });
  await api(page, "/api/pantry", "POST", {
    custom_name: "ماست مونده",
    quantity: 1,
    unit: "piece",
    location: "fridge",
    package_expiry_date: addDays(today, -1),
  });
  await page.reload();
  await expect(
    page.getByRole("button", { name: /سس خونگی/ }).locator("[data-tone]"),
  ).toHaveAttribute("data-tone", "soon");
  await expect(
    page.getByRole("button", { name: /ماست مونده/ }).locator("[data-tone]"),
  ).toHaveAttribute("data-tone", "expired");
  await expect(page.getByText(/داره? خراب می‌شه|دارن خراب می‌شه/)).toBeVisible();
});

test("opened jar: 72 hours in the fridge (acceptance #5, pantry half)", async ({
  page,
  context,
}) => {
  const user = await signInAs(context);
  await createHousehold(user.id);
  await page.goto("/pantry");
  const res = await api(page, "/api/pantry", "POST", {
    ingredient_id: await ingredientId("eggplant_caviar"),
    quantity: 1,
    unit: "can",
    opened: true,
  });
  expect(res.status).toBe(200);
  expect(res.json.item.location).toBe("fridge");
  expect(res.json.item.computedExpiryDate.slice(0, 10)).toBe(addDays(todayIn(), 3));
});

test("shopping list follows the plan immediately, minus the pantry (phase 2 acceptance)", async ({
  page,
  context,
}) => {
  const user = await signInAs(context);
  const household = await createHousehold(user.id);
  const plan = await createPlanWith(household.id, [
    { slug: "shakshuka", slot: "breakfast", day: 1 },
  ]);
  await page.goto("/plan");
  // any plan change regenerates the list — here: add a meal through the UI
  const day2 = page.locator(`section[id="${addDays(todayIn(), 2)}"]`);
  await day2
    .getByRole("listitem")
    .filter({ hasText: "ناهار" })
    .getByRole("button", { name: "اضافه کن" })
    .click();
  await page
    .locator("dialog[open]")
    .getByPlaceholder(/جست‌وجو/)
    .fill("مجدره");
  await page.locator("dialog[open]").getByRole("button", { name: /مجدره/ }).click();
  await expect(day2.getByRole("link", { name: "مجدره" })).toBeVisible();

  await page.goto("/shopping");
  const required = page.locator("section", { has: page.getByRole("heading", { name: "ضروری" }) });
  await expect(required.getByText(/^تخم‌مرغ/)).toBeVisible(); // shakshuka
  await expect(required.getByText(/^عدس/).first()).toBeVisible(); // mujaddara
  await expect(required.getByText(/Яйц/).first()).toBeVisible(); // local name (AM: Armenian · Russian)
  await expect(page.getByText(/^آب$/)).toHaveCount(0);

  // eggs in the pantry → eggs leave the list
  await api(page, "/api/pantry", "POST", {
    ingredient_id: await ingredientId("egg"),
    quantity: 12,
    unit: "piece",
  });
  await page.reload();
  await expect(required.getByText(/^تخم‌مرغ/)).toHaveCount(0);

  // remove mujaddara from the plan → its lentils leave the list
  const meal = await db.plannedMeal.findFirstOrThrow({
    where: { mealPlanId: plan.id, recipe: { slug: "mujaddara" } },
  });
  await api(page, `/api/planned-meals/${meal.id}`, "DELETE");
  await page.reload();
  await expect(page.getByText(/^عدس/)).toHaveCount(0);
});

test("«خریدم» moves the item into the pantry, with undo", async ({ page, context }) => {
  const user = await signInAs(context);
  const household = await createHousehold(user.id);
  await createPlanWith(household.id, [{ slug: "shakshuka", slot: "breakfast", day: 1 }]);
  await page.goto("/shopping");
  await page.getByRole("button", { name: "خریدم: تخم‌مرغ" }).click();
  await page.locator("dialog[open]").getByLabel(/مقدار/).fill("۱۰");
  await page.locator("dialog[open]").getByRole("button", { name: "خریدم" }).click();
  await expect(page.getByText("تخم‌مرغ رفت تو موجودی.")).toBeVisible();
  const lot = await db.pantryItem.findFirstOrThrow({
    where: { householdId: household.id, ingredient: { canonicalName: "egg" }, deletedAt: null },
  });
  expect(Number(lot.quantity)).toBe(10);
  expect(lot.location).toBe("fridge");

  await page.getByRole("button", { name: "برگردون" }).click();
  await expect(page.getByRole("button", { name: "خریدم: تخم‌مرغ" })).toBeVisible();
  expect(
    (await db.pantryItem.findUniqueOrThrow({ where: { id: lot.id } })).deletedAt,
  ).not.toBeNull();
});

test("ticking a meal takes its ingredients out of the pantry; un-ticking puts them back", async ({
  page,
  context,
}) => {
  const user = await signInAs(context);
  const household = await createHousehold(user.id);
  await createPlanWith(household.id, [{ slug: "shakshuka", slot: "breakfast" }]);
  await page.goto("/today");
  await api(page, "/api/pantry", "POST", {
    ingredient_id: await ingredientId("egg"),
    quantity: 10,
    unit: "piece",
  });
  const card = page.getByRole("article", { name: /صبحانه/ });
  await card.getByRole("button", { name: "خوردیم" }).click();
  await expect(card.getByRole("button", { name: "خوردیم" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  const eggs = () =>
    db.pantryItem.findFirstOrThrow({
      where: { householdId: household.id, ingredient: { canonicalName: "egg" } },
    });
  await expect.poll(async () => Number((await eggs()).quantity)).toBeLessThan(10);
  await card.getByRole("button", { name: "خوردیم" }).click();
  await expect.poll(async () => Number((await eggs()).quantity)).toBe(10);
});

test("the list is shared live between household members", async ({ browser }) => {
  const a = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const b = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const owner = await signInAs(a);
  const household = await createHousehold(owner.id);
  await joinHousehold(b, household.id);
  await createPlanWith(household.id, [{ slug: "greek_salad", slot: "lunch", day: 1 }]);
  const pa = await a.newPage();
  const pb = await b.newPage();
  await pa.goto("/shopping");
  await pb.goto("/shopping");
  const item = "خریدم: خیار";
  await expect(pb.getByRole("button", { name: item })).toBeVisible();
  await pa.getByRole("button", { name: item }).click();
  await pa.locator("dialog[open]").getByRole("button", { name: "خریدم" }).click();
  await expect(pb.getByRole("button", { name: item })).toHaveCount(0, { timeout: 12_000 });
  await a.close();
  await b.close();
});

test("copy as plain text for a messenger", async ({ page, context }) => {
  const user = await signInAs(context);
  const household = await createHousehold(user.id);
  await createPlanWith(household.id, [{ slug: "ghormeh_sabzi", slot: "lunch", day: 1 }]);
  await page.goto("/shopping");
  const text = await page.evaluate(() => fetch("/api/shopping?format=text").then((r) => r.text()));
  expect(text).toContain("🛒 لیست خرید");
  expect(text).toContain("ضروری:");
  expect(text).toContain("— گوشت و مرغ");
  expect(text).toMatch(/▫️ .*\(.+\) — /); // name (local name) — package hint
});
