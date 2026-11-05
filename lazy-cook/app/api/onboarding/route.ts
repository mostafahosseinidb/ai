import { NextResponse } from "next/server";
import { auth } from "@/auth";
import { db } from "@/lib/db";
import { onboardingInput } from "@/lib/onboarding-schema";
import { estimateDailyKcal, portionFactor } from "@/lib/domain/nutrition";
import { COUNTRIES } from "@/lib/countries";
import { ZodError } from "zod";

/** Create or update the household from the onboarding wizard (also used to edit it later). */
export async function PUT(req: Request) {
  const session = await auth();
  const userId = session?.user?.id;
  if (!userId) return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  let input;
  try {
    input = onboardingInput.parse(await req.json());
  } catch (e) {
    return NextResponse.json(
      { error: "invalid_input", issues: e instanceof ZodError ? e.issues : undefined },
      { status: 400 },
    );
  }
  const country = COUNTRIES.find((c) => c.code === input.country_code.toUpperCase());
  const year = new Date().getFullYear();

  const householdId = await db.$transaction(async (tx) => {
    await tx.user.update({
      where: { id: userId },
      data: {
        countryCode: input.country_code.toUpperCase(),
        city: input.city ?? null,
        timezone: input.timezone || country?.timezone || "UTC",
        currency: country?.currency ?? null,
        onboardingCompletedAt: new Date(),
      },
    });
    const h = input.household;
    const data = {
      kitchenEquipment: h.kitchen_equipment,
      burners: h.burners,
      activeMealSlots: h.active_meal_slots,
      cookingDaysPerWeek: h.cooking_days_per_week,
      batchPrepWeekday: h.batch_prep_weekday ?? null,
      iranianFoodFrequencyDays: h.iranian_food_frequency_days,
      weeklyBudget: h.weekly_budget ?? null,
      bedtime: h.bedtime ?? null,
    };
    const link = await tx.householdUser.findFirst({
      where: { userId, deletedAt: null },
      orderBy: { createdAt: "asc" },
    });
    let householdId: string;
    if (link) {
      householdId = link.householdId;
      await tx.household.update({ where: { id: householdId }, data });
    } else {
      const created = await tx.household.create({
        data: { ...data, name: "خونه‌ی ما", ownerUserId: userId },
      });
      householdId = created.id;
      await tx.householdUser.create({ data: { householdId, userId, role: "owner" } });
    }

    const existing = await tx.member.findMany({ where: { householdId, deletedAt: null } });
    const keep = new Set(input.members.map((m) => m.id).filter(Boolean));
    for (const m of existing)
      if (!keep.has(m.id))
        await tx.member.update({ where: { id: m.id }, data: { deletedAt: new Date() } });

    for (const [position, m] of input.members.entries()) {
      const est = {
        sex: m.sex ?? null,
        birthYear: m.birth_year ?? null,
        heightCm: m.height_cm ?? null,
        weightKg: m.weight_kg ?? null,
        activity: m.activity_level,
        goal: m.goal,
        currentYear: year,
      };
      const memberData = {
        displayName: m.display_name,
        birthYear: m.birth_year ?? null,
        sex: m.sex ?? null,
        heightCm: m.height_cm ?? null,
        weightKg: m.weight_kg ?? null,
        goal: m.goal,
        activityLevel: m.activity_level,
        portionFactor: m.portion_factor ?? portionFactor(est),
        dailyKcalEstimate: estimateDailyKcal(est),
        position,
      };
      const own = m.id && existing.find((e) => e.id === m.id);
      const row = own
        ? await tx.member.update({ where: { id: own.id }, data: memberData })
        : await tx.member.create({ data: { ...memberData, householdId } });
      await tx.memberPreference.deleteMany({ where: { memberId: row.id } });
      if (m.preferences.length) {
        await tx.memberPreference.createMany({
          data: m.preferences.map((p) => ({
            memberId: row.id,
            type: p.type,
            value: p.value,
            limitPerWeek: p.type === "limit" ? (p.limit_per_week ?? null) : null,
          })),
        });
      }
    }
    return householdId;
  });

  return NextResponse.json({ ok: true, household_id: householdId });
}
