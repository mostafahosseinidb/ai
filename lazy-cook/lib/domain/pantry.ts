/**
 * Pantry rules (spec §5.5): expiry, colour status, and deducting what a meal used.
 * Pure functions on plain data; the server layer reads and writes the rows.
 */
import { addDaysISO, diffDaysISO, type ISODate } from "@/lib/i18n/jalali";
import { convert, type GramsPerUnit } from "./units";

export type Location = "fridge" | "freezer" | "pantry";

export interface ShelfLife {
  fridge: number | null;
  freezer: number | null;
  pantry: number | null;
  after_opening_fridge: number | null;
}

/** "Opened" shelf life applies in the fridge (and the pantry, for things like jars kept out). */
export function computeExpiry(input: {
  location: Location;
  purchasedAt?: ISODate | null;
  openedAt?: ISODate | null;
  packageExpiry?: ISODate | null;
  shelfLife?: ShelfLife | null;
  today: ISODate;
}): ISODate | null {
  const candidates: ISODate[] = [];
  if (input.packageExpiry) candidates.push(input.packageExpiry);
  const base = input.purchasedAt ?? input.today;
  const life = input.shelfLife;
  if (life) {
    const days = life[input.location];
    if (days != null) candidates.push(addDaysISO(base, days));
    if (input.openedAt && input.location !== "freezer" && life.after_opening_fridge != null) {
      candidates.push(addDaysISO(input.openedAt, life.after_opening_fridge));
    }
  }
  // the real date is the earliest of them (spec: «تاریخ واقعی = کمترینِ این دو»)
  return candidates.length ? candidates.sort()[0]! : null;
}

/** True when this ingredient shouldn't be kept in this place at all (e.g. raw chicken in the cupboard). */
export function unsuitableLocation(
  location: Location,
  life?: ShelfLife | null,
  opened = false,
): boolean {
  if (opened && location === "fridge" && life?.after_opening_fridge != null) return false;
  return !!life && life[location] == null;
}

export type ExpiryTone = "ok" | "soon" | "expired" | "unknown";

/** green > 3 days, yellow 1–3 days, red today or past (spec §5.5). */
export function expiryStatus(
  expiry: ISODate | null,
  today: ISODate,
): { tone: ExpiryTone; daysLeft: number | null } {
  if (!expiry) return { tone: "unknown", daysLeft: null };
  const daysLeft = diffDaysISO(expiry, today);
  return { tone: daysLeft <= 0 ? "expired" : daysLeft <= 3 ? "soon" : "ok", daysLeft };
}

export interface PantryLot {
  id: string;
  ingredientId: string;
  quantity: number;
  unit: string;
  expiry: ISODate | null;
}

export interface Need {
  ingredientId: string;
  quantity: number;
  unit: string;
  gramsPerUnit: GramsPerUnit;
}

/**
 * Take each need out of the matching lots, soonest-expiring first, converting units through
 * grams. Returns the new quantity of every lot touched (0 = used up). Needs that can't be
 * converted, or that the pantry doesn't cover, are simply skipped — this is bookkeeping, not a gate.
 */
export function planDeduction(
  needs: Need[],
  lots: PantryLot[],
): Array<{ id: string; before: number; after: number }> {
  const remaining = new Map(lots.map((l) => [l.id, l.quantity]));
  const touched = new Map<string, number>();
  const order = [...lots].sort((a, b) => (a.expiry ?? "9999").localeCompare(b.expiry ?? "9999"));

  for (const need of needs) {
    let left = need.quantity;
    for (const lot of order) {
      if (left <= 1e-9) break;
      if (lot.ingredientId !== need.ingredientId) continue;
      const have = remaining.get(lot.id)!;
      if (have <= 0) continue;
      const haveInNeedUnit = convert(have, lot.unit, need.unit, need.gramsPerUnit);
      if (haveInNeedUnit === null || haveInNeedUnit <= 0) continue;
      const take = Math.min(left, haveInNeedUnit);
      left -= take;
      const takeInLotUnit = convert(take, need.unit, lot.unit, need.gramsPerUnit) ?? 0;
      const after = Math.max(0, round3(have - takeInLotUnit));
      remaining.set(lot.id, after);
      touched.set(lot.id, after);
    }
  }
  const byId = new Map(lots.map((l) => [l.id, l.quantity]));
  return [...touched].map(([id, after]) => ({ id, before: byId.get(id)!, after }));
}

function round3(n: number) {
  return Math.round(n * 1000) / 1000;
}

/** Where something you just bought goes by default. */
export function defaultLocation(
  category: string,
  life?: ShelfLife | null,
  opened = false,
): Location {
  if (category === "frozen") return "freezer";
  if (opened && life?.after_opening_fridge != null) return "fridge"; // «بعد از باز کردن در یخچال»
  if (category === "meat" || category === "dairy") return "fridge";
  if (life?.pantry != null && life.pantry >= 5) return "pantry";
  if (life?.fridge != null) return "fridge";
  return "pantry";
}
