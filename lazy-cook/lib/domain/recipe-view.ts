import { formatQuantity } from "@/lib/i18n/digits";
import { scaleQuantity } from "./scaling";
import { UNIT_LABEL_FA } from "./units";

export interface ViewIngredient {
  key: string;
  nameFa: string;
  quantity: number;
  unit: string;
  prepNote: string | null;
  optional: boolean;
  group: string | null;
}

export interface ScaledLine extends ViewIngredient {
  scaled: number;
  /** "۲ عدد تخم‌مرغ" / "کمی نمک" */
  text: string;
  amountText: string;
}

export function amountText(q: number, unit: string): string {
  if (unit === "pinch") return q <= 1 ? "کمی" : `${formatQuantity(q)} پَر`;
  if (q === 0.5 && ["piece", "clove", "slice", "bunch", "can"].includes(unit))
    return `نصف ${UNIT_LABEL_FA[unit] ?? unit}`;
  return `${formatQuantity(q)} ${UNIT_LABEL_FA[unit] ?? unit}`;
}

export function scaleRecipeLines(
  items: ViewIngredient[],
  baseServings: number,
  portions: number,
): ScaledLine[] {
  const factor = baseServings > 0 ? portions / baseServings : 1;
  return items.map((it) => {
    const scaled = scaleQuantity(it.quantity, it.unit, factor, it.key);
    const a = amountText(scaled, it.unit);
    return { ...it, scaled, amountText: a, text: `${a} ${it.nameFa}` };
  });
}
