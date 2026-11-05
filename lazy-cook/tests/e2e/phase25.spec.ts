import { expect, test, type Page } from "@playwright/test";
import { cleanupUsers, createHousehold, createPlanWith, db, signInAs } from "./helpers";

test.afterAll(async () => {
  await cleanupUsers();
  await db.$disconnect();
});

const json = (page: Page, url: string, method = "GET", body?: unknown) =>
  page.evaluate(
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

test("invite: link → sign in → join the same household; a link works once", async ({ browser }) => {
  const a = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const owner = await signInAs(a);
  const household = await createHousehold(owner.id);
  await createPlanWith(household.id, [{ slug: "shakshuka", slot: "breakfast" }]);
  const pa = await a.newPage();
  await pa.goto("/settings");
  await pa.getByRole("button", { name: /دعوت یه نفر دیگه/ }).click();
  const link = await pa.getByLabel("لینک دعوت").inputValue();
  expect(link).toMatch(/\/join\/[a-z0-9]{10}$/);
  const path = new URL(link).pathname;

  // signed out → sign-in keeps where to come back
  const b = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const pb = await b.newPage();
  await pb.goto(path);
  await expect(pb).toHaveURL(
    new RegExp(`/signin\\?next=${encodeURIComponent(path).replace(/[/]/g, "\\/")}`),
  );
  await expect(pb.locator('input[name="next"]')).toHaveValue(path);

  // now signed in (as a brand-new account) → join
  await signInAs(b);
  await pb.goto(path);
  await pb.getByRole("button", { name: "بپیوند" }).click();
  await expect(pb).toHaveURL(/\/today/);
  await expect(pb.getByRole("article", { name: /صبحانه: شکشوکا/ })).toBeVisible();

  // the owner sees two accounts; the link can't be reused
  await pa.reload();
  await expect(pa.getByText("حساب‌های این خونه")).toBeVisible();
  expect(
    await db.householdUser.count({ where: { householdId: household.id, deletedAt: null } }),
  ).toBe(2);
  const c = await browser.newContext();
  await signInAs(c);
  const pc = await c.newPage();
  await pc.goto(path);
  await expect(pc.getByText(/قبلاً استفاده شده/)).toBeVisible();
  await a.close();
  await b.close();
  await c.close();
});

test("sign-in refuses off-site return paths", async ({ page }) => {
  await page.goto("/signin?next=//evil.example/x");
  await expect(page.locator('input[name="next"]')).toHaveValue("/today");
  await page.goto("/signin?next=https://evil.example");
  await expect(page.locator('input[name="next"]')).toHaveValue("/today");
});

test("wrong codes: after 5 the code is thrown away", async ({ page }) => {
  const email = `rl-${Date.now()}@test.local`;
  await db.verificationToken.create({
    data: { identifier: email, token: "hashed-real-code", expires: new Date(Date.now() + 600_000) },
  });
  for (let i = 0; i < 5; i++) {
    await page.goto(
      `/api/auth/callback/nodemailer?email=${encodeURIComponent(email)}&token=00000${i}&callbackUrl=/today`,
    );
    await expect(page).toHaveURL(/error=Verification/);
  }
  await page.goto(
    `/api/auth/callback/nodemailer?email=${encodeURIComponent(email)}&token=123456&callbackUrl=/today`,
  );
  await expect(page).toHaveURL(/error=too_many/);
  await expect(page.getByText(/چند دقیقه صبر کن/)).toBeVisible();
  expect(await db.verificationToken.count({ where: { identifier: email } })).toBe(0);
  await db.authAttempt.deleteMany({ where: { key: `email:${email}` } });
});

test("too many code requests for one email are refused", async ({ page }) => {
  const email = `flood-${Date.now()}@test.local`;
  for (let i = 0; i < 6; i++) {
    await page.goto("/signin");
    await page.getByLabel("ایمیل").fill(email);
    await page.getByRole("button", { name: "کد رو بفرست" }).click();
    if (i < 5) await expect(page).toHaveURL(/signin\/code/);
  }
  await expect(page).toHaveURL(/error=too_many/);
  // also the per-IP counter, so repeated local runs don't hit the 20/hour IP limit
  await db.authAttempt.deleteMany({
    where: { OR: [{ key: `email:${email}` }, { key: { startsWith: "ip:" } }] },
  });
  await db.user.deleteMany({ where: { email } });
});

test("write your own recipe; nutrition is computed; edit it; copy a public one", async ({
  page,
  context,
}) => {
  const user = await signInAs(context);
  await createHousehold(user.id);
  await page.goto("/recipes");
  await page.getByRole("link", { name: /دستور جدید/ }).click();
  await page.getByLabel("اسم غذا").fill("املت گوجه‌ی مامان");
  await page.getByRole("button", { name: "صبحانه" }).click();
  for (const [q, name, amount] of [
    ["تخم", /^تخم‌مرغ/, "۳"],
    ["گوجه", /^گوجه‌فرنگی/, "۲"],
  ] as const) {
    await page.getByRole("button", { name: "ماده اضافه کن" }).click();
    await page.getByLabel("جست‌وجوی ماده").fill(q);
    await page.locator("dialog[open]").getByRole("button", { name }).first().click();
    await page
      .getByLabel(/^مقدار /)
      .last()
      .fill(amount);
  }
  await page.getByLabel("مرحله‌ی ۱", { exact: true }).fill("گوجه رو با کمی روغن بپز.");
  await page.getByRole("button", { name: "یه مرحله‌ی دیگه" }).click();
  await page.getByLabel("مرحله‌ی ۲", { exact: true }).fill("تخم‌مرغ‌ها رو بشکن و هم بزن.");
  await page.getByLabel("تایمر مرحله‌ی ۲").fill("۵");
  await page.getByRole("button", { name: "ذخیره" }).click();

  await expect(page.getByRole("heading", { name: "املت گوجه‌ی مامان" })).toBeVisible();
  await expect(page.getByText(/حدود .* کالری/)).toBeVisible();
  const r = await db.recipe.findFirstOrThrow({ where: { titleFa: "املت گوجه‌ی مامان" } });
  expect(r.source).toBe("user_manual");
  expect(r.tags).toContain("no_oven");
  expect((r.nutritionPerServing as { kcal: number }).kcal).toBeGreaterThan(80);
  expect(r.steps).toEqual([
    { text: "گوجه رو با کمی روغن بپز." },
    { text: "تخم‌مرغ‌ها رو بشکن و هم بزن.", timer_seconds: 300 },
  ]);

  await page.getByRole("link", { name: "ویرایش" }).click();
  await page.getByLabel("اسم غذا").fill("املت گوجه");
  await page.getByRole("button", { name: "ذخیره" }).click();
  await expect(page.getByRole("heading", { name: "املت گوجه" })).toBeVisible();
  await page.goto("/recipes?mine=1");
  await expect(page.getByRole("link", { name: /املت گوجه/ })).toBeVisible();
  await expect(page.getByRole("link", { name: /شکشوکا/ })).toHaveCount(0);

  // a public recipe can't be edited, only copied
  const shak = await db.recipe.findUniqueOrThrow({ where: { slug: "shakshuka" } });
  expect((await json(page, `/api/recipes/${shak.id}`, "DELETE")).status).toBe(404);
  await page.goto(`/recipes/${shak.id}`);
  await page.getByRole("button", { name: "نسخه‌ی خودم رو بساز" }).click();
  await expect(page).toHaveURL(/\/edit$/);
  await expect(page.getByLabel("اسم غذا")).toHaveValue("شکشوکا (نسخه‌ی من)");
  const copy = await db.recipe.findFirstOrThrow({
    where: { parentRecipeId: shak.id, ownerHouseholdId: { not: null } },
  });
  expect(copy.source).toBe("user_manual");
});

test("wishlist: not in the current plan, in the next one (acceptance #7, planning half)", async ({
  page,
  context,
}) => {
  const user = await signInAs(context);
  const household = await createHousehold(user.id);
  const current = await createPlanWith(household.id, [{ slug: "shakshuka", slot: "breakfast" }]);
  const paella = await db.recipe.findUniqueOrThrow({ where: { slug: "vegetable_paella" } });
  await page.goto(`/recipes/${paella.id}`);
  await page.getByRole("button", { name: "برای برنامه‌ی بعدی" }).click();
  await expect(page.getByRole("button", { name: "تو لیست برنامه‌ی بعدیه" })).toBeVisible();
  expect(
    await db.plannedMeal.count({ where: { mealPlanId: current.id, recipeId: paella.id } }),
  ).toBe(0);

  await page.goto("/plan/new");
  await page.getByRole("button", { name: /خودت بچین/ }).click();
  await expect(page).toHaveURL(/\/today/);
  const next = await db.mealPlan.findFirstOrThrow({
    where: { householdId: household.id, status: "active" },
  });
  const meal = await db.plannedMeal.findFirstOrThrow({
    where: { mealPlanId: next.id, recipeId: paella.id },
  });
  await page.goto("/wishlist");
  await expect(page.getByText(/تو برنامه:/)).toBeVisible();

  // cooking it ticks the wish off
  await json(page, `/api/planned-meals/${meal.id}`, "PATCH", { action: "status", status: "done" });
  expect(
    (await db.wishlistItem.findFirstOrThrow({ where: { householdId: household.id } })).doneAt,
  ).not.toBeNull();
});

test("export all data, then delete the account for good", async ({ page, context }) => {
  const user = await signInAs(context);
  const household = await createHousehold(user.id);
  await createPlanWith(household.id, [{ slug: "shakshuka", slot: "breakfast" }]);
  await page.goto("/settings");
  const exported = await page.evaluate(() => fetch("/api/export").then((r) => r.json()));
  expect(exported.user.id).toBe(user.id);
  expect(exported.households[0].household.mealPlans).toHaveLength(1);
  expect(
    exported.households[0].household.members.map((m: { displayName: string }) => m.displayName),
  ).toContain("رومینا");

  await page.getByRole("button", { name: "حذف کامل حساب" }).click();
  await page.getByLabel(/برای تأیید بنویس/).fill("حذف");
  await page.getByRole("button", { name: "برای همیشه حذف کن" }).click();
  await expect(page).toHaveURL(/\/$/);
  expect(await db.user.findUnique({ where: { id: user.id } })).toBeNull();
  expect(await db.household.findUnique({ where: { id: household.id } })).toBeNull();
});

test("deleting the owner of a shared household hands it over", async ({ browser }) => {
  const a = await browser.newContext();
  const b = await browser.newContext();
  const owner = await signInAs(a);
  const household = await createHousehold(owner.id);
  const member = await signInAs(b);
  await db.householdUser.create({
    data: { householdId: household.id, userId: member.id, role: "member" },
  });
  const pa = await a.newPage();
  await pa.goto("/settings");
  expect((await json(pa, "/api/account", "DELETE", { confirm: "حذف" })).status).toBe(200);
  const h = await db.household.findUniqueOrThrow({ where: { id: household.id } });
  expect(h.ownerUserId).toBe(member.id);
  await a.close();
  await b.close();
});
