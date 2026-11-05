import { describe, expect, it } from "vitest";
import { roundQuantity, scaleIngredients, scaleQuantity } from "@/lib/domain/scaling";
import { convert, toGrams } from "@/lib/domain/units";
import { estimateDailyKcal, portionFactor } from "@/lib/domain/nutrition";
import { seed } from "./seed-fixtures";

describe("smart rounding", () => {
  it("2-person recipe for 1: 2 eggs → 1 egg", () =>
    expect(scaleQuantity(2, "piece", 0.5, "egg")).toBe(1));
  it("a single egg for one person stays 1, never 0.5", () =>
    expect(scaleQuantity(1, "piece", 0.5, "egg")).toBe(1));
  it("half an onion is allowed", () => expect(scaleQuantity(1, "piece", 0.5, "onion")).toBe(0.5));
  it("counts round to whole numbers", () => {
    expect(scaleQuantity(3, "piece", 1.5, "egg")).toBe(5); // 4.5 → 5 (rounds half up)
    expect(scaleQuantity(4, "piece", 0.6, "egg")).toBe(2); // 2.4 → 2
    expect(roundQuantity(2.6, "clove")).toBe(3);
  });
  it("spoons to the nearest quarter, minimum ¼", () => {
    expect(roundQuantity(1.1, "tbsp")).toBe(1);
    expect(roundQuantity(0.6, "tsp")).toBe(0.5);
    expect(roundQuantity(0.05, "tsp")).toBe(0.25);
    expect(roundQuantity(1.4, "cup")).toBe(1.5);
  });
  it("grams to the nearest 5", () => {
    expect(roundQuantity(123, "g")).toBe(125);
    expect(roundQuantity(437.4, "ml")).toBe(435);
    expect(roundQuantity(7.4, "g")).toBe(7);
  });
  it("pinch is at least one", () => expect(roundQuantity(0.3, "pinch")).toBe(1));
});

describe("every seed recipe scales sensibly for 1–4 people", () => {
  for (const r of seed.recipes) {
    it(r.key, () => {
      for (const people of [1, 2, 3, 4]) {
        const scaled = scaleIngredients(
          r.ingredients.map((i) => ({ key: i.ingredient, quantity: i.quantity, unit: i.unit })),
          r.base_servings,
          people,
        );
        for (const s of scaled) {
          expect(s.scaledQuantity).toBeGreaterThan(0);
          if (s.key === "egg") expect(Number.isInteger(s.scaledQuantity)).toBe(true);
          if (["piece", "clove", "slice", "bunch", "can"].includes(s.unit)) {
            expect(Number.isInteger(s.scaledQuantity * 2)).toBe(true);
          }
        }
      }
    });
  }
});

describe("unit conversion", () => {
  const milk = { ml: 1.03, cup: 245 };
  it("cup → grams uses the ingredient's weight", () => expect(toGrams(1, "cup", milk)).toBe(245));
  it("tbsp falls back through ml", () =>
    expect(toGrams(2, "tbsp", { ml: 0.92 })).toBeCloseTo(27.6));
  it("piece ↔ g", () => expect(convert(300, "g", "piece", { piece: 150 })).toBe(2));
  it("unknown conversion is null", () => expect(convert(1, "bunch", "g", {})).toBeNull());
});

describe("nutrition estimates", () => {
  it("Mifflin-St Jeor, rounded to 10", () => {
    // male, 33y, 180cm, 80kg, sedentary: BMR 1765 → ×1.2 = 2118 → 2120
    expect(
      estimateDailyKcal({
        sex: "male",
        birthYear: 1993,
        heightCm: 180,
        weightKg: 80,
        activity: "sedentary",
        goal: "maintain",
        currentYear: 2026,
      }),
    ).toBe(2120);
  });
  it("never below the 1200 floor for women", () => {
    expect(
      estimateDailyKcal({
        sex: "female",
        birthYear: 1997,
        heightCm: 150,
        weightKg: 45,
        activity: "sedentary",
        goal: "lose",
        currentYear: 2026,
      }),
    ).toBe(1200);
  });
  it("missing data → null, portion falls back to the goal", () => {
    expect(estimateDailyKcal({ activity: "light", goal: "maintain" })).toBeNull();
    expect(portionFactor({ activity: "light", goal: "lose" })).toBe(0.85);
  });
  it("tall active man ≈ 1.2+", () => {
    expect(
      portionFactor({
        sex: "male",
        birthYear: 1993,
        heightCm: 190,
        weightKg: 85,
        activity: "moderate",
        goal: "maintain",
        currentYear: 2026,
      }),
    ).toBeGreaterThanOrEqual(1.2);
  });
});
