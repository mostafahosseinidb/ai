import { expect, test } from "@playwright/test";
import { addDays, createHousehold, db, signInAs, todayIn } from "./helpers";

test.afterAll(async () => {
  await db.user.deleteMany({ where: { email: { endsWith: "@test.local" } } });
  await db.$disconnect();
});

test("landing page is Persian and right-to-left", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.locator("html")).toHaveAttribute("lang", "fa");
  await expect(page.getByRole("heading", { name: "آشپز تنبل مهاجر" })).toBeVisible();
  await page.getByRole("link", { name: "شروع کنیم" }).click();
  await expect(page).toHaveURL(/\/signin/);
});

test("signed-out users are sent to sign in; no household → onboarding", async ({
  page,
  context,
}) => {
  await page.goto("/today");
  await expect(page).toHaveURL(/\/signin/);
  await signInAs(context);
  await page.goto("/today");
  await expect(page).toHaveURL(/\/onboarding/);
});

test("onboarding wizard → autofilled 8-day plan with no oven dish (acceptance #1)", async ({
  page,
  context,
}) => {
  const user = await signInAs(context);
  await page.goto("/onboarding");

  // 1. where
  await page.getByLabel("کشور").selectOption("AM");
  await expect(page.getByText("ارمنی، روسی")).toBeVisible();
  await page.getByRole("button", { name: "بعدی" }).click();
  // 2. who — Persian digits are accepted in number fields
  await page.getByLabel("اسم یا لقب").fill("مصطفی");
  await page.getByLabel("قد (سانتی‌متر)").fill("۱۸۵");
  await page.getByRole("button", { name: "بعدی" }).click();
  // 3. restrictions: red meat ≤ 2/week, and a free-text dislike
  await page.getByRole("button", { name: "گوشت قرمز" }).click();
  await page.getByLabel("غذاهایی که دوست ندارید").fill("اوورنایت اوتس");
  await page.getByLabel("غذاهایی که دوست ندارید").blur();
  await page.getByRole("button", { name: "بعدی" }).click();
  // 4. kitchen: no oven
  await expect(page.getByText("فر نداری؟")).toBeVisible();
  await page.getByRole("button", { name: "بعدی" }).click();
  // 5. style
  await page.getByRole("button", { name: "تموم! بریم سراغ برنامه" }).click();

  await expect(page).toHaveURL(/\/plan\/new/);
  const member = await db.member.findFirstOrThrow({
    where: { household: { ownerUserId: user.id } },
    include: { preferences: true },
  });
  expect(member.heightCm).toBe(185);
  expect(member.preferences.map((p) => [p.type, p.value, p.limitPerWeek])).toEqual(
    expect.arrayContaining([
      ["limit", "red_meat", 2],
      ["dislike", "اوورنایت اوتس", null],
    ]),
  );

  await page.getByRole("button", { name: /خودت بچین/ }).click();
  await expect(page).toHaveURL(/\/today/);
  await expect(page.getByRole("article").first()).toBeVisible();

  const meals = await db.plannedMeal.findMany({
    where: { mealPlan: { household: { ownerUserId: user.id } } },
    include: { recipe: true },
  });
  expect(meals.length).toBeGreaterThanOrEqual(20);
  for (const m of meals) {
    expect(m.recipe?.requiresEquipment ?? []).not.toContain("oven");
    expect(m.recipe?.slug).not.toBe("overnight_oats");
  }
});

test("today: tick, dislike with a reason, eaten out with undo", async ({ page, context }) => {
  const user = await signInAs(context);
  const household = await createHousehold(user.id);
  const today = todayIn();
  const shakshuka = await db.recipe.findUniqueOrThrow({ where: { slug: "shakshuka" } });
  const fava = await db.recipe.findUniqueOrThrow({ where: { slug: "greek_fava" } });
  const plan = await db.mealPlan.create({
    data: {
      householdId: household.id,
      startDate: new Date(`${today}T00:00:00Z`),
      days: 3,
      status: "active",
    },
  });
  await db.plannedMeal.createMany({
    data: [
      {
        mealPlanId: plan.id,
        date: new Date(`${today}T00:00:00Z`),
        slot: "breakfast",
        recipeId: shakshuka.id,
      },
      {
        mealPlanId: plan.id,
        date: new Date(`${today}T00:00:00Z`),
        slot: "lunch",
        recipeId: fava.id,
      },
    ],
  });

  await page.goto("/today");
  const breakfast = page.getByRole("article", { name: /صبحانه/ });
  await breakfast.getByRole("button", { name: "خوردیم" }).click();
  await expect(breakfast.getByRole("button", { name: "خوردیم" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await page.reload();
  await expect(
    page.getByRole("article", { name: /صبحانه/ }).getByRole("button", { name: "خوردیم" }),
  ).toHaveAttribute("aria-pressed", "true");

  await breakfast.getByRole("button", { name: "دوست نداشتیم" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "طعم" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "ثبت" }).click();
  await expect(breakfast.getByRole("button", { name: "دوست نداشتیم" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  const fb = await db.mealFeedback.findFirstOrThrow({
    where: { plannedMeal: { mealPlanId: plan.id }, deletedAt: null },
  });
  expect(fb).toMatchObject({ rating: "down", reasons: ["taste"] });

  const lunch = page.getByRole("article", { name: /ناهار/ });
  await lunch.getByRole("button", { name: "گزینه‌های بیشتر" }).click();
  await page
    .getByRole("dialog", { name: "فاوای یونانی" })
    .getByRole("button", { name: /بیرون خوردیم/ })
    .click();
  await page
    .locator("dialog[open]")
    .getByPlaceholder(/چی خوردین/)
    .fill("سالاد سزار و مرغ");
  await page
    .getByRole("dialog", { name: "بیرون خوردیم" })
    .getByRole("button", { name: "ثبت" })
    .click();
  await expect(page.getByText("نوش جان!")).toBeVisible();
  await expect(lunch.getByText("سالاد سزار و مرغ")).toBeVisible();
  await page.getByRole("button", { name: "برگردون" }).click();
  await expect(lunch.getByText("سالاد سزار و مرغ")).toHaveCount(0);
  const restored = await db.plannedMeal.findFirstOrThrow({
    where: { mealPlanId: plan.id, slot: "lunch" },
  });
  expect(restored.status).toBe("planned");
});

test("tonight's prep task for tomorrow's soaked beans (acceptance #8)", async ({
  page,
  context,
}) => {
  const user = await signInAs(context);
  const household = await createHousehold(user.id);
  await page.goto("/plan/new");
  await page.getByRole("button", { name: /برنامه‌ی خالی/ }).click();
  await expect(page).toHaveURL(/\/plan$/);

  const tomorrow = addDays(todayIn(), 1);
  const day = page.locator(`section[id="${tomorrow}"]`);
  await day
    .getByRole("listitem")
    .filter({ hasText: "ناهار" })
    .getByRole("button", { name: "اضافه کن" })
    .click();
  await page
    .getByRole("dialog")
    .getByPlaceholder(/جست‌وجو/)
    .fill("قرمه");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: /قرمه‌سبزی/ })
    .click();
  await expect(day.getByRole("link", { name: "قرمه‌سبزی" })).toBeVisible();

  await page.goto("/today");
  await expect(page.getByText("کار امشب")).toBeVisible();
  await expect(page.getByText(/لوبیا.*خیس/)).toBeVisible();
  const task = await db.prepTask.findFirstOrThrow({ where: { householdId: household.id } });
  expect(task.dueDate.toISOString().slice(0, 10)).toBe(todayIn());
});

test("recipe scales from 2 people to 1 with whole eggs (acceptance #9) and digits are Persian", async ({
  page,
  context,
}) => {
  const user = await signInAs(context);
  await createHousehold(user.id);
  const r = await db.recipe.findUniqueOrThrow({
    where: { slug: "shakshuka" },
    include: { ingredients: { include: { ingredient: true } } },
  });
  const eggs = r.ingredients.find((i) => i.ingredient.canonicalName === "egg")!;
  await page.goto(`/recipes/${r.id}`);
  await page.getByRole("button", { name: "یک نفر" }).click();
  const expected = Math.max(1, Math.round(Number(eggs.quantity) / r.baseServings));
  await expect(page.getByTestId("qty-egg")).toHaveText(`${"۰۱۲۳۴۵۶۷۸۹"[expected]} عدد`);

  // no ASCII digits in visible Persian text
  const text = await page.locator("main").evaluate((el) => {
    const out: string[] = [];
    const walk = (n: Node) => {
      if (
        n.nodeType === 1 &&
        ((n as Element).getAttribute("dir") === "ltr" ||
          (n as Element).getAttribute("dir") === "auto")
      )
        return;
      if (n.nodeType === 3) out.push(n.textContent ?? "");
      n.childNodes.forEach(walk);
    };
    walk(el);
    return out.join(" ");
  });
  expect(text).not.toMatch(/[0-9]/);
});

test("the library hides oven recipes for a kitchen without an oven", async ({ page, context }) => {
  const user = await signInAs(context);
  await createHousehold(user.id, { equipment: [] });
  await page.goto("/recipes");
  await expect(page.getByRole("link", { name: /شکشوکا/ })).toBeVisible();
  await expect(page.getByRole("link", { name: /راتاتویی/ })).toHaveCount(0);
  await page.getByRole("link", { name: "نشونم بده" }).click();
  await expect(page.getByRole("link", { name: /راتاتویی/ })).toBeVisible();
});

test("tab bar runs right-to-left and the manifest is RTL Persian", async ({
  page,
  context,
  request,
}) => {
  const user = await signInAs(context);
  await createHousehold(user.id);
  await page.goto("/recipes");
  const nav = page.getByRole("navigation", { name: "ناوبری اصلی" });
  const today = await nav.getByRole("link", { name: "امروز" }).boundingBox();
  const more = await nav.getByRole("link", { name: "بیشتر" }).boundingBox();
  expect(today!.x).toBeGreaterThan(more!.x);
  await expect(nav.getByRole("link", { name: "بیشتر" })).toHaveAttribute("aria-current", "page");

  const manifest = await (await request.get("/manifest.webmanifest")).json();
  expect(manifest).toMatchObject({ dir: "rtl", lang: "fa", display: "standalone" });
});
