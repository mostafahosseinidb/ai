import { describe, expect, it } from "vitest";
import {
  derivePrepTasks,
  matchesTextDislike,
  validatePlan,
  type MemberFacts,
  type RuleContext,
} from "@/lib/domain/rules";
import { autofillPlan } from "@/lib/domain/autofill";
import { addDaysISO } from "@/lib/i18n/jalali";
import { facts, factsById } from "./seed-fixtures";

const START = "2026-11-07";
const ALL_EQUIPMENT_BUT_OVEN = ["microwave", "blender", "toaster", "freezer"];

function ctx(members: MemberFacts[], extra: Partial<RuleContext> = {}): RuleContext {
  return {
    equipment: ALL_EQUIPMENT_BUT_OVEN,
    members,
    recipes: factsById,
    startDate: START,
    ...extra,
  };
}

const plain: MemberFacts = { id: "m1", displayName: "رومینا", preferences: [] };

function fill(c: RuleContext, days = 8, slots = ["breakfast", "lunch", "dinner"], seed?: number) {
  return autofillPlan({
    startDate: START,
    days,
    slots,
    cookingDaysPerWeek: 4,
    iranianEveryDays: 7,
    recipes: facts,
    ctx: c,
    seed,
  });
}

describe("hard rules", () => {
  it("flags oven recipes when there is no oven", () => {
    const v = validatePlan(
      [{ date: START, slot: "dinner", recipeId: "ratatouille" }],
      ctx([plain]),
    );
    expect(v.map((x) => x.code)).toContain("equipment");
  });

  it("flags forbidden ingredients, unless a substitution is given for that member", () => {
    const vegan: MemberFacts = {
      id: "m2",
      displayName: "مصطفی",
      preferences: [{ type: "diet", value: "vegan" }],
    };
    const meal = { date: START, slot: "breakfast", recipeId: "shakshuka" };
    expect(validatePlan([meal], ctx([vegan])).map((x) => x.code)).toContain("forbidden_ingredient");
    expect(
      validatePlan([{ ...meal, substitutions: { m2: "بدون تخم‌مرغ، با نخود" } }], ctx([vegan])),
    ).toEqual([]);
  });

  it("lactose intolerance blocks yogurt dishes", () => {
    const m: MemberFacts = {
      id: "m3",
      displayName: "x",
      preferences: [{ type: "intolerance", value: "lactose" }],
    };
    expect(
      validatePlan([{ date: START, slot: "snack", recipeId: "tzatziki" }], ctx([m])).length,
    ).toBeGreaterThan(0);
  });

  it("no main dish two days in a row unless it is the leftover", () => {
    const a = { date: START, slot: "dinner", recipeId: "ghormeh_sabzi" };
    const b = { date: addDaysISO(START, 1), slot: "lunch", recipeId: "ghormeh_sabzi" };
    expect(validatePlan([a, b], ctx([plain])).map((x) => x.code)).toContain("repeat_consecutive");
    expect(
      validatePlan([a, { ...b, leftoverOf: { date: START, slot: "dinner" } }], ctx([plain])),
    ).toEqual([]);
  });

  it("recipes rated down in the last 90 days are blocked", () => {
    const c = ctx([plain], { dislikedRecipes: new Map([["overnight_oats", "2026-10-01"]]) });
    expect(
      validatePlan([{ date: START, slot: "breakfast", recipeId: "overnight_oats" }], c).map(
        (x) => x.code,
      ),
    ).toContain("disliked");
    const later = ctx([plain], { dislikedRecipes: new Map([["overnight_oats", "2026-01-01"]]) });
    expect(
      validatePlan([{ date: START, slot: "breakfast", recipeId: "overnight_oats" }], later),
    ).toEqual([]);
  });

  it("free-text dislikes match Persian and English names", () => {
    const r = factsById.get("overnight_oats")!;
    expect(
      matchesTextDislike(r, {
        ...plain,
        preferences: [{ type: "dislike", value: "اوورنایت اوتس" }],
      }),
    ).toBeTruthy();
    expect(
      matchesTextDislike(r, {
        ...plain,
        preferences: [{ type: "dislike", value: "Overnight Oats" }],
      }),
    ).toBeTruthy();
    expect(
      matchesTextDislike(r, { ...plain, preferences: [{ type: "dislike", value: "جگر" }] }),
    ).toBeNull();
  });

  it("weekly limits count every rolling 7-day window", () => {
    const m: MemberFacts = {
      id: "m1",
      displayName: "x",
      preferences: [{ type: "limit", value: "red_meat", limitPerWeek: 2 }],
    };
    const meals = [0, 2, 4].map((d) => ({
      date: addDaysISO(START, d),
      slot: "dinner",
      recipeId: "pan_kabab_tabei",
    }));
    expect(validatePlan(meals, ctx([m])).map((x) => x.code)).toContain("weekly_limit");
    const spaced = [0, 3, 7].map((d) => ({
      date: addDaysISO(START, d),
      slot: "dinner",
      recipeId: "pan_kabab_tabei",
    }));
    expect(validatePlan(spaced, ctx([m]))).toEqual([]);
  });

  it("expiring pantry items must be used before they expire", () => {
    const c = ctx([plain], {
      pantry: [
        {
          ingredientId: null,
          ingredientKey: "eggplant_caviar",
          expiry: addDaysISO(START, 2),
          label: "خاویار بادمجان",
        },
      ],
    });
    expect(
      validatePlan([{ date: START, slot: "lunch", recipeId: "shakshuka" }], c).map((x) => x.code),
    ).toContain("expiring_unused");
  });
});

describe("autofill obeys the hard rules (acceptance #1–#3 at domain level)", () => {
  it("no oven → no oven recipe in an 8-day plan", () => {
    for (const seed of [1, 2, 3, 4, 5]) {
      const plan = fill(ctx([plain]), 8, ["breakfast", "lunch", "dinner"], seed);
      expect(plan.length).toBeGreaterThanOrEqual(20);
      for (const m of plan)
        expect(factsById.get(m.recipeId!)!.requiresEquipment).not.toContain("oven");
    }
  });

  it("red meat ≤ 2 in every 7 consecutive days for the limited member", () => {
    const m: MemberFacts = {
      id: "m1",
      displayName: "x",
      preferences: [{ type: "limit", value: "red_meat", limitPerWeek: 2 }],
    };
    for (const seed of [1, 2, 3, 4, 5, 6]) {
      const plan = fill(
        ctx([m, { ...plain, id: "m2" }]),
        14,
        ["breakfast", "lunch", "dinner"],
        seed,
      );
      for (let d = 0; d < 14; d++) {
        const from = addDaysISO(START, d);
        const to = addDaysISO(START, d + 6);
        const n = plan.filter(
          (x) =>
            x.date >= from &&
            x.date <= to &&
            factsById.get(x.recipeId!)!.flags.includes("red_meat"),
        ).length;
        expect(n).toBeLessThanOrEqual(2);
      }
    }
  });

  it("a disliked dish never appears in the next plan", () => {
    const c = ctx([plain], { dislikedRecipes: new Map([["overnight_oats", "2026-11-06"]]) });
    for (const seed of [1, 2, 3, 4, 5, 6, 7, 8]) {
      expect(
        fill(c, 8, ["breakfast", "lunch", "dinner"], seed).some(
          (m) => m.recipeId === "overnight_oats",
        ),
      ).toBe(false);
    }
  });

  it("the whole filled plan validates clean and includes an Iranian dish", () => {
    const c = ctx([plain]);
    const plan = fill(c);
    expect(validatePlan(plan, c)).toEqual([]);
    expect(plan.some((m) => factsById.get(m.recipeId!)!.cuisine === "iranian")).toBe(true);
    expect(plan.some((m) => m.leftoverOf)).toBe(true);
  });

  it("is deterministic for the same input", () => {
    expect(fill(ctx([plain]), 8, ["lunch", "dinner"], 42)).toEqual(
      fill(ctx([plain]), 8, ["lunch", "dinner"], 42),
    );
  });
});

describe("prep tasks (acceptance #8 at domain level)", () => {
  it("soaking beans for tomorrow's lunch → a task tonight at 21:00", () => {
    const tasks = derivePrepTasks(
      [{ date: "2026-11-08", slot: "lunch", recipeId: "ghormeh_sabzi" }],
      factsById,
    );
    expect(tasks).toHaveLength(1);
    expect(tasks[0]).toMatchObject({ dueDate: "2026-11-07", dueTime: "21:00" });
    expect(tasks[0]!.text).toContain("خیس");
  });
  it("evening task moves to an hour before bedtime", () => {
    const tasks = derivePrepTasks(
      [{ date: "2026-11-08", slot: "lunch", recipeId: "ghormeh_sabzi" }],
      factsById,
      "23:30",
    );
    expect(tasks[0]!.dueTime).toBe("22:30");
  });
  it("leftover meals don't create prep tasks", () => {
    expect(
      derivePrepTasks(
        [
          {
            date: "2026-11-08",
            slot: "lunch",
            recipeId: "ghormeh_sabzi",
            leftoverOf: { date: "2026-11-07", slot: "dinner" },
          },
        ],
        factsById,
      ),
    ).toEqual([]);
  });
});

describe("autofill uses what is about to expire (spec §5.3 rule 6)", () => {
  it("an opened jar of eggplant caviar expiring in 3 days ends up in the plan before it expires", () => {
    const pantry = [
      {
        ingredientId: null,
        ingredientKey: "eggplant_caviar",
        expiry: addDaysISO(START, 2),
        label: "خاویار بادمجان",
      },
    ];
    // only a recipe that uses it can satisfy this; check the seed has one
    const users = facts.filter((f) => f.ingredientKeys.includes("eggplant_caviar"));
    if (users.length === 0) return;
    for (const seed of [1, 2, 3, 4, 5]) {
      const c = ctx([plain], { pantry });
      const plan = fill(c, 8, ["breakfast", "lunch", "dinner", "snack"], seed);
      expect(
        plan.some(
          (m) =>
            m.date <= addDaysISO(START, 2) &&
            factsById.get(m.recipeId!)!.ingredientKeys.includes("eggplant_caviar"),
        ),
      ).toBe(true);
    }
  });

  it("milk about to go off gets used", () => {
    const pantry = [
      { ingredientId: null, ingredientKey: "milk", expiry: addDaysISO(START, 1), label: "شیر" },
    ];
    for (const seed of [1, 2, 3]) {
      const plan = fill(
        ctx([plain], { pantry }),
        8,
        ["breakfast", "lunch", "dinner", "snack"],
        seed,
      );
      expect(
        plan.some(
          (m) =>
            m.date <= addDaysISO(START, 1) &&
            factsById.get(m.recipeId!)!.ingredientKeys.includes("milk"),
        ),
      ).toBe(true);
    }
  });
});
