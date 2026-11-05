import { notFound } from "next/navigation";
import { Clock, Flame, Snowflake } from "lucide-react";
import { requireHousehold } from "@/lib/session";
import { loadRecipe, toFacts } from "@/lib/server/recipes";
import { loadMembers, memberFacts } from "@/lib/server/household";
import { recipeConflict, matchesTextDislike } from "@/lib/domain/rules";
import { Badge, Card } from "@/components/ui/primitives";
import { fa } from "@/lib/i18n/fa";
import { formatNumber, toFaDigits } from "@/lib/i18n/digits";
import { RecipeBody } from "./recipe-body";
import { RecipeActions } from "./recipe-actions";
import { db } from "@/lib/db";

export async function generateMetadata({ params }: { params: Promise<{ id: string }> }) {
  const { household } = await requireHousehold();
  const r = await loadRecipe(household.id, (await params).id);
  return { title: r?.titleFa ?? fa.recipes.title };
}

export default async function RecipePage({ params }: { params: Promise<{ id: string }> }) {
  const { household } = await requireHousehold();
  const recipe = await loadRecipe(household.id, (await params).id);
  if (!recipe) notFound();
  const [members, wish] = await Promise.all([
    loadMembers(household.id),
    db.wishlistItem.findFirst({
      where: { householdId: household.id, recipeId: recipe.id, doneAt: null, deletedAt: null },
    }),
  ]);
  const facts = toFacts(recipe);

  const missing = recipe.requiresEquipment.filter((e) => !household.kitchenEquipment.includes(e));
  const conflicts = memberFacts(members).flatMap((m) => {
    const c = recipeConflict(facts, m);
    const d = matchesTextDislike(facts, m);
    return [
      c && `${m.displayName}: ${fa.limits[c] ?? fa.intolerances[c] ?? c}`,
      d && `${m.displayName} «${d}» دوست ندارد`,
    ].filter(Boolean) as string[];
  });
  const householdPortions =
    members.reduce((a, m) => a + Number(m.portionFactor), 0) || recipe.baseServings;
  const n = recipe.nutritionPerServing as { kcal: number; protein: number } | null;
  const prep = (recipe.prepAhead ?? []) as Array<{ text: string; hours_before: number }>;

  return (
    <article className="flex flex-col gap-4 px-4 pt-5">
      <header>
        <h1 className="text-3xl font-extrabold">{recipe.titleFa}</h1>
        {recipe.titleOriginal && recipe.titleOriginal !== recipe.titleFa && (
          <p className="text-muted text-sm" dir="auto">
            {recipe.titleOriginal}
          </p>
        )}
        {recipe.introFa && <p className="mt-2 leading-7">{recipe.introFa}</p>}
        <div className="mt-3 flex flex-wrap gap-1.5">
          <Badge tone="primary">{fa.cuisines[recipe.cuisine]}</Badge>
          <Badge>
            <Clock className="me-1 size-3" />
            {fa.recipes.activeTime} {toFaDigits(recipe.activeMinutes)} · {fa.recipes.totalTime}{" "}
            {toFaDigits(recipe.totalMinutes)} {fa.recipes.minutes}
          </Badge>
          <Badge>{fa.recipes.difficulty[recipe.difficulty]}</Badge>
          {recipe.tags
            .filter((t) => t !== "no_oven")
            .map((t) => (
              <Badge key={t}>{fa.tags[t]}</Badge>
            ))}
        </div>
      </header>

      <RecipeActions
        recipeId={recipe.id}
        own={recipe.ownerHouseholdId === household.id}
        inWishlist={!!wish}
      />

      {(missing.length > 0 || conflicts.length > 0) && (
        <div role="note" className="bg-warning/10 rounded-[12px] p-3 text-sm leading-6">
          <p className="font-bold">{fa.recipes.unsuitable}</p>
          <ul className="list-inside list-disc">
            {missing.map((e) => (
              <li key={e}>
                {fa.recipes.needs} {fa.equipment[e]}
              </li>
            ))}
            {conflicts.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        </div>
      )}
      {recipe.equipmentAlternativesFa && (
        <Card className="bg-accent/10">
          <p className="text-sm leading-6">
            <b>{missing.includes("oven") ? fa.recipes.noOvenVersion : "نسخه‌ی جایگزین"}:</b>{" "}
            {recipe.equipmentAlternativesFa}
          </p>
        </Card>
      )}

      {prep.length > 0 && (
        <Card className="border-accent/50 bg-accent/10">
          <p className="font-bold">🌙 {fa.recipes.prepAhead}</p>
          {prep.map((p) => (
            <p key={p.text} className="text-sm leading-6">
              {p.text}
            </p>
          ))}
        </Card>
      )}

      <RecipeBody
        baseServings={recipe.baseServings}
        householdPortions={Math.round(householdPortions * 100) / 100}
        ingredients={recipe.ingredients.map((i) => ({
          key: i.ingredient.canonicalName,
          nameFa: i.ingredient.nameFa,
          quantity: Number(i.quantity),
          unit: i.unit,
          prepNote: i.prepNote,
          optional: i.optional,
          group: i.group,
        }))}
        steps={recipe.steps as Array<{ text: string; timer_seconds?: number | null }>}
      />

      {n && (
        <Card className="flex items-center gap-4 text-sm">
          <span className="flex items-center gap-1">
            <Flame className="text-warning size-4" aria-hidden />
            {fa.approx} {formatNumber(n.kcal, 0)} {fa.recipes.kcal}
          </span>
          <span>
            💪 {fa.approx} {formatNumber(n.protein, 0)} {fa.recipes.protein}
          </span>
          <span className="text-muted text-xs">({fa.recipes.perServing}، تقریبی)</span>
        </Card>
      )}

      <Card className="flex flex-col gap-1 text-sm leading-6">
        {recipe.freezerFriendly && (
          <p className="flex items-center gap-1">
            <Snowflake className="text-primary size-4" aria-hidden /> {fa.recipes.freezer}
          </p>
        )}
        {recipe.fridgeDays != null && (
          <p>🧊 {fa.recipes.fridgeDays(toFaDigits(recipe.fridgeDays))}</p>
        )}
        {recipe.reheatNote && (
          <p>
            🔥 {fa.recipes.reheat}: {recipe.reheatNote}
          </p>
        )}
        {recipe.sourceCredit && <p className="text-muted">منبع: {recipe.sourceCredit}</p>}
      </Card>
    </article>
  );
}
