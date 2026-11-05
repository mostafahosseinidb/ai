import { fa } from "@/lib/i18n/fa";
import { CodeForm } from "./code-form";

export const metadata = { title: fa.auth.code };

export default async function CodePage({
  searchParams,
}: {
  searchParams: Promise<{ email?: string }>;
}) {
  const { email } = await searchParams;
  return (
    <main className="mx-auto flex min-h-dvh max-w-md flex-col justify-center gap-6 px-4">
      <h1 className="text-2xl font-extrabold">{fa.auth.code}</h1>
      <p className="text-muted leading-7">
        {fa.auth.codeSent} <span dir="ltr">{email}</span>
      </p>
      <CodeForm email={email ?? ""} />
    </main>
  );
}
