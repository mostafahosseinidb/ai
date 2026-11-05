import { describe, expect, it } from "vitest";
import {
  computeExpiry,
  expiryStatus,
  planDeduction,
  unsuitableLocation,
} from "@/lib/domain/pantry";
import {
  aggregate,
  buildShoppingList,
  packageHint,
  type IngredientInfo,
} from "@/lib/domain/shopping";
import { ingredientsByKey } from "./seed-fixtures";

const T = "2026-11-07";

function info(key: string): IngredientInfo {
  const i = ingredientsByKey.get(key)!;
  return {
    id: key,
    key,
    nameFa: i.name_fa,
    category: i.category,
    defaultUnit: i.default_unit,
    gramsPerUnit: i.grams_per_unit,
  };
}
const infos = (...keys: string[]) => new Map(keys.map((k) => [k, info(k)]));

describe("expiry", () => {
  const milk = ingredientsByKey.get("milk")!.shelf_life_days;

  it("fridge shelf life from the purchase date", () => {
    expect(computeExpiry({ location: "fridge", purchasedAt: T, shelfLife: milk, today: T })).toBe(
      `2026-11-${String(7 + milk.fridge!).padStart(2, "0")}`,
    );
  });

  it("opened jar of eggplant caviar: 72 hours after opening (acceptance #5)", () => {
    const life = ingredientsByKey.get("eggplant_caviar")!.shelf_life_days;
    expect(life.after_opening_fridge).toBe(3);
    expect(
      computeExpiry({
        location: "fridge",
        purchasedAt: "2026-10-01",
        openedAt: T,
        shelfLife: life,
        today: T,
      }),
    ).toBe("2026-11-10");
  });

  it("the earlier of package date and shelf life wins", () => {
    expect(
      computeExpiry({
        location: "fridge",
        purchasedAt: T,
        packageExpiry: "2026-11-08",
        shelfLife: milk,
        today: T,
      }),
    ).toBe("2026-11-08");
  });

  it("opening doesn't shorten frozen food", () => {
    const life = { fridge: 3, freezer: 90, pantry: null, after_opening_fridge: 2 };
    expect(
      computeExpiry({
        location: "freezer",
        purchasedAt: T,
        openedAt: T,
        shelfLife: life,
        today: T,
      }),
    ).toBe("2027-02-05");
  });

  it("custom items with no data have no expiry; raw chicken in the cupboard is flagged", () => {
    expect(computeExpiry({ location: "pantry", today: T })).toBeNull();
    expect(
      unsuitableLocation("pantry", ingredientsByKey.get("chicken_breast")!.shelf_life_days),
    ).toBe(true);
    expect(
      unsuitableLocation("fridge", ingredientsByKey.get("chicken_breast")!.shelf_life_days),
    ).toBe(false);
  });

  it("colours: green > 3 days, yellow 1–3, red today or past", () => {
    expect(expiryStatus("2026-11-11", T).tone).toBe("ok");
    expect(expiryStatus("2026-11-10", T).tone).toBe("soon");
    expect(expiryStatus("2026-11-08", T).tone).toBe("soon");
    expect(expiryStatus(T, T).tone).toBe("expired");
    expect(expiryStatus("2026-11-01", T)).toEqual({ tone: "expired", daysLeft: -6 });
    expect(expiryStatus(null, T).tone).toBe("unknown");
  });
});

describe("deducting a cooked meal from the pantry", () => {
  const egg = ingredientsByKey.get("egg")!.grams_per_unit;
  it("takes from the lot that expires first", () => {
    const res = planDeduction(
      [{ ingredientId: "egg", quantity: 3, unit: "piece", gramsPerUnit: egg }],
      [
        { id: "late", ingredientId: "egg", quantity: 6, unit: "piece", expiry: "2026-11-30" },
        { id: "soon", ingredientId: "egg", quantity: 2, unit: "piece", expiry: "2026-11-09" },
      ],
    );
    expect(res).toEqual(
      expect.arrayContaining([
        { id: "soon", before: 2, after: 0 },
        { id: "late", before: 6, after: 5 },
      ]),
    );
  });

  it("converts units through grams", () => {
    const milk = ingredientsByKey.get("milk")!.grams_per_unit;
    const [r] = planDeduction(
      [{ ingredientId: "milk", quantity: 1, unit: "cup", gramsPerUnit: milk }],
      [{ id: "m", ingredientId: "milk", quantity: 1000, unit: "ml", expiry: null }],
    );
    expect(r!.after).toBeGreaterThan(740);
    expect(r!.after).toBeLessThan(770);
  });

  it("never goes below zero and ignores what it can't convert", () => {
    expect(
      planDeduction(
        [{ ingredientId: "egg", quantity: 10, unit: "piece", gramsPerUnit: egg }],
        [{ id: "e", ingredientId: "egg", quantity: 2, unit: "piece", expiry: null }],
      ),
    ).toEqual([{ id: "e", before: 2, after: 0 }]);
    expect(
      planDeduction(
        [{ ingredientId: "x", quantity: 1, unit: "bunch", gramsPerUnit: {} }],
        [{ id: "x", ingredientId: "x", quantity: 100, unit: "g", expiry: null }],
      ),
    ).toEqual([]);
  });
});

describe("shopping list", () => {
  it("sums one ingredient across recipes into its default unit", () => {
    const [onion] = aggregate(
      [
        { ingredientId: "onion", quantity: 1, unit: "piece", optional: false },
        { ingredientId: "onion", quantity: 300, unit: "g", optional: false },
      ],
      infos("onion"),
    );
    const per = ingredientsByKey.get("onion")!.grams_per_unit.piece!;
    expect(onion!.unit).toBe("piece");
    expect(onion!.quantity).toBeCloseTo(1 + 300 / per);
  });

  it("subtracts the pantry, with unit conversion", () => {
    const list = buildShoppingList(
      [{ ingredientId: "milk", quantity: 2, unit: "cup", optional: false }],
      [{ ingredientId: "milk", quantity: 250, unit: "ml" }],
      infos("milk"),
    );
    expect(list).toHaveLength(1);
    expect(list[0]!.unit).toBe("ml");
    expect(list[0]!.quantity).toBeGreaterThan(200);
    expect(list[0]!.quantity).toBeLessThan(260);
  });

  it("drops what the pantry already covers, and never lists water", () => {
    const list = buildShoppingList(
      [
        { ingredientId: "egg", quantity: 4, unit: "piece", optional: false },
        { ingredientId: "water", quantity: 500, unit: "ml", optional: false },
      ],
      [{ ingredientId: "egg", quantity: 6, unit: "piece" }],
      infos("egg", "water"),
    );
    expect(list).toEqual([]);
  });

  it("required vs optional: optional ingredients and staples go to optional", () => {
    const list = buildShoppingList(
      [
        { ingredientId: "beef_mince", quantity: 400, unit: "g", optional: false },
        { ingredientId: "parmesan", quantity: 20, unit: "g", optional: true },
        { ingredientId: "salt", quantity: 1, unit: "tsp", optional: false },
      ],
      [],
      infos("beef_mince", "parmesan", "salt"),
    );
    const by = Object.fromEntries(list.map((l) => [l.ingredientId, l.section]));
    expect(by).toEqual({ beef_mince: "required", parmesan: "optional", salt: "optional" });
  });

  it("is grouped by aisle, produce first", () => {
    const list = buildShoppingList(
      [
        { ingredientId: "basmati_rice", quantity: 300, unit: "g", optional: false },
        { ingredientId: "milk", quantity: 200, unit: "ml", optional: false },
        { ingredientId: "tomato", quantity: 3, unit: "piece", optional: false },
      ],
      [],
      infos("basmati_rice", "milk", "tomato"),
    );
    expect(list.map((l) => l.aisle)).toEqual(["produce", "dairy", "grains_legumes"]);
  });

  it("package hints talk like a shopper", () => {
    expect(packageHint(3, "piece", info("tomato"))).toBe("۳ عدد");
    expect(packageHint(4, "clove", info("garlic"))).toBe("۱ بوته");
    expect(packageHint(450, "g", info("beef_mince"))).toBe("نیم کیلو");
    expect(packageHint(300, "ml", info("heavy_cream"))).toBe("۱ بطری نیم‌لیتری");
    expect(packageHint(180, "g", info("basmati_rice"))).toBe("۱ بسته‌ی کوچک");
    expect(packageHint(1, "tsp", info("turmeric"))).toBe("۱ بسته‌ی کوچک");
  });
});

describe("default storage place", () => {
  it("an opened jar goes in the fridge, an unopened one in the cupboard", async () => {
    const { defaultLocation } = await import("@/lib/domain/pantry");
    const life = ingredientsByKey.get("eggplant_caviar")!.shelf_life_days;
    expect(defaultLocation("canned", life, false)).toBe("pantry");
    expect(defaultLocation("canned", life, true)).toBe("fridge");
    expect(defaultLocation("meat", ingredientsByKey.get("beef_mince")!.shelf_life_days)).toBe(
      "fridge",
    );
    expect(defaultLocation("frozen", null)).toBe("freezer");
  });
});

describe("shopping rounds up", () => {
  it("1.3 cucumbers → buy 2; 123 g → 125; ⅓ tsp → ½", async () => {
    const { roundUpForShopping } = await import("@/lib/domain/shopping");
    expect(roundUpForShopping(1.3, "piece")).toBe(2);
    expect(roundUpForShopping(2, "piece")).toBe(2);
    expect(roundUpForShopping(123, "g")).toBe(125);
    expect(roundUpForShopping(0.33, "tsp")).toBe(0.5);
  });
  it("a sliver short isn't listed", () => {
    const list = buildShoppingList(
      [{ ingredientId: "egg", quantity: 4.2, unit: "piece", optional: false }],
      [{ ingredientId: "egg", quantity: 4, unit: "piece" }],
      infos("egg"),
    );
    expect(list).toEqual([]);
  });
});

describe("storage warnings", () => {
  it("an opened jar belongs in the fridge even if the sealed jar lives in the cupboard", () => {
    const life = ingredientsByKey.get("eggplant_caviar")!.shelf_life_days;
    expect(unsuitableLocation("fridge", life, true)).toBe(false);
  });
});
