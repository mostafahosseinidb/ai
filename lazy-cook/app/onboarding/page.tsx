import { requireUser } from "@/lib/session";
import { db } from "@/lib/db";
import { fa } from "@/lib/i18n/fa";
import { portionFactor } from "@/lib/domain/nutrition";
import { Wizard, type WizardInitial } from "./wizard";

export const metadata = { title: fa.onboarding.title };

export default async function OnboardingPage() {
  const user = await requireUser();
  const link = await db.householdUser.findFirst({
    where: { userId: user.id, deletedAt: null },
    include: {
      household: {
        include: {
          members: {
            where: { deletedAt: null },
            include: { preferences: { where: { deletedAt: null } } },
            orderBy: { position: "asc" },
          },
        },
      },
    },
  });
  const h = link?.household;
  const initial: WizardInitial = {
    country_code: user.countryCode ?? "",
    city: user.city ?? "",
    members: h?.members.length
      ? h.members.map((m) => ({
          id: m.id,
          display_name: m.displayName,
          birth_year: m.birthYear,
          sex: m.sex,
          height_cm: m.heightCm,
          weight_kg: m.weightKg ? Number(m.weightKg) : null,
          goal: m.goal,
          activity_level: m.activityLevel,
          // only an explicit override is sent back; otherwise the server recomputes it
          portion_factor:
            Number(m.portionFactor) ===
            portionFactor({
              sex: m.sex,
              birthYear: m.birthYear,
              heightCm: m.heightCm,
              weightKg: m.weightKg ? Number(m.weightKg) : null,
              activity: m.activityLevel,
              goal: m.goal,
            })
              ? null
              : Number(m.portionFactor),
          preferences: m.preferences.map((p) => ({
            type: p.type,
            value: p.value,
            limit_per_week: p.limitPerWeek,
          })),
        }))
      : [
          {
            display_name: user.name ?? "",
            goal: "maintain",
            activity_level: "light",
            preferences: [],
          },
        ],
    household: h
      ? {
          kitchen_equipment: h.kitchenEquipment as WizardInitial["household"]["kitchen_equipment"],
          burners: h.burners,
          active_meal_slots: h.activeMealSlots as WizardInitial["household"]["active_meal_slots"],
          cooking_days_per_week: h.cookingDaysPerWeek,
          batch_prep_weekday: h.batchPrepWeekday,
          iranian_food_frequency_days: h.iranianFoodFrequencyDays,
          weekly_budget: h.weeklyBudget ? Number(h.weeklyBudget) : null,
          bedtime: h.bedtime,
        }
      : {
          kitchen_equipment: ["freezer"],
          burners: 2,
          active_meal_slots: ["breakfast", "lunch", "dinner"],
          cooking_days_per_week: 4,
          batch_prep_weekday: null,
          iranian_food_frequency_days: 7,
          weekly_budget: null,
          bedtime: "23:30",
        },
  };
  return <Wizard initial={initial} editing={!!user.onboardingCompletedAt} />;
}
