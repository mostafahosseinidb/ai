import { requireHousehold } from "@/lib/session";
import { PageHeader } from "@/components/ui/primitives";
import { RecipeForm } from "@/components/recipe/recipe-form";

export const metadata = { title: "دستور جدید" };

export default async function NewRecipePage() {
  await requireHousehold();
  return (
    <>
      <PageHeader title="دستور جدید" subtitle="فقط خونه‌ی خودتون می‌بینه." />
      <RecipeForm />
    </>
  );
}
