import { redirect } from "next/navigation";
import { auth, googleEnabled } from "@/auth";
import { Button, Field, inputClass } from "@/components/ui/primitives";
import { fa } from "@/lib/i18n/fa";
import { googleSignIn, sendCode } from "./actions";

export const metadata = { title: fa.auth.signIn };

export default async function SignIn({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  if ((await auth())?.user) redirect("/today");
  const { error } = await searchParams;
  return (
    <main className="mx-auto flex min-h-dvh max-w-md flex-col justify-center gap-6 px-4">
      <div>
        <div className="text-5xl" aria-hidden>
          🍳
        </div>
        <h1 className="mt-3 text-2xl font-extrabold">{fa.appName}</h1>
      </div>
      {error && (
        <p role="alert" className="bg-danger/10 text-danger rounded-[12px] p-3 text-sm">
          {fa.auth.error}
        </p>
      )}
      <form action={sendCode} className="flex flex-col gap-4">
        <Field label={fa.auth.email}>
          <input
            name="email"
            type="email"
            required
            autoComplete="email"
            dir="ltr"
            className={inputClass}
            placeholder="you@example.com"
          />
        </Field>
        <Button type="submit">{fa.auth.sendCode}</Button>
      </form>
      {googleEnabled && (
        <>
          <div className="text-muted text-center text-sm">{fa.auth.or}</div>
          <form action={googleSignIn}>
            <Button type="submit" variant="secondary" className="w-full">
              {fa.auth.google}
            </Button>
          </form>
        </>
      )}
      <p className="text-muted text-xs leading-6">{fa.healthDisclaimer}</p>
    </main>
  );
}
