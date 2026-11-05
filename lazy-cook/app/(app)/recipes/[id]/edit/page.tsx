import { notFound } from "next/navigation";
import { requireHousehold } from "@/lib/session";
import { loadRecipe } from "@/lib/server/recipes";
import { toInput } from "@/lib/server/recipe-write";
import { PageHeader } from "@/components/ui/primitives";
import { RecipeForm } from "@/components/recipe/recipe-form";

export const metadata = { title: "ویرایش دستور" };

export default async function EditRecipePage({ params }: { params: Promise<{ id: string }> }) {
  const { household } = await requireHousehold();
  const { id } = await params;
  const recipe = await loadRecipe(household.id, id);
  if (!recipe || recipe.ownerHouseholdId !== household.id) notFound();
  return (
    <>
      <PageHeader title="ویرایش دستور" subtitle={recipe.titleFa} />
      <RecipeForm initial={toInput(recipe)} recipeId={recipe.id} />
    </>
  );
}
