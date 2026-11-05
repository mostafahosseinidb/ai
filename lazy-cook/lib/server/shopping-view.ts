import "server-only";
import { db } from "@/lib/db";
import { amountText } from "@/lib/domain/recipe-view";
import { AISLE_LABEL_FA, AISLE_ORDER } from "@/lib/domain/shopping";
import { localNames } from "./local-names";

export interface ShoppingItemDTO {
  id: string;
  name: string;
  localName: string | null;
  localNote: string | null;
  amount: string | null;
  packageHint: string | null;
  section: "required" | "optional";
  aisle: string;
  checked: boolean;
  addedBy: string;
  ingredientId: string | null;
  quantity: number | null;
  unit: string | null;
}

export async function loadShopping(
  householdId: string,
  countryCode: string | null,
): Promise<ShoppingItemDTO[]> {
  const rows = await db.shoppingItem.findMany({
    where: { householdId, deletedAt: null },
    include: { ingredient: { select: { id: true, nameFa: true } } },
    orderBy: { createdAt: "asc" },
  });
  const names = await localNames(
    rows.map((r) => r.ingredientId).filter((x): x is string => !!x),
    countryCode,
  );
  return rows
    .map((r) => {
      const all = r.ingredientId ? (names.get(r.ingredientId) ?? []) : [];
      const local = all[0];
      const q = r.quantity == null ? null : Number(r.quantity);
      return {
        id: r.id,
        name: r.ingredient?.nameFa ?? r.customName ?? "",
        // every shelf language of the country, e.g. Armenian · Russian in Yerevan
        localName: all.length ? [...new Set(all.map((n) => n.name))].join(" · ") : null,
        localNote: local?.notes ?? null,
        amount: q != null && r.unit ? amountText(q, r.unit) : null,
        packageHint: r.packageHint,
        section: r.section,
        aisle: r.aisle,
        checked: !!r.checkedAt,
        addedBy: r.addedBy,
        ingredientId: r.ingredientId,
        quantity: q,
        unit: r.unit,
      };
    })
    .sort(
      (a, b) =>
        AISLE_ORDER.indexOf(a.aisle as never) - AISLE_ORDER.indexOf(b.aisle as never) ||
        a.name.localeCompare(b.name, "fa"),
    );
}

/** Plain text for pasting into a messenger (spec §5.6). Unchecked items only. */
export function shoppingText(items: ShoppingItemDTO[]): string {
  const lines: string[] = ["🛒 لیست خرید"];
  for (const section of ["required", "optional"] as const) {
    const list = items.filter((i) => i.section === section && !i.checked);
    if (!list.length) continue;
    lines.push("", section === "required" ? "ضروری:" : "اختیاری:");
    for (const aisle of AISLE_ORDER) {
      const inAisle = list.filter((i) => i.aisle === aisle);
      if (!inAisle.length) continue;
      lines.push(`— ${AISLE_LABEL_FA[aisle]}`);
      for (const i of inAisle) {
        const local = i.localName ? ` (${i.localName})` : "";
        const amount = i.packageHint ?? i.amount;
        lines.push(`▫️ ${i.name}${local}${amount ? ` — ${amount}` : ""}`);
      }
    }
  }
  return lines.join("\n");
}
