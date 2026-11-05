import { db } from "@/lib/db";
import { withHousehold } from "@/lib/server/api";
import { localNames } from "@/lib/server/local-names";

function norm(s: string) {
  return s
    .toLowerCase()
    .replace(/[‌\s]+/g, " ")
    .replace(/ي/g, "ی")
    .replace(/ك/g, "ک")
    .trim();
}

/** Ingredient search by Persian name, alias, English key, or local shelf name. */
export const GET = withHousehold(async (req, { user }) => {
  const q = norm(new URL(req.url).searchParams.get("q") ?? "");
  if (q.length < 1) return { ingredients: [] };
  const [all, local] = await Promise.all([
    db.ingredient.findMany({
      where: { deletedAt: null },
      select: {
        id: true,
        canonicalName: true,
        nameFa: true,
        aliasesFa: true,
        category: true,
        defaultUnit: true,
        shelfLifeDays: true,
      },
    }),
    user.countryCode
      ? db.ingredientLocalName.findMany({
          where: { countryCode: user.countryCode, deletedAt: null },
          select: { ingredientId: true, name: true },
        })
      : Promise.resolve([]),
  ]);
  const localById = new Map<string, string[]>();
  for (const l of local)
    localById.set(l.ingredientId, [...(localById.get(l.ingredientId) ?? []), l.name]);

  const scored = all
    .map((i) => {
      const fa = norm(i.nameFa);
      const names = [
        fa,
        ...i.aliasesFa.map(norm),
        i.canonicalName.replace(/_/g, " "),
        ...(localById.get(i.id) ?? []).map(norm),
      ];
      const score =
        fa === q
          ? 0
          : fa.startsWith(q)
            ? 1
            : names.some((n) => n.startsWith(q))
              ? 2
              : names.some((n) => n.includes(q))
                ? 3
                : 99;
      return { i, score };
    })
    .filter((x) => x.score < 99)
    .sort((a, b) => a.score - b.score || a.i.nameFa.length - b.i.nameFa.length)
    .slice(0, 15);
  const names = await localNames(
    scored.map((s) => s.i.id),
    user.countryCode,
  );
  return {
    ingredients: scored.map(({ i }) => ({
      id: i.id,
      key: i.canonicalName,
      nameFa: i.nameFa,
      category: i.category,
      defaultUnit: i.defaultUnit,
      localName: names.get(i.id)?.[0]?.name ?? null,
    })),
  };
});
