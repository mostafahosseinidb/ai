import "server-only";
import { db } from "@/lib/db";
import { COUNTRIES } from "@/lib/countries";

export interface LocalName {
  name: string;
  language: string;
  notes: string | null;
}

/** Local shelf names for these ingredients in the user's country, in the country's language order. */
export async function localNames(
  ingredientIds: string[],
  countryCode: string | null | undefined,
): Promise<Map<string, LocalName[]>> {
  const out = new Map<string, LocalName[]>();
  if (!countryCode || !ingredientIds.length) return out;
  const order = COUNTRIES.find((c) => c.code === countryCode)?.languages ?? [];
  const rows = await db.ingredientLocalName.findMany({
    where: { ingredientId: { in: ingredientIds }, countryCode, deletedAt: null },
    orderBy: [{ upvotes: "desc" }],
  });
  for (const r of rows)
    out.set(r.ingredientId, [
      ...(out.get(r.ingredientId) ?? []),
      { name: r.name, language: r.language, notes: r.notes },
    ]);
  for (const list of out.values())
    list.sort((a, b) => rank(order, a.language) - rank(order, b.language));
  return out;
}

const rank = (order: string[], l: string) => (order.indexOf(l) === -1 ? 99 : order.indexOf(l));
